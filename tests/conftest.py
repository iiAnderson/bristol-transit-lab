import pytest

from lab.config import LabConfig


@pytest.fixture(scope="session")
def cfg():
    return LabConfig.load()


@pytest.fixture
def tmp_cfg(cfg, tmp_path):
    """The real config, but writing runs into a temp directory."""
    from dataclasses import replace
    return replace(cfg, runs_dir=tmp_path / "runs")
