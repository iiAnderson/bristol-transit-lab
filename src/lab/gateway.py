"""
External rail passengers in a scenario comparison (SPEC §6.1 amendment, 2026-10-18).

`lab demand external` puts a day's external rail trips at gateway stations inside the
extent (`demand_gateway`). What a scheme does for them is the **onward leg**: from the
gateway to the workplace zone (inbound) or from home to the gateway (outbound). Their
main mode is fixed, so a scheme can only change that leg.

* **Skims.** The same r5r PT skim as every other, on the scenario's own network and
  departure window, between the gateways and the zones that appear in the demand: gateway
  → zone for inbound, zone → gateway for outbound. Small beside the OA → LSOA grid.
* **Metric.** Per scenario and parent, over the same trips: mean time, trip-weighted; trips
  at least `by_min` faster or slower; trips newly reachable or lost; person-hours saved on
  an average weekday. Counted per timetable offset and averaged, as the people figures are.

Fixed mode means the trips themselves do not change with the scheme: this is time saved
for people who travel anyway, not new demand.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from . import run as rn

DIRS = ("external_in", "external_out")


def points(con, version: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Gateways and zones (id, lat, lon) that carry demand in ``version``."""
    gw = con.execute("""SELECT g.crs AS id, g.lat, g.lon FROM gateway g
                        WHERE g.crs IN (SELECT gateway FROM demand_gateway WHERE demand_version=?)
                        ORDER BY 1""", [version]).df()
    zones = con.execute("""SELECT l.LSOA21CD AS id, l.lat, l.lon FROM int_lsoa l
                           WHERE l.LSOA21CD IN (SELECT zone FROM demand_gateway WHERE demand_version=?)
                           ORDER BY 1""", [version]).df()
    if gw.empty or zones.empty:
        raise ValueError(f"no gateway demand in version {version!r}: run `lab demand external`")
    return gw, zones


def inputs_key(gw: pd.DataFrame, zones: pd.DataFrame) -> str:
    """Identity of the skimmed points: a change of gateways or zones makes new files."""
    h = hashlib.sha256(gw.round(6).to_csv(index=False).encode() + zones.round(6).to_csv(index=False).encode())
    return h.hexdigest()[:10]


def leg_skim(cfg, raw: dict, ps: dict, nw: dict, period: str, work, day, log,
             gw: pd.DataFrame, zones: pd.DataFrame) -> pd.DataFrame:
    """Both legs' PT skim on one network and period, with a ``dir`` column."""
    inb = rn.pt_skim(cfg, raw, ps, nw, period, work / "in", day, log, origins=gw, destinations=zones)
    out = rn.pt_skim(cfg, raw, ps, nw, period, work / "out", day, log, origins=zones, destinations=gw)
    return pd.concat([inb.assign(dir="external_in"), out.assign(dir="external_out")], ignore_index=True)


def _legs(skim: pd.DataFrame, dg: pd.DataFrame) -> pd.DataFrame:
    """The skim row for each demand row: gateway → zone inbound, zone → gateway outbound.
    A pair the skim does not hold (beyond the trip-time cap) is unreachable."""
    inbound = (dg["dir"] == "external_in").to_numpy()
    k = pd.DataFrame({"from_id": np.where(inbound, dg.gateway, dg.zone),
                      "to_id": np.where(inbound, dg.zone, dg.gateway)}, index=dg.index)
    x = k.merge(skim[["from_id", "to_id", "p50", "unreachable"]].drop_duplicates(["from_id", "to_id"]),
                on=["from_id", "to_id"], how="left")
    x.index = dg.index
    x["unreachable"] = x["unreachable"].fillna(True).astype(bool) | x["p50"].isna()
    return x


def leg_summary(parent: pd.DataFrame, scen: pd.DataFrame, dg: pd.DataFrame,
                by_min: float = 2.0) -> tuple[dict, pd.DataFrame]:
    """Parent against scenario over the gateway trips ``dg`` (dir, gateway, zone, trips).
    Returns the summary by direction and overall, and a per-gateway frame."""
    p, s = _legs(parent, dg), _legs(scen, dg)
    j = dg[["dir", "gateway", "zone", "trips"]].copy()
    j["pu"], j["su"] = p.unreachable, s.unreachable
    j["tp"], j["ts"] = p.p50, s.p50
    both = ~j.pu & ~j.su
    j["d"] = (j.ts - j.tp).where(both)
    j["w_d"] = j.trips * j.d.fillna(0.0)
    j["faster"] = j.trips.where(j.d <= -by_min, 0.0)
    j["slower"] = j.trips.where(j.d >= by_min, 0.0)
    j["gained"] = j.trips.where(j.pu & ~j.su, 0.0)
    j["lost"] = j.trips.where(~j.pu & j.su, 0.0)
    j["both"] = j.trips.where(both, 0.0)

    def agg(g: pd.DataFrame) -> dict:
        b = g["both"].sum()
        return {"trips": float(g.trips.sum()), "trips_reachable_in_both": float(b),
                "mean_time_parent_min": float((g.tp * g["both"]).sum() / b) if b else None,
                "mean_time_scenario_min": float((g.ts * g["both"]).sum() / b) if b else None,
                "mean_change_min": float(g.w_d.sum() / b) if b else None,
                "trips_faster_by_threshold": float(g.faster.sum()),
                "trips_slower_by_threshold": float(g.slower.sum()),
                "trips_newly_reachable": float(g.gained.sum()),
                "trips_newly_unreachable": float(g.lost.sum()),
                "person_hours_saved_per_weekday": float(-g.w_d.sum() / 60.0)}

    out = {"threshold_min": by_min, "all": agg(j)} | {d: agg(j[j.dir == d]) for d in DIRS if (j.dir == d).any()}
    gb = j.groupby(["dir", "gateway"])
    by_gw = pd.DataFrame({
        "trips": gb.trips.sum(),
        "mean_change_min": gb.w_d.sum() / gb["both"].sum().replace(0, np.nan),
        "trips_faster": gb.faster.sum(), "trips_slower": gb.slower.sum(),
        "person_hours_saved": -gb.w_d.sum() / 60.0}).reset_index()
    return out, by_gw


def over_offsets(pairs: list, dg: pd.DataFrame, by_min: float = 2.0) -> tuple[dict, pd.DataFrame]:
    """`leg_summary` on each (parent, scenario) pair of skims (frames or parquet paths),
    then the mean, minimum and maximum over the offsets, as for the people figures."""
    sums, frames = [], []
    for par, scen in pairs:
        par = pd.read_parquet(par) if not isinstance(par, pd.DataFrame) else par
        scen = pd.read_parquet(scen) if not isinstance(scen, pd.DataFrame) else scen
        s, g = leg_summary(par, scen, dg, by_min)
        sums.append(s)
        frames.append(g.set_index(["dir", "gateway"]))
    by_gw = sum(f.astype(float) for f in frames) / len(frames)
    out = rn.across_offsets(sums)
    out["offsets"] = len(pairs)
    return out, by_gw.reset_index()
