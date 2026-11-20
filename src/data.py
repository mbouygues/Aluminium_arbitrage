"""From the DuckDB views to one point-in-time frame per route, a row per LME trade date.

    python -m src.data                    ->  tables routes and panel: one row per route and
                                              trade date, every number a trader could read at
                                              that day's New York close
    dataset("gulf", "rotterdam", "direct") ->  the stored panel of one route, as a frame

Every number carries two stamps. Its date is the day on the file; its _utc stamp is the instant
it became readable, the source's local publication time converted to UTC, so that London and
New York prints sit on one clock. A row is anchored at asof_utc, the New York close. The two LME
official prices are one print and both come from the row's own trade date. Every other leg
joins with merge_asof backward on these instants: the latest number already published, never
one that came after the anchor. A price leg older than the tolerance drops the row rather than
being carried on.

The metal is sold on the premium of the month it arrives: each premium leg is the contract of
the arrival month, trade date plus the days at sea and on land. The rules that are not prices
join on dates. A section 232 rate applies if it was proclaimed by the trade date and is in force
on the arrival date. A war surcharge applies from its effective date, a CBAM price from the day
the Commission publishes it. PortWatch publishes a week of transits every Tuesday: a day's count
is readable from the Tuesday after its week.

days is the time between the LME cash and three-month prompts: the curve cost of a voyage is the
cash to three-month spread, per day of that time, times the days the metal travels.
"""

import numpy as np
import pandas as pd

from src import config as c, routes, sql


def to_utc(dates, market):
    """The local wall-clock time of a market on each date, as a UTC instant."""
    tz, time = market
    return (dates + pd.Timedelta(time)).dt.tz_localize(tz).dt.tz_convert("UTC")


def series(name):
    """One series of the prices view, stamped with the instant each number became readable."""
    df = sql.query(f"SELECT obs_date AS {name}_date, value AS {name} FROM prices "
                   f"WHERE series = '{name}' ORDER BY obs_date")
    published = df[f"{name}_date"]
    if name in ("rate", "eurusd"):
        # the New York Fed and the Fed's H.10 print a day's number the next business day
        published = published.shift(-1).fillna(published + pd.offsets.BDay(1))
    df[f"{name}_utc"] = to_utc(published, c.CLOSE[name])
    return df


def futures(product):
    """One premium's settles, every contract, stamped with the instant each became readable."""
    df = sql.query(f"SELECT contract, delivery_month, trade_date, settle FROM futures "
                   f"WHERE product = '{product}' ORDER BY delivery_month, trade_date")
    df["settle_utc"] = to_utc(df["trade_date"], c.CLOSE[product])
    return df


def prompts(trade_date, business_days):
    """The cash and three-month prompt dates of each trade date, LME Trading Regulation 8."""
    weekdays = pd.bdate_range(business_days.min(), business_days.max())
    holidays = weekdays.difference(business_days).append(pd.to_datetime(c.HOLIDAYS_AHEAD))
    closed = holidays.to_numpy().astype("datetime64[D]")

    day = trade_date.to_numpy().astype("datetime64[D]")
    cash_prompt = np.busday_offset(day, c.CASH_DAYS, holidays=closed)

    target = trade_date + pd.DateOffset(months=c.PROMPT_MONTHS)
    day = target.to_numpy().astype("datetime64[D]")
    # not a business day: the next one, or the one before if the next has left the month
    prompt = np.busday_offset(day, 0, roll="modifiedfollowing", holidays=closed)
    before = np.busday_offset(day, 0, roll="backward", holidays=closed)
    # a Saturday after an open Friday, Good Friday and a weekday Christmas Day go back instead
    saturday = (target.dt.dayofweek == 5) & ~(target - pd.Timedelta(days=1)).isin(holidays)
    good_friday = (target.dt.dayofweek == 4) & target.dt.month.isin([3, 4]) & target.isin(holidays)
    christmas = ((target.dt.month == 12) & (target.dt.day == 25)
                 & target.dt.dayofweek.between(1, 4))
    prompt = np.where(saturday | good_friday | christmas, before, prompt)
    return pd.to_datetime(cash_prompt), pd.to_datetime(prompt)


def market():
    """Each LME trade date as of the New York close: the two official prices and their prompt
    dates, and the latest funding rate and euro already published."""
    cash = series("cash")
    three_month = series("three_month")

    # the two official prices are one print: both from the row's own trade date, or no row
    df = cash.merge(three_month, left_on="cash_date", right_on="three_month_date")
    df = df.rename(columns={"cash_date": "trade_date", "cash_utc": "price_utc"})
    df = df[["trade_date", "cash", "three_month", "price_utc"]]
    df["asof_utc"] = to_utc(df["trade_date"], c.ASOF)
    df["cash_prompt"], df["prompt"] = prompts(df["trade_date"], cash["cash_date"])
    df["days"] = (df["prompt"] - df["cash_prompt"]).dt.days
    df = df[df["trade_date"] >= c.SAMPLE_START]

    for name in ("rate", "eurusd"):
        df = pd.merge_asof(df, series(name), left_on="asof_utc", right_on=f"{name}_utc",
                           direction="backward", tolerance=pd.Timedelta(c.TOLERANCE))
    return df.dropna()  # a leg missing, or older than the tolerance, drops the row


def traffic():
    """Container-ship transits at each passage a route can take, averaged over a week and divided
    by their 2019 average, with the instant PortWatch published them."""
    df = sql.query(f"SELECT date, portname, {c.TRANSITS} AS transits FROM transits "
                   f"WHERE portname IN {tuple(c.PASSAGES.values())}")
    df["date"] = pd.to_datetime(df["date"])
    df = df.pivot(index="date", columns="portname", values="transits").sort_index()
    share = df.rolling("7D").mean() / df.loc["2019"].mean()
    # the week to Sunday comes out on the Tuesday after it
    share["published"] = share.index + pd.to_timedelta(8 - share.index.dayofweek, unit="D")
    share = share.reset_index().melt(id_vars=["date", "published"], var_name="portname",
                                     value_name="share")
    share["published_utc"] = to_utc(share["published"], c.PORTWATCH_RELEASE)
    return share.sort_values(["published_utc", "date"])


def tariff(df, group):
    """The section 232 rate on each row: the latest change proclaimed by the trade date that is
    in force on the arrival date. The changes are in the same order by either date."""
    t = sql.query(f"SELECT signed, effective, rate FROM tariffs "
                  f"WHERE origin_group = '{group}' ORDER BY signed").reset_index()
    known = pd.merge_asof(df, t[["signed", "index"]], left_on="trade_date", right_on="signed")
    in_force = pd.merge_asof(df, t[["effective", "index"]], left_on="arrival",
                             right_on="effective")
    return t.loc[np.minimum(known["index"], in_force["index"]), "rate"].to_numpy()


def align(origin, destination, variant):
    """One route on each trade date as of the New York close: LME, funding and euro, the
    premium of the arrival month, and the duties, surcharges and traffic known that day."""
    r = sql.query(f"SELECT * FROM routes WHERE origin = '{origin}' "
                  f"AND destination = '{destination}' AND variant = '{variant}'").iloc[0]
    df = market()

    # the metal arrives after the days at sea and on land, and is sold on that month's premium
    transit = int(np.ceil(r["sea_days"] + r["land_days"]))  # whole days
    df["arrival"] = df["trade_date"] + pd.Timedelta(days=transit)
    df["delivery_month"] = df["arrival"].dt.to_period("M").dt.to_timestamp()
    legs = {"premium": c.PREMIUMS[destination]}
    if destination == "rotterdam":
        legs["premium_du"] = c.DUTY_UNPAID
    for leg, product in legs.items():
        f = futures(product).add_prefix(f"{leg}_").sort_values(f"{leg}_settle_utc")
        df = pd.merge_asof(df, f, left_on="asof_utc", right_on=f"{leg}_settle_utc",
                           left_by="delivery_month", right_by=f"{leg}_delivery_month",
                           direction="backward", tolerance=pd.Timedelta(c.TOLERANCE))
        df = df.drop(columns=f"{leg}_delivery_month")
    df = df.dropna()  # a premium missing, or older than the tolerance, drops the row

    df["us_tariff"] = tariff(df, c.ORIGINS[origin]["tariff"])
    # the latest CBAM price published by the trade date; none before April 2026
    cbam = sql.query("SELECT published AS cbam_published, eur_per_tco2 AS cbam_price "
                     "FROM cbam_price ORDER BY published")
    df = pd.merge_asof(df, cbam, left_on="trade_date", right_on="cbam_published",
                       tolerance=pd.Timedelta(c.CBAM_TOLERANCE))
    # carrier war surcharges in USD per box, on Gulf cargo and on a Red Sea transit: in force
    # until the file records the next change
    war = sql.query("SELECT effective, area, usd_per_teu FROM war_surcharges ORDER BY effective")
    for area, applies in (("gulf", origin == "gulf"), ("red_sea", "babalmandab" in r["passages"])):
        w = war[war["area"] == area].drop(columns="area")
        df = pd.merge_asof(df, w.rename(columns={"usd_per_teu": f"war_{area}"}),
                           left_on="trade_date", right_on="effective").drop(columns="effective")
        df[f"war_{area}"] = df[f"war_{area}"] * applies

    # the traffic at each passage as published by the anchor, against its 2019 average: the
    # route's is that of its most disrupted passage, and Bab el-Mandeb's sets the route rule
    share = traffic()
    names = [c.PASSAGES[p] for p in r["passages"].split(",") if p in c.PASSAGES]
    shares = pd.DataFrame(index=df.index)
    for name in set(names) | {c.PASSAGES["babalmandab"]}:
        s = share.loc[share["portname"] == name, ["published_utc", "share"]]
        joined = pd.merge_asof(df[["asof_utc"]], s, left_on="asof_utc", right_on="published_utc",
                               direction="backward",
                               tolerance=pd.Timedelta(c.PORTWATCH_TOLERANCE))
        shares[name] = joined["share"].to_numpy()
    df["bab_el_mandeb"] = shares[c.PASSAGES["babalmandab"]]
    df["traffic"] = shares[names].min(axis=1)  # none before PortWatch starts, in 2019

    legs_date = df[["rate_date", "eurusd_date"] + [f"{leg}_trade_date" for leg in legs]]
    df["staleness_days"] = (df["trade_date"] - legs_date.min(axis=1)).dt.days
    return df.set_index("trade_date")


def dataset(origin, destination, variant):
    """The stored panel of one route, indexed by trade date. Only Rotterdam has a duty-unpaid
    leg."""
    df = sql.query(f"SELECT * EXCLUDE (origin, destination, variant) FROM panel "
                   f"WHERE origin = '{origin}' AND destination = '{destination}' "
                   f"AND variant = '{variant}' ORDER BY trade_date")
    if destination != "rotterdam":
        df = df.drop(columns=df.filter(like="premium_du_").columns)
    return df.set_index("trade_date")


def build():
    """Compute the routes, align every route and store both as tables."""
    sql.write("routes", routes.build())
    frames = []
    for _, r in sql.query("SELECT origin, destination, variant FROM routes").iterrows():
        df = align(r["origin"], r["destination"], r["variant"]).reset_index()
        df.insert(0, "variant", r["variant"])
        df.insert(0, "destination", r["destination"])
        df.insert(0, "origin", r["origin"])
        frames.append(df)
    sql.write("panel", pd.concat(frames, ignore_index=True))
    return sql.query("SELECT origin, destination, variant, COUNT(*) AS trade_dates, "
                     "MIN(trade_date) AS first, MAX(trade_date) AS last FROM panel "
                     "GROUP BY 1, 2, 3 ORDER BY 1, 2, 3")


if __name__ == "__main__":
    print(build())
