"""The imi-dashboard command line: subcommand wiring, `serve` and `info`.

Conversion itself is covered by test_pack/test_defaults/test_incremental."""

import json
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from rich.console import detect_legacy_windows

import imi_dashboard
from imi_dashboard.config import Config
from imi_dashboard.info import describe, format_info
from imi_dashboard.serve import check_dashboard, is_loopback, make_server, server_url

from .conftest import EXAMPLE_CONFIG, SAMPLES, TIMESERIES, invoke


@pytest.fixture(autouse=True)
def screen_style(monkeypatch):
    """Under CliRunner stderr is not a terminal, so `--log-style auto` would
    pick the log style; most tests here assert the screen format, so set it
    through the environment variable. The tests of the styles themselves
    override or remove it. FORCE_COLOR, NO_COLOR, TTY_COMPATIBLE and
    GITHUB_ACTIONS are removed so that the caller's environment cannot switch
    on the progress bar or colour: rich colours its output on GitHub Actions
    even when it is not a terminal, which splits "--verbose" in the help
    text into separately coloured "-" and "-verbose"."""
    monkeypatch.setenv("IMI_DASHBOARD_LOG_STYLE", "screen")
    for name in ("FORCE_COLOR", "NO_COLOR", "TTY_COMPATIBLE", "GITHUB_ACTIONS"):
        monkeypatch.delenv(name, raising=False)


def test_entry_point_prints_package_version():
    """The one test that starts the command as its own process, through
    `python -m imi_dashboard.cli` (the console script calls the same
    main()); the others run it in-process, which is much faster."""
    r = subprocess.run(
        [sys.executable, "-m", "imi_dashboard.cli", "--version"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == imi_dashboard.__version__


def test_help_lists_the_subcommands():
    r = invoke("--help")
    assert r.exit_code == 0, r.output
    for command in ("convert", "info", "serve", "init-config"):
        assert command in r.stdout


SUMMARIES = {
    "convert": "Convert an IMI run folder into a dashboard.",
    "info": "Describe an IMI run or a generated dashboard.",
    "serve": "Serve a generated dashboard locally, for previewing.",
    "init-config": "Write a commented config file from an IMI run.",
}


@pytest.fixture(params=["rich", "plain"])
def help_format(request, monkeypatch):
    """Both help formats: typer's rich one (a terminal) and click's plain
    one, which main() switches to in the log style."""
    from imi_dashboard.cli import app

    if request.param == "plain":
        monkeypatch.setattr(app, "rich_markup_mode", None)
    return request.param


def test_command_list_has_one_line_summaries(help_format):
    """Each command's first sentence is its whole entry in the command list,
    on one line: click's plain format cuts a longer one off with '...'."""
    r = invoke("--help")
    assert r.exit_code == 0
    lines = r.stdout.splitlines()
    for name, summary in SUMMARIES.items():
        line = next(l for l in lines if re.match(rf"[\s│]*{name}\s", l))
        assert summary in line, line
        assert "..." not in line, line
    # The details after each summary stay out of the command list.
    assert "production hosting" not in r.stdout
    assert "missing files" not in r.stdout


@pytest.mark.parametrize("command", ["convert", "info", "serve", "init-config"])
def test_help_epilog_keeps_its_lines(command, help_format):
    """The exit statuses and examples at the end of each command's help keep
    their line breaks in both formats."""
    r = invoke(command, "--help")
    assert r.exit_code == 0
    lines = [l.strip() for l in r.stdout.splitlines()]
    at = lines.index("Exit status:")
    assert lines[at + 1].startswith("0    ")
    assert "2    a usage error: an unknown option, a missing argument, a bad value" in lines
    examples = lines[lines.index("Examples:") + 1:]
    assert any(l.startswith(f"imi-dashboard {command}") for l in examples)


def test_option_placeholders_are_named(help_format):
    """Every option value has a descriptive placeholder, FILE and FOLDER the
    same in every command, instead of typer's <file>, <str>, <int>."""
    expected = {
        "convert": ["--config FILE", "--app-bundle FOLDER", "--region domain|roi", "--log-style auto|screen|log", "--log-file FILE"],
        "info": ["--config FILE", "--log-style auto|screen|log", "--log-file FILE"],
        "serve": ["--port PORT", "--bind ADDRESS", "--log-style auto|screen|log", "--log-file FILE"],
        "init-config": ["--output FILE", "--log-style auto|screen|log", "--log-file FILE"],
    }
    for command, options in expected.items():
        text = re.sub(r"[ │]+", " ", invoke(command, "--help").stdout)
        for option in options:
            name, metavar = option.split(" ")
            assert re.search(rf"{re.escape(name)}( -\w)? {re.escape(metavar)} ", text), (command, option)
        # (In the rich format the Arguments panel has a type column of its
        # own, <directory> or <path>, beside the argument's name.)
        option_lines = [l for l in text.splitlines() if l.lstrip().startswith("-")]
        for typer_style in ("<file>", "<str>", "<int>", "<directory>", "<path>"):
            assert not any(typer_style in l for l in option_lines), (command, typer_style)


def test_output_options_have_the_same_wording_everywhere():
    """-v, -q, --log-style and --log-file are on every command; the help of
    the last two is the same, that of -v and -q shares its frame."""
    from imi_dashboard.cli import LOG_FILE_HELP, LOG_STYLE_HELP, app
    from typer.main import get_command

    group = get_command(app)
    for name in SUMMARIES:
        params = {p.name: p for p in group.commands[name].params}
        assert {"verbose", "quiet", "log_style", "log_file"} <= set(params), name
        assert params["log_style"].help == LOG_STYLE_HELP
        assert params["log_file"].help == LOG_FILE_HELP
        assert params["verbose"].help.startswith("Print more on stderr: ")
        assert params["verbose"].help.endswith("the full traceback of an error.")
        assert params["quiet"].help.startswith("Print only errors on stderr: no warnings")


@pytest.mark.parametrize("command", ["convert", "info", "serve", "init-config"])
def test_verbose_flag_is_shown_as_a_counted_flag(command):
    r = invoke(command, "--help")
    assert r.exit_code == 0
    line = next(l for l in r.stdout.splitlines() if "--verbose" in l)
    assert "INTEGER" not in line
    assert "[default: 0]" not in r.stdout


def test_cli_import_does_not_load_xarray():
    """--help, --version, serve and init-config start without importing the
    converter (pack, and so xarray), which takes seconds."""
    r = subprocess.run(
        [sys.executable, "-c", "import sys, imi_dashboard.cli; print('xarray' in sys.modules)"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "False"


def test_info_verbose_lists_the_files_read():
    r = invoke("info", str(SAMPLES / "single_period"), "-v")
    assert r.exit_code == 0
    netcdf = SAMPLES / "single_period" / "netCDF"
    # (Only the "reading" lines: the first netCDF import in a process can
    # add an unrelated numpy binary-compatibility warning.)
    assert [l for l in r.stderr.splitlines() if l.startswith("reading ")] == [
        f"reading {SAMPLES / 'single_period' / 'viz_notebook_statistics.csv'}",
        f"reading {netcdf / 'posterior.nc'}",
        f"reading {netcdf / 'state_vector.nc'}",
    ]


def test_bare_convert_form_is_gone():
    """`imi-dashboard <input> <output>` without a subcommand is an error, not
    a conversion."""
    r = invoke(str(SAMPLES / "single_period"), "site")
    assert r.exit_code != 0


# ── serve ────────────────────────────────────────────────────────────────


def _fake_dashboard(root: Path) -> Path:
    site = root / "site"
    (site / "assets").mkdir(parents=True)
    (site / "data" / "periods").mkdir(parents=True)
    (site / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (site / "assets" / "index-abc.js").write_text("export {}", encoding="utf-8")
    (site / "data" / "manifest.json").write_text(json.dumps({"periods": []}), encoding="utf-8")
    (site / "data" / "periods" / "0000.bin").write_bytes(b"\x00" * 8)
    return site


def test_serve_sends_fixed_types_and_no_store(tmp_path):
    server = make_server(_fake_dashboard(tmp_path), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = server_url(server)
        expected = {
            "": "text/html; charset=utf-8",
            "assets/index-abc.js": "text/javascript; charset=utf-8",
            "data/manifest.json": "application/json",
            "data/periods/0000.bin": "application/octet-stream",
        }
        for path, content_type in expected.items():
            with urllib.request.urlopen(url + path) as res:
                assert res.status == 200
                assert res.headers["Content-Type"] == content_type, path
                assert res.headers["Cache-Control"] == "no-store", path
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(url + "data/periods/0001.bin")
        assert err.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def serve_returns_at_once(monkeypatch):
    """`serve` returns as soon as the server would start serving, instead of
    blocking until Ctrl+C."""
    from http.server import ThreadingHTTPServer

    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", lambda self, poll_interval=0.5: None)


def test_serve_warns_that_it_is_for_local_previewing(tmp_path, serve_returns_at_once):
    site = _fake_dashboard(tmp_path)
    r = invoke("serve", str(site), "--port", "0")
    assert r.exit_code == 0, r.output
    assert r.stderr.splitlines() == [
        "warning: this is a development server, for previewing a dashboard on this "
        "machine; do not use it in production. To share a dashboard, upload its "
        "folder to a static host.",
    ]
    assert re.fullmatch(
        rf"Serving {re.escape(str(site))} at http://127\.0\.0\.1:\d+/ \(press Ctrl\+C to stop\)\n",
        r.stdout,
    )


def test_serve_warns_again_when_reachable_from_the_network(tmp_path, serve_returns_at_once, monkeypatch):
    """is_loopback is patched rather than the server bound to 0.0.0.0, which
    can raise a firewall prompt; the message names the --bind value."""
    monkeypatch.setattr("imi_dashboard.serve.is_loopback", lambda server: False)
    r = invoke("serve", str(_fake_dashboard(tmp_path)), "--port", "0", "--bind", "localhost")
    assert r.exit_code == 0, r.output
    lines = r.stderr.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("warning: this is a development server")
    assert lines[1] == (
        "warning: listening on localhost, so anyone who can reach this machine over the "
        "network can open the dashboard: the server has no access control and is not "
        "hardened for exposure. Leave out --bind (127.0.0.1) to keep it local."
    )


@pytest.fixture
def serve_two_requests(monkeypatch):
    """`serve` answers two requests, one for a file that exists and one for
    a file that does not, then returns, instead of serving until Ctrl+C."""
    from http.server import ThreadingHTTPServer

    real = ThreadingHTTPServer.serve_forever

    def serve_two(self, poll_interval=0.5):
        thread = threading.Thread(target=real, args=(self, 0.05), daemon=True)
        thread.start()
        url = server_url(self)
        urllib.request.urlopen(url + "data/manifest.json").read()
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(url + "data/periods/0001.bin")
        self.shutdown()
        thread.join()

    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", serve_two)


def _request_lines(stderr: str) -> list[str]:
    return [l for l in stderr.splitlines() if '"GET ' in l or "code 404" in l]


def test_serve_logs_failed_requests_and_with_v_every_request(tmp_path, serve_two_requests):
    site = str(_fake_dashboard(tmp_path))
    failed = 'warning: 127.0.0.1 "GET /data/periods/0001.bin HTTP/1.1" 404'
    r = invoke("serve", site, "--port", "0")
    assert r.exit_code == 0, r.output
    assert _request_lines(r.stderr) == [failed]
    r = invoke("serve", site, "--port", "0", "-v")
    assert _request_lines(r.stderr) == ['127.0.0.1 "GET /data/manifest.json HTTP/1.1" 200', failed]
    assert f"serve {site}" in r.stderr.splitlines()[0]
    r = invoke("serve", site, "--port", "0", "-vv")
    assert _request_lines(r.stderr) == [
        '127.0.0.1 "GET /data/manifest.json HTTP/1.1" 200',
        "debug: code 404, message File not found",
        failed,
    ]


def test_serve_quiet_prints_no_warnings_and_no_requests(tmp_path, serve_two_requests):
    r = invoke("serve", str(_fake_dashboard(tmp_path)), "--port", "0", "-q")
    assert r.exit_code == 0, r.output
    assert r.stderr == ""
    assert r.stdout.startswith("Serving ")


def test_serve_log_file_records_every_request(tmp_path, serve_two_requests):
    log_file = tmp_path / "serve.log"
    r = invoke("serve", str(_fake_dashboard(tmp_path)), "--port", "0", "-q", "--log-file", str(log_file))
    assert r.exit_code == 0, r.output
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert any(re.fullmatch(r"\S+Z imi-dashboard info: Serving .* \(press Ctrl\+C to stop\)", l) for l in lines)
    assert any(l.endswith('imi-dashboard info: 127.0.0.1 "GET /data/manifest.json HTTP/1.1" 200') for l in lines)
    assert any(l.endswith('imi-dashboard warning: 127.0.0.1 "GET /data/periods/0001.bin HTTP/1.1" 404') for l in lines)


def test_serve_warnings_follow_the_log_style(tmp_path, serve_returns_at_once, monkeypatch):
    monkeypatch.setenv("IMI_DASHBOARD_LOG_STYLE", "log")
    r = invoke("serve", str(_fake_dashboard(tmp_path)), "--port", "0")
    assert r.exit_code == 0, r.output
    assert re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ imi-dashboard warning: this is a development server, .*\n",
        r.stderr,
    )


@pytest.mark.parametrize(
    ("host", "loopback"),
    [("127.0.0.1", True), ("127.0.0.2", True), ("::1", True), ("0.0.0.0", False),
     ("::", False), ("192.168.1.5", False), ("fe80::1%eth0", False)],
)
def test_is_loopback(host, loopback):
    class FakeServer:
        server_address = (host, 8000)

    assert is_loopback(FakeServer()) is loopback  # type: ignore[arg-type]


def test_serve_refuses_a_folder_that_is_not_a_dashboard(tmp_path):
    with pytest.raises(ValueError, match="no data/manifest.json"):
        check_dashboard(tmp_path)

    site = _fake_dashboard(tmp_path)
    (site / "index.html").unlink()
    with pytest.raises(ValueError, match="its app bundle is missing"):
        check_dashboard(site)

    r = invoke("serve", str(SAMPLES / "single_period"))
    assert r.exit_code == 1
    assert "not a generated dashboard" in r.stderr


# ── info ─────────────────────────────────────────────────────────────────


def test_info_single_period():
    info = describe(SAMPLES / "single_period")
    assert info.mode == "single"
    assert (info.start, info.end) == ("2021-05-01", "2021-06-01")
    assert info.period_days == 31 and info.cadence == "monthly"
    assert not info.cadence_from_config
    assert (info.grid.rows, info.grid.cols) == (23, 21)
    # Same split as test_buffer, and the same crop as `convert` reports
    # (test_defaults: "grid 9x7").
    assert (info.n_elements, info.n_buffers) == (96, 33)
    assert info.roi_shape == (9, 7)
    assert len(info.sectors) == 12 and not info.sectors_from_config
    assert info.missing_required == {} and info.missing_optional == {}
    # IMI 2.3.1 writes the ensemble range into the statistics CSV.
    assert info.has_ensemble is True
    assert "uncertainty band shown" in format_info(info)
    # The totals without the soil sink come from the CSV's TotalExclSoilAbs
    # columns, and the map leaves the sink out too.
    assert info.total_definition == {"numbersIncludeSoilSink": False, "mapIncludesSoilSink": False}
    assert (
        "Total         numbers excluding the soil sink (TotalExclSoilAbsPrior and TotalExclSoilAbsPosterior "
        "of viz_notebook_statistics.csv), map excluding the soil sink"
    ) in format_info(info)


def test_info_names_the_imi_2_2_0_total_columns(tmp_path: Path):
    """IMI 2.1.0 and 2.2.0 name the total without the soil sink
    TotalExclSoilAbsorbPrior/Posterior; info says those are read."""
    import shutil

    import pandas as pd

    src = tmp_path / "run"
    shutil.copytree(SAMPLES / "single_period", src)
    csv = src / "viz_notebook_statistics.csv"
    df = pd.read_csv(csv).rename(columns={
        "TotalExclSoilAbsPrior": "TotalExclSoilAbsorbPrior",
        "TotalExclSoilAbsPosterior": "TotalExclSoilAbsorbPosterior",
    })
    df.to_csv(csv, index=False)
    info = describe(src)
    assert info.total_columns == ("TotalExclSoilAbsorbPrior", "TotalExclSoilAbsorbPosterior")
    assert (
        "numbers excluding the soil sink (TotalExclSoilAbsorbPrior and TotalExclSoilAbsorbPosterior "
        "of viz_notebook_statistics.csv)"
    ) in format_info(info)


def test_info_timeseries_reports_gap_and_missing_files():
    info = describe(SAMPLES / "timeseries")
    assert info.mode == "timeseries"
    assert info.period_numbers == [1, 2, 4]
    assert info.missing_numbers == [3]
    assert (info.start, info.end) == ("2023-01-01", "2023-05-01")
    assert (info.n_elements, info.n_buffers) == (359, 8)
    assert info.roi_shape == (18, 29)
    # This older IMI run writes no ensemble statistics: no ensemble_statistics.csv,
    # which the converter does not read and info therefore does not report, and
    # no ensemble columns in the statistics CSV, which info reports.
    assert info.missing_optional == {}
    assert info.has_ensemble is False
    text = format_info(info)
    assert "numbered 1–4 (missing: 3)" in text
    assert "ensemble_statistics.csv" not in text
    assert "no ensemble minimum and maximum in viz_notebook_statistics.csv (in period_1): no uncertainty band" in text
    # The older IMI's CSV has only totals including the soil sink, so the
    # totals without it are summed from the maps.
    assert info.total_definition == {"numbersIncludeSoilSink": False, "mapIncludesSoilSink": False}
    assert (
        "Total         numbers excluding the soil sink (summed from prior.nc and posterior.nc in period_1: "
        "viz_notebook_statistics.csv in period_1 has no TotalExclSoilAbs columns), map excluding the soil sink"
    ) in text


def test_info_with_config_shows_configured_values():
    cfg = Config.model_validate({
        "sectors": [{"id": "total", "label": "Total", "short": "Total", "color": "red"}],
        "text": {"cadence": "weekly"},
    })
    info = describe(SAMPLES / "timeseries", cfg)
    assert info.sectors == ["total"] and info.sectors_from_config
    assert info.cadence == "weekly" and info.cadence_from_config


def test_info_reports_a_period_lacking_posterior_as_skipped(tmp_path):
    """A period without posterior.nc is left out by `convert`; `info` says
    so and exits with status 0."""
    import shutil

    run = tmp_path / "run"
    shutil.copytree(SAMPLES / "timeseries", run)
    (run / "period_2_plots" / "netCDF" / "posterior.nc").unlink()
    info = describe(run)
    assert info.missing_required == {"posterior.nc": [2]}
    # The skipped period is still listed among the folders found.
    assert info.period_numbers == [1, 2, 4]
    r = invoke("info", str(run))
    assert r.exit_code == 0, r.output
    assert "posterior.nc missing in period(s) 2; convert leaves them out" in r.stdout


def test_info_reads_the_first_complete_period(tmp_path):
    """With period 1 incomplete, the dates, state vector and defaults come
    from period 2."""
    import shutil

    run = tmp_path / "run"
    shutil.copytree(SAMPLES / "timeseries", run)
    (run / "period_1_plots" / "netCDF" / "prior.nc").unlink()
    info = describe(run)
    assert info.missing_required == {"prior.nc": [1]}
    assert info.reference_label == "period_2"
    assert (info.start, info.end) == ("2023-02-01", "2023-05-01")
    assert (info.n_elements, info.n_buffers) == (359, 8)
    assert "359 elements (in period_2)" in format_info(info)


def test_info_fails_when_no_period_is_complete(tmp_path):
    import shutil

    run = tmp_path / "run"
    shutil.copytree(SAMPLES / "single_period", run)
    (run / "netCDF" / "posterior.nc").unlink()
    with pytest.raises(ValueError, match="no period has both posterior.nc and prior.nc"):
        describe(run)


def test_info_describes_a_converted_dashboard(tmp_path, monkeypatch):
    from imi_dashboard import pack

    monkeypatch.setattr(pack, "_utc_now", lambda: "2026-11-04T10:15:30Z")
    site = tmp_path / "site"
    assert invoke("convert", str(TIMESERIES), str(site), "-q").exit_code == 0
    r = invoke("info", str(site))
    assert r.exit_code == 0, r.output
    lines = r.stdout.splitlines()
    assert lines[0] == f"Dashboard  {site}"
    assert lines[1:] == [
        "Converted  2026-11-04T10:15:30Z (UTC)",
        f"Converter  imi-dashboard {imi_dashboard.__version__}",
        "Input      timeseries (the IMI run folder's name)",
        "Layout     timeseries, 3 periods",
        "Grid       18 x 29 cells (rows x cols), 0.25° lat x 0.3125° lon",
        "Region     roi (the region of interest only, buffer elements dropped)",
        "Total      numbers excluding the soil sink, map excluding the soil sink",
    ]


def test_info_on_a_dashboard_without_provenance_says_not_recorded(tmp_path):
    site = _fake_dashboard(tmp_path)
    manifest = {
        "version": 1, "mode": "single", "region": "domain",
        "grid": {"rows": 23, "cols": 21, "dlat": 0.25, "dlon": 0.3125},
        "periods": [{"idx": 0}],
    }
    (site / "data" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    r = invoke("info", str(site))
    assert r.exit_code == 0, r.output
    assert "Provenance  not recorded (written by an older imi-dashboard, which did not record it)" in r.stdout
    assert "Layout      single period" in r.stdout
    assert "Grid        23 x 21 cells (rows x cols), 0.25° lat x 0.3125° lon" in r.stdout
    assert "Region      domain (the complete inversion region, buffer elements included)" in r.stdout


def _strict_json(text: str) -> dict:
    """Parse ``text`` as strict JSON: NaN and Infinity are refused."""
    def refuse(name: str) -> None:
        raise AssertionError(f"not strict JSON: {name}")
    return json.loads(text, parse_constant=refuse)


def test_info_json_single_period():
    r = invoke("info", str(SAMPLES / "single_period"), "--json")
    assert r.exit_code == 0, r.output
    d = _strict_json(r.stdout)
    assert (d["schemaVersion"], d["kind"], d["mode"]) == (1, "run", "single")
    assert d["path"] == str(SAMPLES / "single_period")
    assert d["periods"] == [{
        "label": "single", "number": None, "start": "2021-05-01", "end": "2021-06-01",
        "skipped": False, "skipReason": None, "missingFiles": [],
    }]
    assert d["gaps"] == []
    assert (d["start"], d["end"], d["periodDays"]) == ("2021-05-01", "2021-06-01", 31)
    assert (d["cadence"], d["cadenceSource"]) == ("monthly", "inferred")
    assert d["grid"] == {
        "rows": 23, "cols": 21, "dlat": 0.25, "dlon": 0.3125,
        "latMin": 29.25, "latMax": 34.75, "lonMin": -107.1875, "lonMax": -100.9375,
    }
    assert d["region"] == "roi"
    # The same split and crop as test_info_single_period.
    assert d["stateVector"] == {"period": "single", "elements": 96, "roiElements": 63, "bufferElements": 33}
    assert d["roiGrid"] == {"rows": 9, "cols": 7}
    assert d["ensemble"] is True
    assert len(d["sectors"]) == 12 and d["sectorsSource"] == "detected"
    assert d["sectors"][0] == {"id": "total", "imiVar": "EmisCH4_Total_ExclSoilAbs", "label": "Total"}
    assert d["total"] == {"numbersIncludeSoilSink": False, "mapIncludesSoilSink": False}


def test_info_json_timeseries_with_a_skipped_period(tmp_path):
    import shutil

    run = tmp_path / "run"
    shutil.copytree(TIMESERIES, run)
    (run / "period_2_plots" / "netCDF" / "posterior.nc").unlink()
    (run / "period_4_plots" / "netCDF" / "avkern.nc").unlink()
    r = invoke("info", str(run), "--json", "--config", str(EXAMPLE_CONFIG))
    assert r.exit_code == 0, r.output
    d = _strict_json(r.stdout)
    assert (d["kind"], d["mode"], d["gaps"]) == ("run", "timeseries", [3])
    assert d["periods"] == [
        {"label": "period_1", "number": 1, "start": "2023-01-01", "end": "2023-02-01",
         "skipped": False, "skipReason": None, "missingFiles": []},
        {"label": "period_2", "number": 2, "start": "2023-02-01", "end": "2023-03-01",
         "skipped": True, "skipReason": "missing posterior.nc", "missingFiles": ["posterior.nc"]},
        {"label": "period_4", "number": 4, "start": "2023-04-01", "end": "2023-05-01",
         "skipped": False, "skipReason": None, "missingFiles": ["avkern.nc"]},
    ]
    assert (d["start"], d["end"]) == ("2023-01-01", "2023-05-01")
    assert d["stateVector"] == {"period": "period_1", "elements": 359, "roiElements": 351, "bufferElements": 8}
    assert d["roiGrid"] == {"rows": 18, "cols": 29}
    assert (d["grid"]["rows"], d["grid"]["cols"]) == (60, 63)
    assert d["ensemble"] is False
    # The example config sets the cadence and lists the sectors.
    assert (d["cadence"], d["cadenceSource"]) == ("monthly", "config")
    assert d["sectorsSource"] == "config"
    assert [s["id"] for s in d["sectors"]][:3] == ["total", "coal", "oil"]
    assert d["total"] == {"numbersIncludeSoilSink": False, "mapIncludesSoilSink": False}


def test_info_json_dashboard(tmp_path, monkeypatch):
    from imi_dashboard import pack

    monkeypatch.setattr(pack, "_utc_now", lambda: "2026-11-04T10:15:30Z")
    site = tmp_path / "site"
    assert invoke("convert", str(TIMESERIES), str(site), "-q").exit_code == 0
    r = invoke("info", str(site), "--json")
    assert r.exit_code == 0, r.output
    assert _strict_json(r.stdout) == {
        "schemaVersion": 1,
        "kind": "dashboard",
        "path": str(site),
        "provenance": {
            "converter": {"name": "imi-dashboard", "version": imi_dashboard.__version__},
            "convertedAt": "2026-11-04T10:15:30Z",
            "input": "timeseries",
        },
        "mode": "timeseries",
        "periodCount": 3,
        "periods": [
            {"label": "period_1", "number": 1, "start": "2023-01-01", "end": "2023-02-01"},
            {"label": "period_2", "number": 2, "start": "2023-02-01", "end": "2023-03-01"},
            {"label": "period_4", "number": 4, "start": "2023-04-01", "end": "2023-05-01"},
        ],
        # The grid as written: cropped to the region of interest.
        "grid": {"rows": 18, "cols": 29, "dlat": 0.25, "dlon": 0.3125,
                 "latMin": 43.75, "latMax": 48.0, "lonMin": 20.625, "lonMax": 29.375},
        "region": "roi",
        "total": {"numbersIncludeSoilSink": False, "mapIncludesSoilSink": False},
    }


def test_info_json_dashboard_without_provenance(tmp_path):
    site = _fake_dashboard(tmp_path)
    manifest = {"mode": "single", "region": "domain", "grid": {"rows": 23, "cols": 21, "dlat": float("nan")},
                "periods": [{"idx": 0, "number": 0, "label": "single"}]}
    (site / "data" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    r = invoke("info", str(site), "--json")
    assert r.exit_code == 0, r.output
    d = _strict_json(r.stdout)
    assert d["provenance"] is None and d["total"] is None
    assert (d["mode"], d["periodCount"], d["region"]) == ("single", 1, "domain")
    assert d["periods"] == [{"label": "single", "number": None, "start": None, "end": None}]
    # A NaN in the manifest becomes null; absent keys are null too.
    assert d["grid"] == {"rows": 23, "cols": 21, "dlat": None, "dlon": None,
                         "latMin": None, "latMax": None, "lonMin": None, "lonMax": None}


def test_info_json_keeps_messages_on_stderr(tmp_path):
    """A warning goes to stderr, stdout stays one JSON object; -q silences
    the warning and not the description."""
    import shutil

    run = tmp_path / "run"
    shutil.copytree(TIMESERIES, run)
    (run / "period_1_plots" / "viz_notebook_statistics.csv").write_bytes(b"")
    r = invoke("info", str(run), "--json")
    assert r.exit_code == 0, r.output
    assert "warning: " in r.stderr and "cannot be read as a CSV table" in r.stderr
    d = _strict_json(r.stdout)
    assert d["periods"][0]["start"] is None and d["start"] is None
    quiet = invoke("info", str(run), "--json", "-q")
    assert quiet.exit_code == 0
    assert quiet.stderr == "" and _strict_json(quiet.stdout) == d


# ── console output: errors, -v, -q ───────────────────────────────────────


def test_bad_input_folder_is_one_error_line(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = invoke("convert", str(empty), str(tmp_path / "out"))
    assert r.exit_code == 1
    assert r.stderr == (
        f"error: {empty}: not a single-period folder (no netCDF/ subdir) "
        "and no period_<N>_plots/ subdirs found\n"
    )
    assert "Traceback" not in r.output


def test_invalid_config_lists_each_bad_key(tmp_path):
    cfg = tmp_path / "bad.yaml"
    cfg.write_text('map:\n  max_percentile: "abc"\ncolour: red\n', encoding="utf-8")
    r = invoke("convert", str(TIMESERIES), str(tmp_path / "out"), "--config", str(cfg))
    assert r.exit_code == 1
    lines = r.stderr.splitlines()
    assert lines[0] == f"error: invalid config {cfg}:"
    assert "  map.max_percentile: Input should be a valid number, unable to parse string as a number (got 'abc')" in lines
    assert "  unknown key 'colour'" in lines
    assert not (tmp_path / "out").exists()


def test_unknown_key_suggests_the_closest_valid_one(tmp_path):
    cfg = tmp_path / "typo.yaml"
    cfg.write_text("area_nme: Romania\nmap:\n  rmap: reds\n", encoding="utf-8")
    r = invoke("info", str(TIMESERIES), "--config", str(cfg))
    assert r.exit_code == 1
    lines = r.stderr.splitlines()
    assert "  unknown key 'area_nme'; did you mean 'area_name'?" in lines
    assert "  unknown key 'map.rmap'; did you mean 'ramp'?" in lines


def test_sector_errors_name_the_sector_id(tmp_path):
    """A listed standard sector needs only its id; the errors of a
    non-standard one are located by its id, not by its list index."""
    cfg = tmp_path / "sectors.yaml"
    cfg.write_text("sectors:\n  - id: total\n  - id: coal\n  - id: flaring\n    imi_var: EmisCH4_Flaring\n", encoding="utf-8")
    r = invoke("info", str(TIMESERIES), "--config", str(cfg))
    assert r.exit_code == 1
    assert r.stderr.splitlines()[1:] == [
        "  sectors[flaring].label: Field required",
        "  sectors[flaring].color: Field required",
    ]


def test_invalid_yaml_is_reported_as_such(tmp_path):
    cfg = tmp_path / "broken.yaml"
    cfg.write_text("map: [unclosed\n", encoding="utf-8")
    r = invoke("info", str(TIMESERIES), "--config", str(cfg))
    assert r.exit_code == 1
    assert r.stderr.startswith(f"error: {cfg} is not valid YAML: ")


def test_debug_verbosity_lets_the_error_propagate(tmp_path):
    """With -vv the original exception escapes, so its traceback is printed."""
    empty = tmp_path / "empty"
    empty.mkdir()
    r = invoke("convert", str(empty), str(tmp_path / "out"), "-vv")
    assert r.exit_code == 1
    assert isinstance(r.exception, ValueError)
    assert "not a single-period folder" in str(r.exception)


def test_rebuild_reconverts_every_period(tmp_path):
    """A second run reuses the periods; with --rebuild it converts them all again."""
    out = tmp_path / "out"
    assert invoke("convert", str(TIMESERIES), str(out)).exit_code == 0
    again = invoke("convert", str(TIMESERIES), str(out))
    assert " · reused 3, rebuilt 0 -> " in again.stdout
    rebuilt = invoke("convert", str(TIMESERIES), str(out), "--rebuild", "-v", "--log-style", "screen")
    assert rebuilt.exit_code == 0, rebuilt.output
    assert "reused" not in rebuilt.stdout
    assert "full conversion: --rebuild given" in rebuilt.stderr.splitlines()


def test_verbose_and_quiet_are_exclusive(tmp_path):
    r = invoke("convert", str(TIMESERIES), str(tmp_path / "out"), "-v", "-q")
    assert r.exit_code == 2
    assert "cannot be combined" in r.output


def test_verbose_reports_periods_and_decisions(tmp_path):
    """-v names each period as it is converted or reused, and says why the
    converter chose a full conversion or an incremental update."""
    import os
    import shutil

    src = tmp_path / "input"
    shutil.copytree(TIMESERIES, src)
    out = tmp_path / "out"
    common = ("convert", str(src), str(out), "-v")

    first = invoke(*common)
    assert first.exit_code == 0, first.output
    lines = first.stderr.splitlines()
    assert f"found a timeseries run in {src}: 3 period(s) to convert" in lines
    assert "region roi: 8 of the 359 state-vector elements are buffer elements and are dropped" in lines
    assert f"full conversion: no readable data/manifest.json in {out}" in lines
    # On a full conversion the per-period line says `read`: the binaries are
    # written afterwards, in one go, reported by the `wrote` line.
    read = [line.split(" (")[0] for line in lines if line.startswith("[")]
    assert read == ["[1/3] period_1: read", "[2/3] period_2: read", "[3/3] period_4: read"]
    assert any(re.fullmatch(r"wrote 3 period file\(s\), manifest and colour-bar bounds \(\d+\.\d s\)", l) for l in lines)

    # An input newer than its binary is rebuilt, and the reason is given.
    netcdf = src / "period_2_plots" / "netCDF" / "posterior.nc"
    future = (out / "data" / "periods" / "0001.bin").stat().st_mtime + 10_000
    os.utime(netcdf, (future, future))
    second = invoke(*common)
    assert second.exit_code == 0, second.output
    lines = second.stderr.splitlines()
    assert f"incremental update of {out}: 2 of 3 period(s) reused, 1 to convert" in lines
    progress = [line for line in lines if line.startswith("[")]
    assert len(progress) == 1
    assert progress[0].startswith("[2/3] period_2: converted (its input files changed; ")

    # A different region cannot reuse the binaries.
    third = invoke(*common, "--region", "domain")
    assert third.exit_code == 0, third.output
    assert "full conversion: the existing dashboard has region 'roi', this run 'domain'" in third.stderr.splitlines()


def test_default_output_is_the_summary_and_warnings(tmp_path, monkeypatch):
    """Without -v: no progress lines, warnings as `warning: ...` on stderr,
    and the one-line summary on stdout."""
    from imi_dashboard import pack

    out = tmp_path / "out"
    r = invoke("convert", str(TIMESERIES), str(out))
    assert r.exit_code == 0, r.output
    # The summary ends with the command's total elapsed time.
    assert re.fullmatch(rf"converted timeseries run · 3 period\(s\) · grid 18x29 -> {re.escape(str(out))} \(\d+\.\d s\)\n", r.stdout)
    assert r.stderr == ""

    # An update with no app bundle to copy keeps the dashboard's own, and says so.
    monkeypatch.setattr(pack, "REPO_BUNDLE_DIR", tmp_path / "no_repo_dist")
    monkeypatch.setattr(pack, "PACKAGE_BUNDLE_DIR", tmp_path / "no_pkg_bundle")
    r = invoke("convert", str(TIMESERIES), str(out))
    assert r.exit_code == 0, r.output
    assert " · reused 3, rebuilt 0 -> " in r.stdout
    assert r.stderr.startswith(f"warning: no app bundle to copy, so {out} keeps the one it holds")
    assert "[1/3]" not in r.stderr


def test_quiet_silences_warnings_and_summary(tmp_path, monkeypatch):
    from imi_dashboard import pack

    assert invoke("convert", str(TIMESERIES), str(tmp_path / "out")).exit_code == 0
    monkeypatch.setattr(pack, "REPO_BUNDLE_DIR", tmp_path / "no_repo_dist")
    monkeypatch.setattr(pack, "PACKAGE_BUNDLE_DIR", tmp_path / "no_pkg_bundle")
    r = invoke("convert", str(TIMESERIES), str(tmp_path / "out"), "-q")
    assert r.exit_code == 0, r.output
    assert r.stdout == ""
    assert r.stderr == ""   # the warning that the app bundle is kept, silenced
    assert (tmp_path / "out" / "index.html").exists()


def test_truncated_netcdf_is_one_error_line(tmp_path):
    """A truncated posterior.nc leaves its period out; when it is the only
    period, that is one `error:` line naming the file and netCDF4's error,
    not a traceback."""
    import shutil

    src = tmp_path / "input"
    shutil.copytree(SAMPLES / "single_period", src)
    posterior = src / "netCDF" / "posterior.nc"
    posterior.write_bytes(posterior.read_bytes()[:5000])
    r = invoke("convert", str(src), str(tmp_path / "out"))
    assert r.exit_code == 1
    # (netCDF4 may add an unrelated import-time warning line the first time
    # it is loaded in the process.)
    errors = [line for line in r.stderr.splitlines() if not line.startswith("warning: ")]
    assert len(errors) == 1
    assert errors[0].startswith("error: no period can be read: single (posterior.nc: [Errno -101] NetCDF: HDF error")
    # OSError names the file with repr(), which on Windows doubles each
    # backslash; on POSIX the two spellings are the same check.
    assert str(posterior) in errors[0] or repr(str(posterior)) in errors[0]
    assert "Traceback" not in r.output


# ── console output: screen and log styles, --log-file, progress bar ──────

# rich renders for a legacy Windows console whenever the process's own console
# lacks VT support, which includes output to a pipe or to CliRunner: it draws
# the progress bar with "-" instead of "━", replaces rounded box corners (╭)
# with square ones (┌) and makes the output one column narrower. Elsewhere
# these are "━" and "╭", as before.
_BAR = "-" if detect_legacy_windows() else "━"
_BOX_CORNERS = ("╭", "┌")


def _unboxed(text: str) -> str:
    """`text` with rich's panel borders removed and runs of whitespace
    collapsed, so that a message reads the same however the panel wrapped it
    (its width depends on the platform and the terminal)."""
    return " ".join(re.sub(r"[─-╿]", " ", text).split())


LOG_LINE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ imi-dashboard (debug|info|warning|error|critical): ")


def _assert_log_format(text: str) -> list[str]:
    """Every line is a log-style record or a continuation (four spaces)."""
    lines = text.splitlines()
    assert lines
    for line in lines:
        assert LOG_LINE.match(line) or line.startswith("    "), line
    return lines


def test_auto_style_is_log_when_stderr_is_not_a_terminal(tmp_path, monkeypatch):
    monkeypatch.delenv("IMI_DASHBOARD_LOG_STYLE")
    cfg = tmp_path / "bad.yaml"
    cfg.write_text('map:\n  max_percentile: "abc"\ncolour: red\n', encoding="utf-8")
    r = invoke("info", str(TIMESERIES), "--config", str(cfg), "-v")
    assert r.exit_code == 1
    lines = _assert_log_format(r.stderr)
    assert lines[0].endswith(f" imi-dashboard info: imi-dashboard {imi_dashboard.__version__} info {TIMESERIES}")
    assert lines[1].endswith(f" imi-dashboard error: invalid config {cfg}:")
    # The config's problem list continues the error record, indented.
    assert "      unknown key 'colour'" in lines


def test_style_option_wins_over_the_variable(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    message = f"error: {empty}: not a single-period folder"

    r = invoke("info", str(empty))  # IMI_DASHBOARD_LOG_STYLE=screen (fixture)
    assert r.exit_code == 1 and r.stderr.startswith(message)

    r = invoke("info", str(empty), "--log-style", "log")
    assert r.exit_code == 1
    assert LOG_LINE.match(r.stderr) and f" imi-dashboard {message}" in r.stderr

    monkeypatch.setenv("IMI_DASHBOARD_LOG_STYLE", "log")
    r = invoke("info", str(empty), "--log-style", "screen")
    assert r.exit_code == 1 and r.stderr.startswith(message)


def test_unknown_style_is_a_usage_error(tmp_path, monkeypatch):
    r = invoke("info", str(tmp_path), "--log-style", "fancy")
    assert r.exit_code == 2
    assert "must be one of auto, screen, log, got 'fancy'" in _unboxed(r.output)
    monkeypatch.setenv("IMI_DASHBOARD_LOG_STYLE", "bogus")
    r = invoke("info", str(tmp_path))
    assert r.exit_code == 2
    assert "IMI_DASHBOARD_LOG_STYLE" in _unboxed(r.output) and "got 'bogus'" in _unboxed(r.output)


def test_no_color_removes_the_colour_of_the_screen_style(tmp_path, monkeypatch):
    """FORCE_COLOR makes rich colour the `error:` prefix even though stderr
    is not a terminal here; NO_COLOR then removes the colour again."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("FORCE_COLOR", "1")
    coloured = invoke("info", str(empty), "--log-style", "screen")
    assert coloured.exit_code == 1 and "\x1b[" in coloured.stderr
    monkeypatch.setenv("NO_COLOR", "1")
    plain = invoke("info", str(empty), "--log-style", "screen")
    assert plain.exit_code == 1
    assert "\x1b[" not in plain.stderr
    assert plain.stderr.startswith(f"error: {empty}: not a single-period folder")


def test_log_file_records_everything_whatever_stderr_shows(tmp_path):
    out = tmp_path / "out"
    log_file = tmp_path / "convert.log"
    common = ("convert", str(TIMESERIES), str(out), "--log-file", str(log_file))

    first = invoke(*common)
    assert first.exit_code == 0, first.output
    # Default verbosity: no progress lines on stderr ...
    assert not [l for l in first.stderr.splitlines() if l.startswith(("[", "found ", "imi-dashboard "))]
    # ... but the file has them, in the log style, after the opening record.
    lines = _assert_log_format(log_file.read_text(encoding="utf-8"))
    assert lines[0].endswith(f" info: imi-dashboard {imi_dashboard.__version__} convert {TIMESERIES} -> {out}")
    assert any(l.endswith(" info: [2/3] period_2: read") or " info: [2/3] period_2: read (" in l for l in lines)

    # A second run appends; -q leaves stderr empty, the file still gets the
    # debug detail (each reused period).
    second = invoke(*common, "-q")
    assert second.exit_code == 0, second.output
    assert second.stderr == "" and second.stdout == ""
    lines = _assert_log_format(log_file.read_text(encoding="utf-8"))
    assert sum(" convert " in l and " -> " in l and " info: imi-dashboard " in l for l in lines) == 2
    assert any(l.endswith(" debug: [1/3] period_1: reused 0000.bin") for l in lines)

    # A user error: one line on stderr, the traceback in the file.
    empty = tmp_path / "empty"
    empty.mkdir()
    third = invoke("convert", str(empty), str(tmp_path / "out2"), "--log-file", str(log_file))
    assert third.exit_code == 1
    assert len(third.stderr.splitlines()) == 1 and third.stderr.startswith("error: ")
    text = log_file.read_text(encoding="utf-8")
    _assert_log_format(text)
    assert " debug: traceback of the error that follows\n    Traceback (most recent call last):\n" in text
    assert text.splitlines()[-1].endswith(f" error: {empty}: not a single-period folder (no netCDF/ subdir) and no period_<N>_plots/ subdirs found")
    assert " critical: " not in text


def test_log_file_records_the_result_once(tmp_path):
    """The summary line (and info's description) goes to the file as one
    info record, but to stdout only on the terminal side, even at -v."""
    log_file = tmp_path / "run.log"
    r = invoke("convert", str(TIMESERIES), str(tmp_path / "out"), "-v", "--log-file", str(log_file))
    assert r.exit_code == 0, r.output
    summary = r.stdout.strip()
    assert summary.startswith("converted timeseries run · 3 period(s) · grid ") and len(r.stdout.splitlines()) == 1
    assert "converted timeseries run" not in r.stderr
    lines = _assert_log_format(log_file.read_text(encoding="utf-8"))
    assert [l for l in lines if "converted timeseries run" in l] == [lines[-1]]
    assert lines[-1].endswith(f" info: {summary}")

    r = invoke("info", str(TIMESERIES), "-v", "--log-file", str(log_file))
    assert r.exit_code == 0, r.output
    description = r.stdout.rstrip("\n").splitlines()
    assert len(description) > 1 and description[0] not in r.stderr
    text = log_file.read_text(encoding="utf-8")
    _assert_log_format(text)
    # One record: the first line after the prefix, the rest indented.
    assert text.endswith(f" info: {description[0]}\n" + "".join(f"    {l}\n" for l in description[1:]))


def test_unexpected_error_goes_to_the_log_file_and_propagates(tmp_path, monkeypatch):
    import typer

    from imi_dashboard import pack

    def broken(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(pack, "convert", broken)
    log_file = tmp_path / "crash.log"
    without = invoke("convert", str(TIMESERIES), str(tmp_path / "a"))
    logged = invoke("convert", str(TIMESERIES), str(tmp_path / "b"), "--log-file", str(log_file))
    # Unchanged by --log-file: the exception propagates (typer, or Python,
    # prints its traceback), nothing is added to stderr.
    for r in (without, logged):
        assert r.exit_code == 1
        assert isinstance(r.exception, RuntimeError) and str(r.exception) == "boom"
    assert logged.stderr == without.stderr and "boom" not in logged.stderr
    text = log_file.read_text(encoding="utf-8")
    lines = _assert_log_format(text)
    critical = [i for i, l in enumerate(lines) if " imi-dashboard critical: " in l]
    assert len(critical) == 1 and lines[critical[0]].endswith(" critical: unexpected error: RuntimeError: boom")
    assert lines[critical[0] + 1] == "    Traceback (most recent call last):"
    assert lines[-1] == "    RuntimeError: boom"

    # typer's own exit is not a crash.
    def exits(**kwargs):
        raise typer.Exit(3)

    monkeypatch.setattr(pack, "convert", exits)
    r = invoke("convert", str(TIMESERIES), str(tmp_path / "c"), "--log-file", str(log_file))
    assert r.exit_code == 3
    assert log_file.read_text(encoding="utf-8").count(" critical: ") == 1


def test_unwritable_log_file_is_one_error_line(tmp_path):
    r = invoke("info", str(tmp_path), "--log-file", str(tmp_path / "no_such_dir" / "x.log"))
    assert r.exit_code == 1
    assert r.stderr.startswith("error: [Errno ") and "no_such_dir" in r.stderr
    assert "Traceback" not in r.output


def test_progress_bar_only_in_the_screen_style_on_a_terminal(tmp_path, monkeypatch):
    """stderr is never a terminal under CliRunner; FORCE_COLOR makes rich
    treat it as one, so the bar can be seen (positive control). It is then
    absent in the log style and with -q."""
    monkeypatch.setenv("FORCE_COLOR", "1")
    single = str(SAMPLES / "single_period")

    shown = invoke("convert", single, str(tmp_path / "a"))
    assert shown.exit_code == 0, shown.output
    assert _BAR in shown.stderr and "1/1" in shown.stderr

    logged = invoke("convert", single, str(tmp_path / "b"), "--log-style", "log")
    assert logged.exit_code == 0, logged.output
    assert "━" not in logged.stderr and "\x1b[" not in logged.stderr
    assert "1/1" not in logged.stderr  # the bar's counter, whichever glyph draws it

    quiet = invoke("convert", single, str(tmp_path / "c"), "-q")
    assert quiet.exit_code == 0, quiet.output
    assert quiet.stderr == ""


_CONVERT_USAGE = "Usage: imi-dashboard convert [OPTIONS] {INPUT_FOLDER} {OUTPUT_FOLDER}"


def test_usage_errors_read_like_the_other_errors(tmp_path, monkeypatch):
    """A usage error is `error: <message>` (lower-case, no final full stop),
    then the usage line and the --help hint, in the style of every other
    error, with exit status 2; not click's `Error: ...` or typer's box."""
    expected = [
        "error: invalid value for --region: region must be 'domain' or 'roi'",
        _CONVERT_USAGE,
        "Try 'imi-dashboard convert --help' for help.",
    ]
    # --log-style given before the bad value is followed.
    r = invoke("convert", str(tmp_path), "out", "--log-style", "screen", "--region", "bogus")
    assert r.exit_code == 2
    assert r.stderr.splitlines() == expected
    # Raised by click before the command runs: the environment variable sets the style.
    monkeypatch.setenv("IMI_DASHBOARD_LOG_STYLE", "screen")
    r = invoke("info", str(tmp_path / "nope"))
    assert r.exit_code == 2
    # click quotes the path with repr() (so a Windows path has doubled
    # backslashes). The word before it is click's: "Directory" up to typer
    # 0.28, "dir" from 0.29.
    first = r.stderr.splitlines()[0]
    assert first.startswith("error: invalid value for 'INPUT_FOLDER': ")
    assert first.endswith(f" {str(tmp_path / 'nope')!r} does not exist")
    r = invoke("info")
    assert r.stderr.splitlines()[0] == "error: missing argument 'INPUT_FOLDER'"
    # The log style: one record, its continuation lines indented.
    monkeypatch.setenv("IMI_DASHBOARD_LOG_STYLE", "log")
    r = invoke("convert", str(tmp_path), "out", "--force")
    lines = r.stderr.splitlines()
    assert r.exit_code == 2
    assert re.fullmatch(r"\S+Z imi-dashboard error: no such option: --force", lines[0])
    assert lines[1:] == [f"    {_CONVERT_USAGE}", "    Try 'imi-dashboard convert --help' for help."]
    assert not any(c in r.stderr for c in _BOX_CORNERS) and "Error" not in r.stderr


def test_console_script_usage_errors_have_no_box(tmp_path):
    """The same through main() (a subprocess: CliRunner does not go through
    it), where typer would otherwise draw its box in the screen style."""
    r = subprocess.run(
        [sys.executable, "-m", "imi_dashboard.cli", "convert", str(tmp_path), "out", "--region", "bogus"],
        capture_output=True, text=True, encoding="utf-8", cwd=tmp_path,
        env={**os.environ, "IMI_DASHBOARD_LOG_STYLE": "screen", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"},
    )
    assert r.returncode == 2
    assert r.stderr.splitlines()[0] == "error: invalid value for --region: region must be 'domain' or 'roi'"
    assert not any(c in r.stderr for c in _BOX_CORNERS)
