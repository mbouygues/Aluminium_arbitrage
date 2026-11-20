"""Paths, when each number becomes readable, the LME prompt-date rules, the five origins and three
destinations, and every physical, financial and regulatory assumption behind the netback, each
with its unit and its source."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DB = DATA / "aluminium_arbitrage.duckdb"

MONTH_CODES = "FGHJKMNQUVXZ"  # futures month codes, January to December: AUPZ2026 is December 2026

# The four premium futures start between 2014 and 2016; the duty-paid Rotterdam contract is the
# last, on 13 March 2016. The panel starts with the first month every destination prices.
SAMPLE_START = "2016-04-01"
# Trade statistics from January 2016: the first months the replay can be set against
FLOWS_START = "2016-01"

# --- when a number becomes readable -------------------------------------------
# Each source publishes on its own wall clock, and London and New York change to summer time on
# different dates: every stamp goes through UTC.
CLOSE = {"cash": ("Europe/London", "13:30:00"),         # second ring 13:05-13:10, official price
         "three_month": ("Europe/London", "13:30:00"),  # about ten minutes later (LME methodology)
         "rate": ("America/New_York", "09:00:00"),      # New York Fed, the business day after
         "eurusd": ("America/New_York", "12:00:00"),    # Fed H.10 noon buying rate, the day after
         "AUP": ("America/New_York", "13:30:00"),       # COMEX daily settlement window
         "EDP": ("America/New_York", "13:30:00"),
         "AEP": ("America/New_York", "13:30:00"),
         "MJP": ("America/New_York", "13:30:00")}

# The cargo is sold FOB at the origin port, so a row for trade date D is anchored at the New York
# close, after London and the COMEX settlement have printed: every number on the row is older.
ASOF = ("America/New_York", "17:00:00")

# Past this a number is stale rather than late: the row is dropped instead of carrying it on.
TOLERANCE = "5D"

# --- the prompt dates ---------------------------------------------------------
# LME Trading Regulation 8. Cash is two business days after the trade date. The three-month prompt
# is the same day three months on; if that is not a business day it moves to the next one, except
# a Saturday, Good Friday and a weekday Christmas Day, which move back, and it never leaves its
# calendar month. A business day is a weekday on which the price file prints.
CASH_DAYS = 2
PROMPT_MONTHS = 3
# The closures after the last print on file: LME notice 26/231, 21 September 2026
HOLIDAYS_AHEAD = ["2026-12-25", "2026-12-28", "2027-01-01", "2027-03-26", "2027-03-29",
                  "2027-05-03", "2027-05-31", "2027-08-30", "2027-12-27", "2027-12-28"]

# --- the premiums -------------------------------------------------------------
# CME futures, each settling on the average over its contract month of a price agency's daily
# assessment of the spot premium over LME cash. The metal is sold on the premium of the month it
# arrives, so each route takes the contract of its arrival month.
PREMIUMS = {"midwest": "AUP",    # Platts Midwest US transaction premium, duty-paid, USD/lb
            "rotterdam": "EDP",  # Fastmarkets P1020A in-warehouse Rotterdam, duty-paid, USD/t
            "japan": "MJP"}      # Platts Main Japanese Ports spot premium, CIF, USD/t
DUTY_UNPAID = "AEP"              # Fastmarkets P1020A in-warehouse Rotterdam, duty-unpaid, USD/t
LB_PER_T = 2204.62262  # the Midwest premium is quoted in US dollars per pound

# --- the money ----------------------------------------------------------------
# The cargo is financed at the effective federal funds rate plus a constant spread: Glencore's
# "marketing funding cost of c.5%", 2 Oct 2026, less EFFR at 3.88% that day.
FUNDING_SPREAD = 0.011
# Cargo insurance, a share of the metal's value per voyage: 2025 cargo premiums over goods trade,
# IUMI 24.2bn / WTO 26,260bn
INSURANCE_RATE = 0.0009

# --- the five origins and three destinations ----------------------------------
# Each port is the coordinate searoute's port database gives it; routes.py lets searoute snap to
# the nearest port and notebook 01 prints its name and UN/LOCODE. tariff is the origin's group
# in data/policy/tariffs.csv, eu_free whether its metal enters the EU duty-free (EEA, CETA), cbam
# the default direct emissions of unwrought aluminium (CN 7601) in tCO2e per tonne,
# Implementing Regulation (EU) 2026/1740, Annex I (Iceland is in the EEA and outside CBAM).
# partners are the countries whose exports the trade statistics count for the origin: for the
# Gulf, the UAE and Oman, which ship from Khor Fakkan and Sohar, outside Hormuz.
ORIGINS = {
    "quebec": {"port": (-66.361, 50.203), "tariff": "canada", "eu_free": True, "cbam": 1.960,
               "partners": {"CA": 124}},       # Sept-Iles; to the Midwest by rail, no sea leg
    "iceland": {"port": (-21.789, 64.351), "tariff": "other", "eu_free": True, "cbam": 0.0,
                "partners": {"IS": 352}},      # Grundartangi
    "gulf": {"port": (56.347, 25.362), "tariff": "uae", "eu_free": False, "cbam": 1.870,
             "partners": {"AE": 784, "OM": 512}},  # Khor Fakkan
    "india": {"port": (86.667, 20.250), "tariff": "other", "eu_free": False, "cbam": 1.870,
              "partners": {"IN": 699}},        # Paradip
    "malaysia": {"port": (113.030, 3.175), "tariff": "other", "eu_free": False, "cbam": 1.700,
                 "partners": {"MY": 458}},     # Bintulu, for the Samalaju smelter
}
# The Midwest is reached through the nearer of two US ports by sea, then by barge or rail
DESTINATIONS = {"midwest": [(-76.596, 39.281), (-90.063, 29.950)],  # Baltimore, New Orleans
                "rotterdam": [(4.442, 51.904)],
                "japan": [(139.635, 35.458)]}                       # Yokohama
# A voyage searoute sends through the Red Sea also gets a detour with Suez and Bab el-Mandeb
# closed: round the Cape, or through Panama when that is shorter
CAPE_RESTRICTIONS = ["northwest", "suez", "babalmandab"]

# --- the voyage ---------------------------------------------------------------
SPEED_KNOTS = 14  # a laden box ship or bulk carrier, slow-steaming
PORT_DAYS = 10    # waiting, loading and discharge at both ends, financed like the sea days
# The land legs, assumed: USD per tonne and days. Quebec's smelters ship to the Midwest by rail
# or truck; a US port is about two weeks from the Midwest by barge or rail.
QUEBEC_LAND = (80, 7)
US_PORT_LAND = (40, 10)
HANDLING_ROTTERDAM = 20  # discharge and in-charge into an LME-listed shed, USD/t, assumed

# --- the freight --------------------------------------------------------------
# No route the netback uses has a free freight series. The rate per tonne and nautical mile is
# calibrated on the one Drewry World Container Index lane whose length searoute can measure and
# that looks like these voyages, Shanghai to Rotterdam: 3,504 USD per 40-foot box on 12 November
# 2026 (composite 4,305). A box carries 22.5 t of ingots whatever its size: heavy cargo fills a
# 20-foot box to its weight limit and a 40-foot one barely more. Assumed, held flat over the
# replay, and moved by 30% in the sensitivities.
WCI_USD_PER_BOX = 3504
WCI_LANE = [(121.90, 30.60), (4.442, 51.904)]  # Shanghai (Yangshan), Rotterdam
TONNES_PER_BOX = 22.5
# Carrier war surcharges, data/freight/war_surcharges.csv, in USD per box: "gulf" on cargo
# loaded in the Gulf, "red_sea" on a voyage through Bab el-Mandeb

# --- the route rule -----------------------------------------------------------
# Through the Red Sea while traffic at Bab el-Mandeb, in IMF PortWatch's transit calls averaged
# over the latest published week, stays above half its 2019 average; round the Cape below it.
# Container ships only: the metal travels in boxes, and tankers and bulkers kept going through
# the Red Sea after the container lines left it in December 2023.
CAPE_THRESHOLD = 0.5
TRANSITS = "n_container"
PASSAGES = {"suez": "Suez Canal", "babalmandab": "Bab el-Mandeb Strait",  # searoute -> PortWatch
            "malacca": "Malacca Strait", "panama": "Panama Canal",
            "gibraltar": "Gibraltar Strait", "south_africa": "Cape of Good Hope"}
# PortWatch updates on Tuesdays at 9:00 New York time with the transits to the Sunday before;
# past two weeks, one release missed, a count is stale
PORTWATCH_RELEASE = ("America/New_York", "09:00:00")
PORTWATCH_TOLERANCE = "14D"

# --- the trade statistics -------------------------------------------------------
# Eurostat puts a month's EU trade into Comext around the middle of the second month after: the
# September 2026 figures were in the update of 13 November, 44 days on. A month is taken as
# readable 46 days after it ends. Comtrade records each month's first release itself.
COMEXT_LAG_DAYS = 46
# The EU-27 in Comtrade's M49 codes, to read Canada's own exports to Europe
EU27 = [40, 56, 100, 191, 196, 203, 208, 233, 246, 251, 276, 300, 348, 372, 381, 428, 440, 442,
        470, 528, 616, 620, 642, 703, 705, 724, 752]

# --- the duties ---------------------------------------------------------------
# US: section 232, data/policy/tariffs.csv, on the customs value, which excludes international
# freight and insurance (19 U.S.C. 1401a). The MFN rate on 7601.10.60 is Free.
# EU: 3% conventional duty on CN 7601 10 00 (TARIC), on the value of the metal in the shed
EU_DUTY = 0.03

# --- CBAM ---------------------------------------------------------------------
# Regulation (EU) 2023/956, definitive since 1 January 2026, on the duty-paid import only.
# Certificates per tonne = default emissions x (1 + mark-up) - benchmark x CBAM factor, priced at
# data/policy/cbam_price.csv (EUR per tCO2e) and converted at the euro of the day.
# The mark-up on default values: Implementing Regulation (EU) 2026/1740, Annex I
CBAM_MARKUP = {2026: 0.10, 2027: 0.20, 2028: 0.30}  # 30% from 2028 on
# The share of free allocation EU smelters keep, which imports are spared: Directive
# 2003/87/EC, article 10a(1a). 97.5% in 2026: CBAM bites on 2.5% of the benchmark
CBAM_FACTOR = {2026: 0.975, 2027: 0.95, 2028: 0.90, 2029: 0.775, 2030: 0.515, 2031: 0.39,
               2032: 0.265, 2033: 0.14}  # none from 2034
# CBAM benchmark for unwrought primary aluminium, route (K): Implementing Regulation (EU)
# 2025/2620, Annex, point 5.3, in tCO2e per tonne
CBAM_BENCHMARK = 1.423
# The certificate price is quarterly and comes out a few days after its quarter: past a quarter
# and a few days the latest one is stale
CBAM_TOLERANCE = "100D"

# --- the voyage in bulk -------------------------------------------------------
# A bulk cargo under a voyage charter, Khor Fakkan to Rotterdam. Laytime starts TURN_HOURS after
# notice of readiness; SHINC counts every day, SHEX leaves Sundays out. Assumed: terms of the
# order of a Supramax charter. Demurrage is usually set near the ship's daily hire: the Baltic
# Supramax 11TC average was 23,203 USD/day in the week to 13 November 2026 (Baltic Exchange,
# via ShipUniverse) and 22,714 at the end of September (Handybulk).
CARGO_T = 30_000
LOAD_RATE = 5_000       # t per day
DISCHARGE_RATE = 4_000  # t per day
TURN_HOURS = 12
DEMURRAGE_USD = 20_000  # per day, per the charter party
DESPATCH_USD = 10_000   # half demurrage, the usual convention
