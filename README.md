# Aluminium arbitrage

Where should the next tonne of primary aluminium go, and do the physical flows follow the netback ?

Five origins (Quebec, Iceland, the Gulf outside Hormuz, India, Malaysia) and three destinations (the US Midwest, Rotterdam, Japan): what a tonne nets back at its loading port over LME cash on every trade date since April 2016, the premium at which each origin would switch destination, and a check against the tonnes the importing countries declare.

## Notebooks

| Notebook | Content |
|---|---|
| [`notebooks/01_data.ipynb`](notebooks/01_data.ipynb) | Data pipeline: sources and conventions, raw files into DuckDB, validation, point-in-time stamps, ports and routes, `merge_asof` alignment, no-look-ahead assertions, final dataset, trade statistics |
| [`notebooks/02_netback.ipynb`](notebooks/02_netback.ipynb) | Research and results: key findings, method, assumptions (observed / sourced / assumed), the netback stack checked by hand, the tipping-point ladder, what the Midwest premium is made of, historical replay, routes and chokepoint risk, the cost of a day at anchor, sensitivities, limitations |
| [`notebooks/03_flows.ipynb`](notebooks/03_flows.ipynb) | The check on the tonnes: key findings, method, publication lags, netback against shipments origin by origin, three episodes, agreement table and lag, the months the tonnes go the other way, limitations |

All three are committed with their outputs and read on GitHub without being run.

## Dashboard

[`docs/index.html`](docs/index.html) is a static page for the desk: three lines written from the numbers, the tipping-point ladder, the netbacks, the routes on a map coloured by traffic, the Midwest premium cut into duty and premium, and the check on the tonnes. GitHub Pages serves it from `docs/` (Settings, Pages, deploy from the branch, folder `/docs`), and [`.github/workflows/dashboard.yml`](.github/workflows/dashboard.yml) rebuilds it every weekday evening: download, sql, data, netback, dashboard. A repository secret `COMTRADE_KEY` is optional and only makes Comtrade faster.

## Run

```bash
pip install -r requirements.txt
python -m src.download      # optional: appends new prices and new trade releases; the premiums need tvDatafeed
jupyter lab                 # run notebooks/01_data.ipynb, then 02_netback.ipynb, then 03_flows.ipynb
python -m src.netback       # the netbacks of every route into the database
python -m src.dashboard     # docs/index.html
```

Notebook 01 builds the database itself. The same two steps from a shell are `python -m src.sql` and `python -m src.data`, run from the repository root.

Two kinds of files are made by hand. IMF PortWatch now asks for a sign-in: `data/portwatch/daily_chokepoints.csv` is the "Daily Chokepoint Transit Calls and Trade Volume Estimates" downloaded as csv from portwatch.imf.org, kept as exported. `data/policy`, `data/freight` and `data/press` are typed from the documents they cite, one source per row.

Two dependencies beyond the other projects: `searoute` gives the distance, the straits and the line of each voyage on a maritime network; `comtradeapicall` is UN Comtrade's own client for the US, Japanese and Canadian trade statistics. The map is drawn by Leaflet, which the browser loads: nothing more to install.

## Data architecture

```
data/lme/aluminium.csv            LME cash and three-month official prices and stock (Westmetall);
                                  download only ever appends to it
data/fred/effr.csv, eurusd.csv    effective federal funds rate and the euro (FRED); append-only
data/premiums/*.csv               one file per CME premium contract: AUP Midwest, EDP Rotterdam
                                  duty-paid, AEP Rotterdam duty-unpaid, MJP Japan; append-only
data/flows/comtrade_*.csv         US and Japanese imports, Canadian exports, HS 7601 (Comtrade)
data/flows/comext_eu_imports.csv  EU-27 imports from the origins, HS 7601 (Comext); every
                                  release downloaded is kept next to the ones before
data/policy/tariffs.csv           section 232 changes, signed and effective dates, by origin
data/policy/cbam_price.csv        CBAM certificate price by quarter, with its publication date
data/freight/war_surcharges.csv   carrier war surcharges, Gulf and Red Sea
data/press/*.csv                  premium assessments quoted in the press, to check the futures
data/portwatch/daily_chokepoints.csv  daily transits at 28 straits and canals, by hand
data/aluminium_arbitrage.duckdb   built from the files, not committed
  raw_prices, raw_flows           tables: the files as they are, with ingested_at; idempotent
  prices, futures, flows          views: clean layer, each number with its unit, latest vintage
  tariffs, cbam_price, ...        tables: the hand files, with their sources
  routes                          table: ports, distance, days at sea, passages, freight, line
  panel                           table: one row per route and trade date, every input as
                                  readable at the New York close, with its date and instant
  netbacks                        table: the cost stack of every origin and destination
src/config.py                     paths, publication times, origins, destinations, every
                                  assumption with its unit and source
src/download.py                   Westmetall, FRED, TradingView, Comtrade, Comext, through curl
src/sql.py                        raw files -> raw tables -> clean views; idempotent
src/routes.py                     searoute voyages and the calibrated freight
src/data.py                       prompt dates, arrival month, duties in force, merge_asof
                                  backward on UTC instants, panel
src/netback.py                    the cost stack, the route rule, the tipping-point ladder
src/voyage.py                     laytime, demurrage and the cost of a day at anchor
src/flows.py                      shipments, shares, the monthly signal and the agreement count
src/dashboard.py                  docs/index.html
```

Loading twice adds no row. A price keeps its first print; a trade release the source republishes is a new row.

## Principal results

Data through 17 November 2026, 2,688 trade dates since April 2016. Netbacks in USD/t FOB at the origin port, over LME cash.

| | Quebec | Iceland | Gulf | India | Malaysia |
|---|---|---|---|---|---|
| Best destination on 17 November 2026 | Midwest | Rotterdam | Rotterdam | Rotterdam | Japan |
| Netback there | 501 | 484 | 123 | 175 | 176 |
| Lead over the second best | 117 | 67 | 8 | 26 | 13 |
| Midwest premium that switches it, US c/lb | 100.0 | 107.8 | 103.8 | 105.0 | 104.9 |
| Midwest premium quoted for its arrival month | 108.0 | 103.2 | 103.2 | 103.2 | 103.2 |
| Days the Midwest / Rotterdam / Japan paid most, % | 91 / 9 / 0 | 34 / 66 / 0 | 58 / 23 / 20 | 36 / 12 / 52 | 21 / 7 / 72 |

Of the Midwest premium of 2,276 USD/t on 17 November, 1,779 pays the 50% section 232 duty and about 500 is premium: 206 on average in 2025, 484 in 2026. The detour round the Cape, taken since 27 December 2023, costs the Gulf 76 USD/t to Rotterdam on average. A day at anchor costs a tonne 2.4 USD, of which 1.3 is the LME curve and 0.7 demurrage.

Against the trade statistics, 128 complete months from January 2016 to August 2026 declared by the US, the EU-27 and Japan:

| | 1 month later | 2 months later | 3 months later |
|---|---|---|---|
| Months in which the destination whose share rose the most is the one the netback said paid most | 35% | 37% | 38% |
| Chance, with three destinations | 33% | 33% | 33% |

Counted that way, the tonnes barely beat chance, and three months is the lag retained by a thin margin. In level they do follow: three months on, a destination takes a larger share of an origin's tonnes after the months it paid most in 12 of the 13 pairs that can be compared, and by a lot for Quebec (Europe 25% against 6%) and India (Europe 74% against 51%). Quebec's turn to Europe under the 25% duty is the clearest case: Canada's exports to the EU-27 went from 0.2% of the total in the first quarter of 2025 to 20% in the third. The tonnes move with a regime that lasts, not with a month's lead.

## Limits

Premiums are CME settlement marks, often unchanged from one day to the next, checked against five press assessments only: the agencies' daily series are paid. Freight is calibrated on one Drewry lane and held flat over ten years, and it is the assumption that moves the answer east of Suez. The US duty is applied on the FOB netback as customs value and from the day each proclamation is signed, not announced. CBAM is charged on default values, which overstate what a producer with verified emissions pays. Annual contracts, quotas and port capacity are invisible to a spot netback. The trade statistics are importers' declarations at country level, published one to four months late and revised; the check counts months, it does not estimate anything.
