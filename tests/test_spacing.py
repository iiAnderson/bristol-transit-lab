import numpy as np
import pandas as pd

from lab.congestion import calibrate as cal, spacing as sp


def _seg(n=60):
    return pd.DataFrame({
        "u": np.arange(n), "v": np.arange(n) + 1000, "road_class": "local_a",
        "area_type": "urban", "bus_flag": False, "direction": "inbound", "ff": 48.0,
        "lon_u": -2.6 + np.arange(n) * 1e-3, "lat_u": 51.45,
        "lon_v": -2.6 + np.arange(n) * 1e-3 + 5e-4, "lat_v": 51.45})


def _trav(seg, speeds: dict[str, float], legs=12, start=0):
    rows = []
    for per, kmh in speeds.items():
        for u, v in zip(seg["u"], seg["v"]):
            for k in range(legs):
                rows.append({"u": u, "v": v, "kmh": kmh, "period": per,
                             "leg": start + len(rows), "hour": 8 if per == "AM" else 12})
    return pd.DataFrame(rows)


def test_pm_shape_comes_from_live_days_only():
    seg = _seg()
    archive = _trav(seg, {"AM": 18.0, "IP": 20.0})
    live = _trav(seg, {"IP": 25.0, "PM": 20.0})
    nat = {"AM": 0.99, "PM": 0.94, "OP": 1.25}
    r, diag, _ = cal.bus_shape(seg, archive, 10, 20, nat, live)
    assert abs(r[("PM", "inbound", "urban", "main")] - 0.8) < 1e-6      # 20 / 25, live
    assert diag[("PM", "inbound", "urban", "main")][0] == "bus_live"
    assert abs(r[("AM", "inbound", "urban", "main")] - 0.9) < 1e-6      # 18 / 20, archive
    r0, diag0, _ = cal.bus_shape(seg, archive, 10, 20, nat)
    assert r0[("PM", "inbound", "urban", "main")] == nat["PM"]
    assert diag0[("PM", "inbound", "urban", "main")][0] == "national_fallback"


def test_spacing_ratio_and_stop_bands():
    seg = _seg()
    full = _trav(seg, {"IP": 20.0})
    thin = _trav(seg, {"IP": 19.0})
    stops = pd.DataFrame({"lon": [seg["lon_u"][0] + 2.5e-4], "lat": [51.45]})
    cells, res = sp.compare(full, thin, seg, stops, 10, stop_radius_m=30)
    assert abs(res["overall"]["median_ratio"] - 0.95) < 1e-6
    assert len(cells) == len(seg)
    bands = {r["stop_band"]: r["cells"] for r in res["by_stop_band"]}
    assert bands["1"] == 1 and bands["0"] == len(seg) - 1
