"""A static page for the desk, rebuilt every evening: docs/index.html, served by GitHub Pages.

    python -m src.dashboard

In this order, to be read in thirty seconds: three lines written from the numbers (today's
arbitrage, what moved since the day before, the nearest tipping point) and an alert when an
origin's best destination has changed; the tipping-point ladder against the quoted premiums; the
netbacks by origin and destination; the routes on a map coloured by their traffic; the Midwest
premium cut into duty and premium; the check on the tonnes.

The charts are matplotlib images inside the page. The map is the routes' GeoJSON drawn by
Leaflet over OpenStreetMap tiles, loaded by the browser: nothing to install.
"""

import base64
import io
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src import config as c, data, flows, netback, sql

COLORS = {"midwest": "C0", "rotterdam": "C1", "japan": "C2"}


def image(fig):
    """A figure as an <img> tag, the PNG inside the page."""
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return f'<img src="data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode()}">'


def headline(nb, ladder):
    """Three sentences from the numbers, and the origins whose best destination changed."""
    days = sorted(nb["trade_date"].unique())
    before, today = (nb[nb["trade_date"] == d].pivot(index="origin", columns="destination",
                                                     values="netback") for d in days[-2:])
    best, best_before = today.idxmax(axis=1), before.idxmax(axis=1)
    lead = today.apply(lambda row: row.nlargest(2).iloc[0] - row.nlargest(2).iloc[1], axis=1)
    moved = (today - before).stack().abs().idxmax()
    gap = (ladder["midwest_quoted"] - ladder["midwest_tipping"]).abs()
    nearest = gap.idxmin()
    lines = [
        "Today: " + "; ".join(f"{o} to {best[o]} (+{lead[o]:.0f} USD/t)" for o in c.ORIGINS) + ".",
        f"Largest move since {pd.Timestamp(days[-2]):%d %b}: {moved[0]} to {moved[1]}, "
        f"{(today - before).loc[moved]:+.0f} USD/t.",
        f"Nearest tipping point: {nearest} switches at a Midwest premium of "
        f"{ladder.loc[nearest, 'midwest_tipping']:.1f} c/lb, quoted "
        f"{ladder.loc[nearest, 'midwest_quoted']:.1f}."]
    alerts = [f"{o} now goes to {best[o]}, not {best_before[o]}" for o in c.ORIGINS
              if best[o] != best_before[o]]
    return lines, alerts


def charts(nb, ladder):
    """The four charts of the page, as <img> tags."""
    wide = nb.pivot_table(index="trade_date", columns=["origin", "destination"], values="netback")
    y = np.arange(len(c.ORIGINS))
    fig, axes = plt.subplots(1, 2, figsize=(12, 3))
    for ax, market, unit in ((axes[0], "midwest", "US cents per lb"), (axes[1], "japan", "USD/t")):
        ax.scatter(ladder[f"{market}_tipping"], y, color=COLORS[market], s=50, label="tipping")
        ax.scatter(ladder[f"{market}_quoted"], y, color="k", marker="|", s=250, label="quoted")
        ax.set_yticks(y, list(c.ORIGINS)); ax.set_xlabel(unit); ax.grid(alpha=.3)
        ax.set_title(f"{market.title()} premium that switches each origin")
    axes[0].legend()
    ladder_png = image(fig)

    fig, axes = plt.subplots(1, len(c.ORIGINS), figsize=(15, 2.8), sharey=True)
    recent = wide.loc[wide.index[-1] - pd.DateOffset(years=1):]
    for ax, origin in zip(axes, c.ORIGINS):
        for destination in c.DESTINATIONS:
            recent[(origin, destination)].plot(ax=ax, color=COLORS[destination], lw=1,
                                               label=destination)
        ax.axhline(0, color="k", lw=.6); ax.set_title(origin); ax.set_xlabel(""); ax.grid(alpha=.3)
    axes[0].set_ylabel("USD/t over LME cash"); axes[0].legend(fontsize=8)
    netback_png = image(fig)

    split = netback.midwest_split(data.dataset("iceland", "midwest", "direct"))
    fig, ax = plt.subplots(figsize=(12, 2.8))
    ax.fill_between(split.index, split["excluding_duty"], split["premium"], color="0.8",
                    label="pays the section 232 duty")
    ax.plot(split.index, split["excluding_duty"], color=COLORS["midwest"], label="premium left")
    ax.axhline(0, color="k", lw=.6); ax.set_ylabel("USD/t"); ax.grid(alpha=.3); ax.legend()
    ax.set_title("Midwest premium: duty and premium left")
    split_png = image(fig)

    table = flows.agreement(netbacks=nb)
    counts = table.groupby("origin")[["agree_1", "agree_2", "agree_3"]].agg(["sum", "count"])
    counts = counts.reindex(list(c.ORIGINS))
    fig, ax = plt.subplots(figsize=(12, 2.4))
    for k in (1, 2, 3):
        rate = 100 * counts[(f"agree_{k}", "sum")] / counts[(f"agree_{k}", "count")]
        label = f"{k} month{'s' if k > 1 else ''} later"
        ax.bar(np.arange(len(rate)) + (k - 2) * 0.25, rate, width=0.25, label=label)
    ax.set_xticks(np.arange(len(counts)), counts.index); ax.set_ylabel("% of months")
    ax.axhline(100 / 3, color="k", lw=.6, ls=":"); ax.legend(); ax.grid(alpha=.3)
    ax.set_title("Months in which the destination whose share rose is the one the netback "
                 "said paid most (dotted: chance)")
    flows_png = image(fig)
    return ladder_png, netback_png, split_png, flows_png


def routes_geojson(date):
    """Every route the rule picks on a date, as GeoJSON lines with their traffic."""
    features = []
    picked = sql.query(f"SELECT origin, destination, variant FROM netbacks "
                       f"WHERE trade_date = DATE '{date:%Y-%m-%d}'")
    routes = sql.query("SELECT * FROM routes").merge(picked)
    for _, r in routes[routes["variant"] != "rail"].iterrows():
        traffic = data.dataset(r["origin"], r["destination"], r["variant"]).loc[date, "traffic"]
        properties = {"route": f"{r['origin']} to {r['destination']}",
                      "passages": r["passages"] or "none",
                      "traffic": None if np.isnan(traffic) else round(traffic, 2)}
        features.append({"type": "Feature", "geometry": json.loads(r["geometry"]),
                         "properties": properties})
    return json.dumps({"type": "FeatureCollection", "features": features})


def page():
    """The whole page as html."""
    nb = sql.query("SELECT * FROM netbacks")
    date = nb["trade_date"].max()
    ladder = netback.tipping(nb, date)
    lines, alerts = headline(nb, ladder)
    ladder_png, netback_png, split_png, flows_png = charts(nb, ladder)
    today = nb[nb["trade_date"] == date].pivot(index="origin", columns="destination",
                                               values="netback").loc[list(c.ORIGINS)]
    table = today.round(0).astype(int).to_html(classes="numbers")
    alert = "".join(f'<p class="alert">{a}</p>' for a in alerts)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Aluminium netback</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
body {{font-family: Helvetica, Arial, sans-serif; max-width: 1200px; margin: 0 auto;
      padding: 16px; color: #222; background: #fff}}
h1 {{font-size: 22px}} h2 {{font-size: 16px; margin-top: 28px}}
img {{max-width: 100%}} #map {{height: 380px}}
.alert {{background: #fde8e8; padding: 6px 10px}}
.numbers td, .numbers th {{padding: 2px 10px; text-align: right}}
.small {{color: #666; font-size: 12px}}
</style></head><body>
<h1>Where should the next tonne go? Primary aluminium netbacks, {date:%d %B %Y}</h1>
{alert}<p>{"<br>".join(lines)}</p>
<h2>Tipping-point ladder</h2>{ladder_png}
<h2>Netback by origin and destination, USD/t FOB over LME cash</h2>{table}{netback_png}
<h2>Routes, coloured by container traffic at their most disrupted passage against 2019</h2>
<div id="map"></div>
<h2>What the Midwest premium is made of</h2>{split_png}
<h2>Do the tonnes follow the netback?</h2>{flows_png}
<p class="small">LME official prices (Westmetall), CME premium futures (TradingView), EFFR and
EUR/USD (FRED), section 232 proclamations, CBAM prices (European Commission), IMF PortWatch,
UN Comtrade, Eurostat Comext. Every number as readable at the New York close. Freight is
calibrated on Drewry's WCI and held flat. Relative values from settlement marks, not prices.</p>
<script>
const map = L.map("map", {{worldCopyJump: true}}).setView([25, 40], 2);
L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png",
            {{attribution: "&copy; OpenStreetMap"}}).addTo(map);
const color = t => t === null ? "#888" : t < 0.5 ? "#c0392b" : t < 0.9 ? "#e67e22" : "#27ae60";
L.geoJSON({routes_geojson(date)}, {{
  style: f => ({{color: color(f.properties.traffic), weight: 2}}),
  onEachFeature: (f, layer) => layer.bindTooltip(
    `${{f.properties.route}}: ${{f.properties.passages}}, traffic ${{f.properties.traffic}}`)
}}).addTo(map);
</script></body></html>
"""


if __name__ == "__main__":
    (c.ROOT / "docs").mkdir(exist_ok=True)
    (c.ROOT / "docs" / "index.html").write_text(page())
    print(f"docs/index.html written")
