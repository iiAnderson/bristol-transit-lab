import pandas as pd

from lab.congestion import road_class as rc


def road(ref, n, lon0=-3.0, step=0.01, node0=1, both=True):
    rows = []
    for i in range(n):
        for fwd in ((True, False) if both else (True,)):
            x0, x1 = lon0 + i * step, lon0 + (i + 1) * step
            u, v, lu, lv = ((node0 + i, node0 + i + 1, x0, x1) if fwd
                            else (node0 + i + 1, node0 + i, x1, x0))
            rows.append({"way_id": 1, "seq": i, "forward": fwd, "u": u, "v": v, "ref": ref,
                         "length_m": 700.0, "lon_u": lu, "lat_u": 51.5,
                         "lon_v": lv, "lat_v": 51.5})
    return pd.DataFrame(rows)


def test_class_splits_between_count_points_and_basis():
    s = road("A4042", 10)
    cps = pd.DataFrame([
        {"cp_id": 1, "road_ref": "A4042", "cp_category": "TA", "lon": -2.9995, "lat": 51.5,
         "link_length_km": 1.0},
        {"cp_id": 2, "road_ref": "A4042", "cp_category": "PA", "lon": -2.9105, "lat": 51.5,
         "link_length_km": 1.0}])
    out, bounds = rc.propagate(s, cps)
    f = out[out["forward"]].sort_values("seq")
    assert list(f["dft_class"]) == ["srn"] * 5 + ["local_a"] * 5
    assert f.iloc[0]["basis"] == "count_point" and f.iloc[3]["basis"] == "propagated"
    assert len(bounds) == 1 and bounds[0]["classes"] == ["local_a", "srn"]


def test_no_count_point_falls_back_to_osm():
    out, _ = rc.propagate(road("A999", 3), pd.DataFrame(
        columns=["cp_id", "road_ref", "cp_category", "lon", "lat", "link_length_km"]))
    assert set(out["basis"]) == {"osm_fallback"}


def test_class_does_not_jump_a_gap():
    s = pd.concat([road("A1", 3), road("A1", 3, lon0=-2.5, node0=100)])
    cps = pd.DataFrame([{"cp_id": 1, "road_ref": "A1", "cp_category": "PA", "lon": -2.9995,
                         "lat": 51.5, "link_length_km": 1.0}])
    out, _ = rc.propagate(s, cps)
    assert (out[out["seq"].isin([0, 1, 2])].groupby("basis").size() > 0).any()
    far = out.iloc[len(road("A1", 3)):]
    assert set(far["basis"]) == {"osm_fallback"}
