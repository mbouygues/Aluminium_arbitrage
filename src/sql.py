"""Load the raw files into data/aluminium_arbitrage.duckdb, and read it back.

    python -m src.sql

Two layers. raw_prices holds the price files as they are, in long form: one row per file, series
and date, stamped with ingested_at, the time the file was last downloaded. Loading is idempotent:
the primary key is the file, the series and the date, a row already there is left alone, so
running it twice adds nothing and a number keeps its first print. raw_flows does the same for the
trade statistics, except that a month the source republishes is a new row: its key carries the
release, so every vintage downloaded is kept next to the ones before.

The clean layer is views on the raw tables, so it cannot drift from them. prices is the LME,
the funding rate and the euro, each with its unit. futures is the premium contracts: the
contract is the file name, product, month code and year, so AUPZ2026.csv is December 2026 Midwest.
flows is the latest vintage of each month's trade, with the date it was first released. Each
keeps the date on the file; when a number became readable is data.py's business.

The files typed or downloaded by hand (tariffs, CBAM prices, war surcharges, press premiums,
PortWatch transits) are republished whole, so each replaces its table at each load, which is
idempotent too.
"""

from datetime import datetime, timezone

import duckdb

from src import config as c

# hand files -> table
HAND = {"policy/tariffs.csv": "tariffs", "policy/cbam_price.csv": "cbam_price",
        "freight/war_surcharges.csv": "war_surcharges", "press/premiums.csv": "press",
        "press/mjp_quarterly.csv": "mjp_quarterly", "portwatch/daily_chokepoints.csv": "transits"}


def query(sql):
    with duckdb.connect(c.DB, read_only=True) as con:
        con.execute("SET TimeZone = 'UTC'")  # instants read back in UTC on any machine
        return con.sql(sql).df()


def write(table, df):
    """Replace one derived table (the routes, the panel, the netbacks) with a frame."""
    with duckdb.connect(c.DB) as con:
        con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM df")


def stamp(path):
    """When a file was last downloaded, in UTC."""
    ingested_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return ingested_at.replace(tzinfo=None, microsecond=0)


def prices(con):
    """Append the LME, FRED and premium files to raw_prices; (re)create the views on them."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS raw_prices (
            file VARCHAR,            -- folder and file name under data/
            series VARCHAR,          -- the column on the file
            obs_date DATE,           -- the date as the source stamps it
            value DOUBLE,
            ingested_at TIMESTAMP,   -- UTC, when the file was last downloaded
            PRIMARY KEY (file, series, obs_date)
        )""")
    before = con.sql("SELECT COUNT(*) FROM raw_prices").fetchone()[0]
    files = sorted(c.DATA.glob("lme/*.csv")) + sorted(c.DATA.glob("fred/*.csv"))
    for path in files + sorted(c.DATA.glob("premiums/*.csv")):
        # one row per column and date; a cell the source left empty is no row
        con.execute(f"""
            INSERT INTO raw_prices
            SELECT ?, series, date, value, ?
            FROM (UNPIVOT read_csv('{path}') ON COLUMNS(* EXCLUDE (date))
                  INTO NAME series VALUE value)
            ON CONFLICT DO NOTHING""", [f"{path.parent.name}/{path.name}", stamp(path)])
    after = con.sql("SELECT COUNT(*) FROM raw_prices").fetchone()[0]
    print(f"{after - before:,} rows added to raw_prices, {after:,} in total")

    # the one row Westmetall dates on a Saturday is a copy of the Friday before
    con.execute("""
        CREATE OR REPLACE VIEW prices AS
        SELECT series, obs_date, value,
               CASE series WHEN 'rate' THEN '% a year' WHEN 'eurusd' THEN 'USD/EUR'
                           WHEN 'stock' THEN 't' ELSE 'USD/t' END AS unit
        FROM raw_prices
        WHERE file NOT LIKE 'premiums/%' AND isodow(obs_date) < 6""")
    # the contract is the file name; its last five characters are the month code and the year
    con.execute(f"""
        CREATE OR REPLACE VIEW futures AS
        SELECT left(contract, length(contract) - 5) AS product, contract,
               make_date(right(contract, 4)::INTEGER,
                         strpos('{c.MONTH_CODES}', contract[-5]), 1) AS delivery_month,
               obs_date AS trade_date, value AS settle,
               CASE WHEN contract LIKE 'AUP%' THEN 'USD/lb' ELSE 'USD/t' END AS unit
        FROM (SELECT parse_filename(file, true) AS contract, obs_date, value
              FROM raw_prices WHERE file LIKE 'premiums/%')""")


def flows(con):
    """Append every vintage of the trade files to raw_flows; (re)create the flows view."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS raw_flows (
            file VARCHAR,             -- file name under data/flows/
            partner VARCHAR,          -- the other country, ISO alpha-2 for Comext, M49 for Comtrade
            period DATE,              -- the first day of the month the number describes
            tonnes DOUBLE,
            released TIMESTAMP,       -- when the source released this vintage
            first_released TIMESTAMP, -- when it first released the month (Comtrade only)
            ingested_at TIMESTAMP,    -- UTC, when the file was last downloaded
            PRIMARY KEY (file, partner, period, released)
        )""")
    before = con.sql("SELECT COUNT(*) FROM raw_flows").fetchone()[0]
    for path in sorted(c.DATA.glob("flows/comtrade_*.csv")):
        con.execute(f"""
            INSERT INTO raw_flows
            SELECT ?, partnerCode, strptime(period::VARCHAR, '%Y%m'), netWgt / 1000,
                   lastReleased, firstReleased, ?
            FROM read_csv('{path}', types = {{'partnerCode': 'VARCHAR'}})
            ON CONFLICT DO NOTHING""", [path.name, stamp(path)])
    path = c.DATA / "flows" / "comext_eu_imports.csv"
    # Comext counts in 100 kg and dates its last update dd/mm/yy
    con.execute(f"""
        INSERT INTO raw_flows
        SELECT ?, partner, strptime(TIME_PERIOD, '%Y-%m'), OBS_VALUE / 10,
               strptime("LAST UPDATE", '%d/%m/%y %H:%M:%S'), NULL, ?
        FROM read_csv('{path}', all_varchar = true, types = {{'OBS_VALUE': 'DOUBLE'}})
        ON CONFLICT DO NOTHING""", [path.name, stamp(path)])
    after = con.sql("SELECT COUNT(*) FROM raw_flows").fetchone()[0]
    print(f"{after - before:,} rows added to raw_flows, {after:,} in total")

    # the reporter is the file: who declared the trade, and which way it went
    con.execute("""
        CREATE OR REPLACE VIEW flows AS
        SELECT CASE file WHEN 'comtrade_us_imports.csv' THEN 'midwest'
                         WHEN 'comtrade_japan_imports.csv' THEN 'japan'
                         WHEN 'comext_eu_imports.csv' THEN 'rotterdam'
                         ELSE 'canada_exports' END AS reporter,
               partner, period, arg_max(tonnes, released) AS tonnes,
               MIN(first_released) AS first_released, MAX(released) AS released,
               COUNT(*) AS vintages
        FROM raw_flows GROUP BY file, partner, period""")


def build():
    """Load the raw files and (re)create the clean views and the hand tables."""
    with duckdb.connect(c.DB) as con:
        prices(con)
        flows(con)
        for file, table in HAND.items():
            con.execute(f"CREATE OR REPLACE TABLE {table} AS "
                        f"SELECT *, ? AS ingested_at FROM read_csv('{c.DATA / file}')",
                        [stamp(c.DATA / file)])
            print(f"{con.sql(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:,} rows in {table}")


if __name__ == "__main__":
    build()
    print(query("SELECT product, unit, COUNT(DISTINCT contract) AS contracts, "
                "MIN(trade_date) AS first, MAX(trade_date) AS last, COUNT(*) AS settles "
                "FROM futures GROUP BY 1, 2 ORDER BY 1"))
