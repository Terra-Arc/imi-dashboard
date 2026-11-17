"""Browser tests: convert the two sample runs with the real web app, serve
each dashboard, open it in headless Chromium (Playwright) and check what a
reader sees against the values in the sample files.

Prerequisites, beyond the `dev` extra:

- the `browser` extra (`pip install -e "tools/imi-dashboard[browser]"`),
  which brings pytest-playwright; without it this module is skipped;
- Playwright's Chromium (`playwright install --with-deps chromium`);
- a built web app in web/dist/ (`npm --prefix web run build`).

When one of the last two is missing, every test is skipped with the reason,
unless the environment variable IMI_DASHBOARD_REQUIRE_BROWSER is set (as in
CI's browser job), in which case they fail instead. A web/dist/ older than
the web source always fails them: the tests would otherwise check the web
app as it was at the last build, not the source being worked on.

The browser never reaches the network: map tiles are answered with a blank
tile, and any other request outside the local server is blocked and fails the
test (the fonts are part of the bundle, so they come from the local server). A
test also fails when the page logs a console error or throws, unless it
expected that.
"""

from __future__ import annotations

import base64
import json
import os
import re
import struct
import threading
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

import pytest

pytest.importorskip(
    "pytest_playwright",
    reason='needs the browser extra: pip install -e "tools/imi-dashboard[browser]"',
)

from playwright.sync_api import Browser, BrowserContext, ConsoleMessage, Error, Page, Route, expect  # noqa: E402

from imi_dashboard import pack  # noqa: E402
from imi_dashboard.serve import make_server, server_url  # noqa: E402

from .conftest import EXAMPLE_CONFIG, REPO_ROOT, SINGLE, TIMESERIES, invoke  # noqa: E402

WEB_DIR = REPO_ROOT / "web"
WEB_DIST = WEB_DIR / "dist"
OSM_TILES = "https://tile.openstreetmap.org/"
# The tile server of the dashboard converted with a custom `map.tiles`.
OTHER_TILES = "https://tiles.example.test/"


def _missing(reason: str) -> NoReturn:
    """Skip for a missing prerequisite, or fail when CI requires the tests."""
    if os.environ.get("IMI_DASHBOARD_REQUIRE_BROWSER"):
        pytest.fail(reason)
    pytest.skip(reason)


def _blank_png(width: int, height: int) -> bytes:
    """A fully transparent RGBA PNG, standing in for a basemap tile."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    rows = b"".join(b"\x00" + b"\x00" * 4 * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


BLANK_TILE = _blank_png(256, 256)


# --- browser set-up (overrides of pytest-playwright fixtures) -------------


@pytest.fixture(scope="session")
def browser(launch_browser: Callable[[], Browser]) -> Iterator[Browser]:
    """pytest-playwright's `browser`, with a missing or unstartable Chromium
    reported as a missing prerequisite instead of an error in every test."""
    try:
        b = launch_browser()
    except Error as err:
        first_line = str(err).strip().splitlines()[0]
        _missing(
            f"Playwright's Chromium does not start ({first_line}); install it "
            "with `playwright install --with-deps chromium`"
        )
    yield b
    b.close()


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    """A fixed window size and locale, so layout and number formatting
    ("1,399") do not depend on the machine, and a time zone seven or eight
    hours behind UTC, so a date formatted in local time instead of UTC shows
    the wrong day (IMI periods start at midnight UTC)."""
    return {
        **browser_context_args,
        "viewport": {"width": 1440, "height": 900},
        "locale": "en-US",
        "timezone_id": "America/Los_Angeles",
    }


# --- dashboards and servers ----------------------------------------------


@pytest.fixture(scope="session")
def web_bundle() -> Path:
    """The clone's built web app, checked to be present and up to date."""
    index = WEB_DIST / "index.html"
    if not index.is_file():
        _missing("web/dist/ is not built; run `npm --prefix web run build`")
    newest = pack._newest_build_input(WEB_DIR)
    if newest is not None and newest[1] > index.stat().st_mtime:
        changed = newest[0].relative_to(REPO_ROOT).as_posix()
        pytest.fail(
            f"web/dist/ is older than {changed}, so these tests would check "
            "the web app as it was at the last build; run "
            "`npm --prefix web run build`"
        )
    return WEB_DIST


@pytest.fixture(scope="module")
def dashboards(tmp_path_factory: pytest.TempPathFactory, web_bundle: Path) -> dict[str, Path]:
    """The two samples converted as build.sh converts them, but with the
    real web app (the autouse fake bundle is bypassed by --app-bundle); and the
    single-period sample twice more, with another tile server and with no
    base map."""
    root = tmp_path_factory.mktemp("browser")
    other_tiles = root / "other-tiles.yaml"
    other_tiles.write_text(
        "map:\n"
        f"  tiles: {OTHER_TILES}{{z}}/{{x}}/{{y}}.png\n"
        "  tiles_attribution: © Example Tiles\n"
        "  attribution_compact: false\n",
        encoding="utf-8",
    )
    no_tiles = root / "no-tiles.yaml"
    no_tiles.write_text("map:\n  tiles: none\n", encoding="utf-8")
    # The soil sink, all of whose values are negative, as a sector of its own.
    soil = root / "soil.yaml"
    soil.write_text("sectors:\n  - id: total\n  - id: soil_absorb\n", encoding="utf-8")
    # The user's own branding, with no logo, name or tagline given.
    custom = root / "custom.yaml"
    custom.write_text("header_brand: custom\n", encoding="utf-8")
    runs = {
        "single": [str(SINGLE)],
        "romania": [str(TIMESERIES), "--config", str(EXAMPLE_CONFIG)],
        "other_tiles": [str(SINGLE), "--config", str(other_tiles)],
        "no_tiles": [str(SINGLE), "--config", str(no_tiles)],
        "soil": [str(SINGLE), "--config", str(soil)],
        "custom": [str(SINGLE), "--config", str(custom)],
    }
    out = {}
    for name, (src, *options) in runs.items():
        dest = root / name
        r = invoke("convert", src, str(dest), *options, "--app-bundle", str(web_bundle))
        assert r.exit_code == 0, r.output
        out[name] = dest
    return out


def _serve(folder: Path) -> Iterator[str]:
    server = make_server(folder, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server_url(server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture(scope="module")
def single_url(dashboards: dict[str, Path]) -> Iterator[str]:
    yield from _serve(dashboards["single"])


@pytest.fixture(scope="module")
def romania_url(dashboards: dict[str, Path]) -> Iterator[str]:
    yield from _serve(dashboards["romania"])


@pytest.fixture(scope="module")
def other_tiles_url(dashboards: dict[str, Path]) -> Iterator[str]:
    yield from _serve(dashboards["other_tiles"])


@pytest.fixture(scope="module")
def no_tiles_url(dashboards: dict[str, Path]) -> Iterator[str]:
    yield from _serve(dashboards["no_tiles"])


@pytest.fixture(scope="module")
def soil_url(dashboards: dict[str, Path]) -> Iterator[str]:
    yield from _serve(dashboards["soil"])


@pytest.fixture(scope="module")
def custom_url(dashboards: dict[str, Path]) -> Iterator[str]:
    yield from _serve(dashboards["custom"])


# --- what the page did ----------------------------------------------------


@dataclass
class BrowserLog:
    """Console errors, uncaught exceptions, blocked requests and map tile
    requests of one test."""

    errors: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    tiles: list[str] = field(default_factory=list)

    def expect_errors(self, pattern: str) -> None:
        """Drop the logged errors matching `pattern`, which the test caused
        on purpose; fails if there is none."""
        kept = [e for e in self.errors if not re.search(pattern, e)]
        assert len(kept) < len(self.errors), f"no console error matched {pattern!r}"
        self.errors[:] = kept


@pytest.fixture(autouse=True)
def browser_log(context: BrowserContext, page: Page) -> Iterator[BrowserLog]:
    log = BrowserLog()

    def route(r: Route) -> None:
        url = r.request.url
        if url.startswith("http://127.0.0.1:"):
            r.continue_()
        elif url.startswith((OSM_TILES, OTHER_TILES)):
            log.tiles.append(url)
            r.fulfill(status=200, content_type="image/png", body=BLANK_TILE)
        else:
            log.blocked.append(url)
            r.abort()

    def console(msg: ConsoleMessage) -> None:
        if msg.type == "error":
            log.errors.append(f"{msg.text} [{msg.location.get('url', '')}]")

    context.route("**/*", route)
    page.on("console", console)
    page.on("pageerror", lambda exc: log.errors.append(f"uncaught: {exc}"))
    yield log
    problems = log.errors + [f"request outside the dashboard: {u}" for u in log.blocked]
    assert not problems, "\n".join(problems)


# --- helpers --------------------------------------------------------------


def stat(page: Page, label: str):
    """The value of the scalar card whose label is `label`."""
    card = page.locator(".scalars .stat").filter(has=page.locator(".stat-label", has_text=label))
    return card.locator(".stat-num")


def stat_sub(page: Page, label: str):
    """The subtext of the scalar card whose label is `label`."""
    card = page.locator(".scalars .stat").filter(has=page.locator(".stat-label", has_text=label))
    return card.locator(".stat-sub")


def open_dashboard(page: Page, url: str) -> None:
    page.goto(url)
    expect(page.locator(".app")).to_be_visible()
    # The first period's rasters have arrived once the loading note is gone.
    expect(page.locator(".map-msg")).to_have_count(0)


_RED_FRACTION_JS = """async (b64) => {
  const img = new Image();
  img.src = "data:image/png;base64," + b64;
  await img.decode();
  const c = document.createElement("canvas");
  c.width = img.width;
  c.height = img.height;
  const ctx = c.getContext("2d");
  ctx.drawImage(img, 0, 0);
  const d = ctx.getImageData(0, 0, c.width, c.height).data;
  let red = 0;
  for (let i = 0; i < d.length; i += 4) {
    if (d[i] - Math.max(d[i + 1], d[i + 2]) > 60) red++;
  }
  return red / (d.length / 4);
}"""


def red_fraction(page: Page) -> float:
    """Fraction of the map's pixels that are clearly red (red channel more
    than 60 above both green and blue), with the overlays (colour bar,
    load note) hidden. The background, #F4F7FA, and the blank tiles have
    none; the default "reds" colour ramp gives them to cells with emissions."""
    png = page.locator(".map-stage").first.screenshot(
        style=".map-stage > :not(.map-canvas-host) { visibility: hidden !important; }",
    )
    return float(page.evaluate(_RED_FRACTION_JS, base64.b64encode(png).decode()))


# --- single period (Permian Basin sample) --------------------------------


def test_single_period_layout(page: Page, single_url: str):
    open_dashboard(page, single_url)
    expect(page.locator(".app")).to_have_class(re.compile(r"\bmode-single\b"))
    expect(page.locator(".hdr-h1")).to_have_text("Methane emissions")
    expect(page.locator(".hdr-crumb-muted")).to_have_text("Single inversion")
    # Period 2021-05-01 to 2021-06-01; IMI's end date is exclusive.
    expect(page.locator(".hdr-sub")).to_have_text("Single period inversion · May 1, 2021 → May 31, 2021")
    expect(page.locator(".period-nav-date")).to_have_text("May 1, 2021→May 31")
    # No timeseries chart, playback or period stepping with one period.
    expect(page.locator(".ts-panel")).to_have_count(0)
    expect(page.locator(".ts-play")).to_have_count(0)
    expect(page.get_by_role("button", name="Next period", exact=True)).to_have_count(0)


def test_single_period_scalars_and_sectors(page: Page, single_url: str):
    open_dashboard(page, single_url)
    # Total: posterior 0.6023, prior 0.4087, ensemble 0.4939-0.7823 Mt CH4/yr.
    expect(stat(page, "Total methane emissions")).to_have_text("0.602")
    expect(stat(page, "Prior estimate")).to_have_text("0.409")
    expect(stat(page, "Ensemble range")).to_have_text("0.494–0.782")
    # IMI 2.3.1: the total's numbers and its map leave out the soil sink.
    expect(stat_sub(page, "Total methane emissions")).to_have_text("Posterior estimate, excluding the soil sink")
    expect(stat_sub(page, "Prior estimate")).to_have_text("Bottom-up inventory, excluding the soil sink")
    expect(stat_sub(page, "Ensemble range")).to_have_text("Min – max across the run ensemble, excluding the soil sink")
    expect(page.locator(".sc-hd .overline")).to_have_text("Bottom-up inventory vs inversion estimate, excluding the soil sink")
    expect(page.locator(".map-caption .overline")).to_have_text("Total methane emissions, excluding the soil sink")

    oil = page.get_by_role("tab", name=re.compile(r"^Oil\b"))
    oil.click()
    expect(oil).to_have_attribute("aria-selected", "true")
    # Oil: posterior 0.1826 Mt CH4/yr, 30.3% of the total's 0.6023.
    expect(stat(page, "Oil methane emissions")).to_have_text("0.183")
    expect(stat(page, "Share of total")).to_have_text("30.3")
    # Only the total's own values carry the soil-sink label.
    expect(stat_sub(page, "Oil methane emissions")).to_have_text("Posterior estimate")
    expect(stat_sub(page, "Share of total")).to_have_text("Total 0.602 Mt CH₄/yr, excluding the soil sink")
    expect(page.locator(".map-caption .overline")).to_have_text("Oil methane emissions")
    expect(stat(page, "Ensemble range")).to_have_text("0.145–0.248")


def test_single_period_map_is_drawn(page: Page, single_url: str):
    open_dashboard(page, single_url)
    expect(page.locator(".map-canvas-host canvas").first).to_be_visible()
    # About a fifth of the map's pixels when this test was written; none
    # when the raster layer is missing.
    assert red_fraction(page) > 0.05


def drag_map(page: Page) -> None:
    """Drag the overview map 120 px to the left."""
    box = page.locator(".map-stage").first.bounding_box()
    assert box is not None
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x - 120, y, steps=8)
    page.mouse.up()


def test_default_base_map_is_openstreetmap_with_compact_attribution(page: Page, single_url: str, browser_log: BrowserLog):
    """Without map settings the tiles come from OpenStreetMap, and the
    compact attribution shows its text until the map is first dragged."""
    with page.expect_request(lambda r: r.url.startswith(OSM_TILES)):
        open_dashboard(page, single_url)
    attribution = page.locator(".map-stage .maplibregl-ctrl-attrib").first
    expect(attribution).to_have_class(re.compile(r"\bmaplibregl-compact-show\b"))
    expect(attribution).to_contain_text("© OpenStreetMap contributors")
    drag_map(page)
    expect(attribution).not_to_have_class(re.compile(r"\bmaplibregl-compact-show\b"))
    assert not [u for u in browser_log.tiles if not u.startswith(OSM_TILES)]


def test_configured_tile_server_and_full_attribution(page: Page, other_tiles_url: str, browser_log: BrowserLog):
    """map.tiles replaces OpenStreetMap; attribution_compact: false keeps the
    attribution text visible after a drag."""
    with page.expect_request(lambda r: r.url.startswith(OTHER_TILES)):
        open_dashboard(page, other_tiles_url)
    attribution = page.locator(".map-stage .maplibregl-ctrl-attrib").first
    expect(attribution).not_to_have_class(re.compile(r"\bmaplibregl-compact\b"))
    drag_map(page)
    expect(attribution.locator(".maplibregl-ctrl-attrib-inner")).to_be_visible()
    expect(attribution).to_contain_text("© Example Tiles")
    expect(attribution).not_to_contain_text("OpenStreetMap")
    assert browser_log.tiles and all(u.startswith(OTHER_TILES) for u in browser_log.tiles)


def test_no_base_map(page: Page, no_tiles_url: str, browser_log: BrowserLog):
    """map.tiles: none requests no tiles and shows no attribution; the data
    layer is still drawn."""
    open_dashboard(page, no_tiles_url)
    assert red_fraction(page) > 0.05
    expect(page.locator(".maplibregl-ctrl-attrib")).to_have_count(0)
    assert browser_log.tiles == []


def test_fonts_come_from_the_bundle(page: Page, single_url: str):
    """The three font families load from the dashboard's own assets/ (any
    request outside the local server fails the test, see browser_log)."""
    open_dashboard(page, single_url)
    loaded = page.evaluate(
        """async () => {
          await document.fonts.ready;
          return [...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family);
        }"""
    )
    families = {f.strip("'\"") for f in loaded}
    assert {"DM Sans Variable", "Inter Variable", "JetBrains Mono Variable"} <= families


def test_negative_sector_is_drawn_on_its_own_range(page: Page, soil_url: str):
    """The soil sink's values are all negative (−0.058 to −0.047 kg km⁻² h⁻¹
    over the Permian sample's region of interest). Its colour bar spans them,
    instead of collapsing to "≤0 … 1.0e-12" by clamping the scale at 0, and
    the map is coloured. The log scale cannot show such values, so the
    sector stays linear."""
    open_dashboard(page, soil_url)
    page.get_by_role("button", name="+1 more").click()
    page.get_by_role("tab", name=re.compile(r"^Soil absorption")).click()
    expect(page.locator(".map-caption .overline")).to_have_text("Soil absorption methane emissions")
    labels = page.locator(".map-stage .cbar-tick-label").all_inner_texts()
    assert labels[0] == "-0.06" and labels[-1] == "-0.05", labels
    assert red_fraction(page) > 0.05
    expect(page.locator(".map-stage .cbar-scale")).to_have_count(0)


def test_chart_text_uses_the_bundled_fonts(page: Page, romania_url: str):
    """The chart's SVG text asks for the font tokens, which name the bundled
    "… Variable" families; the plain family names it used to ask for are not
    registered, so the text fell back to the browser's default fonts."""
    open_dashboard(page, romania_url)
    families = page.evaluate(
        """() => [...document.querySelectorAll('.ts-panel svg text')]
             .map((t) => getComputedStyle(t).fontFamily.split(',')[0].replace(/["']/g, '').trim())"""
    )
    assert families, "no chart text found"
    assert set(families) <= {"Inter Variable", "JetBrains Mono Variable", "DM Sans Variable"}, set(families)


def test_default_branding_is_terra_arc_in_the_header(page: Page, single_url: str):
    open_dashboard(page, single_url)
    expect(page.locator(".hdr-brandname")).to_have_text("Terra Arc")
    expect(page.locator(".ftr-col-mark")).to_have_class(re.compile(r"\bis-hidden\b"))
    expect(page.locator(".ftr-mark")).to_have_count(0)


def test_custom_branding_without_a_logo_credits_the_software_in_the_footer(page: Page, custom_url: str):
    """header_brand: custom with no logo, name or tagline: the header has no
    brand at all (no placeholder), and the footer credits the software with
    a link to the project."""
    open_dashboard(page, custom_url)
    expect(page.locator(".hdr")).to_have_class(re.compile(r"\bhdr-no-brand\b"))
    expect(page.locator(".hdr-brand")).to_have_count(0)
    expect(page.locator(".hdr-h1")).to_be_visible()
    expect(page.locator(".ftr-col-mark .ftr-lbl")).to_have_text("Made with IMI Dashboard by")
    mark = page.locator("a.ftr-mark")
    expect(mark).to_have_attribute("href", "https://github.com/Terra-Arc/imi-dashboard")
    expect(mark.locator(".ftr-mark-name")).to_have_text("Terra Arc")


def test_footer_shows_provenance(page: Page, single_url: str, dashboards: dict[str, Path]):
    prov = json.loads((dashboards["single"] / "data" / "manifest.json").read_text())["provenance"]
    day = datetime.strptime(prov["convertedAt"], "%Y-%m-%dT%H:%M:%SZ")
    version = prov["converter"]["version"].split("+")[0]
    open_dashboard(page, single_url)
    expect(page.locator(".ftr-meta-wrap")).to_have_text(
        f"Converted {day:%b} {day.day}, {day.year} · imi-dashboard {version}"
    )


# --- timeseries (Romania sample: periods 1, 2 and 4) ---------------------


def test_timeseries_layout_and_first_period(page: Page, romania_url: str):
    open_dashboard(page, romania_url)
    expect(page.locator(".app")).to_have_class(re.compile(r"\bmode-ts\b"))
    expect(page.locator(".hdr-h1")).to_have_text("Methane emissions over Romania")
    expect(page).to_have_title("Methane emissions over Romania")  # display_name
    expect(page.locator(".hdr-sub")).to_have_text(
        "Monthly Kalman-filter inversion · Jan 1, 2023 – Apr 30, 2023 · demonstration data, not valid estimates"
    )
    expect(page.locator(".ftr-col-dis .ftr-body")).to_contain_text("known to be wrong")
    # The default attribution credits the Copernicus data with the years the
    # run covers ({years}: Jan-Apr 2023).
    expect(page.locator(".ftr-col-ack .ftr-body")).to_contain_text("contains modified Copernicus Sentinel data (2023).")
    expect(page.locator(".ts-panel svg").first).to_be_visible()
    # The app opens on the first period and shows folder numbers, so the
    # missing period 3 leaves the count at 4.
    expect(page.locator(".period-nav-date")).to_have_text("Jan 1, 2023→Jan 31")
    expect(page.locator(".period-nav-meta")).to_have_text("1 of 4")
    expect(page.get_by_role("button", name="Previous period", exact=True)).to_be_disabled()
    # Total without the soil sink, summed from the maps (this older IMI's
    # CSV totals include it): posterior 0.9486, prior 1.3168 Mt CH4/yr; this
    # run has no ensemble.
    expect(stat(page, "Total methane emissions")).to_have_text("0.949")
    expect(stat(page, "Prior estimate")).to_have_text("1.32")
    expect(stat(page, "Ensemble range")).to_have_text("—")
    expect(page.locator(".stat-sub", has_text="This run has no ensemble")).to_be_visible()
    expect(stat_sub(page, "Total methane emissions")).to_have_text("Posterior estimate, excluding the soil sink")
    # Kalman mode: period 1 starts from the inventory.
    expect(stat_sub(page, "Prior estimate")).to_have_text("Bottom-up inventory, excluding the soil sink")
    expect(page.locator(".map-caption .overline")).to_have_text("Total methane emissions, excluding the soil sink")
    expect(page.locator(".ts-panel .panel-title")).to_have_text(re.compile(r", excluding the soil sink$"))


def test_timeseries_period_navigation(page: Page, romania_url: str):
    open_dashboard(page, romania_url)
    nav_next = page.get_by_role("button", name="Next period", exact=True)
    nav_next.click()
    expect(page.locator(".period-nav-date")).to_have_text("Feb 1, 2023→Feb 28")
    expect(page.locator(".period-nav-meta")).to_have_text("2 of 4")
    expect(stat(page, "Total methane emissions")).to_have_text("0.947")
    # Kalman mode: every later period starts from the previous posterior.
    expect(stat_sub(page, "Prior estimate")).to_have_text("From the previous period's estimate, excluding the soil sink")
    nav_next.click()
    # Period 3 is missing: the step from 2 goes straight to 4.
    expect(page.locator(".period-nav-date")).to_have_text("Apr 1, 2023→Apr 30")
    expect(page.locator(".period-nav-meta")).to_have_text("4 of 4")
    expect(stat(page, "Total methane emissions")).to_have_text("0.879")
    expect(nav_next).to_be_disabled()
    expect(page.locator(".map-msg")).to_have_count(0)
    # The left arrow key steps back.
    page.locator(".hdr-h1").click()
    page.keyboard.press("ArrowLeft")
    expect(page.locator(".period-nav-meta")).to_have_text("2 of 4")


def test_timeseries_playback_stops_on_the_last_period(page: Page, romania_url: str):
    open_dashboard(page, romania_url)
    page.get_by_role("button", name="Play", exact=True).click()
    expect(page.locator(".period-nav-meta")).to_have_text("4 of 4")
    expect(page.get_by_role("button", name="Play", exact=True)).to_be_visible()


def test_timeseries_chart_first_layout(page: Page, romania_url: str):
    open_dashboard(page, romania_url)
    page.get_by_role("tab", name=re.compile(r"^Chart-first")).click()
    expect(page.locator(".app")).to_have_class(re.compile(r"\blayout-chart-first\b"))
    expect(page.locator(".ts-panel-hero svg").first).to_be_visible()


def test_diagnostics_view(page: Page, romania_url: str):
    open_dashboard(page, romania_url)
    page.get_by_role("button", name="Diagnostics", exact=True).click()
    expect(page.locator(".hdr-h1")).to_have_text("Inversion diagnostics")

    def current(label: str):
        """The current period's value on the scalar chart card `label`."""
        name = page.locator(".dv-card-label", has_text=re.compile(f"^{re.escape(label)}$"))
        return page.locator(".dv-ts-grid .dv-card").filter(has=name).locator(".dv-card-val")

    # Period 1: DOFS 4.533, 1399 observations; period 2: DOFS 2.459, 2458.
    expect(current("DOFS")).to_have_text("4.53")
    expect(current("N obs")).to_have_text("1,399")
    page.locator(".dv-hd").get_by_role("button", name="Next period", exact=True).click()
    expect(page.locator(".dv-hd-meta")).to_have_text("2 of 4")
    expect(current("DOFS")).to_have_text("2.46")
    expect(current("N obs")).to_have_text("2,458")
    expect(page.locator(".map-msg")).to_have_count(0)

    page.get_by_role("button", name="Overview", exact=True).click()
    expect(page.locator(".hdr-h1")).to_have_text("Methane emissions over Romania")
    expect(page).to_have_title("Methane emissions over Romania")  # display_name
    # The period chosen in the diagnostics view carries over.
    expect(page.locator(".period-nav-meta")).to_have_text("2 of 4")


def test_failed_period_load_is_reported_and_retried(page: Page, romania_url: str, browser_log: BrowserLog):
    """A period whose rasters fail to download shows a note over the map,
    while its scalars (from the manifest) still show; showing the period
    again fetches it again."""
    failing = "**/data/periods/0001.bin"
    page.route(failing, lambda r: r.fulfill(status=503, body=""))
    open_dashboard(page, romania_url)
    page.get_by_role("button", name="Next period", exact=True).click()
    expect(page.locator(".map-msg-error")).to_have_text(
        "Could not load the maps for this period. Change period to try again."
    )
    expect(stat(page, "Total methane emissions")).to_have_text("0.947")
    # The previous period's raster is not left on the map.
    assert red_fraction(page) < 0.01

    page.unroute(failing)
    page.get_by_role("button", name="Previous period", exact=True).click()
    page.get_by_role("button", name="Next period", exact=True).click()
    expect(page.locator(".period-nav-meta")).to_have_text("2 of 4")
    expect(page.locator(".map-msg")).to_have_count(0)
    browser_log.expect_errors(r"503|period 1 failed to load")
