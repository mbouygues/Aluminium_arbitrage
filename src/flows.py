"""Do the tonnes follow the netback? Monthly imports of unwrought aluminium (HS 7601), declared by
the importing country, set against what each origin nets back at each destination.

    releases()   ->  the months each destination has published, and when
    shipments()  ->  tonnes by origin, destination and month, with the day the month came out
    shares()     ->  each destination's share of what an origin shipped to the three, in %
    signal()     ->  each origin's mean netback by destination and month, the winner and its lead
    agreement()  ->  each month's winner against the destination whose share rose the most one,
                     two and three months later

The statistics are the importers' declarations: US and Japanese imports from UN Comtrade, EU-27
imports from Eurostat Comext. For the origins they are mirror data, which matters for the Gulf
and India, whose own statistics come late. The EU stands for Rotterdam, the United States for the
Midwest, Japan for itself. A month is readable from the day it was first released: Comtrade
gives that date, and for Comext it is the convention COMEXT_LAG_DAYS after the month, tested
against the download in notebook 03. Values are the latest vintage on file.

This is a check, not a model: months are counted, nothing is estimated.
"""

import pandas as pd

from src import config as c, sql


def releases():
    """The months each destination has published, whatever the partner, with the day each first
    came out. Comext gives no release date per month: the convention, from the end of the month."""
    df = sql.query("SELECT reporter AS destination, period, MIN(first_released) AS released "
                   "FROM flows WHERE reporter <> 'canada_exports' GROUP BY 1, 2")
    convention = df["period"] + pd.offsets.MonthEnd(0) + pd.Timedelta(days=c.COMEXT_LAG_DAYS)
    df["published"] = df.pop("released").fillna(convention)
    return df


def shipments():
    """Tonnes each destination declares from each origin, by month, with the day that month was
    first published and the number of vintages on file."""
    df = sql.query("SELECT * FROM flows WHERE reporter <> 'canada_exports'")
    codes = {}
    for origin, o in c.ORIGINS.items():
        codes |= {str(code): origin for code in o["partners"].values()}  # Comtrade, M49
        codes |= {iso: origin for iso in o["partners"]}                    # Comext, ISO alpha-2
    df["origin"] = df["partner"].map(codes)
    df = df.dropna(subset="origin").rename(columns={"reporter": "destination"})
    df = df.groupby(["origin", "destination", "period"], as_index=False).agg(
        tonnes=("tonnes", "sum"), vintages=("vintages", "max"))
    return df.merge(releases(), on=["destination", "period"])


def shares():
    """Each destination's share of an origin's shipments to the three, in %, for the months all
    three have published; a destination that declares nothing from the origin took none. With
    the tonnes and the day the month was complete."""
    r = releases()
    complete = r.groupby("period")["destination"].nunique() == len(c.DESTINATIONS)
    published = r.groupby("period")["published"].max()[complete]
    rows = pd.MultiIndex.from_product([list(c.ORIGINS), published.index],
                                      names=["origin", "period"])
    tonnes = shipments().pivot_table(index=["origin", "period"], columns="destination",
                                     values="tonnes", aggfunc="sum")
    tonnes = tonnes.reindex(index=rows, columns=list(c.DESTINATIONS)).fillna(0)
    out = 100 * tonnes.div(tonnes.sum(axis=1), axis=0)
    out["tonnes"] = tonnes.sum(axis=1)
    out["published"] = published.reindex(out.index.get_level_values("period")).to_numpy()
    out.columns.name = None
    return out.dropna()  # a month the origin shipped nothing to the three has no shares


def signal(netbacks=None):
    """Each origin's mean netback at each destination over the trade dates of a month, the
    destination that paid the most, and its lead over the second best."""
    df = sql.query("SELECT * FROM netbacks") if netbacks is None else netbacks
    df["period"] = df["trade_date"].dt.to_period("M").dt.to_timestamp()
    out = df.pivot_table(index=["origin", "period"], columns="destination", values="netback")
    out = out[list(c.DESTINATIONS)]
    ranked = out.apply(lambda row: row.sort_values(ascending=False).to_numpy(), axis=1)
    out["winner"] = out[list(c.DESTINATIONS)].idxmax(axis=1)
    out["lead"] = ranked.str[0] - ranked.str[1]
    out.columns.name = None
    return out


def agreement(lags=(1, 2, 3), netbacks=None):
    """For each origin and month: the destination the netback said paid most, and for each lag
    the destination whose share of shipments rose the most from that month to the lag later.
    available is the day the last of the months used was published."""
    s = shares()
    sig = signal(netbacks)
    rows = []
    for origin in c.ORIGINS:
        share = s.loc[origin]
        winner = sig.loc[origin].reindex(share.index)
        out = winner[["winner", "lead"]].copy()
        for k in lags:
            change = share[list(c.DESTINATIONS)].shift(-k) - share[list(c.DESTINATIONS)]
            out[f"rose_{k}"] = change.dropna().idxmax(axis=1)
            out[f"agree_{k}"] = (out[f"rose_{k}"] == out["winner"]).where(out[f"rose_{k}"].notna())
            out[f"available_{k}"] = share["published"].shift(-k)
        out.insert(0, "origin", origin)
        rows.append(out.reset_index())
    return pd.concat(rows, ignore_index=True).dropna(subset="winner")
