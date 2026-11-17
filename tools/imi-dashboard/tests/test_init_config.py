"""`imi-dashboard init-config`: the template, and the config filled from a run."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from imi_dashboard.config import Config, TextConfig, load_config
from imi_dashboard.defaults import fill_defaults_from_data
from imi_dashboard.discover import discover, split_incomplete
from imi_dashboard.info import describe
from imi_dashboard.init_config import TEXT_GROUPS, render_for_run, render_generic, template_text
from imi_dashboard.pack import convert

from .conftest import SAMPLES, TIMESERIES, invoke
from .test_defaults import PERMIAN_SECTORS, ROMANIA_SECTORS

SINGLE = SAMPLES / "single_period"


def _uncomment(text: str, key: str) -> dict:
    """The commented `# <key>:` block of a generated file, uncommented and
    parsed: what a reader gets by deleting the `# ` of each of its setting
    lines (the `##` explanations inside it stay comments)."""
    lines = text.splitlines()
    start = lines.index(f"# {key}:")
    block = []
    for line in lines[start:]:
        if line.startswith("##"):
            continue
        if not line.startswith("# ") or (block and not line.startswith("#  ")):
            break
        block.append(line[2:])
    return yaml.safe_load("\n".join(block))[key]


def _uncomment_all(text: str) -> str:
    """The whole file with every setting line uncommented."""
    return "\n".join(line[2:] if line.startswith("# ") else line for line in text.splitlines()) + "\n"


@pytest.fixture(scope="module")
def generated() -> dict[str, str]:
    return {name: render_for_run(describe(folder), today="2026-11-04") for name, folder in
            (("romania", TIMESERIES), ("permian", SINGLE))}


# ── the layout of the file ───────────────────────────────────────────────


@pytest.mark.parametrize("name", ["generic", "romania", "permian"])
def test_explanations_and_settings_are_told_apart(generated, name):
    """A line is blank, an explanation (`##`) or a setting (`# `); nothing
    else, so a reader never has to guess which a line is. Explanations sit
    above what they explain: no `##` line follows a top-level setting
    directly."""
    text = render_generic() if name == "generic" else generated[name]
    lines = text.splitlines()
    odd = [l for l in lines if l and not l.startswith(("##", "# "))]
    assert odd == []
    for before, after in zip(lines, lines[1:]):
        if re.match(r"# [a-z_]+:( |$)", before) and not before.endswith(":"):
            assert not after.startswith("##"), (before, after)


def test_text_groups_are_the_text_keys_in_order():
    assert [k for _, keys in TEXT_GROUPS for k in keys] == list(TextConfig.model_fields)


# ── the generic template ─────────────────────────────────────────────────


def test_generic_form_is_the_template_with_the_text_block():
    text = render_generic()
    assert "#%" not in text
    assert text.startswith("## imi-dashboard config.\n")
    # Up to the text block, only the marker lines are removed.
    head = [l for l in template_text().split("#%text\n")[0].splitlines() if not l.startswith("#%")]
    assert text.splitlines()[: len(head)] == head
    assert "\n# text:\n" in text
    assert "\n#   chart_title_split: Monthly methane emissions\n" in text


def test_generic_form_uncommented_is_a_valid_config(tmp_path):
    """Every example in the template is valid as written."""
    path = tmp_path / "all.yaml"
    path.write_text(_uncomment_all(render_generic()), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.area_name == "Romania"
    assert [s.id for s in cfg.sectors] == ["total", "coal", "flaring"]
    assert (cfg.sectors[1].label, cfg.sectors[2].short) == ("Hard coal", "Flaring")


def test_init_config_without_input_writes_the_template(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = invoke("init-config")
    assert r.exit_code == 0, r.output
    assert r.stdout == "wrote dashboard.yaml; edit it, then pass it with --config dashboard.yaml\n"
    written = tmp_path / "dashboard.yaml"
    assert written.read_bytes() == render_generic().encode("utf-8")  # UTF-8, LF line ends
    assert load_config(written) == Config()


def test_init_config_without_input_does_not_load_xarray(tmp_path):
    code = (
        "import sys; from typer.testing import CliRunner; from imi_dashboard.cli import app; "
        "r = CliRunner().invoke(app, ['init-config', '-o', '-']); "
        "print(r.exit_code, 'xarray' in sys.modules)"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp_path)
    assert r.stdout.split()[-2:] == ["0", "False"], r.stderr


# ── the config filled from a run ─────────────────────────────────────────


@pytest.mark.parametrize("name", ["romania", "permian"])
def test_generated_file_changes_nothing_as_written(generated, name, tmp_path):
    path = tmp_path / "dashboard.yaml"
    path.write_text(generated[name], encoding="utf-8")
    assert load_config(path) == Config()
    assert "#%" not in generated[name]


@pytest.mark.parametrize("name,folder", [("romania", TIMESERIES), ("permian", SINGLE)])
def test_generated_file_uncommented_is_the_run_config(generated, name, folder, tmp_path):
    """Uncommenting every setting gives the config `convert` uses for the
    run without one, as the header says."""
    path = tmp_path / "all.yaml"
    path.write_text(_uncomment_all(generated[name]), encoding="utf-8")
    cfg = load_config(path)
    run = describe(folder).config
    assert cfg.model_dump(exclude={"sectors", "text"}) == run.model_dump(exclude={"sectors", "text"})

    def resolve(c: Config) -> dict[str, str]:
        return c.text.resolved(area_name=c.area_name, overview_heading_fallback=c.display_name)

    assert resolve(cfg) == resolve(run)


def test_generated_header_names_the_run(generated):
    head = generated["romania"].splitlines()[:2]
    assert head[0] == "## imi-dashboard config for the IMI run `timeseries`, written by"
    assert head[1].startswith("## imi-dashboard ") and head[1].endswith(" init-config on 2026-11-04.")


def test_generated_title_and_branding_are_the_run_values(generated):
    """The template's example title and branding are not this run's: the
    generated file shows what the run gets, an empty value where none."""
    text = generated["romania"]
    assert "\n# area_name:\n" in text
    assert "\n# display_name: Methane emissions\n" in text
    assert "\n# header_brand: terra           # terra | custom\n" in text
    assert "\n# logo:                         # an image;" in text
    assert "\n# brand_name:\n# brand_tagline:\n" in text
    assert [l for l in text.splitlines() if l.startswith("# ") and "Romania" in l] == []


@pytest.mark.parametrize(
    "name,folder,ids",
    [("romania", TIMESERIES, ROMANIA_SECTORS), ("permian", SINGLE, PERMIAN_SECTORS)],
)
def test_uncommented_sectors_are_the_detected_ones(generated, name, folder, ids):
    """Uncommenting the whole sectors block gives the sectors `convert`
    detects, every field included, the total's variable still chosen from
    the data."""
    raw = _uncomment(generated[name], "sectors")
    assert [s["id"] for s in raw] == ids
    assert "imi_var" not in raw[0]
    complete, _ = split_incomplete(discover(folder))
    detected = fill_defaults_from_data(Config(), complete).sectors
    from_file = fill_defaults_from_data(Config.model_validate({"sectors": raw}), complete).sectors
    assert from_file == detected


def test_generated_sector_rows(generated):
    """One aligned row per sector, with the fields a reader edits; the
    standard variable and short label are left to the standard table."""
    text = generated["romania"]
    assert "#   - {id: gas,        label: Gas,                 color: '#7A4E9E',            primary: true}\n" in text
    raw = {s["id"]: s for s in _uncomment(text, "sectors")}
    assert raw["gas"] == {"id": "gas", "label": "Gas", "color": "#7A4E9E", "primary": True}
    assert raw["other_anth"]["label"] == "Other anthropogenic" and raw["other_anth"]["primary"] is False
    assert "## This run's 14 sectors (found in period_1). To edit them, uncomment the\n" in text
    # The note above the list names the variable the total's map is read
    # from: the total without the soil sink, for older IMI outputs too.
    for name in ("romania", "permian"):
        flat = " ".join(l.removeprefix("## ") for l in generated[name].splitlines() if l.startswith("## "))
        assert "The total's map shows EmisCH4_Total_ExclSoilAbs, the total without the soil sink" in flat


def test_sector_row_of_a_sector_that_is_not_standard():
    """A detected sector outside the standard table gets its variable in
    its row, after the columns every row has; a value with a comma is
    quoted, or it would split the mapping."""
    from imi_dashboard.config import SectorConfig
    from imi_dashboard.init_config import _sector_cells

    s = SectorConfig(id="flaring", imi_var="EmisCH4_Flaring", label="Flaring", color="#9CA3AF")
    assert _sector_cells(s) == (
        ["id: flaring", "label: Flaring", "color: '#9CA3AF'", "primary: false"],
        ["imi_var: EmisCH4_Flaring"],
    )
    s = SectorConfig.model_validate({"id": "coal", "label": "Coal, hard"})
    assert _sector_cells(s) == (
        ["id: coal", "label: 'Coal, hard'", "color: var(--arc-red)", "primary: true"],
        [],  # the standard variable and short label
    )


@pytest.mark.parametrize("name", ["romania", "permian"])
def test_uncommented_text_is_what_convert_writes(generated, name):
    """Every text key is written, and uncommenting the block gives exactly
    the strings the dashboard shows without a config."""
    raw = _uncomment(generated[name], "text")
    assert list(raw) == list(TextConfig.model_fields)
    text = TextConfig.model_validate(raw)
    defaults = TextConfig(cadence="monthly").resolved(area_name=None, overview_heading_fallback="Methane emissions")
    assert text.resolved(area_name=None, overview_heading_fallback="Methane emissions") == defaults
    assert raw["cadence"] == "monthly"
    assert raw["heading_context_timeseries"] == "Monthly timeseries"
    assert raw["subtitle_timeseries"] == "Monthly Kalman-filter inversion · {date_start} – {date_end}"
    assert raw["footer_disclaimer"].startswith("Estimates are derived from atmospheric inverse modeling and carry")


def test_generated_text_matches_config_json(generated, tmp_path):
    """The uncommented text equals the `text` written into config.json."""
    import json

    out = tmp_path / "site"
    convert(input_folder=TIMESERIES, output_folder=out, cfg=Config())
    written = json.loads((out / "data" / "config.json").read_text(encoding="utf-8"))["text"]
    raw = _uncomment(generated["romania"], "text")
    assert {k: written[k] for k in raw} == raw


def test_generated_notes_on_cadence_and_region(generated):
    romania, permian = generated["romania"], generated["permian"]
    assert (
        "##   detected from the length of the first period, here 31 days (in\n"
        "##   period_1).\n"
    ) in romania
    assert "##   detected from the length of the first period, here 31 days.\n" in permian
    # The run's note sits right above the setting it is about.
    assert (
        "##\n"
        "## This run: 8 of its 359 state-vector elements (in period_1) are buffer\n"
        "## elements. roi keeps 18 x 29 of the domain's 60 x 63 cells.\n"
        "# region: roi                   # roi | domain\n"
    ) in romania
    assert (
        "## This run: 33 of its 96 state-vector elements are buffer elements. roi\n"
        "## keeps 9 x 7 of the domain's 23 x 21 cells.\n"
        "# region: roi"
    ) in permian


def test_init_config_from_a_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = invoke("init-config", str(TIMESERIES), "-o", "romania.yaml")
    assert r.exit_code == 0, r.output
    assert r.stdout == (
        "wrote romania.yaml with the values of timeseries (14 sectors, cadence monthly); "
        "edit it, then pass it with --config romania.yaml\n"
    )
    assert load_config(tmp_path / "romania.yaml") == Config()
    assert "#   - {id: coal, " in (tmp_path / "romania.yaml").read_text(encoding="utf-8")


def test_init_config_to_stdout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = invoke("init-config", str(SINGLE), "-o", "-")
    assert r.exit_code == 0, r.output
    assert r.stdout.startswith("## imi-dashboard config for the IMI run `single_period`")
    assert "#   - {id: termites, " in r.stdout
    assert list(tmp_path.iterdir()) == []


def test_init_config_verbose_lists_the_files_read(tmp_path):
    r = invoke("init-config", str(SINGLE), "-o", str(tmp_path / "c.yaml"), "-v", "--log-style", "screen")
    assert r.exit_code == 0
    assert f"reading {SINGLE / 'netCDF' / 'posterior.nc'}" in r.stderr.splitlines()


# ── refusals ─────────────────────────────────────────────────────────────


def _error(*args: str) -> str:
    r = invoke("init-config", *args, "--log-style", "screen")
    assert r.exit_code == 1, r.output
    assert r.stdout == ""
    return r.stderr


def test_init_config_refuses_to_overwrite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dashboard.yaml").write_text("area_name: Romania\n", encoding="utf-8")
    assert _error() == "error: dashboard.yaml already exists; pass --overwrite to replace it\n"
    assert (tmp_path / "dashboard.yaml").read_text(encoding="utf-8") == "area_name: Romania\n"
    r = invoke("init-config", "--overwrite")
    assert r.exit_code == 0, r.output
    assert load_config(tmp_path / "dashboard.yaml") == Config()


def test_init_config_refuses_a_folder_as_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()
    expected = Path("configs") / "dashboard.yaml"
    assert _error("-o", "configs") == f"error: configs is a folder; name the file to write, e.g. -o {expected}\n"


def test_init_config_says_how_to_name_the_output(tmp_path, monkeypatch):
    """A YAML name given as the input, as with the old form of the command."""
    monkeypatch.chdir(tmp_path)
    assert _error("dashboard.yaml") == (
        "error: dashboard.yaml is not an IMI output folder; "
        "to write the config to dashboard.yaml, use -o dashboard.yaml\n"
    )
    (tmp_path / "old.yaml").write_text("{}", encoding="utf-8")
    assert _error("old.yaml") == (
        "error: old.yaml is a file, not an IMI output folder; to write the config to old.yaml, use -o old.yaml\n"
    )
    assert _error("missing") == "error: input folder does not exist: missing\n"


def test_init_config_refuses_a_dashboard(tmp_path):
    site = tmp_path / "site"
    convert(input_folder=SINGLE, output_folder=site, cfg=Config())
    assert _error(str(site), "-o", str(tmp_path / "c.yaml")) == (
        f"error: {site} is a generated dashboard; pass the IMI output folder it was made from "
        f"(`imi-dashboard info {site}` names it)\n"
    )


def test_init_config_points_into_a_folder_of_runs(tmp_path):
    """sample_files/ holds two runs: the error names them."""
    err = _error(str(SAMPLES), "-o", str(tmp_path / "c.yaml"))
    assert err == (
        f"error: {SAMPLES}: not a single-period folder (no netCDF/ subdir) and no period_<N>_plots/ "
        f"subdirs found; it holds IMI runs, pass one of them: {SINGLE}, {TIMESERIES}\n"
    )


def test_init_config_refuses_a_folder_that_is_not_a_run(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.txt").write_text("x", encoding="utf-8")
    err = _error(str(tmp_path / "notes"), "-o", str(tmp_path / "c.yaml"))
    assert err == (
        f"error: {tmp_path / 'notes'}: not a single-period folder (no netCDF/ subdir) "
        "and no period_<N>_plots/ subdirs found\n"
    )
    assert not (tmp_path / "c.yaml").exists()


def test_quiet_leaves_out_the_wrote_line(tmp_path):
    """-q: nothing on stdout or stderr, the file written all the same; with
    -o - the config itself is still printed."""
    out = tmp_path / "dashboard.yaml"
    r = invoke("init-config", str(SINGLE), "-o", str(out), "-q")
    assert r.exit_code == 0, r.output
    assert r.stdout == "" and r.stderr == ""
    assert out.read_text(encoding="utf-8").startswith("## imi-dashboard config for the IMI run `single_period`")
    r = invoke("init-config", str(SINGLE), "-o", "-", "-q")
    assert r.exit_code == 0
    assert r.stdout == out.read_text(encoding="utf-8")
