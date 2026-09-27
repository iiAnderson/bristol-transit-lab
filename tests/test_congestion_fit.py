import numpy as np
import pandas as pd

from lab.congestion import fit

P = {"AM": 0.12, "IP": 0.25, "PM": 0.12, "OP": 0.21, "WE": 0.30}
RHO = {"OP": 1.25, "WE": 1.15}
AREAS = ["centre", "urban", "rural"]


def synthetic(seed=0, n_roads=40, per_road=30):
    rng = np.random.default_rng(seed)
    rows = []
    auths = ["A1", "A2", "A3", "A4"]
    for i in range(n_roads):
        auth = auths[i % len(auths)]
        mix = rng.dirichlet([1, 3, 2])
        for j in range(per_road):
            rows.append({"road": f"{auth}:R{i}", "authority": auth,
                         "area": rng.choice(AREAS, p=mix),
                         "direction": rng.choice(["inbound", "outbound"]),
                         "length_m": rng.uniform(50, 400), "w": rng.uniform(5e3, 3e4),
                         "ff_kmh": rng.choice([32.0, 48.0, 64.0, 96.0])})
    return pd.DataFrame(rows)


R = {("AM", "inbound", "centre"): 0.80, ("AM", "outbound", "centre"): 0.90,
     ("AM", "inbound", "urban"): 0.85, ("AM", "outbound", "urban"): 0.93,
     ("AM", "inbound", "rural"): 0.92, ("AM", "outbound", "rural"): 0.97,
     ("PM", "inbound", "centre"): 0.90, ("PM", "outbound", "centre"): 0.80,
     ("PM", "inbound", "urban"): 0.93, ("PM", "outbound", "urban"): 0.85,
     ("PM", "inbound", "rural"): 0.97, ("PM", "outbound", "rural"): 0.92}


def test_period_weights_sum_to_one():
    prof = pd.DataFrame([{"dow": d, "hour": h, "share": 1 / 168}
                         for d in range(7) for h in range(24)])
    w = fit.period_weights(prof, {"AM": ("07:00", "10:00"), "IP": ("10:00", "16:00"),
                                  "PM": ("16:00", "19:00")})
    assert abs(sum(w.values()) - 1) < 1e-12
    assert abs(w["AM"] - 15 / 168) < 1e-12 and abs(w["WE"] - 48 / 168) < 1e-12


def test_allday_speed_is_flow_weighted_harmonic():
    seg = pd.DataFrame({"road": ["x", "x"], "length_m": [100.0, 100.0], "w": [1.0, 3.0],
                        "ff_kmh": [20.0, 60.0]})
    ones = {p: np.ones(2) for p in fit.PERIODS}
    s = fit.allday_speed(seg, ones, {p: 0.2 for p in fit.PERIODS})
    assert abs(s["x"] - (400 / (100 / 20 + 300 / 60))) < 1e-9


def test_fit_recovers_known_levels_and_authority_multipliers():
    seg = synthetic()
    g_true = {"centre": 0.45, "urban": 0.62, "rural": 0.78}
    A_true = {"A1": 1.0, "A2": 0.92, "A3": 1.07, "A4": 0.97}
    targets = fit.allday_speed(seg, fit.segment_factors(seg, g_true, A_true, R, RHO), P)
    res = fit.fit_level(seg, targets, R, RHO, P, AREAS, lam=1e-6)
    assert res["success"] and res["max_abs_rel_error"] < 1e-4
    # g and A are identified only up to a common scale; compare products
    for a in AREAS:
        for k in A_true:
            assert abs(res["g"][a] * res["A"][k] / (g_true[a] * A_true[k]) - 1) < 1e-3


def test_ridge_pulls_authorities_together_when_targets_are_noisy():
    seg = synthetic(seed=1)
    g_true = {"centre": 0.45, "urban": 0.62, "rural": 0.78}
    A_true = {k: 1.0 for k in ["A1", "A2", "A3", "A4"]}
    t = fit.allday_speed(seg, fit.segment_factors(seg, g_true, A_true, R, RHO), P)
    noisy = t * np.random.default_rng(2).lognormal(0, 0.05, len(t))
    loose = fit.fit_level(seg, noisy, R, RHO, P, AREAS, lam=1e-6)
    tight = fit.fit_level(seg, noisy, R, RHO, P, AREAS, lam=100.0)
    spread = lambda A: np.std(np.log(list(A.values())))  # noqa: E731
    assert spread(tight["A"]) < spread(loose["A"])
