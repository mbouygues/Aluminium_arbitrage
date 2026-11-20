"""A bulk cargo under a voyage charter: the laytime the charter party allows, what the time used
costs or earns, and what one more day before delivery costs the owner of the metal.

Laytime starts TURN_HOURS after the notice of readiness is tendered and runs while the ship
waits and works. SHINC counts every day; SHEX leaves Sundays out, on average one day in seven:

    allowed = cargo / load rate + cargo / discharge rate                    (days)
    used    = (waiting - turn time + cargo / load rate + cargo / discharge rate) x counted share
    demurrage = (used - allowed) x DEMURRAGE_USD when positive, despatch = (allowed - used) x
    DESPATCH_USD when negative

A day before delivery costs three things, per tonne. Demurrage, once laytime has run out, is
paid to the shipowner. Funding: the metal is financed one more day. The curve: in backwardation
the metal is sold later at a lower LME price, one more day of the cash to three-month spread.
"""

import pandas as pd

from src import config as c


def day_cost(df, **changes):
    """What one more day before delivery costs a tonne of the cargo, on each trade date of the
    aligned dataset, by component."""
    p = {"demurrage": c.DEMURRAGE_USD, "cargo": c.CARGO_T, "funding_spread": c.FUNDING_SPREAD,
         **changes}
    out = pd.DataFrame(index=df.index)
    out["demurrage"] = p["demurrage"] / p["cargo"]
    out["funding"] = df["cash"] * (df["rate"] / 100 + p["funding_spread"]) / 360
    out["curve"] = (df["cash"] - df["three_month"]) / df["days"]
    out["total"] = out[["demurrage", "funding", "curve"]].sum(axis=1)
    return out


def laytime(waiting_days, terms="SHINC", **changes):
    """The charter-party account of one cargo that waits waiting_days at anchor at discharge:
    laytime allowed and used, in days, and the demurrage owed (positive) or the despatch
    earned (negative), in USD."""
    p = {"cargo": c.CARGO_T, "load_rate": c.LOAD_RATE, "discharge_rate": c.DISCHARGE_RATE,
         "turn_hours": c.TURN_HOURS, "demurrage": c.DEMURRAGE_USD, "despatch": c.DESPATCH_USD,
         **changes}
    working = p["cargo"] / p["load_rate"] + p["cargo"] / p["discharge_rate"]
    counted = 6 / 7 if terms == "SHEX" else 1.0
    used = (max(waiting_days - p["turn_hours"] / 24, 0) + working) * counted
    allowed = working  # the charter party allows the time the agreed rates take
    owed = (used - allowed) * (p["demurrage"] if used > allowed else p["despatch"])
    return pd.Series({"allowed_days": allowed, "used_days": used, "demurrage_usd": owed,
                      "per_tonne": owed / p["cargo"]})
