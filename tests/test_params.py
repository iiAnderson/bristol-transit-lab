import pytest
import yaml

from lab import params


def test_repo_params_are_all_tagged(cfg):
    for f in (cfg.root / "params").glob("*.yaml"):
        assert params.load(f), f"{f.name} has no parameters"


def _load(tmp_path, doc):
    p = tmp_path / "p.yaml"
    p.write_text(yaml.safe_dump(doc))
    return params.load(p)


@pytest.mark.parametrize("doc", [
    {"a": 1.0},                                               # bare number
    {"a": {"value": 1.0}},                                    # no tag
    {"a": {"value": 1.0, "tag": "GUESS"}},                    # unknown tag
    {"a": {"value": 1.0, "tag": "SOURCED"}},                  # no source
    {"a": {"value": 1.0, "tag": "MODELLED"}},                 # no reasoning
    {"a": {"value": 1.0, "tag": "CALIBRATED"}},               # no target
    {"a": {"value": 1.0, "unit": "GBP", "tag": "SOURCED", "source": "x"}},  # no price year
    {"a": {"value": 1.0, "tag": "PLACEHOLDER", "extra": 2}},  # stray key
])
def test_bad_params_fail(tmp_path, doc):
    with pytest.raises(params.ParamError):
        _load(tmp_path, doc)


def test_placeholders_are_listed(tmp_path):
    ps = _load(tmp_path, {"a": {"value": 1, "tag": "PLACEHOLDER"},
                          "b": {"value": 2, "tag": "MODELLED", "reasoning": "r"}})
    assert [p.path for p in params.placeholders(ps)] == ["a"]
