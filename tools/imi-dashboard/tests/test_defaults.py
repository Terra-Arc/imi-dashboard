"""Conversion without a config file: the defaults taken from the data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from imi_dashboard.config import Config
from imi_dashboard.pack import convert

from .conftest import SAMPLES, invoke


# Sectors that have an EmisCH4_* variable in posterior.nc *and* Prior/Posterior
# columns in viz_notebook_statistics.csv, in DEFAULT_SECTOR_TABLE order. The
# Permian CSV has no Coal or Rice columns; neither sample has Lakes or
# SoilAbsorb columns.
PERMIAN_SECTORS = [
    "total", "oil", "gas", "livestock", "landfills", "wastewater", "other_anth",
    "wetlands", "biomass", "reservoirs", "seeps", "termites",
]
ROMANIA_SECTORS = [
    "total", "oil", "gas", "coal", "livestock", "landfills", "wastewater", "rice",
    "other_anth", "wetlands", "biomass", "reservoirs", "seeps", "termites",
]


@pytest.mark.parametrize(
    "sample,sectors",
    [("single_period", PERMIAN_SECTORS), ("timeseries", ROMANIA_SECTORS)],
    ids=["single", "timeseries"],
)
def test_no_config_takes_sectors_and_cadence_from_data(tmp_path, sample, sectors):
    out = tmp_path / "out"
    convert(input_folder=SAMPLES / sample, output_folder=out, cfg=Config())
    cfg = json.loads((out / "data" / "config.json").read_text(encoding="utf-8"))
    manifest = json.loads((out / "data" / "manifest.json").read_text(encoding="utf-8"))

    assert [s["id"] for s in cfg["sectors"]] == sectors
    assert [f["name"] for f in manifest["fields"]][: len(sectors)] == sectors
    # Both samples have one-month periods (Permian 2021-05-01 to 06-01,
    # Romania 2023-01-01 to 02-01).
    assert cfg["text"]["cadence"] == "monthly"
    assert cfg["areaName"] is None
    assert cfg["text"]["chart_title_split"] == "Monthly methane emissions"
    # Every default sector has CSV totals for the first period.
    p0 = manifest["periods"][0]
    for sid in sectors:
        assert p0["sectorScalars"][sid]["posterior"] is not None, sid


def test_config_sectors_override_detection(tmp_path):
    """A config that lists sectors keeps exactly those; one that sets only the
    cadence still gets the detected sectors."""
    cfg = Config.model_validate({
        "sectors": [{"id": "total", "label": "Total", "short": "Total", "color": "red"}],
        "text": {"cadence": "weekly"},
    })
    out = tmp_path / "listed"
    convert(input_folder=SAMPLES / "timeseries", output_folder=out, cfg=cfg)
    c = json.loads((out / "data" / "config.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in c["sectors"]] == ["total"]
    assert c["text"]["cadence"] == "weekly"

    out2 = tmp_path / "cadence_only"
    convert(input_folder=SAMPLES / "timeseries", output_folder=out2,
            cfg=Config.model_validate({"text": {"cadence": "weekly"}}))
    c2 = json.loads((out2 / "data" / "config.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in c2["sectors"]] == ROMANIA_SECTORS
    assert c2["text"]["cadence"] == "weekly"


def test_cli_converts_without_config(tmp_path, monkeypatch):
    """No --config, and a relative output name lands in the working directory."""
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "site"
    r = invoke("convert", str(SAMPLES / "single_period"), "site")
    assert r.exit_code == 0, r.output
    assert "converted single run" in r.stdout
    assert "grid 9x7" in r.stdout          # roi is the default region
    assert (out / "data" / "manifest.json").exists()


def test_root_option_is_gone(tmp_path, monkeypatch):
    """A relative output folder is taken from the current directory; there
    is no --root to place it elsewhere."""
    monkeypatch.chdir(tmp_path)
    r = invoke("convert", str(SAMPLES / "single_period"), "site", "--root", "dashboards")
    assert r.exit_code == 2
    assert "no such option: --root" in r.stderr
    assert not (tmp_path / "site").exists() and not (tmp_path / "dashboards").exists()
