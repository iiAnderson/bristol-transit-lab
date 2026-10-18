"""External rail gateways (src/lab/demand/external.py): tiny synthetic data, no raw files."""
import duckdb
import pytest

from lab.demand import external

PS = dict(distance_bands_km=[5, 15], rail_share_min_n=10, origin_station_k=2,
          distance_offset_km=1.0, fallback_decay_km=5.0, gateway_min_commuters=3,
          weekday_factor=0.01, max_unlocated_share=0.05)


@pytest.fixture
def wu(tmp_path):
    p = tmp_path / "wu.csv"
    p.write_text('"Area of residence","Area of workplace","All categories: Method of travel to work",'
                 '"Work mainly at or from home","Train"\n'
                 'A,M1,100,0,20\nM1,A,50,0,5\nB,M1,4,0,4\nC,M1,100,0,10\nM1,M1,999,0,500\n')
    return p


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("ATTACH ':memory:' AS up")
    c.execute("CREATE TABLE up.msoa_pwc (MSOA21CD VARCHAR, lon DOUBLE, lat DOUBLE)")
    c.execute("INSERT INTO up.msoa_pwc VALUES ('A', -2.50, 51.60), ('B', -1.00, 51.50), ('C', -1.00, 51.50)")
    c.execute("CREATE TABLE station (crs VARCHAR, lon DOUBLE, lat DOUBLE, name VARCHAR, tiploc VARCHAR)")
    c.execute("""INSERT INTO station VALUES ('G1', -2.6, 51.45, 'g1', 't'), ('G2', -2.4, 51.40, 'g2', 't'),
        ('X1', -2.50, 51.62, 'x1', 't'), ('X2', -1.00, 51.50, 'x2', 't')""")
    c.execute("CREATE TABLE gateway AS SELECT * FROM station WHERE crs LIKE 'G%'")
    c.execute("""CREATE TABLE odm AS SELECT o, d, CAST(j AS DOUBLE) AS j FROM (VALUES
        ('X1','G1',100.), ('G1','X1',100.), ('X1','G2',50.), ('G2','X1',50.),
        ('X2','G1',10.), ('G1','X2',10.), ('G1','G2',999.)) t(o, d, j)""")
    c.execute("CREATE TABLE int_lsoa (LSOA21CD VARCHAR, lon DOUBLE, lat DOUBLE, MSOA21CD VARCHAR)")
    c.execute("INSERT INTO int_lsoa VALUES ('L1',-2.60,51.45,'M1'), ('L2',-2.40,51.40,'M1')")
    c.execute("""CREATE TABLE demand (demand_version VARCHAR, external VARCHAR, o_zone VARCHAR,
        o_level VARCHAR, d_zone VARCHAR, d_level VARCHAR, trips DOUBLE)""")
    c.execute("""INSERT INTO demand VALUES
        ('v','external_in','A','MSOA','L1','LSOA',30), ('v','external_in','A','MSOA','L2','LSOA',10),
        ('v','external_in','B','MSOA','L2','LSOA',40), ('v','external_in','C','MSOA','L2','LSOA',40),
        ('v','external_out','L1','LSOA','A','MSOA',20), ('v','external_out','L2','LSOA','A','MSOA',20),
        ('v',NULL,'L1','LSOA','L2','LSOA',500)""")
    c.execute("CREATE TABLE hbw_ext_dist (msoa VARCHAR, km DOUBLE)")
    c.execute("INSERT INTO hbw_ext_dist VALUES ('A', 2.0), ('B', 60.0), ('C', 55.0)")
    c.execute("""CREATE TABLE landuse (landuse_version VARCHAR, zone_id VARCHAR, level VARCHAR,
        parent_id VARCHAR, residents BIGINT, jobs DOUBLE)""")
    c.execute("""INSERT INTO landuse VALUES ('L','o1','OA','L1',100,300), ('L','o2','OA','L2',100,100)""")
    yield c
    c.close()


def build(con, wu, ps=PS):
    flows = external._gateway_flows(con, PS["max_unlocated_share"])
    external._rail_share(con, wu, ps, "v")
    external._origin_weights(con, ps)
    out = external._demand(con, ps, "v", "x", "L")
    return flows, out


def test_a_pair_between_two_gateways_is_internal(con, wu):
    flows, _ = build(con, wu)
    assert flows["journeys"] == 2 * (100 + 50 + 10)
    assert con.execute("SELECT count(*) FROM gateway_flow WHERE ext_crs LIKE 'G%'").fetchone()[0] == 0


def test_volume_is_the_orr_total_times_the_weekday_factor(con, wu):
    _, out = build(con, wu)
    got = {(g["dir"], g["gateway"]): g["weekday_trips"] for g in out["gateways"]}
    assert got[("external_in", "G1")] == pytest.approx((100 + 10) * 0.01)
    assert got[("external_out", "G2")] == pytest.approx(50 * 0.01)
    placed = con.execute("SELECT dir, gateway, sum(trips) FROM demand_gateway GROUP BY ALL").fetchall()
    assert {(d, g): t for d, g, t in placed} == pytest.approx(got)


def test_rail_share_uses_train_only_and_the_band_when_the_cell_is_thin(con, wu):
    build(con, wu)
    s = {(m, d): (sh, b) for m, d, sh, b in con.execute(
        "SELECT msoa, dir, share, basis FROM ext_rail_share").fetchall()}
    assert s[("A", "external_in")] == (pytest.approx(0.2), "msoa")
    assert s[("A", "external_out")] == (pytest.approx(0.1), "msoa")
    assert s[("B", "external_in")] == (pytest.approx(0.1), "band")   # n=4: pooled with C, same band


def test_the_nearest_origin_station_takes_most_of_an_msoas_commuters(con, wu):
    build(con, wu)
    w = dict(con.execute("SELECT crs, w FROM ext_origin_weight WHERE msoa='A' AND dir='external_in'").fetchall())
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["X1"] > 0.9


def test_a_gateway_with_too_few_census_commuters_falls_back_and_says_so(con, wu):
    _, out = build(con, wu)
    basis = {(g["dir"], g["gateway"]): g["basis"] for g in out["gateways"]}
    assert basis[("external_in", "G1")] == "census"
    assert basis[("external_in", "G2")] == "fallback"
    zones = dict(con.execute("""SELECT zone, sum(trips) FROM demand_gateway
        WHERE dir='external_in' AND gateway='G2' GROUP BY ALL""").fetchall())
    assert zones["L2"] > zones["L1"] and sum(zones.values()) == pytest.approx(0.5)  # jobs gravity from G2


def test_inbound_follows_the_workplace_pattern_and_outbound_the_home_pattern(con, wu):
    build(con, wu, PS | {"gateway_min_commuters": 1})
    a = dict(con.execute("SELECT zone, sh FROM msoa_zone_share WHERE msoa='A' AND dir='external_in'").fetchall())
    assert a == pytest.approx({"L1": 0.75, "L2": 0.25})          # A sends 30:10 to L1:L2
    out = dict(con.execute("""SELECT zone, sum(trips) FROM demand_gateway
        WHERE dir='external_out' AND gateway='G1' GROUP BY ALL""").fetchall())
    assert out["L1"] == pytest.approx(out["L2"])                  # 20:20 from L1:L2


def test_a_station_without_a_point_fails_loudly(con, wu):
    con.execute("DELETE FROM station WHERE crs='X2'")
    con.execute("INSERT INTO odm VALUES ('X2','G1',5000), ('G1','X2',5000)")
    with pytest.raises(external.ExternalError, match="no NaPTAN point"):
        external._gateway_flows(con, PS["max_unlocated_share"])
