"""The voyages: for every origin and destination, the ports, the distance by sea, the days it
takes, the straits and canals it goes through, its freight and its line for the map.

    python -m src.routes   ->  table routes: one row per origin, destination and variant

Distances come from searoute's maritime network, port to port. A voyage that goes through the
Red Sea also gets a detour with Suez and Bab el-Mandeb closed, round the Cape of Good Hope or
through Panama: which of the two a cargo takes on a given day is the route rule of data.py. The
Midwest is reached by sea through the nearer of Baltimore and New Orleans, then by barge or
rail; Quebec reaches it by rail alone.

Freight has no free series per route. It is the Shanghai-Rotterdam lane of Drewry's World
Container Index, per tonne and nautical mile, times the distance: an assumption, held flat.
"""

import json

import pandas as pd
import searoute as sr

from src import config as c, sql


def leg(start, end, restrictions=("northwest",)):
    """One voyage between two coordinates: the ports searoute snaps them to, the distance port to
    port in nautical miles, the passages crossed and the line. Two calls, because searoute 1.6
    fails when asked for the ports and the passages at once."""
    ports = sr.searoute(start, end, units="naut", include_ports=True,
                        restrictions=list(restrictions))["properties"]
    start, end = ports["port_origin"], ports["port_dest"]
    path = sr.searoute((start["x"], start["y"]), (end["x"], end["y"]), units="naut",
                       return_passages=True, restrictions=list(restrictions))
    return {"port_from": start["name"], "locode_from": start["port"],
            "port_to": end["name"], "locode_to": end["port"],
            "distance_nm": path["properties"]["length"],
            "passages": ",".join(path["properties"]["traversed_passages"]),
            "geometry": json.dumps(path["geometry"])}


def build():
    """Every voyage, with its days at sea, its freight and its land leg."""
    lane = leg(*c.WCI_LANE)
    per_tonne_mile = c.WCI_USD_PER_BOX / c.TONNES_PER_BOX / lane["distance_nm"]

    rows = []
    for origin, o in c.ORIGINS.items():
        for destination, ports in c.DESTINATIONS.items():
            if origin == "quebec" and destination == "midwest":
                rows.append({"origin": origin, "destination": destination, "variant": "rail",
                             "distance_nm": 0.0, "passages": "", "land_usd": c.QUEBEC_LAND[0],
                             "land_days": c.QUEBEC_LAND[1]})
                continue
            # by sea to the nearer port when there are two, Baltimore or New Orleans
            end = min(ports, key=lambda port: leg(o["port"], port)["distance_nm"])
            voyage = leg(o["port"], end)
            land = c.US_PORT_LAND if destination == "midwest" else (0, 0)
            base = {"origin": origin, "destination": destination, "land_usd": land[0],
                    "land_days": land[1]}
            rows.append({**base, "variant": "direct", **voyage})
            if "babalmandab" in voyage["passages"]:
                detour = leg(o["port"], end, c.CAPE_RESTRICTIONS)
                rows.append({**base, "variant": "detour", **detour})

    df = pd.DataFrame(rows)
    df["sea_days"] = df["distance_nm"] / c.SPEED_KNOTS / 24
    df["freight_sea"] = df["distance_nm"] * per_tonne_mile
    return df


if __name__ == "__main__":
    routes = build()
    sql.write("routes", routes)
    print(routes.drop(columns="geometry").round(1).to_string())
