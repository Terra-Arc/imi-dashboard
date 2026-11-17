"""``imi-dashboard init-config``: write a commented config file.

Without an input folder it writes the packaged template
(``templates/config.yaml``) with illustrative examples. Given an IMI output
folder, it runs the discovery of ``info`` and fills the same template with
the values that run's dashboard would use without a config: the detected
sectors, the detected cadence and every text string as resolved from it,
and what ``region: roi`` does to this run.

The file keeps explanations and settings apart: a line starting with ``##``
explains, a line starting with ``# `` is a setting, commented out.
Deleting the ``# `` of every setting line gives a valid config; in the form
filled from a run, that config is the one ``convert`` uses without one. In
both forms the file as written is the same as no config.

The template marks what is swapped in with lines of their own: ``#%region``
(a single line) and ``#%header`` / ``#%sectors`` … ``#%end``; the ``text:``
block (``#%text``, a single line) is written by ``_text_block`` in both
forms. Those marker lines never reach the written file.

xarray is imported only for the data-derived form (through ``info``), so the
template-only form starts as quickly as ``--help``.
"""

from __future__ import annotations

import re
import textwrap
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from . import __version__
from .config import DEFAULT_SECTOR_TABLE, SectorConfig, TextConfig

if TYPE_CHECKING:
    from .info import RunInfo

TEMPLATE_NAME = "config.yaml"

# Column of the comments that follow a setting on its line.
_COMMENT_COLUMN = 32
# Width that the explanation lines are wrapped to (the "## " included).
_WRAP = 76
# A text value longer than this is written as a folded block scalar.
_FOLD_AT = 72

_BLOCK = re.compile(r"^#%(header|sectors)\n(.*?)^#%end\n", re.MULTILINE | re.DOTALL)
_LINE = re.compile(r"^#%(region|text)\n", re.MULTILINE)

# The text keys in the groups the file shows them in, each with the
# explanation above it. Together they are TextConfig's fields, in order
# (a test checks this).
TEXT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("", ("cadence", "cadence_singular", "cadence_plural")),
    (
        "Page headings, and the small line above each: heading_context, then "
        "the part for the page (the overview of a timeseries or of a single "
        "period, or the diagnostics page).",
        ("heading_context", "overview_heading", "diagnostics_heading",
         "heading_context_timeseries", "heading_context_single", "heading_context_diagnostics"),
    ),
    (
        "The line under the page heading.",
        ("subtitle_timeseries", "subtitle_single",
         "subtitle_diagnostics_timeseries", "subtitle_diagnostics_single"),
    ),
    (
        "The chart's title and subtitle in the Split and the Chart-first "
        "layouts, and the unit of the playback speed.",
        ("chart_title_split", "chart_subtitle_split",
         "chart_title_chart_first", "chart_subtitle_chart_first", "speed_unit"),
    ),
    ("The section labels of the diagnostics page.", ("diagnostics_spatial_label", "diagnostics_scalar_label")),
    ('The footer. An empty string ("") leaves a paragraph out.',
     ("footer_attribution", "footer_disclaimer", "footer_version")),
)

# Comments written after a text key's value.
_TEXT_COMMENTS = {
    "cadence": "daily | weekly | biweekly | monthly | annual | custom",
    "overview_heading": "overrides display_name for the heading only",
}

_STANDARD_SECTORS = {row[0]: row for row in DEFAULT_SECTOR_TABLE}


def template_text() -> str:
    """The packaged template, marker lines included."""
    return resources.files("imi_dashboard").joinpath("templates", TEMPLATE_NAME).read_text(encoding="utf-8")


def render_generic() -> str:
    """The template with its illustrative examples (the marker lines dropped)."""
    text = _BLOCK.sub(lambda m: m.group(2), template_text())
    blocks = {"region": "", "text": _text_block(TextConfig(cadence="monthly"), None)}
    return _LINE.sub(lambda m: blocks[m.group(1)], text)


def render_for_run(info: RunInfo, today: str | None = None) -> str:
    """The template with this run's detected values in place of the examples.

    ``today`` (ISO date) is the generation date written in the header;
    default: today in UTC."""
    day = today or datetime.now(timezone.utc).date().isoformat()
    blocks = {
        "header": _header(info, day),
        "sectors": _sectors_block(info),
        "region": _region_note(info),
        "text": _text_block(info.config.text, info),
    }
    text = _BLOCK.sub(lambda m: blocks[m.group(1)], template_text())
    text = _LINE.sub(lambda m: blocks[m.group(1)], text)
    # The template's example values, replaced by this run's: none of them
    # is set without a config, except the heading.
    cfg = info.config
    for key, value in (
        ("area_name", cfg.area_name),
        ("display_name", cfg.display_name),
        ("header_brand", cfg.header_brand),
        ("logo", cfg.logo),
        ("brand_name", cfg.brand_name),
        ("brand_tagline", cfg.brand_tagline),
    ):
        text = _set_value(text, key, value)
    return text


# ── formatting helpers ───────────────────────────────────────────────────


def _scalar(value: object) -> str:
    """One YAML scalar on one line, quoted only where YAML needs it."""
    dumped = yaml.safe_dump(value, allow_unicode=True, default_flow_style=True, width=1_000_000)
    return dumped.removesuffix("\n").removesuffix("\n...")


def _flow_scalar(value: object) -> str:
    """One YAML scalar as it may stand inside a flow mapping ({...}), where
    a comma or a bracket needs quotes too."""
    return yaml.safe_dump([value], allow_unicode=True, default_flow_style=True, width=1_000_000).strip()[1:-1]


def _setting(line: str, comment: str = "") -> str:
    """A setting line, with its comment (if any) at the comment column."""
    if not comment:
        return line
    return f"{line.ljust(_COMMENT_COLUMN - 2)}  # {comment}"


def _explain(text: str, indent: str = "") -> list[str]:
    """An explanation, wrapped, as `##` lines."""
    prefix = f"##{indent} "
    return [prefix + line for line in textwrap.wrap(text, _WRAP - len(prefix), break_on_hyphens=False)]


def _set_value(text: str, key: str, value: object) -> str:
    """Replace the value of the top-level setting line `# <key>: …`, keeping
    the comment after it; None leaves the value empty."""
    pattern = re.compile(rf"^# {key}:(?: (?P<value>.*?))??(?: +# (?P<comment>.*))?$", re.MULTILINE)
    shown = "" if value is None else " " + _scalar(str(value) if isinstance(value, Path) else value)

    def replace(m: re.Match[str]) -> str:
        return _setting(f"# {key}:{shown}", m.group("comment") or "")

    text, n = pattern.subn(replace, text, count=1)
    assert n == 1, f"template has no `# {key}:` line"
    return text


def _where(info: RunInfo) -> str:
    return f" (in {info.reference_label})" if info.mode == "timeseries" else ""


# ── the swapped-in blocks ────────────────────────────────────────────────


def _header(info: RunInfo, day: str) -> str:
    lines = [
        f"## imi-dashboard config for the IMI run `{info.folder.name}`, written by",
        f"## imi-dashboard {__version__} init-config on {day}.",
        "##",
        *_explain(
            "Lines starting with `##` explain. Lines starting with `# ` are settings, "
            "each shown with the value this run gets without a config, so the file "
            "changes nothing as written. To change a setting, delete its `# ` (and "
            "that of its section line, such as `# map:`) and edit the value; the "
            "settings left commented keep following the data. Then run"
        ),
    ]
    return "\n".join(lines) + "\n"


def _region_note(info: RunInfo) -> str:
    g = info.grid
    if info.n_elements is None or info.n_buffers is None:
        note = (
            f"This run has no state_vector.nc{_where(info)}, so roi shows the whole "
            f"domain: {g.rows}\xa0x\xa0{g.cols} cells."
        )
    else:
        note = (
            f"This run: {info.n_buffers} of its {info.n_elements} state-vector "
            f"elements{_where(info)} are buffer elements."
        )
        if info.roi_shape:
            note += (
                f" roi keeps {info.roi_shape[0]}\xa0x\xa0{info.roi_shape[1]} of the "
                f"domain's {g.rows}\xa0x\xa0{g.cols} cells."
            )
    # No-break spaces keep "18 x 29" on one line (textwrap breaks at ASCII whitespace only).
    return "##\n" + "".join(line.replace("\xa0", " ") + "\n" for line in _explain(note))


def _sector_cells(s: SectorConfig) -> tuple[list[str], list[str]]:
    """A sector's table row: the four columns every row has (id, label,
    colour, primary flag), and the fields written only where needed (the
    variable of a sector that is not standard, or not the standard one; a
    short label other than the one it would get)."""
    columns = [f"{k}: {_flow_scalar(getattr(s, k))}" for k in ("id", "label", "color", "primary")]
    standard = _STANDARD_SECTORS.get(s.id)
    extra = []
    if s.id != "total" and (standard is None or s.imi_var != standard[1]):
        extra.append(f"imi_var: {_flow_scalar(s.imi_var)}")
    if s.short != (standard[3] if standard else s.label):
        extra.append(f"short: {_flow_scalar(s.short)}")
    return columns, extra


def _sectors_block(info: RunInfo) -> str:
    sectors = info.config.sectors
    note = (
        f"This run's {len(sectors)} sectors"
        + (f" (found in {info.reference_label})" if info.mode == "timeseries" else "")
        + ". To edit them, uncomment the whole list."
    )
    total = next((s for s in sectors if s.id == "total"), None)
    if total is not None:
        includes = (info.total_definition or {}).get("mapIncludesSoilSink")
        sink = "" if includes is None else (", with the soil sink" if includes else ", the total without the soil sink")
        note += f" The total's map shows {total.imi_var}{sink}, chosen from the data."
    rows = [_sector_cells(s) for s in sectors]
    widths = [max((len(cols[i]) + 1 for cols, _ in rows), default=0) for i in range(3)]
    lines = ["##", *_explain(note), "# sectors:"]
    for cols, extra in rows:
        cells = [(c + ",").ljust(w) for c, w in zip(cols[:3], widths)]
        last = ", ".join([cols[3], *extra])
        lines.append("#   - {" + " ".join(cells) + " " + last + "}")
    return "\n".join(lines) + "\n"


def _text_block(text: TextConfig, info: RunInfo | None) -> str:
    """The `text:` block: every key, with the string the dashboard shows
    (for a run, as its cadence resolves it; for the template, a monthly
    run's), grouped by the part of the page it sets."""
    if info is None:
        resolved = text.resolved(area_name=None, overview_heading_fallback="Methane emissions")
        detected = "Not set: detected from the length of the first period. Shown here for monthly periods."
    else:
        cfg = info.config
        resolved = text.resolved(area_name=cfg.area_name, overview_heading_fallback=cfg.display_name)
        days = f"{info.period_days:g} days" if info.period_days is not None else "of unknown length"
        detected = f"Not set: detected from the length of the first period, here {days}{_where(info)}."
    lines = _explain(
        "The text on the page. Each string follows the cadence (and area_name) "
        "until it is uncommented. The subtitles, the chart's text and "
        "footer_attribution may contain {count}, {date}, {date_start}, "
        "{date_end}, {years}, {cadence_singular} and {cadence_plural}."
    )
    lines.append("# text:")
    lines += _explain(
        f'cadence sets the wording ("Monthly timeseries", "months/sec"). {detected}',
        indent="  ",
    )
    for explanation, keys in TEXT_GROUPS:
        if explanation:
            lines.append("##")
            lines += _explain(explanation, indent="  ")
        for key in keys:
            value = resolved[key]
            if len(value) > _FOLD_AT and "\n" not in value and "  " not in value:
                # A folded block without a final newline (>-): its lines join with
                # single spaces back into exactly this string.
                lines.append(f"#   {key}: >-")
                lines += [f"#     {part}" for part in textwrap.wrap(value, _FOLD_AT, break_on_hyphens=False)]
            else:
                lines.append(_setting(f"#   {key}: {_scalar(value)}", _TEXT_COMMENTS.get(key, "")))
    return "\n".join(lines) + "\n"


# ── checking the input and the output ────────────────────────────────────


def check_input(folder: Path) -> None:
    """Refuse an input that cannot be an IMI output folder, with a message
    that says what to do instead. Raises ValueError (or FileNotFoundError).

    A YAML file name is most likely the output file, given out of habit as
    the first argument."""
    from .info import is_dashboard

    as_output = f"; to write the config to {folder}, use -o {folder}" if folder.suffix.lower() in (".yaml", ".yml") else ""
    if not folder.exists():
        if as_output:
            raise ValueError(f"{folder} is not an IMI output folder{as_output}")
        raise FileNotFoundError(f"input folder does not exist: {folder}")
    if not folder.is_dir():
        raise ValueError(f"{folder} is a file, not an IMI output folder{as_output}")
    if is_dashboard(folder):
        raise ValueError(
            f"{folder} is a generated dashboard; pass the IMI output folder it was made from "
            f"(`imi-dashboard info {folder}` names it)"
        )
