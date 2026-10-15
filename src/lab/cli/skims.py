"""Skims: PT, car, walk and cycle."""
from __future__ import annotations

from pathlib import Path

import click

from .. import params, runrecord
from ..config import LabConfig

@click.group()
def skims() -> None:
    """P2c skims: PT (r5r), walk and cycle (r5py)."""


@skims.command("pt")
@click.option("--period", type=click.Choice(["AM", "IP"]), required=True)
@click.option("--chunk", default=100, show_default=True)
@click.option("--provisional/--final", default=True, show_default=True,
              help="Provisional until the unresolved same-start trip variants are settled.")
def skims_pt(period: str, chunk: int, provisional: bool) -> None:
    """PT skims OA -> clip-box LSOAs from r5r's expanded matrix (per-pair summary)."""
    import datetime as dt
    import shutil
    import duckdb
    import yaml
    from .. import skims as sk
    from ..supply import feeds
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    day = raw["modelled_date"]
    day = day if isinstance(day, dt.date) else dt.date.fromisoformat(day)
    for fid in ("osm_clip", "bus_gtfs", "rail_gtfs"):
        feeds.check_file_unchanged(feeds.get(cfg, fid))
        if fid != "osm_clip":
            feeds.require_covers(feeds.get(cfg, fid), day)
    from .. import network
    nw = network.settings(cfg, raw, ps)
    work = nw["skims"] / "_work" / f"pt_{period}"
    net = nw["r5r_net"]
    work.mkdir(parents=True, exist_ok=True)
    net.mkdir(parents=True, exist_ok=True)
    for src in (raw["osm"]["clip"], raw["bus"]["out"], raw["rail"]["out"]):
        dst = net / Path(src).name
        if not dst.is_file() or params.file_hash(dst) != params.file_hash(cfg.root / src):
            shutil.copyfile(cfg.root / src, dst)
    if nw["elevation"] and not ((net / "elevation.tif").is_file()
                                and params.file_hash(net / "elevation.tif") == nw["hashes"]["dem_clip"]):
        shutil.copyfile(nw["tif"], net / "elevation.tif")
    with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
        con.execute("SELECT OA21CD id, lat, lon FROM int_oa_pwc ORDER BY 1").df() \
            .to_csv(work / "origins.csv", index=False)
        con.execute("SELECT LSOA21CD id, lat, lon FROM skim_dest ORDER BY 1").df() \
            .to_csv(work / "destinations.csv", index=False)
    shutil.copyfile(cfg.root / "data" / "interim" / "r5r" / "d3_origins.csv",
                    work / "sample_ids.csv")
    rcfg = {"net_dir": str(net), "origins": str(work / "origins.csv"),
            "destinations": str(work / "destinations.csv"),
            "departure": f"{day} {ps[f'skims.window_start.{period}']}",
            "window_min": ps["skims.departure_window_min"],
            "max_rides": ps["routing.max_rides"], "walk_speed_kmh": nw["walk_kmh"],
            "elevation": nw["elevation"], "network_version": nw["version"],
            "max_walk_min": ps["routing.max_walk_min"], "max_trip_min": ps["routing.max_trip_min"],
            "reach_share_min": ps["routing.pt_reachable_share_min"], "chunk": chunk,
            "sample_ids": str(work / "sample_ids.csv"), "out_dir": str(work / "chunks"),
            "java_mem": raw["router"]["java_mem"]}
    rec = runrecord.build(cfg, command=f"skims-pt-{period}", inputs=[
        {"name": f, "sha256": h} for f, h in nw["hashes"].items()]
        + [{"name": "r5r", "version": "2.4.0 (R5 7.5.1)"},
           {"name": "network_version", "version": nw["version"]}])
    runrecord.write(cfg, rec)
    try:
        sk.run_pt(cfg.root / "src" / "lab" / "r" / "pt_skims.R", rcfg, work / "config.json",
                  lambda m: click.echo(f"  {m}", err=True),
                  [nw["hashes"][f] for f in ("osm_clip", "bus_gtfs", "rail_gtfs")]
                  + [params.file_hash(cfg.root / "src" / "lab" / "r" / "pt_skims.R"), nw["version"]])
        s = sk.combine(work / "chunks")
        w = {k: ps[f"generalised_cost.{k}"] for k in ("w_walk", "w_wait", "p_interchange")}
        curve = ps["generalised_cost.first_wait_curve"]
        s["gc_min"] = sk.gc_tag(s, w, curve).where(~s["unreachable"])
        s["gc_min_random_arrival"] = sk.gc_from_components(s, w).where(~s["unreachable"])
        # the PT alternative proper: at least one ride, in at least the same share of
        # window minutes as the reachability rule
        s["gc_ride_min"] = sk.gc_tag(sk.ride_only(s), w, curve).where(
            s["ride_share"] >= ps["routing.pt_reachable_share_min"])
        s["provisional"] = provisional
        out = network.skim(nw, "pt", period)
        s.to_parquet(out, compression="zstd")
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {"pairs": len(s), "unreachable_share": float(s["unreachable"].mean()),
                     "median_p50": float(s["p50"].median()), "provisional": provisional,
                     "network_version": nw["version"], "walk_speed_kmh": nw["walk_kmh"],
                     "out": str(out)}
    click.echo(f"  {rec['result']}")
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@skims.command("pt-gc")
@click.option("--period", "periods", multiple=True, default=["AM", "IP"], show_default=True)
def skims_pt_gc(periods: tuple[str, ...]) -> None:
    """Recompute PT GC on stored skims from params (no re-routing): gc_min uses the TAG
    M3.2 first-wait curve; gc_min_random_arrival keeps half-headway waiting."""
    import pandas as pd
    from .. import skims as sk
    import yaml
    from .. import network
    cfg = LabConfig.load()
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    nw = network.settings(cfg, yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text()), ps)
    w = {k: ps[f"generalised_cost.{k}"] for k in ("w_walk", "w_wait", "p_interchange")}
    curve = ps["generalised_cost.first_wait_curve"]
    rec = runrecord.build(cfg, command="skims-pt-gc")
    runrecord.write(cfg, rec)
    res = {}
    for per in periods:
        f = network.skim(nw, "pt", per)
        s = pd.read_parquet(f)
        s["gc_min"] = sk.gc_tag(s, w, curve).where(~s["unreachable"])
        s["gc_min_random_arrival"] = sk.gc_from_components(s, w).where(~s["unreachable"])
        s["gc_ride_min"] = sk.gc_tag(sk.ride_only(s), w, curve).where(
            s["ride_share"] >= ps["routing.pt_reachable_share_min"])
        s.to_parquet(f, compression="zstd")
        ok = s[~s["unreachable"]]
        d = ok["gc_min_random_arrival"] - ok["gc_min"]
        res[per] = {"median_gc_tag": float(ok["gc_min"].median()),
                    "median_gc_random": float(ok["gc_min_random_arrival"].median()),
                    "median_reduction_min": float(d.median()),
                    "p90_reduction_min": float(d.quantile(0.9))}
        click.echo(f"  {per}: {res[per]}")
    rec["result"] = res
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@skims.command("car")
@click.option("--period", "periods", multiple=True, default=["AMPH", "IP", "AM"],
              show_default=True, help="AMPH = 08:00–09:00 skim hour; AM (07:00–10:00) is "
              "the stored sensitivity.")
@click.option("--variant", type=click.Choice(["hybrid", "base"]), default="hybrid",
              show_default=True)
def skims_car(periods: tuple[str, ...], variant: str) -> None:
    """Car skims OA -> clip-box LSOAs from OSRM with calibrated per-period speeds."""
    import duckdb
    import numpy as np
    import pandas as pd
    import yaml
    from .. import network
    from ..congestion import validate as va
    from ..supply import osrm
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    d = cfg.root / "data" / "interim" / "congestion"
    sp = d / ("osrm_speeds" if variant == "hybrid" else "osrm_speeds_base")
    base = cfg.root / raw["osm"]["osrm_base"]
    rec = runrecord.build(cfg, command="skims-car", inputs=[
        {"name": str(sp / f"speeds_{p}.csv"), "sha256": params.file_hash(sp / f"speeds_{p}.csv")}
        for p in periods] + [{"name": "osrm", "version": osrm.version()}])
    runrecord.write(cfg, rec)
    res = {}
    try:
        with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
            o = con.execute("SELECT OA21CD id, lon, lat FROM int_oa_pwc ORDER BY 1").df()
            dst = con.execute("""SELECT d.LSOA21CD id, d.lon, d.lat,
                CASE WHEN d.internal THEN 1 ELSE 0 END internal FROM skim_dest d ORDER BY 1""").df()
        park = {k: ps.get(f"car.parking_search_min.{k}") for k in ("centre", "urban", "rural")}
        walk = {k: ps.get(f"car.access_walk_min.{k}") for k in ("centre", "urban", "rural")}
        for per in periods:
            ds = va.period_dataset(base, sp / f"speeds_{per}.csv",
                                   cfg.root / "data" / "interim" / "osrm" / f"{variant}_{per}")
            with osrm.Server(ds) as srv:
                m = srv.table(list(zip(o.lon, o.lat)), list(zip(dst.lon, dst.lat))) / 60
            t = pd.DataFrame({"from_id": np.repeat(o["id"].to_numpy(), len(dst)),
                              "to_id": np.tile(dst["id"].to_numpy(), len(o)),
                              "ivt_min": m.ravel()})
            t["parking_search_min"] = np.nan    # modelled range, applied in `lab gapmap`
            t["access_walk_min"] = np.nan       # (by destination area type)
            t["gc_min"] = np.nan
            t["variant"], t["period"] = variant, per
            out = network.skim(network.settings(cfg, raw, ps), "car", per)
            t.to_parquet(out, compression="zstd")
            res[per] = {"pairs": len(t), "unroutable": int(t["ivt_min"].isna().sum()),
                        "median_ivt_min": float(t["ivt_min"].median())}
            click.echo(f"  {per}: {res[per]}")
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {"variant": variant, **res,
                     "note": "in-vehicle time only; car GC adds the modelled parking/access "
                             "range in `lab gapmap`"}
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")


@skims.command("active")
def skims_active() -> None:
    """Walk and cycle skims OA -> clip-box LSOAs (r5py, shared routing settings)."""
    import datetime as dt
    import duckdb
    import geopandas as gpd
    import yaml
    from shapely.geometry import Point
    from r5py import TransportMode, TransportNetwork, TravelTimeMatrix
    from ..supply import feeds
    cfg = LabConfig.load()
    raw = yaml.safe_load((cfg.root / "config" / "lab.yaml").read_text())
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    day = raw["modelled_date"]
    day = day if isinstance(day, dt.date) else dt.date.fromisoformat(day)
    with duckdb.connect(str(cfg.lab_db), read_only=True) as con:
        o = con.execute("SELECT OA21CD id, lon, lat FROM int_oa_pwc ORDER BY 1").df()
        d = con.execute("SELECT LSOA21CD id, lon, lat FROM skim_dest ORDER BY 1").df()
    g = lambda df: gpd.GeoDataFrame({"id": df["id"]}, crs="EPSG:4326",  # noqa: E731
                                    geometry=[Point(x, y) for x, y in zip(df.lon, df.lat)])
    rec = runrecord.build(cfg, command="skims-active", inputs=[
        {"name": "osm_clip", "sha256": feeds.get(cfg, "osm_clip")["sha256"]}])
    runrecord.write(cfg, rec)
    res = {}
    try:
        from r5py import ElevationCostFunction
        from .. import network
        from ..supply import dem
        nw = network.settings(cfg, raw, ps)
        net = (TransportNetwork(str(cfg.root / raw["osm"]["clip"]), []) if not nw["elevation"] else
               TransportNetwork(str(cfg.root / raw["osm"]["clip"]), [],
                                elevation_model=[dem.for_function(nw["tif"], nw["elevation"])],
                                elevation_cost_function=ElevationCostFunction(nw["elevation"])))
        dep = dt.datetime.combine(day, dt.time.fromisoformat(ps["skims.window_start.AM"]))
        mx = dt.timedelta(minutes=ps["routing.max_trip_min"])
        for mode, tm in (("walk", TransportMode.WALK), ("cycle", TransportMode.BICYCLE)):
            t = TravelTimeMatrix(net, origins=g(o), destinations=g(d), departure=dep,
                                 transport_modes=[tm], max_time=mx,
                                 speed_walking=nw["walk_kmh"],
                                 speed_cycling=ps["routing.cycle_speed_kmh"],
                                 max_bicycle_traffic_stress=ps["routing.max_bicycle_lts"])
            out = network.skim(nw, mode)
            t["travel_time_corrected"] = t["travel_time"] + ps["routing.r5py_truncation_correction_min"]
            t.to_parquet(out)
            res[mode] = {"pairs": len(t), "reachable": int(t["travel_time"].notna().sum()),
                         "median_min": float(t["travel_time"].median())}
            click.echo(f"  {mode}: {res[mode]}")
    except Exception:
        runrecord.finish(cfg, rec, "failed")
        raise
    rec["result"] = {**res, "network_version": nw["version"], "walk_speed_kmh": nw["walk_kmh"]}
    click.echo(f"wrote {runrecord.finish(cfg, rec, 'ok')}")
