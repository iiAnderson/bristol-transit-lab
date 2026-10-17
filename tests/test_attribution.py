import duckdb
import pandas as pd

from lab import attribution


def _skim(path, rows):
    pd.DataFrame(rows, columns=["from_id", "to_id", "p50", "unreachable", "top_routes"]).to_parquet(path)


def test_gain_is_credited_only_to_a_route_the_old_itinerary_did_not_use(tmp_path):
    db = tmp_path / "lab.duckdb"
    with duckdb.connect(str(db)) as con:
        con.execute("CREATE TABLE skim_dest AS SELECT * FROM (VALUES ('Z1'), ('Z2'), ('Z3'), ('Z4')) t(LSOA21CD)")
        con.execute("CREATE TABLE nat_bres AS SELECT * FROM (VALUES ('Z1', 10), ('Z2', 20), ('Z3', 40), ('Z4', 80)) t(LSOA21CD, jobs)")
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(); new.mkdir()
    _skim(old / "AM.parquet", [("A", "Z1", 50.0, False, "bus1"), ("A", "Z2", 50.0, False, "NEW|bus1"),
                               ("A", "Z3", 46.0, False, "bus1"), ("A", "Z4", None, True, None)])
    _skim(new / "AM.parquet", [("A", "Z1", 44.0, False, "NEW"),          # 6 min, now on the scheme route: credited
                               ("A", "Z2", 40.0, False, "NEW"),          # 10 min but NEW was already used: not credited
                               ("A", "Z3", 45.0, False, "bus1"),         # 1 min: under the threshold
                               ("A", "Z4", 40.0, False, "NEW")])         # newly reachable: counts
    r = attribution.compute(db, new, old, ["NEW"], 2.0).set_index("OA21CD").loc["A"]
    assert r.jobs_faster == 10 + 20 + 80
    assert r.jobs_faster_via_scheme == 10 + 80
    assert r.jobs_new_within == 10 + 20 + 80     # Z1, Z2 and Z4 all end inside 45 by a real saving


def test_reach_keeps_zones_inside_the_limit_or_clearly_faster(tmp_path):
    db = tmp_path / "lab.duckdb"
    duckdb.connect(str(db)).close()
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(); new.mkdir()
    _skim(old / "AM.parquet", [("A", "Z1", 50.0, False, "x"), ("A", "Z2", 30.0, False, "x"),
                               ("A", "Z3", 90.0, False, "x"), ("A", "Z4", 90.0, False, "x"),
                               ("B", "Z1", 10.0, False, "x")])
    _skim(new / "AM.parquet", [("A", "Z1", 44.0, False, "x"),     # crosses 45: kept
                               ("A", "Z2", 30.0, False, "x"),     # inside both: kept
                               ("A", "Z3", 80.0, False, "x"),     # outside, 10 min faster: kept
                               ("A", "Z4", 89.5, False, "x"),     # outside, 0.5 min faster: dropped
                               ("B", "Z1", 10.0, False, "x")])    # another origin: not asked for
    r = attribution.reach(db, new, old, ["A"], 2.0)
    assert sorted(r.zone) == ["Z1", "Z2", "Z3"] and set(r.oa) == {"A"}
