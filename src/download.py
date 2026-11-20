"""Fetch the raw files into data/. LME official prices come from Westmetall's public tables, one
html page per year; the federal funds rate and the euro from FRED; the four CME premium futures
from TradingView, one file per contract, which needs tvDatafeed; monthly imports and exports of
unwrought aluminium (HS 7601) from UN Comtrade and Eurostat Comext. The files are committed, so
the repo never has to run this.

Price files only grow: a rerun adds the dates printed since the last one and rewrites nothing.
Trade files only grow too, but a month the source republishes comes back as new rows next to the
old ones: every vintage downloaded is kept. data/press, data/policy, data/freight and
data/portwatch are typed or downloaded by hand and are not fetched.

    python -m src.download                 # everything
    python -m src.download lme premiums    # only these

Requests go through curl rather than urllib: FRED sits behind a CDN that hangs up on Python's
TLS handshake, while curl on the same URL answers in half a second. Comtrade needs no key; with
one in the environment variable COMTRADE_KEY it serves the same data faster.
"""

import io
import json
import os
import re
import subprocess
import sys
import time

import comtradeapicall as ct
import pandas as pd
from tvDatafeed import Interval, TvDatafeed

from src import config as c

CURL = ["curl", "-sS", "-L", "--retry", "3", "--max-time", "300"]
WESTMETALL = "https://www.westmetall.com/en/markdaten.php?action=table&field=LME_Al_cash&year="
FIRST_YEAR = 2008  # the first year Westmetall serves
# one table row: date, cash settlement, three-month, stock
ROW = (r'<td >(\d+\. \w+ \d{4})</td>\s*<td >(.*?)</td>\s*<td >(.*?)</td>\s*'
       r'<td class="last">(.*?)</td>')
FRED = {"effr": ("EFFR", "rate"), "eurusd": ("DEXUSEU", "eurusd")}  # file -> series id, column

FIRST_CONTRACT = "2016-01"  # the first contract month the panel can need
MONTHS_AHEAD = 4            # the arrival month of the longest voyage is at most two months out
BARS = 100                  # settles per contract, enough to reach back from any arrival month

# UN Comtrade reporter and flow -> file and first month. The United States and Japan declare their
# imports, Canada its exports, read for Quebec's turn to Europe in 2025 only. Release dates come
# from Comtrade's data availability, one call per reporter.
COMTRADE = {(842, "M"): ("comtrade_us_imports", c.FLOWS_START),
            (392, "M"): ("comtrade_japan_imports", c.FLOWS_START),
            (124, "X"): ("comtrade_canada_exports", "2024-01")}
AVAILABILITY = "https://comtradeapi.un.org/public/v1/getDA/C/M/HS?reporterCode="
# Eurostat Comext, EU-27 imports from the origins, in 100 kg
COMEXT = ("https://ec.europa.eu/eurostat/api/comext/dissemination/sdmx/2.1/data/DS-045409/"
          "M.EU27_2020.IS+CA+AE+OM+IN+MY.7601.1.QUANTITY_IN_100KG?format=SDMX-CSV&startPeriod=")


def get(url):
    return subprocess.run(CURL + [url], capture_output=True, check=True).stdout


def save(new, path):
    """Append the new dates to the file. A date already on file keeps its first print: an
    official price, a settle or a published rate is not meant to change, so a difference is
    reported and not written."""
    if path.exists():
        old = pd.read_csv(path, index_col="date", parse_dates=True)
        again = new.reindex(old.index)  # the dates fetched a second time
        changed = (again.notna() & (again != old)).any(axis=1)
        if changed.any():
            print(f"{path.stem}: {changed.sum()} dates differ from the file, first print kept")
        new = pd.concat([old, new[~new.index.isin(old.index)]])
    new = new.sort_index()
    new.to_csv(path, date_format="%Y-%m-%d")
    print(f"{path.stem}: {len(new)} dates to {new.index.max():%Y-%m-%d}")


def append(new, path, key):
    """Append the rows of a trade download that the file does not hold yet: new months, and the
    months the source has republished since. Nothing on file is rewritten."""
    if path.exists():
        old = pd.read_csv(path, dtype=str)
        seen = pd.MultiIndex.from_frame(old[key])
        new = new[~pd.MultiIndex.from_frame(new[key].astype(str)).isin(seen)]
    new.to_csv(path, mode="a", header=not path.exists(), index=False)
    print(f"{path.stem}: {len(new)} rows added")


def lme():
    """Cash settlement and three-month official prices in USD/t and the LME stock in tonnes, as
    Westmetall prints them. Only the years from the last date on file are asked for again."""
    path = c.DATA / "lme" / "aluminium.csv"
    first = int(pd.read_csv(path)["date"].max()[:4]) if path.exists() else FIRST_YEAR
    rows = []
    for year in range(first, pd.Timestamp.now().year + 1):
        rows += re.findall(ROW, get(WESTMETALL + str(year)).decode("utf-8", "ignore"))
        time.sleep(1)  # a small site: one page a second
    df = pd.DataFrame(rows, columns=["date", "cash", "three_month", "stock"])
    df["date"] = pd.to_datetime(df["date"], format="%d. %B %Y")
    df = df.set_index("date")
    for column in df:
        # 3,150.50 becomes 3150.5; a cell the source leaves empty is printed "-"
        df[column] = pd.to_numeric(df[column].str.replace(",", ""), errors="coerce")
    save(df, path)


def fred():
    """The effective federal funds rate in % a year, and the dollar price of a euro, on the days
    each is printed."""
    for name, (series, column) in FRED.items():
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
        df = pd.read_csv(io.BytesIO(get(url)), na_values=".")
        df.columns = ["date", column]
        df["date"] = pd.to_datetime(df["date"])
        save(df.dropna().set_index("date"), c.DATA / "fred" / f"{name}.csv")


def premiums():
    """Daily settles of the four premium futures, one file per contract month. A contract that
    has expired and is already on file is not asked for again."""
    os.environ["TZ"] = "UTC"  # tvDatafeed returns the stamps in the machine's local time
    time.tzset()
    tv = TvDatafeed()
    now = pd.Timestamp.now().to_period("M")
    for symbol in c.PREMIUMS.values():
        for month in pd.period_range(FIRST_CONTRACT, now + MONTHS_AHEAD, freq="M"):
            contract = f"{symbol}{c.MONTH_CODES[month.month - 1]}{month.year}"
            path = c.DATA / "premiums" / f"{contract}.csv"
            if path.exists() and month < now - 1:
                continue
            for _ in range(3):  # the socket drops now and then
                bars = tv.get_hist(symbol=contract, exchange="COMEX", interval=Interval.in_daily,
                                   n_bars=BARS)
                if bars is not None and not bars.empty:
                    break
            if bars is None or bars.empty:  # never the continuous series: one file, one contract
                print(f"{contract}: nothing returned, skipped")
                continue
            # a daily bar is stamped at its session open, for CME the evening before: +6h lands
            # it on its trade date, whatever the season
            settle = bars[["close"]].rename(columns={"close": "settle"})
            settle.index = (settle.index + pd.Timedelta(hours=6)).normalize().rename("date")
            # today's bar may still be trading: its settle is left for the next run
            save(settle[settle.index < pd.Timestamp.now(tz="America/New_York").normalize()
                        .tz_localize(None)], path)


def comtrade():
    """One reporter's trade in HS 7601 with every partner, a month per call, kept with the dates
    Comtrade first and last released that month. A month already on file in its latest release
    is not asked for again, so a run the quota stops is finished by the next one. Without a key
    the quota is about a hundred calls an hour."""
    key = os.environ.get("COMTRADE_KEY")
    for (reporter, flow), (name, first) in COMTRADE.items():
        path = c.DATA / "flows" / f"{name}.csv"
        answer = json.loads(get(AVAILABILITY + str(reporter)))
        if "data" not in answer:  # out of quota: stop, the next run carries on
            print(f"{name}: stopped at the release dates, {answer.get('message')}")
            return
        releases = pd.DataFrame(answer["data"])
        releases = releases[releases["period"].astype(str) >= first.replace("-", "")]
        on_file = set()
        if path.exists():
            old = pd.read_csv(path, dtype=str)
            on_file = set(zip(old["period"], old["lastReleased"]))
        for _, month in releases.sort_values("period").iterrows():
            if (str(month["period"]), month["lastReleased"]) in on_file:
                continue
            args = dict(typeCode="C", freqCode="M", clCode="HS", period=str(month["period"]),
                        reporterCode=str(reporter), cmdCode="7601", flowCode=flow,
                        partnerCode=None, partner2Code=None, customsCode=None, motCode=None,
                        breakdownMode="classic", includeDesc=True)
            try:
                df = ct.getFinalData(key, **args) if key else ct.previewFinalData(**args)
            except RuntimeError as error:  # out of quota: stop, the next run carries on
                print(f"{name}: stopped at {month['period']}, {error}")
                return
            time.sleep(1)
            if df is None or df.empty:
                continue
            df = df[["period", "reporterCode", "flowCode", "partnerCode", "partnerDesc",
                     "cmdCode", "netWgt", "primaryValue"]].copy()
            df["firstReleased"] = month["firstReleased"]
            df["lastReleased"] = month["lastReleased"]
            df["downloaded"] = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S")
            append(df, path, ["period", "partnerCode", "lastReleased"])


def comext():
    """EU-27 imports of HS 7601 from each origin, as Eurostat publishes them, with the date it
    last updated the series."""
    df = pd.read_csv(io.BytesIO(get(COMEXT + c.FLOWS_START)))
    df["downloaded"] = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S")
    append(df, c.DATA / "flows" / "comext_eu_imports.csv", ["partner", "TIME_PERIOD", "OBS_VALUE"])


SOURCES = {"lme": lme, "fred": fred, "premiums": premiums, "comtrade": comtrade, "comext": comext}

if __name__ == "__main__":
    for folder in ("lme", "fred", "premiums", "flows"):
        (c.DATA / folder).mkdir(parents=True, exist_ok=True)
    for name in sys.argv[1:] or SOURCES:
        print(name)
        SOURCES[name]()
