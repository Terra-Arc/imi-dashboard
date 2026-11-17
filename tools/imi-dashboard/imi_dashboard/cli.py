"""The ``imi-dashboard`` command.

    imi-dashboard convert INPUT_FOLDER OUTPUT_FOLDER [--config FILE] ...  # build a dashboard
    imi-dashboard info INPUT_FOLDER [--config FILE] [--json]              # describe an IMI run or a dashboard
    imi-dashboard serve DASHBOARD [--port PORT] [--open]                  # preview locally
    imi-dashboard init-config [INPUT_FOLDER] [-o FILE]                    # write a commented config
    imi-dashboard --version

Every command takes the same output options: -v (more on stderr: for
`convert` its progress and decisions, for `info` and `init-config` the files
read, for `serve` every request), -vv (also the full traceback of an error,
and debug detail), -q (errors only), --log-style and --log-file.

All of them write their messages (everything but the result) to stderr in one of
two styles, chosen by --log-style auto|screen|log, else by the
IMI_DASHBOARD_LOG_STYLE environment variable, else `auto`, which picks
`screen` when stderr is a terminal and `log` otherwise:

    screen   [2/3] period_2: read (0.7 s)            INFO bare, other levels
             warning: no app bundle (...)            as `<level>: `, coloured
                                                     unless NO_COLOR is set;
                                                     `convert` also shows a
                                                     progress bar on a terminal
    log      2026-11-04T10:15:30Z imi-dashboard info: [2/3] period_2: read (0.7 s)
             (UTC timestamp, program name, lower-case level on every line;
             continuation lines indented by four spaces; no colour, no bar)

--log-file FILE appends the log style at debug detail to FILE, whatever -v
or -q say about stderr, together with the result and the traceback of any
error, an unexpected one included. The result (the summary line of
`convert`, the description of `info`, the address `serve` listens on, the
`wrote` line of `init-config`) goes to stdout, plain, in both styles.

A usage error (a missing or misspelled argument or option, a bad value) is
printed in the same style, `error: <message>` followed by the command's usage
line and a pointer to --help, with exit status 2, instead of click's own
`Error: ...` form (_CommandGroup). It is reported while the command line is
parsed, before --log-style is read, so its style follows --log-style only
when that option was already parsed; otherwise IMI_DASHBOARD_LOG_STYLE or
terminal detection.
"""

from __future__ import annotations

import difflib
import errno
import inspect
import logging
import os
import sys
import time
import warnings
import webbrowser
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

import typer
import yaml
from pydantic import BaseModel, ValidationError
from typer.core import TyperCommand, TyperGroup

from . import __version__
from .config import ChartConfig, Config, MapConfig, SectorConfig, TextConfig, load_config

if TYPE_CHECKING:  # rich is imported only by the commands that print through it
    from rich.console import Console


class _CommandGroup(TyperGroup):
    """The command group, with usage errors reported by _report_usage_error.

    click raises a usage error while it parses the group's arguments
    (make_context) or a subcommand's, and the subcommands raise BadParameter
    themselves; both happen inside these two methods. Exiting here, before
    typer's own handler sees the error, works the same under CliRunner as
    from the console script."""

    def make_context(self, info_name, args, parent=None, **extra):  # type: ignore[no-untyped-def]
        with _usage_errors():
            return super().make_context(info_name, args, parent=parent, **extra)

    def invoke(self, ctx):  # type: ignore[no-untyped-def]
        with _usage_errors():
            return super().invoke(ctx)


app = typer.Typer(
    name="imi-dashboard",
    help="Turn Integrated Methane Inversion (IMI) outputs into a static web dashboard.",
    no_args_is_help=True,
    add_completion=False,
    cls=_CommandGroup,
)

# Placeholders (metavars) of option values, the same in every command:
# FILE for a file and FOLDER for a folder (as in the arguments INPUT_FOLDER
# and OUTPUT_FOLDER), the accepted words for a choice, and a name in
# capitals for anything else (PORT, ADDRESS).
FILE = "FILE"
FOLDER = "FOLDER"

LOG_STYLES = ("auto", "screen", "log")
LOG_STYLE_ENV = "IMI_DASHBOARD_LOG_STYLE"


# The four output options every command takes. The help of -v and -q has a
# shared wording with a command-specific part; that of --log-style and
# --log-file is the same everywhere.
def _verbose_help(what: str, vv: str = "") -> str:
    return f"Print more on stderr: {what}. Repeat (-vv) to add {vv}the full traceback of an error."


def _quiet_help(stderr_extra: str, stdout: str) -> str:
    return f"Print only errors on stderr: no warnings{stderr_extra}. {stdout}"


LOG_STYLE_HELP = (
    "Format of the messages on stderr: 'screen' (plain lines, coloured "
    "level prefixes, and convert's progress bar on a terminal), 'log' (each "
    "line starting with the UTC time and the program name, for a file), or "
    "'auto' (screen when stderr is a terminal, log otherwise). Default: the "
    f"{LOG_STYLE_ENV} environment variable, else auto."
)

LOG_FILE_HELP = (
    "Also append every message, debug detail and tracebacks included, to "
    "FILE in the log style, whatever -v or -q set for stderr. What the "
    "command prints on stdout is recorded there too."
)

# The user guide, which documents every option, file and key at length.
GUIDE_URL = "https://github.com/Terra-Arc/imi-dashboard/blob/main/docs/guide.md"

APP_BUNDLE_HELP = (
    "Developer option, not needed with a release install, which includes "
    "the app bundle. The app bundle is the dashboard's pages (index.html "
    "and assets/), built from the repository's web/ folder; this option "
    "names the one to copy: web/dist/ of a clone after `npm --prefix web "
    "run build`, or a dashboard written by this version (only the files "
    "listed in its imi-dashboard-app.json are copied). Default: web/dist/ "
    "of the clone, then the copy installed with the package; with neither, "
    "an update keeps the dashboard's own app bundle and a new dashboard is "
    "an error."
)

CONFIG_HELP = (
    "YAML config file. Optional: without it the sectors are those detected "
    "in the IMI output, and the cadence (the period length as a word: daily, "
    "weekly, monthly, ..., which sets the dashboard's wording) is inferred "
    "from the length of the first period. The title is set by the keys "
    "display_name and area_name, the branding by header_brand, logo, "
    "brand_name and brand_tagline, the sectors by the sectors list. "
    "`imi-dashboard init-config INPUT_FOLDER` writes a config documenting "
    "every key, with the values detected in the run, all commented out."
)

# Exit statuses shared by every command, for their epilogs.
EXIT_USAGE = "  2    a usage error: an unknown option, a missing argument, a bad value"

# The parent of every module logger (``imi_dashboard.pack`` and so on). Only
# this logger is configured, never the root one, so third-party libraries
# (xarray, netCDF4) stay quiet even at -vv.
log = logging.getLogger("imi_dashboard")

# Errors caused by the input or the config rather than by a bug: reported as
# one `error: ...` line, without a traceback unless -vv is given. pydantic's
# ValidationError is a ValueError; load-time YAML and validation errors are
# rewritten into a readable ValueError by _load_config. OSError covers a
# missing or unreadable file or folder (FileNotFoundError, PermissionError,
# NotADirectoryError) and a damaged netCDF file, which netCDF4 reports as an
# OSError naming the file. ValueError also stands for input errors that xarray
# reports that way (a file that is not netCDF at all, for one), which is why
# no narrower class is used; -vv shows the traceback of any of them.
USER_ERRORS = (ValueError, OSError)

# The command-line library's own exceptions: a normal or early exit (Exit,
# also raised for an exit status), Ctrl+C at a prompt (Abort) and a usage
# error (BadParameter, exit status 2). Exit and Abort are RuntimeErrors, so
# they are listed to keep them from being logged as crashes. typer does not
# export click's ClickException; BadParameter is the only one these commands
# raise.
CLI_EXCEPTIONS = (typer.Exit, typer.Abort, typer.BadParameter)

# click's UsageError, the parent of every error in the command line (and of
# BadParameter). typer does not export it, and since typer 0.26 it is typer's
# own copy of click rather than the click package, so it is taken from
# BadParameter's ancestry, which works with either. Typed code names it as
# BadParameter, the subclass typer exports, which has the same attributes.
UsageError: type[Exception] = next(c for c in typer.BadParameter.__mro__ if c.__name__ == "UsageError")

# Printed by `serve` on every start, like the development servers of web
# frameworks, and the second one when it listens beyond this machine.
SERVE_LOCAL_WARNING = (
    "this is a development server, for previewing a dashboard on this machine; "
    "do not use it in production. To share a dashboard, upload its folder to a static host."
)
SERVE_NETWORK_WARNING = (
    "listening on %s, so anyone who can reach this machine over the network can open "
    "the dashboard: the server has no access control and is not hardened for exposure. "
    "Leave out --bind (127.0.0.1) to keep it local."
)

# `extra=` for a record meant for the --log-file only (the result, which
# stdout already shows); the stderr handler drops it.
FILE_ONLY = {"file_only": True}


class _LogFormatter(logging.Formatter):
    """The log style: `<UTC time> imi-dashboard <level>: <message>` for every
    level, with continuation lines (and a traceback) indented by four
    spaces, so every line that starts in column 0 begins a record."""

    def format(self, record: logging.LogRecord) -> str:
        text = record.getMessage()
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info).rstrip("\n")
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created))
        return f"{stamp} imi-dashboard {record.levelname.lower()}: " + text.replace("\n", "\n    ")


# The colour of each level's prefix in the screen style (INFO has none).
_SCREEN_STYLES = {logging.DEBUG: "dim", logging.WARNING: "yellow", logging.ERROR: "bold red", logging.CRITICAL: "bold red"}


class _ScreenHandler(logging.Handler):
    """The screen style: INFO as the bare message, anything else prefixed
    with its level (`warning: ...`, `error: ...`), the prefix coloured.

    Written through a rich console (which writes no escape codes when stderr
    is not a terminal), the one the progress bar draws on, so
    messages appear above the bar instead of breaking it."""

    def __init__(self, console: Console) -> None:
        super().__init__()
        self.console = console

    def emit(self, record: logging.LogRecord) -> None:
        from rich.text import Text

        try:
            # A Text object, so that `[1/3]` in a message is not read as markup.
            line = Text()
            if record.levelno != logging.INFO:
                # rich's NO_COLOR handling removes colours but keeps bold and
                # dim; drop the prefix style altogether, for plain text.
                style = "" if self.console.no_color else _SCREEN_STYLES.get(record.levelno, "")
                line.append(f"{record.levelname.lower()}:", style=style)
                line.append(" ")
            line.append(record.getMessage())
            self.console.print(line, soft_wrap=True, highlight=False)
        except Exception:
            self.handleError(record)


def _not_file_only(record: logging.LogRecord) -> bool:
    """stderr filter: drop the records written for --log-file only. These are
    the ones carrying a traceback (a user error's, which propagates at -vv,
    and an unexpected exception's, which always propagates, so Python prints
    the traceback itself) and those marked FILE_ONLY."""
    return not record.exc_info and not getattr(record, "file_only", False)


def _style_from_env() -> str | None:
    """IMI_DASHBOARD_LOG_STYLE, lower-cased, or None when unset or empty."""
    return os.environ.get(LOG_STYLE_ENV, "").strip().lower() or None


def _resolve_style(option: str | None) -> str:
    """'screen' or 'log': the --log-style option, else the environment
    variable, else auto (screen when stderr is a terminal). An unknown value
    in either is a usage error."""
    if option is not None:
        style, hint = option.strip().lower(), "--log-style"
    else:
        style, hint = _style_from_env() or "auto", LOG_STYLE_ENV
    if style not in LOG_STYLES:
        raise typer.BadParameter(f"must be one of {', '.join(LOG_STYLES)}, got {style!r}", param_hint=hint)
    if style == "auto":
        return "screen" if sys.stderr.isatty() else "log"
    return style


def _stderr_handler(style: str) -> tuple[logging.Handler, Console | None]:
    """The stderr handler of a resolved style ('screen' or 'log'), and the
    rich console it prints through in the screen style."""
    if style == "screen":
        from rich.console import Console

        console = Console(stderr=True)
        return _ScreenHandler(console), console
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_LogFormatter())
    return handler, None


def _report_usage_error(exc: typer.BadParameter) -> None:
    """Print a usage error like any other error: `error: <message>` in the
    screen or log style, then the command's usage line and the --help hint.

    The style is the --log-style option when it was parsed before the error,
    else what _resolve_style finds without it; a bad style value falls back
    to terminal detection, so that error itself can be reported. click's
    messages start with a capital ("Missing argument 'INPUT_FOLDER'."); the
    first letter is lowered (unless the first word is an acronym) and the
    final full stop dropped, to match the converter's messages."""
    message = exc.format_message()
    if len(message) > 1 and message[0].isupper() and message[1].islower():
        message = message[0].lower() + message[1:]
    if message.endswith(".") and not message.endswith(".."):
        message = message[:-1]
    lines = [message]
    ctx = exc.ctx
    if ctx is not None:
        lines.append(ctx.get_usage())
        if ctx.command.get_help_option(ctx) is not None:
            lines.append(f"Try '{ctx.command_path} --help' for help.")
    option = ctx.params.get("log_style") if ctx is not None else None
    try:
        style = _resolve_style(option)
    except UsageError:  # a bad --log-style or IMI_DASHBOARD_LOG_STYLE
        style = "screen" if sys.stderr.isatty() else "log"
    handler, _ = _stderr_handler(style)
    record = logging.makeLogRecord({"name": log.name, "levelno": logging.ERROR, "levelname": "ERROR", "msg": "\n".join(lines)})
    handler.handle(record)
    handler.close()


@contextmanager
def _usage_errors() -> Iterator[None]:
    """Report a usage error with _report_usage_error and exit with its status
    (2). The error raised for a command given without arguments, which shows
    the help, passes through unchanged."""
    try:
        yield
    except UsageError as exc:
        if type(exc).__name__ == "NoArgsIsHelpError":
            raise
        usage = cast(typer.BadParameter, exc)
        _report_usage_error(usage)
        sys.exit(usage.exit_code)


def _show_warning(message, category, filename, lineno, file=None, line=None) -> None:
    """Route a converter warning through the logger, so -q can silence it and
    it shares the `warning: ...` format."""
    log.warning("%s", message)


def _level(verbose: int, quiet: bool) -> int:
    if quiet:
        return logging.ERROR
    return {0: logging.WARNING, 1: logging.INFO}.get(verbose, logging.DEBUG)


@contextmanager
def _console(
    verbose: int = 0,
    quiet: bool = False,
    *,
    style: str | None = None,
    log_file: Path | None = None,
) -> Iterator[Console | None]:
    """Set up console output for one command and report user errors.

    Messages go to stderr in the style resolved by _resolve_style: errors
    always, warnings unless -q, progress with -v, debug detail with -vv. The
    level is set on the stderr handler, not on the logger, so that a
    --log-file handler can receive everything at DEBUG. An error in
    USER_ERRORS is logged as one `error:` line (its traceback goes to the log
    file only) and exits with status 1; with -vv it then propagates so the
    traceback shows. Any other exception, except the command-line library's
    own (CLI_EXCEPTIONS), is logged at CRITICAL with its traceback to the
    log file only and propagates unchanged. Yields the rich console in the
    screen style (for the progress bar), None in the log style. The logger and
    `warnings.showwarning` are restored afterwards, so the command can be
    invoked repeatedly in one process (as the tests do).
    """
    if verbose and quiet:
        raise typer.BadParameter("--verbose and --quiet cannot be combined", param_hint="-v/-q")
    stderr_handler, console = _stderr_handler(_resolve_style(style))
    stderr_handler.setLevel(_level(verbose, quiet))
    stderr_handler.addFilter(_not_file_only)
    handlers: list[logging.Handler] = [stderr_handler]
    saved = (log.level, log.propagate, warnings.showwarning)
    log.addHandler(stderr_handler)
    log.setLevel(stderr_handler.level)
    log.propagate = False
    warnings.showwarning = _show_warning
    try:
        if log_file is not None:
            # Opened here, inside the try, so that an unwritable path is an
            # ordinary one-line error.
            file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
            file_handler.setFormatter(_LogFormatter())
            file_handler.setLevel(logging.DEBUG)
            handlers.append(file_handler)
            log.addHandler(file_handler)
            log.setLevel(logging.DEBUG)
        yield console
    except USER_ERRORS as exc:
        log.debug("traceback of the error that follows", exc_info=True)
        log.error("%s", exc)
        if verbose >= 2:
            raise
        raise typer.Exit(1)
    except CLI_EXCEPTIONS:
        raise
    except Exception as exc:
        # A bug: recorded in the log file (the stderr filter drops the record)
        # and re-raised, so stderr shows the traceback exactly as without it.
        log.critical("unexpected error: %s: %s", type(exc).__name__, exc, exc_info=True)
        raise
    finally:
        for handler in handlers:
            log.removeHandler(handler)
            handler.close()
        log.setLevel(saved[0])
        log.propagate = saved[1]
        warnings.showwarning = saved[2]


def _load_config(path: Path | None) -> Config:
    """load_config, with a YAML syntax error or a failed validation rewritten
    as a ValueError listing each offending key, so _console prints it as an
    ordinary one-line-per-problem error."""
    try:
        return load_config(path)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path} is not valid YAML: {exc}") from exc
    except ValidationError as exc:
        # The file parsed a moment ago; read it again to name each sector by
        # its id in the error locations.
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) if path else None
        problems = []
        for err in exc.errors():
            loc = err["loc"]
            where = _error_location(loc, raw) or "(top level)"
            if err["type"] == "extra_forbidden":
                model = _SECTION_MODELS.get(str(loc[0])) if len(loc) > 1 else Config
                close = difflib.get_close_matches(str(loc[-1]), list(model.model_fields), n=1, cutoff=0.7) if model else []
                hint = f"; did you mean {close[0]!r}?" if close else ""
                problems.append(f"  unknown key '{where}'{hint}")
                continue
            got = err.get("input")
            shown = f" (got {got!r})" if loc and not isinstance(got, (dict, list)) else ""
            problems.append(f"  {where}: {err['msg']}{shown}")
        raise ValueError(f"invalid config {path}:\n" + "\n".join(problems)) from exc


# The model holding the keys of each config section, for suggesting the
# closest valid key when one is misspelled.
_SECTION_MODELS: dict[str, type[BaseModel]] = {"map": MapConfig, "chart": ChartConfig, "text": TextConfig, "sectors": SectorConfig}


def _error_location(loc: tuple, raw: object) -> str:
    """A validation error's location as dotted keys, with a list index in
    brackets; a sector is named by its id when it has one, so
    ('sectors', 1, 'label') reads sectors[coal].label."""
    parts: list[str] = []
    for i, part in enumerate(loc):
        if not isinstance(part, int):
            parts.append(str(part))
            continue
        sid = None
        if loc[:i] == ("sectors",) and isinstance(raw, dict):
            items = raw.get("sectors")
            if isinstance(items, list) and part < len(items) and isinstance(items[part], dict):
                sid = items[part].get("id")
        index = f"[{sid}]" if isinstance(sid, str) else f"[{part}]"
        if parts:
            parts[-1] += index
        else:
            parts.append(index)
    return ".".join(parts)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_print_version,
        is_eager=True,
        help="Print the imi-dashboard version and exit.",
    ),
) -> None:
    """Turn Integrated Methane Inversion (IMI) outputs into a static web dashboard."""


@contextmanager
def _progress_bar(console: Console | None, show: bool) -> Iterator[Callable[[int, int, str], None] | None]:
    """A progress bar over the periods of a conversion, removed when it ends.

    Drawn only in the screen style (``console`` is given), when ``show``
    (not -q, not -vv) and when stderr is a real terminal, since a bar
    written to a file or pipe is noise. Yields the ``on_period`` callback
    for ``pack.convert``, or None when there is no bar.
    """
    if console is None or not show or not console.is_terminal:
        yield None
        return
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

    progress = Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    task = progress.add_task("reading the run", total=None)

    # Started at the first period, not before: a confirmation asked before
    # any period is read (see _confirm) must not have a bar redrawn over it.
    def on_period(done: int, total: int, label: str) -> None:
        if not progress.live.is_started:
            progress.start()
        progress.update(task, completed=done, total=total, description=label)

    try:
        yield on_period
    finally:
        progress.stop()


def _stdin_is_terminal() -> bool:
    """Whether someone can answer a question on stdin (a function of its own,
    so that tests can pretend)."""
    return sys.stdin.isatty()


def _confirm(yes: bool) -> Callable[[str], bool]:
    """The ``confirm`` callback of ``pack.convert``: with --yes, go ahead
    (and log what is removed); on a terminal, ask, defaulting to no;
    otherwise (a scheduled job, a pipe) refuse, since nobody can answer."""
    def confirm(message: str) -> bool:
        if yes:
            log.info("%s; going ahead (--yes)", message)
            return True
        if not _stdin_is_terminal():
            raise ValueError(f"{message}. Pass --yes to allow this when convert runs without a terminal")
        return typer.confirm(f"{message}. Continue?", default=False, err=True)
    return confirm


class _Command(TyperCommand):
    """A command whose epilog (exit statuses, examples) keeps its line
    breaks in both help formats. typer's rich format prints the epilog's
    lines as written; click's plain format, used in the log style (see
    main()), would join and rewrap them, so it is written verbatim here."""

    def format_epilog(self, ctx, formatter) -> None:  # type: ignore[no-untyped-def]
        if self.epilog:
            formatter.write_paragraph()
            for line in inspect.cleandoc(self.epilog).splitlines():
                formatter.write(f"  {line}".rstrip() + "\n")


# Every argument names its placeholder (metavar). Without one, typer 0.27
# shows the parameter name in lower case (input_folder) in --help, in the
# usage line and in error messages; with it, INPUT_FOLDER everywhere, in
# braces in the usage line, which is how typer 0.27 marks a required
# argument ([...] marks an optional one). Every option that takes a value
# names it too (FILE, FOLDER, PORT, ...), instead of typer's <file>, <str>.
#
# The epilogs below are printed as written: lines of at most 78 characters,
# and no square brackets, which the rich format would read as markup.
CONVERT_EPILOG = f"""
Exit status:
  0    converted, also when warnings were printed
  1    an error in the input, the config, the output folder or the log
       file; without a terminal and without --yes, a refusal to remove
       periods or to replace a dashboard; a confirmation answered no or
       interrupted; an unexpected error (printed with its traceback)
{EXIT_USAGE}
  130  interrupted with Ctrl+C while converting

Examples:
  First conversion and a preview:
    imi-dashboard info run/
    imi-dashboard convert run/ site/ --config dashboard.yaml
    imi-dashboard serve site/ --open
  Update the dashboard once the run has written new periods:
    imi-dashboard convert run/ site/ --config dashboard.yaml
  Scheduled update (cron, a batch job): errors only, everything in a log
  file; it refuses, with status 1, to remove periods that left the input
  unless --yes is added:
    imi-dashboard convert run/ site/ --config dashboard.yaml -q \\
        --log-file convert.log
"""


@app.command(cls=_Command, epilog=CONVERT_EPILOG)
def convert(
    input_folder: Path = typer.Argument(..., exists=True, file_okay=False, readable=True, metavar="INPUT_FOLDER", help="IMI run folder: one period (a netCDF/ folder) or a timeseries (period_<N>_plots/ folders)."),
    output_folder: Path = typer.Argument(..., file_okay=False, metavar="OUTPUT_FOLDER", help="Dashboard folder to write: new, empty, or a dashboard written earlier, which is updated (see above) or, with --rebuild, rewritten. Files there that imi-dashboard does not write, such as a .git/, are kept. A folder holding other files but no dashboard is refused. A relative path is taken from the current directory."),
    config: Path | None = typer.Option(None, "--config", "-c", exists=True, dir_okay=False, readable=True, metavar=FILE, help=CONFIG_HELP),
    region: str | None = typer.Option(
        None,
        "--region",
        metavar="domain|roi",
        show_default=False,
        help=(
            "Spatial extent: 'domain' keeps every cell of the inversion, "
            "buffer elements included; 'roi' keeps the region of interest "
            "only (buffer elements dropped, the grid cropped to what "
            "remains). Default: the config's region key, else roi."
        ),
    ),
    rebuild: bool = typer.Option(
        False,
        "--rebuild",
        help=(
            "Reconvert every period and rewrite the dashboard's files, "
            "instead of reconverting only the periods that are new or "
            "changed. Files in the folder that imi-dashboard does not write "
            "are kept either way."
        ),
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help=(
            "Do not ask before removing periods that are no longer in the "
            "input, or replacing a dashboard converted from another input "
            "folder. Without a terminal, convert refuses to do either unless "
            "this is given."
        ),
    ),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, metavar="", show_default=False, help=_verbose_help("the progress and the reason for each decision", "debug detail (each period reused) and ")),
    quiet: bool = typer.Option(False, "--quiet", "-q", help=_quiet_help(", no progress bar", "The summary line on stdout is left out too.")),
    log_style: str | None = typer.Option(None, "--log-style", metavar="auto|screen|log", show_default=False, help=LOG_STYLE_HELP),
    log_file: Path | None = typer.Option(None, "--log-file", dir_okay=False, metavar=FILE, help=LOG_FILE_HELP),
    app_bundle: Path | None = typer.Option(None, "--app-bundle", exists=True, file_okay=False, readable=True, metavar=FOLDER, rich_help_panel="Developer options", help=APP_BUNDLE_HELP),
) -> None:
    """Convert an IMI run folder into a dashboard.

    A period is the time span one inversion estimates emissions for: a
    single-period run holds one, a timeseries one period_<N>_plots/ folder
    per period. Every period is converted into gridded maps and totals,
    written into OUTPUT_FOLDER together with the dashboard's pages (the app
    bundle: index.html and assets/).

    When run again into the same OUTPUT_FOLDER, convert reconverts only the
    periods that are new or whose fingerprint changed: a hash of the name,
    size and modification time of every file in the period's folder. A
    period skipped because posterior.nc or prior.nc is missing or cannot be
    read is converted by the first later run that finds both readable.
    Every period is reconverted with --rebuild, or when the input folder,
    the converter release, the region, the grid, the sector list or a
    sector's netCDF variable differs from the existing dashboard's.

    Before sharing a dashboard, note what its folder holds: the app bundle
    (index.html, assets/ and imi-dashboard-app.json), with the custom logo
    in assets/ when the config names one;
    data/ with each period's gridded fields, emission totals, scalar
    diagnostics, dates and folder name; data/config.json with the titles,
    labels, branding and base-map settings of the config; and in
    data/manifest.json the converter version, the UTC time of the
    conversion, the input folder's name (not its path), a hash of the input
    folder's path, each sector's netCDF variable name and each period's
    fingerprint. The user guide lists every file:
    https://github.com/Terra-Arc/imi-dashboard/blob/main/docs/guide.md"""
    started = time.perf_counter()
    if region is not None and region not in ("domain", "roi"):
        raise typer.BadParameter("region must be 'domain' or 'roi'", param_hint="--region")
    out = output_folder
    with _console(verbose, quiet, style=log_style, log_file=log_file) as console:
        log.info("imi-dashboard %s convert %s -> %s", __version__, input_folder, out)
        # Imported here, not at module level: pack pulls in xarray, which
        # takes seconds to import, and --help, --version, serve and
        # init-config do not need it. Inside _console, so that a warning
        # raised on import is printed like any other.
        from .pack import ConversionCancelled
        from .pack import convert as convert_run

        cfg = _load_config(config)
        # CLI flag wins; otherwise fall back to the config's region key.
        effective_region = region if region is not None else cfg.region
        try:
            with _progress_bar(console, show=not quiet and verbose < 2) as on_period:
                summary = convert_run(
                    input_folder=input_folder,
                    output_folder=out,
                    cfg=cfg,
                    bundle_dir=app_bundle,
                    region=effective_region,
                    force=rebuild,
                    on_period=on_period,
                    confirm=_confirm(yes),
                )
        except ConversionCancelled as exc:
            log.error("%s", exc)
            raise typer.Exit(1) from None
        reuse_note = ""
        if "reused" in summary:
            reuse_note = f" · reused {summary['reused']}, rebuilt {summary['rebuilt']}"
        result = (
            f"converted {summary['mode']} run · {summary['period_count']} period(s) "
            f"· grid {summary['rows']}x{summary['cols']}{reuse_note} -> {summary['output']} "
            f"({time.perf_counter() - started:.1f} s)"
        )
        # In the log file too, even with -q, so it records each run's outcome.
        log.info("%s", result, extra=FILE_ONLY)
    if not quiet:
        typer.echo(result)


INFO_EPILOG = f"""
Exit status:
  0    described, also when warnings were printed or periods are skipped
  1    an error: not an IMI run or a dashboard, no period with both
       posterior.nc and prior.nc, an invalid config, an unreadable file
{EXIT_USAGE}

Examples:
  imi-dashboard info run/
  imi-dashboard info run/ --config dashboard.yaml
  imi-dashboard info site/
  imi-dashboard info run/ --json > run.json
"""


@app.command(cls=_Command, epilog=INFO_EPILOG)
def info(
    input_folder: Path = typer.Argument(..., exists=True, file_okay=False, readable=True, metavar="INPUT_FOLDER", help="IMI run folder, or a dashboard generated by `convert`."),
    config: Path | None = typer.Option(None, "--config", "-c", exists=True, dir_okay=False, readable=True, metavar=FILE, help="YAML config file; the sectors and cadence it sets are shown instead of the detected ones. Not used for a dashboard."),
    as_json: bool = typer.Option(False, "--json", help="Print the description as one JSON object on stdout instead of the text; its keys are listed in the user guide, " + GUIDE_URL + ". Messages stay on stderr."),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, metavar="", show_default=False, help=_verbose_help("the files read")),
    quiet: bool = typer.Option(False, "--quiet", "-q", help=_quiet_help("", "The description on stdout is printed as usual.")),
    log_style: str | None = typer.Option(None, "--log-style", metavar="auto|screen|log", show_default=False, help=LOG_STYLE_HELP),
    log_file: Path | None = typer.Option(None, "--log-file", dir_okay=False, metavar=FILE, help=LOG_FILE_HELP),
) -> None:
    """Describe an IMI run or a generated dashboard.

    For an IMI run, without converting it: its layout, periods and gaps,
    dates, grid, buffer elements, the sectors and cadence convert would use,
    and missing files. For a dashboard (a folder holding
    data/manifest.json): when and by which converter version it was built,
    from which input folder, its layout, number of periods, grid and
    region."""
    with _console(verbose, quiet, style=log_style, log_file=log_file):
        log.info("imi-dashboard %s info %s", __version__, input_folder)
        from .info import (
            dashboard_info_json,
            describe,
            format_dashboard_info,
            format_info,
            is_dashboard,
            run_info_json,
        )

        if is_dashboard(input_folder):
            text = (
                _json_text(dashboard_info_json(input_folder)) if as_json
                else format_dashboard_info(input_folder)
            )
        else:
            run = describe(input_folder, _load_config(config), period_dates=as_json)
            text = _json_text(run_info_json(run)) if as_json else format_info(run)
        log.info("%s", text, extra=FILE_ONLY)
    typer.echo(text)


def _json_text(obj: dict) -> str:
    """Strict JSON (no NaN or Infinity), indented, ASCII only."""
    import json

    return json.dumps(obj, indent=2, allow_nan=False)


SERVE_EPILOG = f"""
Exit status:
  0    stopped with Ctrl+C
  1    an error: the folder is not a dashboard, the port is in use, the
       address cannot be listened on
{EXIT_USAGE}

Examples:
  imi-dashboard serve site/ --open
  imi-dashboard serve site/ --port 0 -v
"""


@app.command(cls=_Command, epilog=SERVE_EPILOG)
def serve(
    dashboard: Path = typer.Argument(..., metavar="DASHBOARD", help="A generated dashboard folder (the output of `convert`)."),
    port: int = typer.Option(8000, "--port", "-p", metavar="PORT", help="Port to listen on; 0 picks a free one."),
    bind: str = typer.Option("127.0.0.1", "--bind", metavar="ADDRESS", help="Address to listen on. 0.0.0.0 makes the dashboard reachable from other machines on the network."),
    open_browser: bool = typer.Option(False, "--open", help="Open the dashboard in the default browser."),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, metavar="", show_default=False, help=_verbose_help("every request, not only the failed ones", "the reason given for each failed request and ")),
    quiet: bool = typer.Option(False, "--quiet", "-q", help=_quiet_help(", no request lines", "The address line on stdout is printed as usual.")),
    log_style: str | None = typer.Option(None, "--log-style", metavar="auto|screen|log", show_default=False, help=LOG_STYLE_HELP),
    log_file: Path | None = typer.Option(None, "--log-file", dir_okay=False, metavar=FILE, help=LOG_FILE_HELP),
) -> None:
    """Serve a generated dashboard locally, for previewing.

    Failed requests (status 400 and above) are printed on stderr as
    warnings, every request with -v. Not for production hosting: to share
    a dashboard, upload its folder to a static host."""
    from .serve import is_loopback, make_server, server_url

    # _console for the warnings' and the request lines' format, and for the
    # `error:` line of a folder that is not a dashboard (a ValueError).
    with _console(verbose, quiet, style=log_style, log_file=log_file):
        log.info("imi-dashboard %s serve %s", __version__, dashboard)
        try:
            server = make_server(dashboard, bind=bind, port=port)
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                raise ValueError(f"port {port} is in use; choose another with --port") from exc
            raise
        url = server_url(server)
        log.warning(SERVE_LOCAL_WARNING)
        if not is_loopback(server):
            log.warning(SERVE_NETWORK_WARNING, bind)
        result = f"Serving {dashboard} at {url} (press Ctrl+C to stop)"
        log.info("%s", result, extra=FILE_ONLY)
        typer.echo(result)
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


INIT_CONFIG_EPILOG = f"""
Exit status:
  0    written
  1    an error: the output file exists (without --overwrite) or is a
       folder, the input is not an IMI run, no period is complete
{EXIT_USAGE}

Examples:
  imi-dashboard init-config run/
  imi-dashboard init-config run/ -o romania.yaml --overwrite
  imi-dashboard init-config run/ -o -
  imi-dashboard init-config
"""


@app.command("init-config", cls=_Command, epilog=INIT_CONFIG_EPILOG)
def init_config(
    input_folder: Path | None = typer.Argument(
        None,
        show_default=False,
        metavar="[INPUT_FOLDER]",
        help=(
            "IMI run folder (one period or a timeseries) to take the values from: the "
            "detected sectors, the cadence and the text it gives. Without it, the template "
            "with illustrative examples is written."
        ),
    ),
    output: Path = typer.Option(Path("dashboard.yaml"), "--output", "-o", metavar=FILE, help="File to write; '-' writes to stdout."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace the output file if it exists."),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, metavar="", show_default=False, help=_verbose_help("the files read")),
    quiet: bool = typer.Option(False, "--quiet", "-q", help=_quiet_help("", "The 'wrote' line on stdout is left out too; -o - still prints the config.")),
    log_style: str | None = typer.Option(None, "--log-style", metavar="auto|screen|log", show_default=False, help=LOG_STYLE_HELP),
    log_file: Path | None = typer.Option(None, "--log-file", dir_okay=False, metavar=FILE, help=LOG_FILE_HELP),
) -> None:
    """Write a commented config file from an IMI run.

    The file documents every key: the title (display_name, area_name), the
    branding (header_brand, logo, brand_name, brand_tagline), the
    sectors (the sectors list), the region, the map, the chart and the text.
    Under each key it shows the value the dashboard uses without a config,
    as detected in INPUT_FOLDER. Every key is commented out, so the file as
    written changes nothing; uncomment and edit only what you want to
    change, then pass the file to convert with --config."""
    to_stdout = str(output) == "-"
    with _console(verbose, quiet, style=log_style, log_file=log_file):
        if not to_stdout:
            if output.is_dir():
                raise ValueError(f"{output} is a folder; name the file to write, e.g. -o {output / 'dashboard.yaml'}")
            if output.exists() and not overwrite:
                raise ValueError(f"{output} already exists; pass --overwrite to replace it")
        from .init_config import check_input, render_for_run, render_generic

        if input_folder is None:
            text, detail = render_generic(), ""
        else:
            log.info("imi-dashboard %s init-config %s", __version__, input_folder)
            check_input(input_folder)
            from .info import describe

            run = describe(input_folder)
            text = render_for_run(run)
            detail = (
                f" with the values of {input_folder.name} ({len(run.config.sectors)} sectors, "
                f"cadence {run.cadence})"
            )
        if to_stdout:
            # UTF-8 and LF whatever the platform, as in a written file;
            # the text has non-ASCII characters (·, –, →).
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", newline="\n")
            sys.stdout.write(text)
            sys.stdout.flush()
            return
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8", newline="\n")
        result = f"wrote {output}{detail}; edit it, then pass it with --config {output}"
        log.info("%s", result, extra=FILE_ONLY)
    if not quiet:
        typer.echo(result)


def main() -> None:
    """Console-script entry point.

    Usage errors are reported by _CommandGroup in either style. In the log
    style (IMI_DASHBOARD_LOG_STYLE, or stderr not a terminal: --log-style is
    not parsed yet when typer sets up), switch off typer's rich formatting:
    --help is then plain click text, and an unexpected exception prints
    Python's standard traceback instead of rich's."""
    style = _style_from_env()
    # An unknown value is treated as auto here; the command rejects it.
    if style == "log" or (style != "screen" and not sys.stderr.isatty()):
        app.rich_markup_mode = None
        app.pretty_exceptions_enable = False
    app()


if __name__ == "__main__":
    main()
