"""The economics of one tonne of primary aluminium: what each destination pays over LME cash,
what it costs to get there, and what is left at the loading port.

Every term is USD per tonne, so the row subtracts exactly:

    netback = premium - us_duty - eu_duty - cbam - freight_sea - freight_land - handling - war
              - insurance - funding - curve

The netback is a premium FOB at the origin port, over LME cash: loaded on the ship, the metal is
worth LME cash plus the netback.

US Midwest. The Midwest premium is duty-paid, delivered to the consumer. The section 232 duty is
charged on the customs value, which excludes international freight and insurance, so it is a
share of the FOB value itself and has a closed form:

    FOB     = (LME + MWP - freight_sea - freight_land - war - insurance - funding - curve) / (1 + t)
    us_duty = t x FOB

Rotterdam. Two sales: duty-unpaid, in a bonded shed, with neither duty nor CBAM; or duty-paid,
cleared into the EU, paying the 3% duty on the metal in the shed (LME cash plus the duty-unpaid
premium) unless the origin trades duty-free, and CBAM unless it is in the EEA. The better of the
two is taken. A duty-paid sale whose CBAM price is not published yet cannot be priced and is not
taken. Japan: CIF main ports, no duty.

Funding, insurance and the curve are charged on LME cash, not on the FOB value, so that the stack
is exact without iteration. The metal is financed from loading to sale, at sea, on land and in
port; the curve is what the LME spread says the price loses while it travels, a cost in
backwardation and a gain in contango:

    funding = LME x (rate + spread) x (sea_days + land_days + port_days) / 360
    curve   = (LME cash - LME three-month) / days between the prompts x (sea_days + land_days)
"""

import numpy as np
import pandas as pd

from src import config as c, data, sql

COSTS = ["us_duty", "eu_duty", "cbam", "freight_sea", "freight_land", "handling", "war",
         "insurance", "funding", "curve"]


def netback(origin, destination, variant, df, **changes):
    """The cost stack and the netback, one row per trade date of the aligned dataset. Any
    assumption can be changed by name for a sensitivity, as in
    netback("gulf", "rotterdam", "direct", df, freight_scale=1.3)."""
    r = sql.query(f"SELECT * FROM routes WHERE origin = '{origin}' "
                  f"AND destination = '{destination}' AND variant = '{variant}'").iloc[0]
    p = {"funding_spread": c.FUNDING_SPREAD, "insurance_rate": c.INSURANCE_RATE,
         "port_days": c.PORT_DAYS, "freight_scale": 1.0, "tonnes_per_box": c.TONNES_PER_BOX,
         "handling": c.HANDLING_ROTTERDAM, "eu_duty": c.EU_DUTY, "us_tariff": None,
         "cbam_benchmark": c.CBAM_BENCHMARK, "emissions": c.ORIGINS[origin]["cbam"],
         "cbam_markup": None, "land_scale": 1.0, **changes}
    transit = r["sea_days"] + r["land_days"]

    out = pd.DataFrame(index=df.index)
    out["premium"] = df["premium_settle"] * (c.LB_PER_T if destination == "midwest" else 1)
    out["freight_sea"] = r["freight_sea"] * p["freight_scale"]
    out["freight_land"] = r["land_usd"] * p["land_scale"]
    out["handling"] = p["handling"] if destination == "rotterdam" else 0.0
    out["war"] = (df["war_gulf"] + df["war_red_sea"]) / p["tonnes_per_box"]  # per box, per t
    out["insurance"] = df["cash"] * p["insurance_rate"]
    rate = df["rate"] / 100 + p["funding_spread"]
    out["funding"] = df["cash"] * rate * (transit + p["port_days"]) / 360
    out["curve"] = (df["cash"] - df["three_month"]) / df["days"] * transit
    out["us_duty"] = 0.0
    out["eu_duty"] = 0.0
    out["cbam"] = 0.0
    shipping = out[["freight_sea", "freight_land", "handling", "war", "insurance", "funding",
                    "curve"]].sum(axis=1)

    if destination == "midwest":
        t = df["us_tariff"] if p["us_tariff"] is None else p["us_tariff"]
        fob = (df["cash"] + out["premium"] - shipping) / (1 + t)  # customs value: no freight
        out["us_duty"] = t * fob
    if destination == "rotterdam":
        # certificates per tonne: default emissions marked up, less the free allocation EU
        # smelters keep; nothing before 2026
        year = df["arrival"].dt.year
        markup = year.map(c.CBAM_MARKUP) if p["cbam_markup"] is None else p["cbam_markup"]
        certificates = (p["emissions"] * (1 + markup)
                        - p["cbam_benchmark"] * year.map(c.CBAM_FACTOR)).clip(lower=0)
        cbam = (certificates * df["cbam_price"] * df["eurusd"]).where(year >= 2026, 0.0)
        free = c.ORIGINS[origin]["eu_free"]  # EEA or CETA: no duty
        duty = p["eu_duty"] * (df["cash"] + df["premium_du_settle"]) * (not free)
        paid = out["premium"] - duty - cbam - shipping
        unpaid = df["premium_du_settle"] - shipping
        out["duty_paid"] = paid > unpaid  # False when CBAM cannot be priced yet
        out["premium"] = out["premium"].where(out["duty_paid"], df["premium_du_settle"])
        out["eu_duty"] = duty.where(out["duty_paid"], 0.0)
        out["cbam"] = cbam.where(out["duty_paid"], 0.0)

    out["netback"] = out["premium"] - out[COSTS].sum(axis=1)
    return out


def route(origin, destination, **changes):
    """The netback of an origin at a destination on each trade date, by the route the rule
    picks: the detour, round the Cape or through Panama, when traffic at Bab el-Mandeb is under
    the threshold, the direct voyage otherwise and before PortWatch starts."""
    variants = sql.query(f"SELECT variant FROM routes WHERE origin = '{origin}' "
                         f"AND destination = '{destination}'")["variant"].tolist()
    first = "direct" if "detour" in variants else variants[0]  # Quebec's rail to the Midwest
    df = data.dataset(origin, destination, first)
    out = netback(origin, destination, first, df, **changes)
    out["variant"] = first
    if "detour" in variants:
        detour = netback(origin, destination, "detour",
                         data.dataset(origin, destination, "detour"), **changes)
        closed = df["bab_el_mandeb"] < c.CAPE_THRESHOLD  # False before 2019: no data, Red Sea open
        out = out.where(~closed, detour.reindex(out.index))
        out["variant"] = np.where(closed, "detour", "direct")
    return out


def table(**changes):
    """Every origin and destination on every trade date, by the route the rule picks."""
    frames = []
    for origin in c.ORIGINS:
        for destination in c.DESTINATIONS:
            df = route(origin, destination, **changes).reset_index()
            df.insert(0, "destination", destination)
            df.insert(0, "origin", origin)
            frames.append(df)
    return pd.concat(frames, ignore_index=True)


def tipping(netbacks, date):
    """For each origin on one trade date: the Midwest premium, in US cents per pound, at which the
    Midwest nets back exactly what the origin's best other destination does, and the Japan
    premium at which Japan does. The Midwest netback moves by 1/(1+t) a dollar of premium, the
    duty taking its share; Japan's moves one for one. Each against the premium quoted for the
    origin's arrival month."""
    rows = []
    for origin in c.ORIGINS:
        nb = netbacks[(netbacks["origin"] == origin) & (netbacks["trade_date"] == date)]
        nb = nb.set_index("destination")["netback"]
        mw = data.dataset(origin, "midwest", "rail" if origin == "quebec" else "direct").loc[date]
        jp = data.dataset(origin, "japan", "direct").loc[date]
        gap_mw = nb.drop("midwest").max() - nb["midwest"]
        gap_jp = nb.drop("japan").max() - nb["japan"]
        rows.append({"origin": origin, "best": nb.idxmax(),
                     "midwest_quoted": 100 * mw["premium_settle"],
                     "midwest_tipping": 100 * mw["premium_settle"]
                     + 100 * gap_mw * (1 + mw["us_tariff"]) / c.LB_PER_T,
                     "japan_quoted": jp["premium_settle"],
                     "japan_tipping": jp["premium_settle"] + gap_jp})
    return pd.DataFrame(rows).set_index("origin")


def midwest_split(df):
    """The Midwest premium cut in two, USD/t: the part that pays the section 232 duty and the
    premium that is left. The duty-paid price LME + MWP is the duty-free value times (1 + t), so
    the duty is t/(1+t) of it. That value still holds the freight to the Midwest, so the duty
    part is overstated by t/(1+t) of the delivery cost."""
    out = pd.DataFrame(index=df.index)
    out["premium"] = df["premium_settle"] * c.LB_PER_T
    out["duty"] = df["us_tariff"] / (1 + df["us_tariff"]) * (df["cash"] + out["premium"])
    out["excluding_duty"] = out["premium"] - out["duty"]
    return out


if __name__ == "__main__":
    netbacks = table()
    sql.write("netbacks", netbacks)
    last = netbacks["trade_date"].max()
    print(netbacks[netbacks["trade_date"] == last]
          .pivot(index="origin", columns="destination", values="netback").round(1))
