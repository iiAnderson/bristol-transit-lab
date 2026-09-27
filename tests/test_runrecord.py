import copy

import pytest

from lab import runrecord


@pytest.fixture(scope="module")
def rec(cfg):
    return runrecord.build(cfg, command="noop")


def test_record_pins_upstream(rec):
    up = rec["upstream"]
    assert up["package"] == "ons_to_subwaybuilder"
    assert up["db_path"].endswith("data/interim/BRS/census.duckdb")
    assert len(up["db_sha256"]) == 64
    assert up["commit"] and len(up["commit"]) == 40
    assert up["package_version"]
    assert [s["stage"] for s in up["stage_log"]][:2] == ["ingest", "extract"]


def test_record_validates(rec):
    runrecord.validate(rec)


@pytest.mark.parametrize("mutate", [
    lambda r: r.pop("upstream"),
    lambda r: r["upstream"].update(db_sha256="abc"),
    lambda r: r["upstream"].update(stage_log=[]),
    lambda r: r.update(status="done"),
    lambda r: r["git"].update(sha=None, dirty=False),
    lambda r: r.update(scenario={"id": "S001"}),
])
def test_invalid_records_are_rejected(rec, mutate):
    bad = copy.deepcopy(rec)
    mutate(bad)
    with pytest.raises(runrecord.RunRecordError):
        runrecord.validate(bad)


def test_finished_record_needs_finish_time(rec):
    bad = copy.deepcopy(rec)
    bad["status"] = "ok"
    with pytest.raises(runrecord.RunRecordError):
        runrecord.validate(bad)
