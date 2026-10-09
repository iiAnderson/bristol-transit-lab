import numpy as np
import pandas as pd

from lab.congestion import signals as sg

PER = {"AM": ("07:00", "10:00"), "IP": ("10:00", "16:00"), "PM": ("16:00", "19:00")}


def _counts(tmp_path, stamps):
    f = tmp_path / "c.parquet"
    pd.DataFrame({"link_id": "1", "t_ms": [int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)
                                           for s in stamps],
                  "matches": 10, "journey_s": [100.0 + i for i in range(len(stamps))],
                  "mph": 20.0}).to_parquet(f)
    return [f]


def test_hour_stamp_end_shifts_the_hour_back(tmp_path):
    # Wed 15 Nov 2023 (GMT): the 09:00 stamp is 08:00–09:00 if it marks the hour's end
    c = _counts(tmp_path, ["2023-11-15 09:00", "2023-11-15 10:00"])
    end = sg.anpr_obs(c, "Europe/London", PER, "end").set_index("period")["obs_s"]
    assert end["AMPH"] == 100.0 and end["AM"] == 100.5          # 08–09 and 09–10 are both AM
    start = sg.anpr_obs(c, "Europe/London", PER, "start").set_index("period")["obs_s"]
    assert "AMPH" not in start.index and start["IP"] == 101.0   # 09–10 AM, 10–11 IP


def test_weekend_and_night_periods(tmp_path):
    c = _counts(tmp_path, ["2023-11-18 13:00", "2023-11-15 03:00", "2023-11-13 13:00"])
    o = sg.anpr_obs(c, "Europe/London", PER, "end").set_index("period")["obs_s"]
    assert o["WE"] == 100.0 and o["OP"] == 101.0 and "IP" not in o.index   # Monday dropped


def test_balanced_split_keeps_blocks_whole_and_halves_each_area():
    rng = np.random.default_rng(1)
    n = 200
    paths = pd.DataFrame({"link_id": np.arange(n).astype(str),
                          "mlon": -2.6 + rng.uniform(0, 0.08, n),
                          "mlat": 51.45 + rng.uniform(0, 0.05, n)})
    area = pd.Series(np.where(np.arange(n) < 80, "centre", "urban"), index=paths["link_id"])
    half = sg.split_links_balanced(paths, area, 1.0)
    for a in ("centre", "urban"):
        share = (half[area == a] == "calibration").mean()
        assert 0.35 < share < 0.65
    bx = np.floor(paths["mlon"] * 111.32 * 0.623).astype(int).astype(str)
    by = np.floor(paths["mlat"] * 111.32).astype(int).astype(str)
    key = area.to_numpy() + bx.to_numpy() + "_" + by.to_numpy()
    assert pd.Series(half.reindex(paths["link_id"]).to_numpy()).groupby(key).nunique().max() == 1


def test_fit_layer_recovers_multipliers_and_delay():
    rng = np.random.default_rng(0)
    areas, periods = ["centre", "urban"], ["IP", "AM"]
    k_true = {("centre", "IP"): 0.8, ("centre", "AM"): 0.7, ("urban", "IP"): 1.1, ("urban", "AM"): 0.9}
    d_true = {"centre": 4.0, "urban": 15.0}
    links = [str(i) for i in range(120)]
    comp, obs = {}, []
    for p in periods:
        c = pd.DataFrame({"T_centre": rng.uniform(0, 200, 120) * (rng.random(120) < 0.5),
                          "S_centre": rng.integers(0, 6, 120).astype(float),
                          "T_urban": rng.uniform(20, 300, 120),
                          "S_urban": rng.integers(0, 8, 120).astype(float),
                          "T_other": rng.uniform(0, 30, 120)}, index=links)
        c.loc[c["T_centre"] == 0, "S_centre"] = 0
        comp[p] = c
        t = sg.layer_times(c, {a: k_true[(a, p)] for a in areas}, d_true)
        obs.append(pd.DataFrame({"link_id": links, "period": p,
                                 "obs_s": t.to_numpy() * np.exp(rng.normal(0, 0.02, 120))}))
    fit = sg.fit_layer(comp, pd.concat(obs), areas, periods)
    for key, v in k_true.items():
        assert abs(fit["k"][key] - v) < 0.05
    for a, v in d_true.items():
        assert abs(fit["d"][a] - v) < 2.0
