"""End-to-end tests for the conversion pipeline against the shipped samples."""

from __future__ import annotations

import json
import logging
import shutil
import struct
import warnings
from pathlib import Path

import numpy as np
import pytest

from imi_dashboard.config import Config, SectorConfig, load_config
from imi_dashboard.pack import convert

from .conftest import EXAMPLE_CONFIG, SINGLE, TIMESERIES, write_fake_web_app


@pytest.fixture(scope="module")
def single_output(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("single")
    convert(
        input_folder=SINGLE,
        output_folder=out,
        cfg=load_config(EXAMPLE_CONFIG),
        region="domain",
    )
    return out


@pytest.fixture(scope="module")
def timeseries_output(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("timeseries")
    convert(
        input_folder=TIMESERIES,
        output_folder=out,
        cfg=load_config(EXAMPLE_CONFIG),
        region="domain",
    )
    return out


def _load_manifest(out: Path) -> dict:
    return json.loads((out / "data" / "manifest.json").read_text(encoding="utf-8"))


def _load_config_json(out: Path) -> dict:
    return json.loads((out / "data" / "config.json").read_text(encoding="utf-8"))


def _period_by_label(m: dict, label: str) -> dict:
    """Look a period up by its ``period_<N>`` label rather than by position.

    A period's index in the manifest is its position among the folders
    present, so it shifts whenever a folder is added or removed; its label
    (``period_<N>``, from the folder name) does not.
    """
    for p in m["periods"]:
        if p["label"] == label:
            return p
    raise AssertionError(f"period {label!r} not found in manifest")


def test_single_period_output_shape(single_output: Path):
    m = _load_manifest(single_output)
    assert m["mode"] == "single"
    assert len(m["periods"]) == 1
    # Permian quick-start grid: 23 latitudes x 21 longitudes
    assert m["grid"]["rows"] == 23
    assert m["grid"]["cols"] == 21
    # bbox is (west, south, east, north)
    bbox = m["grid"]["bbox"]
    assert bbox[0] < bbox[2] and bbox[1] < bbox[3]
    assert (single_output / "data" / "periods" / "0000.bin").exists()


def test_timeseries_output_shape(timeseries_output: Path):
    m = _load_manifest(timeseries_output)
    assert m["mode"] == "timeseries"
    # Monthly sample with period_3 missing: idx is contiguous, number is not.
    assert [p["idx"] for p in m["periods"]] == [0, 1, 2]
    assert [p["number"] for p in m["periods"]] == [1, 2, 4]
    assert [p["start"] for p in m["periods"]] == ["2023-01-01", "2023-02-01", "2023-04-01"]
    for p in m["periods"]:
        assert (timeseries_output / "data" / p["file"]).exists()


def test_field_layout_matches_sectors_then_diagnostics(timeseries_output: Path):
    m = _load_manifest(timeseries_output)
    sector_ids = [s["id"] for s in _load_config_json(timeseries_output)["sectors"]]
    field_names = [f["name"] for f in m["fields"]]
    # Sectors come first, in config order.
    assert field_names[: len(sector_ids)] == sector_ids
    # Diagnostics follow.
    diag_names = field_names[len(sector_ids):]
    assert diag_names == ["kernel", "scale", "delta_emissions", "delta_xch4", "obs_count", "tropomi_xch4"]


def test_period_binary_size_matches_manifest(timeseries_output: Path):
    m = _load_manifest(timeseries_output)
    cells = m["grid"]["rows"] * m["grid"]["cols"]
    expected_floats = sum(f["length"] for f in m["fields"])
    assert expected_floats == cells * len(m["fields"])
    for p in m["periods"]:
        size = (timeseries_output / "data" / p["file"]).stat().st_size
        assert size == expected_floats * 4  # float32


def test_period_binary_round_trip(timeseries_output: Path):
    """The total sector field for period_1 should decode to the same values
    that xarray reads directly from the source posterior.nc.
    """
    import xarray as xr

    m = _load_manifest(timeseries_output)
    p = _period_by_label(m, "period_1")
    cells = m["grid"]["rows"] * m["grid"]["cols"]
    blob = (timeseries_output / "data" / p["file"]).read_bytes()
    floats = np.frombuffer(blob, dtype=np.float32)
    assert floats.size == cells * len(m["fields"])

    total_field = m["fields"][0]
    assert total_field["name"] == "total"
    arr_from_blob = floats[total_field["offset"]: total_field["offset"] + total_field["length"]]

    nc = TIMESERIES / "period_1_plots" / "netCDF" / "posterior.nc"
    # IMI emissions ship as kg m⁻² s⁻¹; the converter rescales to the
    # display unit (kg km⁻² h⁻¹), so compare against the rescaled reference.
    from imi_dashboard.pack import EMIS_KG_M2_S_TO_KG_KM2_H
    with xr.open_dataset(nc) as ds:
        # The total's map is the total without the soil sink.
        ref = (ds["EmisCH4_Total_ExclSoilAbs"].values * EMIS_KG_M2_S_TO_KG_KM2_H).astype(np.float32).reshape(-1)
    assert arr_from_blob.shape == ref.shape
    np.testing.assert_allclose(arr_from_blob, ref, rtol=1e-5, atol=0)


def test_diagnostic_scalars_match_csv(timeseries_output: Path):
    m = _load_manifest(timeseries_output)
    p = _period_by_label(m, "period_1")
    assert p["diagScalars"]["dofs"] == pytest.approx(4.533214)
    assert p["diagScalars"]["n_obs"] == 1399
    assert p["diagScalars"]["bias_posterior"] == pytest.approx(1.6)
    # avg_kernel is computed from avkern.nc, just check it's a sane number in [0, 1]-ish.
    ak = p["diagScalars"]["avg_kernel"]
    assert ak is not None and 0 < ak < 1


def test_sector_scalars_from_csv(single_output: Path):
    # The Permian (IMI 2.3.1) statistics CSV carries the ensemble columns.
    m = _load_manifest(single_output)
    s = m["periods"][0]["sectorScalars"]
    # Total comes from the TotalExclSoilAbsPrior / TotalExclSoilAbsPosterior
    # columns (here equal to PriorEmissions / PosteriorEmissions).
    assert s["total"]["prior"] == pytest.approx(0.4087327477516389)
    assert s["total"]["posterior"] == pytest.approx(0.602334359307776)
    assert s["oil"]["prior"] == pytest.approx(0.1159987734234263)
    assert s["oil"]["posterior"] == pytest.approx(0.1826111872302235)
    assert s["oil"]["ens_min"] == pytest.approx(0.1450357906946043)
    assert s["oil"]["ens_max"] == pytest.approx(0.2476371554964339)
    # Total uses the EnsemblePosterior_{Min,Max} columns (not Total_ensMin/Max,
    # which don't exist in the CSV).
    assert s["total"]["ens_min"] == pytest.approx(0.49386518631926585)
    assert s["total"]["ens_max"] == pytest.approx(0.7823478220013)


def test_sector_scalars_without_ensemble_columns(timeseries_output: Path):
    # The timeseries sample comes from an older IMI whose CSV has no ensemble
    # columns: the sector values are still read, the ensemble range is None.
    m = _load_manifest(timeseries_output)
    s = _period_by_label(m, "period_1")["sectorScalars"]
    # The total without the soil sink, summed from the maps (the CSV's
    # PriorEmissions 1.299558 and PosteriorEmissions 0.931382 include it).
    assert s["total"]["prior"] == pytest.approx(1.316822, abs=1e-6)
    assert s["total"]["posterior"] == pytest.approx(0.948646, abs=1e-6)
    assert s["oil"]["prior"] == pytest.approx(0.0465083374968862)
    assert s["oil"]["posterior"] == pytest.approx(0.0449007859416571)
    assert s["total"]["ens_min"] is None and s["total"]["ens_max"] is None
    assert s["oil"]["ens_min"] is None and s["oil"]["ens_max"] is None


def test_config_json_includes_resolved_text(timeseries_output: Path):
    c = _load_config_json(timeseries_output)
    assert c["headerBrand"] == "terra"
    assert c["areaName"] == "Romania"
    assert c["text"]["cadence"] == "monthly"
    assert c["text"]["heading_context_timeseries"] == "Monthly timeseries"
    assert c["text"]["speed_unit"] == "months/sec"
    assert c["text"]["chart_title_split"] == "Monthly emissions over Romania"
    sectors = [s["id"] for s in c["sectors"]]
    assert sectors[0] == "total"
    # The example config sets no tiles, so the base map is OpenStreetMap's.
    assert c["map"]["tiles"] == "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    assert c["map"]["tilesAttribution"] == "© OpenStreetMap contributors"
    assert c["map"]["attributionCompact"] is True


def test_config_json_carries_the_configured_base_map(tmp_path: Path):
    cfg = tmp_path / "tiles.yaml"
    cfg.write_text(
        "map:\n"
        "  tiles: https://tiles.example.org/{z}/{x}/{y}.png\n"
        "  tiles_attribution: © Example Tiles\n"
        "  tile_size: 512\n"
        "  attribution_compact: false\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    convert(input_folder=SINGLE, output_folder=out, cfg=load_config(cfg))
    m = _load_config_json(out)["map"]
    assert m["tiles"] == "https://tiles.example.org/{z}/{x}/{y}.png"
    assert (m["tilesAttribution"], m["tileSize"], m["attributionCompact"]) == ("© Example Tiles", 512, False)


def test_globals_bin_size_matches_grid(timeseries_output: Path):
    m = _load_manifest(timeseries_output)
    expected = m["grid"]["rows"] * m["grid"]["cols"]
    assert (timeseries_output / "data" / "globals.bin").stat().st_size == expected


def test_found_bundle_is_copied_into_output(single_output: Path, fake_web_bundle: Path):
    """With no --app-bundle, the app bundle found by the lookup (here the fake
    web/dist/ from conftest) is copied, its marker included. The other cases
    are test_bundle_lookup_falls_back_to_packaged_copy and test_output_folder."""
    from imi_dashboard import pack

    assert (single_output / "index.html").read_text(encoding="utf-8") == "<!doctype html>"
    assert (single_output / "assets" / "index.js").exists()
    assert json.loads((single_output / pack.APP_MARKER).read_text())["files"] == ["assets/index.js", "index.html"]


def test_readme_written_with_display_name(single_output: Path):
    readme = single_output / "README.md"
    assert readme.exists()
    text = readme.read_text(encoding="utf-8")
    cfg = _load_config_json(single_output)
    # The display name is substituted into the heading; no placeholder remains.
    assert cfg["displayName"] in text
    assert "{display_name}" not in text
    # Points the end user at the two supported ways to view it.
    assert "python -m http.server" in text
    assert "static host" in text.lower()


def test_period_entries_carry_folder_number(timeseries_output: Path):
    m = _load_manifest(timeseries_output)
    for p in m["periods"]:
        assert p["number"] == int(p["label"].split("_")[1])


@pytest.mark.parametrize("output", ["single_output", "timeseries_output"])
def test_satellite_field_in_layout_and_decodable(output: str, request):
    # obs_ds_regridded.nc names the observed column "satellite" in the
    # single-period sample (IMI 2.3.1) and "tropomi" in the timeseries sample
    # (older IMI); both must land in the tropomi_xch4 field.
    out: Path = request.getfixturevalue(output)
    m = _load_manifest(out)
    by_name = {f["name"]: f for f in m["fields"]}
    assert "tropomi_xch4" in by_name
    for key in ("tropomi_xch4", "delta_xch4"):
        f = by_name[key]
        entry = m["periods"][0]
        blob = (out / "data" / entry["file"]).read_bytes()
        arr = np.frombuffer(blob, dtype=np.float32)[f["offset"]: f["offset"] + f["length"]]
        vals = arr[np.isfinite(arr)]
        assert vals.size > 0, key
        if key == "tropomi_xch4":
            # Column methane in ppb — sanity-check the plausible TROPOMI range.
            assert 1500 < float(vals.min()) and float(vals.max()) < 2200


def test_diag_bounds_fixed_across_periods(timeseries_output: Path):
    m = _load_manifest(timeseries_output)
    db = m["diagBounds"]
    for key in ("kernel", "obs_count", "tropomi_xch4", "delta_emissions", "delta_xch4", "scale"):
        assert key in db, key
        assert db[key]["min"] <= db[key]["max"]


def test_bundle_lookup_falls_back_to_packaged_copy(tmp_path: Path, monkeypatch):
    """Outside a clone (no web/dist), the web app shipped inside the package
    is copied; with neither, a new dashboard is refused (test_output_folder
    covers the update that keeps its own web app)."""
    from imi_dashboard import pack

    packaged = write_fake_web_app(tmp_path / "pkg_bundle", {"index.html": "<!doctype html>", "assets/app.js": ""})
    monkeypatch.setattr(pack, "REPO_BUNDLE_DIR", tmp_path / "no_clone" / "web" / "dist")
    monkeypatch.setattr(pack, "PACKAGE_BUNDLE_DIR", packaged)

    out = tmp_path / "out"
    out.mkdir()
    pack._copy_web_app(out, pack._find_web_app(None, out))
    assert (out / "index.html").exists() and (out / "assets" / "app.js").exists()

    monkeypatch.setattr(pack, "PACKAGE_BUNDLE_DIR", tmp_path / "no_pkg_bundle")
    bare = tmp_path / "bare"
    bare.mkdir()
    # Not a clone: the remedy is a release wheel, and no web/ folder is named.
    with pytest.raises(ValueError, match=r"has no app bundle .*: install imi-dashboard from a release wheel") as err:
        pack._find_web_app(None, bare)
    assert "npm" not in str(err.value)


def _fake_clone_web(tmp_path: Path, monkeypatch) -> Path:
    """A web/ folder with a source file and a dist/ build, wired in as the
    clone's REPO_BUNDLE_DIR. Returns the web/ folder."""
    from imi_dashboard import pack

    web = tmp_path / "web"
    (web / "src").mkdir(parents=True)
    (web / "src" / "App.tsx").write_text("", encoding="utf-8")
    (web / "dist").mkdir()
    write_fake_web_app(web / "dist")
    monkeypatch.setattr(pack, "REPO_BUNDLE_DIR", web / "dist")
    return web


def _set_mtime(path: Path, t: float) -> None:
    import os

    os.utime(path, (t, t))


def test_stale_repo_bundle_warns(tmp_path: Path, monkeypatch):
    """A web source file modified after web/dist/index.html was written gives
    a StaleBundleWarning naming the file; the web app is still used."""
    from imi_dashboard import pack

    web = _fake_clone_web(tmp_path, monkeypatch)
    _set_mtime(web / "dist" / "index.html", 1_000_000)
    _set_mtime(web / "src" / "App.tsx", 2_000_000)

    out = tmp_path / "out"
    out.mkdir()
    with pytest.warns(pack.StaleBundleWarning, match="web/src/App.tsx"):
        app = pack._find_web_app(None, out)
    assert app is not None and app.folder == web / "dist"

    # An explicit --app-bundle pointing at the clone's web/dist/ is checked too.
    with pytest.warns(pack.StaleBundleWarning):
        pack._find_web_app(web / "dist", out)


def test_fresh_or_foreign_bundle_does_not_warn(tmp_path: Path, monkeypatch):
    """No warning when web/dist/ is newer than every source file, nor for a
    bundle that is not the clone's web/dist/ (packaged copy, other --app-bundle),
    whatever the clone's source times are."""
    import warnings as _warnings

    from imi_dashboard import pack

    web = _fake_clone_web(tmp_path, monkeypatch)
    _set_mtime(web / "src" / "App.tsx", 1_000_000)
    _set_mtime(web / "dist" / "index.html", 2_000_000)

    other = write_fake_web_app(tmp_path / "other_bundle")
    _set_mtime(other / "index.html", 0)

    out = tmp_path / "out"
    out.mkdir()
    with _warnings.catch_warnings():
        _warnings.simplefilter("error", pack.StaleBundleWarning)
        pack._find_web_app(None, out)
        _set_mtime(web / "src" / "App.tsx", 3_000_000)  # clone now stale
        pack._find_web_app(other, out)


def test_roi_without_state_vector_warns_and_keeps_buffers(tmp_path: Path):
    """region roi needs state_vector.nc to tell buffer elements apart; without
    it the converter says so instead of silently keeping them."""
    import shutil

    from imi_dashboard import pack

    src = tmp_path / "input"
    shutil.copytree(SINGLE, src)
    (src / "netCDF" / "state_vector.nc").unlink()
    with pytest.warns(pack.RegionFallbackWarning, match="buffer elements cannot be told apart"):
        convert(input_folder=src, output_folder=tmp_path / "out", cfg=load_config(None), region="roi")
    assert _load_manifest(tmp_path / "out")["region"] == "roi"


def test_empty_roi_mask_warns_and_keeps_full_grid(tmp_path: Path, monkeypatch):
    """If no cell is active there is nothing to crop to: the full grid is
    kept, with a warning."""
    from imi_dashboard import pack

    monkeypatch.setattr(pack, "_global_mask", lambda payloads, sv, grid: np.zeros_like(sv))
    out = tmp_path / "out"
    with pytest.warns(pack.RegionFallbackWarning, match="no cell is in the region of interest"):
        convert(
            input_folder=SINGLE,
            output_folder=out,
            cfg=load_config(None),
            region="roi",
        )
    domain = tmp_path / "domain"
    convert(
        input_folder=SINGLE,
        output_folder=domain,
        cfg=load_config(None),
        region="domain",
    )
    grid, full = _load_manifest(out)["grid"], _load_manifest(domain)["grid"]
    assert (grid["rows"], grid["cols"]) == (full["rows"], full["cols"])


def _decode_period(out: Path, m: dict, label: str) -> dict[str, np.ndarray]:
    p = _period_by_label(m, label)
    floats = np.fromfile(out / "data" / p["file"], dtype=np.float32)
    return {f["name"]: floats[f["offset"]: f["offset"] + f["length"]] for f in m["fields"]}


def test_delta_xch4_is_nan_where_no_observations(timeseries_output: Path):
    """obs_ds_regridded.nc holds 0 in every variable of a cell without
    super-observations; ΔXCH4 there is "no data", not a change of 0."""
    m = _load_manifest(timeseries_output)
    fields = _decode_period(timeseries_output, m, "period_1")
    delta, count = fields["delta_xch4"], fields["obs_count"]
    assert int((count == 0).sum()) == 1452
    assert int((count > 0).sum()) == 1408
    np.testing.assert_array_equal(np.isfinite(delta), count > 0)


def test_delta_emissions_without_total_sector(tmp_path: Path, single_output: Path):
    """The posterior − prior emissions map comes from the total excluding the
    soil sink, which is read even when the config does not list the total
    sector."""
    out = tmp_path / "oil_only"
    cfg = Config(sectors=[SectorConfig(id="oil", imi_var="EmisCH4_Oil", label="Oil", short="Oil", color="#000000")])
    convert(input_folder=SINGLE, output_folder=out, cfg=cfg, region="domain")
    m = _load_manifest(out)
    assert [f["name"] for f in m["fields"]][0] == "oil"
    assert "total" not in {f["name"] for f in m["fields"]}
    assert "totalDefinition" not in m
    full = _load_manifest(single_output)
    assert m["diagBounds"]["delta_emissions"] == full["diagBounds"]["delta_emissions"]
    delta = _decode_period(out, m, "single")["delta_emissions"]
    np.testing.assert_array_equal(delta, _decode_period(single_output, full, "single")["delta_emissions"])
    assert int(np.isfinite(delta).sum()) > 0


def test_refuses_output_folder_holding_other_files(tmp_path: Path):
    """convert empties its output folder before a full conversion, so a
    non-empty folder that is not a dashboard is refused, with --rebuild too."""
    out = tmp_path / "notes"
    out.mkdir()
    (out / "thesis.txt").write_text("keep me", encoding="utf-8")
    for force in (False, True):
        with pytest.raises(ValueError, match="holds no dashboard"):
            convert(input_folder=SINGLE, output_folder=out, cfg=Config(), force=force)
    assert (out / "thesis.txt").read_text(encoding="utf-8") == "keep me"
    assert sorted(p.name for p in out.iterdir()) == ["thesis.txt"]


def test_refuses_output_folder_that_is_or_contains_the_input(tmp_path: Path):
    run = tmp_path / "run"
    shutil.copytree(SINGLE, run)
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    for out in (run, tmp_path):
        with pytest.raises(ValueError, match="is the input folder or contains it"):
            convert(input_folder=run, output_folder=out, cfg=Config(), force=True)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == before


def _sector(sid: str, imi_var: str) -> SectorConfig:
    return SectorConfig(id=sid, imi_var=imi_var, label=sid, short=sid, color="#000000")


def test_mistyped_imi_var_warns_and_mask_falls_back_to_the_grid(tmp_path: Path):
    """A sector whose imi_var is not in posterior.nc is named in a warning.
    With no state_vector.nc either, the fallback mask (cells with a positive
    emission in some sector) is built on the grid's shape and is empty,
    instead of failing for want of a sector field to copy the shape from."""
    from imi_dashboard import pack

    src = tmp_path / "input"
    shutil.copytree(SINGLE, src)
    (src / "netCDF" / "state_vector.nc").unlink()
    out = tmp_path / "out"
    cfg = Config(sectors=[_sector("coal", "EmisCH4_Cola")])
    with pytest.warns(pack.MissingSectorVariableWarning, match=r"so their maps are empty: coal \(EmisCH4_Cola\);"):
        convert(input_folder=src, output_folder=out, cfg=cfg, region="domain")
    m = _load_manifest(out)
    assert m["fields"][0]["name"] == "coal"
    mask = np.fromfile(out / "data" / "globals.bin", dtype=np.uint8)
    assert mask.size == 23 * 21 and int(mask.sum()) == 0
    assert m["globals"]["dataBbox"] is None
    assert not np.isfinite(_decode_period(out, m, "single")["coal"]).any()


def _logo_config(tmp_path: Path, logo_line: str) -> Path:
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()
    cfg = cfg_dir / "dashboard.yaml"
    cfg.write_text(f"header_brand: custom\n{logo_line}\n", encoding="utf-8")
    return cfg


def test_logo_is_read_beside_the_config_and_kept_apart_from_the_bundle(tmp_path: Path, monkeypatch):
    """A relative logo is resolved against the config file's folder,
    not the current directory, and is copied as assets/custom-logo.<ext>, so
    a logo named like the bundle's own Terra Arc mark cannot replace it."""
    cfg_path = _logo_config(tmp_path, "logo: logo_small.png")
    (cfg_path.parent / "logo_small.png").write_bytes(b"custom mark")
    bundle = write_fake_web_app(tmp_path / "bundle", {"index.html": "<!doctype html>", "assets/logo_small.png": "terra mark"})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    out = tmp_path / "out"
    convert(input_folder=SINGLE, output_folder=out, cfg=load_config(cfg_path), bundle_dir=bundle, region="domain")
    assert (out / "assets" / "custom-logo.png").read_bytes() == b"custom mark"
    assert (out / "assets" / "logo_small.png").read_bytes() == b"terra mark"
    assert _load_config_json(out)["logoUrl"] == "./assets/custom-logo.png"


def test_missing_logo_is_an_error(tmp_path: Path):
    cfg_path = _logo_config(tmp_path, "logo: nowhere.png")
    out = tmp_path / "out"
    with pytest.raises(ValueError, match=r"logo .*nowhere.png does not exist"):
        convert(input_folder=SINGLE, output_folder=out, cfg=load_config(cfg_path))
    assert not out.exists()


def test_period_without_statistics_csv_warns(tmp_path: Path):
    """A period without viz_notebook_statistics.csv still converts, with a
    warning naming it; its dates, totals and scalar diagnostics are null."""
    from imi_dashboard import pack

    src = tmp_path / "input"
    shutil.copytree(TIMESERIES, src)
    (src / "period_2_plots" / "viz_notebook_statistics.csv").unlink()
    out = tmp_path / "out"
    with pytest.warns(pack.MissingStatisticsWarning, match=r"^1 period\(s\) have no viz_notebook_statistics.csv.*: period_2$"):
        convert(input_folder=src, output_folder=out, cfg=load_config(EXAMPLE_CONFIG))
    m = _load_manifest(out)
    p2 = _period_by_label(m, "period_2")
    assert (p2["start"], p2["end"]) == (None, None)
    assert p2["sectorScalars"]["total"]["posterior"] is None
    assert p2["diagScalars"]["dofs"] is None
    assert _period_by_label(m, "period_1")["start"] is not None


def test_manifest_records_provenance(timeseries_output: Path):
    import re

    import imi_dashboard

    prov = _load_manifest(timeseries_output)["provenance"]
    assert prov["converter"] == {"name": "imi-dashboard", "version": imi_dashboard.__version__}
    # The input folder's name, never its path.
    assert prov["input"] == "timeseries"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", prov["convertedAt"])
    assert set(prov) == {"converter", "convertedAt", "input"}


def test_manifest_and_config_are_strict_json(timeseries_output: Path):
    """No NaN or Infinity, which browsers' JSON.parse rejects."""
    def refuse(constant: str):
        raise AssertionError(f"non-standard JSON constant {constant}")

    for name in ("manifest.json", "config.json"):
        json.loads((timeseries_output / "data" / name).read_text(encoding="utf-8"), parse_constant=refuse)


def test_non_finite_csv_value_is_null():
    from imi_dashboard.pack import _scalar

    assert _scalar({"a": float("nan"), "b": float("inf"), "c": 2.5}, "a") is None
    assert _scalar({"b": float("inf")}, "b") is None
    assert _scalar({"c": 2.5}, "c") == 2.5


def test_package_logger_has_a_null_handler():
    """A library leaves output to the application: the package logger
    carries a NullHandler, so WARNING records are not printed by Python's
    last-resort handler when the caller configured no logging."""
    import logging

    import imi_dashboard  # noqa: F401

    assert any(isinstance(h, logging.NullHandler) for h in logging.getLogger("imi_dashboard").handlers)


# --- The total without the soil sink ---------------------------------------
# The soil sink (EmisCH4_SoilAbsorb, methane oxidised by soils, stored as
# negative emissions) is always left out of the total: its numbers, its map
# and the posterior − prior map (totals.py; test_totals.py checks the
# numbers against every layout of the CSV's headline columns).

def _nc(folder: Path, name: str, var: str) -> np.ndarray:
    import xarray as xr

    from imi_dashboard.pack import EMIS_KG_M2_S_TO_KG_KM2_H

    with xr.open_dataset(folder / "netCDF" / name) as ds:
        return (ds[var].values * EMIS_KG_M2_S_TO_KG_KM2_H).astype(np.float32).reshape(-1)


def _roi_sum_tg_per_yr(field: np.ndarray, folder: Path) -> float:
    """Sum of a kg km⁻² h⁻¹ field over the region-of-interest cells, times
    the cell areas, in Tg/yr (= Mt/yr) with 365-day years, the IMI's unit."""
    from imi_dashboard.readers import open_emissions_grid, read_area, read_buffer_regions

    nc = folder / "netCDF"
    grid = open_emissions_grid(nc / "posterior.nc")
    area_km2 = read_area(nc / "posterior.nc", grid).reshape(-1) / 1e6
    regions = read_buffer_regions(nc / "state_vector.nc", grid)
    assert regions is not None
    roi = regions.roi_mask.reshape(-1)
    return float(np.sum(field[roi].astype(np.float64) * area_km2[roi])) * 8760 / 1e9


def test_permian_total_map_excludes_soil_sink_like_the_csv(single_output: Path):
    """IMI 2.3.1: PosteriorEmissions = TotalExclSoilAbsPosterior, so the
    total map is EmisCH4_Total_ExclSoilAbs, and its sum over the region of
    interest reproduces the CSV total (0.602334 Mt/yr; including the sink,
    the sum would be 0.578967 Mt/yr, 3.9 % lower)."""
    m = _load_manifest(single_output)
    assert m["source"]["sectorVars"]["total"] == "EmisCH4_Total_ExclSoilAbs"
    assert m["source"]["deltaEmissionsVar"] == "EmisCH4_Total_ExclSoilAbs"
    assert m["totalDefinition"] == {"numbersIncludeSoilSink": False, "mapIncludesSoilSink": False}
    fields = _decode_period(single_output, m, "single")
    np.testing.assert_allclose(fields["total"], _nc(SINGLE, "posterior.nc", "EmisCH4_Total_ExclSoilAbs"), rtol=1e-6)
    assert _roi_sum_tg_per_yr(fields["total"], SINGLE) == pytest.approx(0.602334359307776, rel=1e-6)
    p = m["periods"][0]["sectorScalars"]["total"]
    assert (p["prior"], p["posterior"]) == (0.4087327477516389, 0.602334359307776)

    delta = fields["delta_emissions"]
    ref = _nc(SINGLE, "posterior.nc", "EmisCH4_Total_ExclSoilAbs") - _nc(SINGLE, "prior.nc", "EmisCH4_Total_ExclSoilAbs")
    np.testing.assert_allclose(delta, ref, rtol=1e-5, atol=1e-6)
    assert _roi_sum_tg_per_yr(delta, SINGLE) == pytest.approx(0.602334359307776 - 0.4087327477516389, rel=1e-5)


def test_romania_total_excludes_soil_sink_summed_from_the_maps(timeseries_output: Path):
    """Older IMI: the CSV has no TotalExclSoilAbs columns, and its totals
    include the sink (period_1: PriorEmissions 1.299558, PosteriorEmissions
    0.931382 Mt/yr). The total's numbers are the sums of
    EmisCH4_Total_ExclSoilAbs over the region of interest instead (1.316822
    and 0.948646 Mt/yr, the CSV's values minus the sink, −0.017264), and its
    map shows that variable. The posterior − prior map is the difference of
    the totals excluding the sink, which matches the CSV's difference,
    −0.368175 Mt/yr; the difference of EmisCH4_Total would not (its prior.nc
    leaves the sink out), and comes to −0.385440 Mt/yr."""
    folder = TIMESERIES / "period_1_plots"
    m = _load_manifest(timeseries_output)
    assert m["source"]["sectorVars"]["total"] == "EmisCH4_Total_ExclSoilAbs"
    assert m["totalDefinition"] == {"numbersIncludeSoilSink": False, "mapIncludesSoilSink": False}
    fields = _decode_period(timeseries_output, m, "period_1")
    np.testing.assert_allclose(fields["total"], _nc(folder, "posterior.nc", "EmisCH4_Total_ExclSoilAbs"), rtol=1e-6)
    p = m["periods"][0]["sectorScalars"]["total"]
    assert p["prior"] == pytest.approx(1.316822, abs=1e-6)
    assert p["posterior"] == pytest.approx(0.948646, abs=1e-6)
    assert _roi_sum_tg_per_yr(fields["total"], folder) == pytest.approx(p["posterior"], rel=1e-6)
    assert m["periods"][0]["diagScalars"]["prior_mt"] == p["prior"]
    assert m["periods"][0]["diagScalars"]["posterior_mt"] == p["posterior"]

    delta = fields["delta_emissions"]
    ref = _nc(folder, "posterior.nc", "EmisCH4_Total_ExclSoilAbs") - _nc(folder, "prior.nc", "EmisCH4_Total_ExclSoilAbs")
    np.testing.assert_allclose(delta, ref, rtol=1e-5, atol=1e-6)
    assert _roi_sum_tg_per_yr(delta, folder) == pytest.approx(0.9313823092482354 - 1.2995577839426047, rel=1e-5)
    old = _nc(folder, "posterior.nc", "EmisCH4_Total") - _nc(folder, "prior.nc", "EmisCH4_Total")
    assert _roi_sum_tg_per_yr(old, folder) == pytest.approx(-0.385440, abs=1e-6)


def test_kalman_headline_prior_is_not_shown(tmp_path: Path):
    """In Kalman mode IMI writes the total with the soil sink as
    PriorEmissions and without it as PosteriorEmissions. The Permian sample
    with its PriorEmissions set the Kalman way (to its TotalPrior, 0.385365
    instead of 0.408733 Mt/yr) converts to the same numbers as the sample
    as published, which is also what a fixed IMI would write."""
    import pandas as pd

    src = tmp_path / "input"
    shutil.copytree(SINGLE, src)
    csv = src / "viz_notebook_statistics.csv"
    df = pd.read_csv(csv)
    df.loc[0, "PriorEmissions"] = df.loc[0, "TotalPrior"]
    df.to_csv(csv, index=False)
    out = tmp_path / "out"
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)  # a known layout: no warning
        convert(input_folder=src, output_folder=out, cfg=Config(), region="domain")
    p = _load_manifest(out)["periods"][0]
    assert p["sectorScalars"]["total"]["prior"] == 0.4087327477516389
    assert p["sectorScalars"]["total"]["posterior"] == 0.602334359307776
    assert p["diagScalars"]["prior_mt"] == 0.4087327477516389


def test_imi_2_2_1_ensemble_range_excludes_the_soil_sink(tmp_path: Path, caplog):
    """IMI 2.2.1 sums the ensemble range with the soil sink. The Permian
    sample rewritten that way (headline pair and ensemble with the sink, so
    the ensemble mean equals TotalPosterior, 0.578967 Mt/yr; range
    0.470497-0.758980) converts to the range without the sink,
    0.493865-0.782348 Mt/yr, around the posterior of 0.602334."""
    import pandas as pd

    src = tmp_path / "input"
    shutil.copytree(SINGLE, src)
    csv = src / "viz_notebook_statistics.csv"
    df = pd.read_csv(csv)
    sink = df.loc[0, "TotalPosterior"] - df.loc[0, "TotalExclSoilAbsPosterior"]
    df.loc[0, "PriorEmissions"] = df.loc[0, "TotalPrior"]
    df.loc[0, "PosteriorEmissions"] = df.loc[0, "TotalPosterior"]
    df.loc[0, "EnsemblePosterior_Mean"] = df.loc[0, "TotalPosterior"]
    df.loc[0, "EnsemblePosterior_Min"] += sink
    df.loc[0, "EnsemblePosterior_Max"] += sink
    df.to_csv(csv, index=False)
    with caplog.at_level(logging.INFO, logger="imi_dashboard"), warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)  # a known layout: no warning
        convert(input_folder=src, output_folder=tmp_path / "out", cfg=Config(), region="domain")
    total = _load_manifest(tmp_path / "out")["periods"][0]["sectorScalars"]["total"]
    assert (total["prior"], total["posterior"]) == (0.4087327477516389, 0.602334359307776)
    assert total["ens_min"] == pytest.approx(0.4938651863192658, abs=1e-9)
    assert total["ens_max"] == pytest.approx(0.7823478220013, abs=1e-9)
    assert "ensemble range" in caplog.text and "(IMI 2.2.1)" in caplog.text


def test_unknown_headline_layout_is_reported(tmp_path: Path):
    """A PriorEmissions that matches neither of the CSV's prior totals is a
    layout this converter does not know: a warning, and the numbers without
    the sink are shown all the same."""
    import pandas as pd

    from imi_dashboard.totals import UnknownTotalsLayoutWarning

    src = tmp_path / "input"
    shutil.copytree(SINGLE, src)
    csv = src / "viz_notebook_statistics.csv"
    df = pd.read_csv(csv)
    df.loc[0, "PriorEmissions"] = 0.5
    df.to_csv(csv, index=False)
    with pytest.warns(UnknownTotalsLayoutWarning, match=r"in 1 period\(s\) \(single\)"):
        convert(input_folder=src, output_folder=tmp_path / "out", cfg=Config(), region="domain")
    total = _load_manifest(tmp_path / "out")["periods"][0]["sectorScalars"]["total"]
    assert (total["prior"], total["posterior"]) == (0.4087327477516389, 0.602334359307776)


def _copy_without(tmp_path: Path, drop: list[str]) -> Path:
    """The Permian sample copied into tmp_path, with `drop` removed from its
    posterior.nc and prior.nc."""
    import xarray as xr

    src = tmp_path / "input"
    shutil.copytree(SINGLE, src)
    for name in ("posterior.nc", "prior.nc"):
        with xr.open_dataset(SINGLE / "netCDF" / name) as ds:
            ds = ds.drop_vars(drop).load()
            ds.encoding.pop("unlimited_dims", None)
            ds.to_netcdf(src / "netCDF" / name)
    return src


def test_total_falls_back_to_emis_total_with_a_warning(tmp_path: Path):
    """Without EmisCH4_Total_ExclSoilAbs, the total map and the posterior −
    prior map use EmisCH4_Total, with a warning."""
    from imi_dashboard.defaults import TotalIncludesSoilSinkWarning

    src = _copy_without(tmp_path, ["EmisCH4_Total_ExclSoilAbs"])
    out = tmp_path / "out"
    with pytest.warns(TotalIncludesSoilSinkWarning, match="has no EmisCH4_Total_ExclSoilAbs"):
        convert(input_folder=src, output_folder=out, cfg=Config(), region="domain")
    m = _load_manifest(out)
    assert m["source"]["sectorVars"]["total"] == "EmisCH4_Total"
    # The numbers still leave the sink out; the map now includes it.
    assert m["totalDefinition"] == {"numbersIncludeSoilSink": False, "mapIncludesSoilSink": True}
    fields = _decode_period(out, m, "single")
    np.testing.assert_allclose(fields["total"], _nc(SINGLE, "posterior.nc", "EmisCH4_Total"), rtol=1e-6)
    ref = _nc(SINGLE, "posterior.nc", "EmisCH4_Total") - _nc(SINGLE, "prior.nc", "EmisCH4_Total")
    np.testing.assert_allclose(fields["delta_emissions"], ref, rtol=1e-5, atol=1e-6)


def test_explicit_total_imi_var_is_kept(tmp_path: Path):
    """A total sector that names its variable gets that variable."""
    out = tmp_path / "out"
    cfg = Config.model_validate({"sectors": [{"id": "total", "imi_var": "EmisCH4_Total"}]})
    convert(input_folder=SINGLE, output_folder=out, cfg=cfg, region="domain")
    m = _load_manifest(out)
    assert m["source"]["sectorVars"]["total"] == "EmisCH4_Total"
    assert m["totalDefinition"] == {"numbersIncludeSoilSink": False, "mapIncludesSoilSink": True}
    np.testing.assert_allclose(_decode_period(out, m, "single")["total"], _nc(SINGLE, "posterior.nc", "EmisCH4_Total"), rtol=1e-6)
    assert m["periods"][0]["sectorScalars"]["total"]["posterior"] == 0.602334359307776


def test_total_definition_of_another_variable_and_without_csv(tmp_path: Path):
    """A total sector pointed at another variable has a map of unknown
    definition; without a statistics CSV the numbers' definition is unknown."""
    from imi_dashboard import pack

    src = tmp_path / "input"
    shutil.copytree(SINGLE, src)
    (src / "viz_notebook_statistics.csv").unlink()
    out = tmp_path / "out"
    cfg = Config.model_validate({"sectors": [{"id": "total", "imi_var": "EmisCH4_Oil"}]})
    with pytest.warns(pack.MissingStatisticsWarning):
        convert(input_folder=src, output_folder=out, cfg=cfg, region="domain")
    assert _load_manifest(out)["totalDefinition"] == {"numbersIncludeSoilSink": None, "mapIncludesSoilSink": None}
