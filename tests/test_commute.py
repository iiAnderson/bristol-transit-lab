"""
P1 — the analysis-grade commute matrix.

Runs the whole build into an in-memory DuckDB (the upstream DB attached read-only),
then checks the SPEC §10 P1 acceptance: reconciliation in parts, the centroid zone
rule, externals tagged not spread and on the average-weekday basis, the discount
sourced from the raw tables, and the factor check in place of the cap.
"""
import duckdb
import pytest

from lab import params
from lab.demand import bres_discount, commute


@pytest.fixture(scope="module")
def raw(cfg):
    p = cfg.root / "data" / "raw"
    if not (p / "census2021" / "ODWP14EW_MSOA.csv").is_file():
        pytest.skip("P1 raw inputs not downloaded; see sources.md P1")
    return p


@pytest.fixture(scope="module")
def built(cfg, raw):
    con = duckdb.connect()
    b = commute.run(cfg, con=con)
    yield b, con
    con.close()


def one(con, sql, *args):
    return con.execute(sql, list(args)).fetchone()[0]


def test_all_reconciliation_checks_pass(built):
    b, _ = built
    assert b.checks and all(ok for *_, ok in b.checks), b.checks


def test_upstream_equivalent_inputs_are_exact(built):
    b, _ = built
    s0 = b.summaries["reconciliation"][0]
    assert s0["fixed"] == pytest.approx(356_286, abs=1e-6)       # upstream Phase 4
    assert s0["after_step1"] == pytest.approx(432_002.52, abs=0.01)


def test_zone_rule_is_lsoa_centroid(built, cfg):
    _, con = built
    min_lon, min_lat, max_lon, max_lat = cfg.extent
    outside = one(con, f"""SELECT count(*) FROM int_lsoa WHERE lon NOT BETWEEN {min_lon} AND {max_lon}
                           OR lat NOT BETWEEN {min_lat} AND {max_lat}""")
    assert outside == 0
    # the two sliver MSOAs that used to reach the cap are no longer internal
    for msoa in ("E02004625", "E02006062"):                     # Cotswold 011, Sedgemoor 002
        assert one(con, "SELECT count(*) FROM int_lsoa WHERE MSOA21CD=?", msoa) == 0


def test_reclassification_accounts_for_every_commuter(built):
    _, con = built
    total = one(con, "SELECT sum(commuters) FROM p1_reclass_flows")
    assert total == one(con, "SELECT sum(n) FROM nat_oa_flows")
    moved = one(con, "SELECT count(*) FROM p1_reclass_zones")
    assert moved > 0


def test_externals_tagged_by_direction_and_not_spread(built):
    _, con = built
    kinds = {r[0] for r in con.execute("SELECT DISTINCT external FROM demand").fetchall()}
    assert kinds == {None, "external_in", "external_out"}
    missing = one(con, """SELECT count(*) FROM demand WHERE external IS NOT NULL
                          AND ext_dist_km IS NULL AND d_level <> 'COUNTRY'""")
    assert missing == 0


def test_external_out_varies_with_d(built):
    _, con = built
    t = dict(con.execute("""SELECT demand_version, sum(trips) FROM demand
                            WHERE external='external_out' GROUP BY 1""").fetchall())
    assert t["p1-low"] > t["p1-central"] > t["p1-high"]


def test_no_destination_factor_above_check(built, cfg):
    b, _ = built
    check = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}[
        "commute.destination_factor_check"]
    for v in commute.VARIANTS:
        assert b.summaries[v]["max_raw_factor"] <= check


def test_demand_versions_and_segments(built):
    _, con = built
    rows = con.execute("""SELECT demand_version, count(DISTINCT segment), min(trips)
                          FROM demand GROUP BY 1 ORDER BY 1""").fetchall()
    assert [r[0] for r in rows] == ["p1-central", "p1-high", "p1-low"]
    assert all(r[1] == 2 and r[2] >= 0 for r in rows)


def test_segment_split_conserves_trips(built):
    _, con = built
    for v in commute.VARIANTS:
        od = one(con, f"SELECT sum(trips) FROM hbw_{v}_od")
        dm = one(con, "SELECT sum(trips) FROM demand WHERE demand_version=?", f"p1-{v}")
        assert dm == pytest.approx(od, rel=1e-9)


def test_destination_split_conserves_internal_destinations(built):
    _, con = built
    m = one(con, "SELECT sum(n) FROM hbw_central_matrix WHERE d_msoa NOT LIKE 'OUT:%'")
    od = one(con, """SELECT sum(trips) FROM hbw_central_od
                     WHERE external IS DISTINCT FROM 'external_out'""")
    assert od == pytest.approx(m, rel=1e-9)


def test_ca_fallback_levels(built):
    b, _ = built
    assert set(b.summaries["ca_basis"]) <= {"pair", "origin MSOA x destination LAD", "origin MSOA"}
    assert "origin MSOA x destination LAD" in b.summaries["ca_basis"]


def test_discount_orders_the_matrix(built):
    b, _ = built
    tot = {v: b.summaries[v]["after_step2"] for v in commute.VARIANTS}
    assert tot["low"] > tot["central"] > tot["high"]


def test_params_match_the_derivation(cfg, raw):
    r = bres_discount.derive(raw)
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    assert ps["commute.bres_discount.low"] == pytest.approx(r.d_low, abs=5e-4)
    assert ps["commute.bres_discount.central"] == pytest.approx(r.d_central, abs=5e-4)
    assert ps["commute.bres_discount.high"] == pytest.approx(r.d_high, abs=5e-4)
    assert r.d_low < r.d_central < r.d_high


def test_game_tables_only_in_reconciliation():
    """Upstream game tables may be read only by the reconciliation functions."""
    import inspect
    recon = {"_upstream_equivalent_view", "reconcile_upstream", "recon_checks",
             "reconcile_checks", "_reclass_zones"}
    for name, fn in inspect.getmembers(commute, inspect.isfunction):
        if name in recon or fn.__module__ != commute.__name__:
            continue
        src = inspect.getsource(fn)
        for t in ("od_msoa_adj", "base_flows", "ext_clamp", "edge_fold", "oa_points",
                  "pops_s1", "dest_factor FROM up", "up.dest_factor"):
            assert t not in src, (name, t)
