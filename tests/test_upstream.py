import duckdb
import pytest

from lab import upstream


def test_upstream_db_is_the_package_build(cfg):
    assert cfg.upstream_db.parts[-3:] == ("interim", "BRS", "census.duckdb")


def test_connection_is_read_only(cfg):
    con = upstream.connect(cfg)
    try:
        with pytest.raises(duckdb.Error):
            con.execute("CREATE TABLE lab_should_not_write (x INT)")
    finally:
        con.close()


@pytest.mark.parametrize("table", ["od_msoa_adj", "base_flows", "ext_clamp", "edge_fold"])
def test_game_tables_are_refused(cfg, table):
    con = upstream.connect(cfg)
    try:
        with pytest.raises(upstream.UpstreamError):
            upstream.read_table(con, table)
    finally:
        con.close()


def test_raw_flows_are_readable(cfg):
    con = upstream.connect(cfg)
    try:
        n, total = upstream.read_table(con, "flows").aggregate("count(*), sum(n)").fetchone()
    finally:
        con.close()
    # upstream sources.md, Phase 2: 265,473 OA pairs, 378,905 retained commuters
    assert (n, total) == (265_473, 378_905)
