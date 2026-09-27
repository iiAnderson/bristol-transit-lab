"""
P1 — the analysis-grade commute matrix.

Runs the whole build into an in-memory DuckDB (the upstream DB attached read-only),
then checks the SPEC §10 P1 acceptance: reconciliation in parts, externals tagged not
spread, the discount sourced from the raw tables, and the cap decision recorded.
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


def test_all_reconciliation_checks_pass(built):
    b, _ = built
    assert b.checks and all(ok for *_, ok in b.checks), b.checks


def test_upstream_equivalent_inputs_are_exact(built):
    b, _ = built
    s0 = b.summaries["reconciliation"][0]
    assert s0["fixed"] == pytest.approx(356_286, abs=1e-6)       # upstream Phase 4
    assert s0["after_step1"] == pytest.approx(432_002.52, abs=0.01)


def test_externals_tagged_by_direction_and_not_spread(built):
    _, con = built
    got = dict(con.execute("""SELECT external, sum(n) FROM hbw_base
                              WHERE external IS NOT NULL GROUP BY 1""").fetchall())
    assert got == {"external_in": 66_186, "external_out": 37_350}
    # external_in origins stay at their real (external) MSOA
    n_inmap = con.execute("""SELECT count(*) FROM hbw_base WHERE external='external_in'
                             AND o_msoa IN (SELECT MSOA21CD FROM ext_oa)""").fetchone()[0]
    assert n_inmap < con.execute("SELECT count(*) FROM hbw_base WHERE external='external_in'"
                                 ).fetchone()[0]
    missing = con.execute("""SELECT count(*) FROM demand WHERE external IS NOT NULL
                             AND ext_dist_km IS NULL AND d_level <> 'COUNTRY'""").fetchone()[0]
    assert missing == 0


def test_demand_versions_and_segments(built):
    _, con = built
    rows = con.execute("""SELECT demand_version, count(DISTINCT segment), min(trips)
                          FROM demand GROUP BY 1 ORDER BY 1""").fetchall()
    assert [r[0] for r in rows] == ["p1-central", "p1-high", "p1-low"]
    assert all(r[1] == 2 and r[2] >= 0 for r in rows)


def test_segment_split_conserves_trips(built):
    _, con = built
    for v in commute.VARIANTS:
        od = con.execute(f"SELECT sum(trips) FROM hbw_p1_{v}_od").fetchone()[0]
        dm = con.execute("SELECT sum(trips) FROM demand WHERE demand_version=?",
                         [f"p1-{v}"]).fetchone()[0]
        assert dm == pytest.approx(od, rel=1e-9)


def test_destination_split_conserves_trips(built):
    _, con = built
    m = con.execute("SELECT sum(n) FROM hbw_central_matrix").fetchone()[0]
    od = con.execute("SELECT sum(trips) FROM hbw_p1_central_od").fetchone()[0]
    assert od == pytest.approx(m, rel=1e-9)


def test_discount_orders_the_matrix(built):
    b, _ = built
    tot = {v: b.summaries[v]["after_step2"] for v in commute.VARIANTS}
    assert tot["low"] > tot["central"] > tot["high"]


def test_cap_decision_recorded(built):
    b, _ = built
    assert set(b.cap) == set(commute.VARIANTS)


def test_params_match_the_derivation(cfg, raw):
    r = bres_discount.derive(raw)
    ps = {p.path: p.value for p in params.load(cfg.root / "params" / "base.yaml")}
    assert ps["commute.bres_discount.low"] == pytest.approx(r.d_low, abs=5e-4)
    assert ps["commute.bres_discount.central"] == pytest.approx(r.d_central, abs=5e-4)
    assert ps["commute.bres_discount.high"] == pytest.approx(r.d_high, abs=5e-4)
    assert r.d_low < r.d_central < r.d_high


def test_no_game_table_is_read():
    """The build may read upstream game tables only inside the reconciliation."""
    import inspect
    src = inspect.getsource(commute)
    body = src.split("def _upstream_equivalent_view")[0] + src.split("def split_destinations")[1]
    for t in ("od_msoa_adj", "base_flows", "ext_clamp", "edge_fold", "oa_points"):
        assert f"up.{t}" not in body.split("def recon_checks")[0], t
