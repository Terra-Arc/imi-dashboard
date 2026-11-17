"""Config schema for imi-dashboard.

The schema mirrors `examples/config.example.yaml`. Every field is optional,
and so is the file itself: the sector list falls back to the sectors found in
the IMI output (`default_sectors`), the cadence to one inferred from the
period length (`cadence_from_days`), and the `text:` block to cadence-derived
defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


Cadence = Literal["weekly", "monthly", "biweekly", "daily", "annual", "custom"]
# Standard scientific colormaps only — keep in sync with RAMP_OPTIONS in
# web/src/map/ramps.ts and the RampName union in web/src/data/types.ts.
RampName = Literal["viridis", "magma", "plasma", "reds"]
ChartStyle = Literal["line", "area", "candle", "bar"]
HeaderBrand = Literal["terra", "custom"]

# The diagnostic maps packed after the sector fields in every period binary,
# in order (mirrored in manifest.fields). Defined here rather than in pack.py
# so SectorConfig can refuse a sector id that would collide with one.
FIELD_LAYOUT_DIAGNOSTICS = [
    ("kernel", "averaging kernel diagonal"),
    ("scale", "posterior scaling factor"),
    ("delta_emissions", "posterior − prior emissions (total)"),
    ("delta_xch4", "posterior − prior ΔXCH4 from observations"),
    ("obs_count", "TROPOMI super-observation count"),
    ("tropomi_xch4", "period-averaged TROPOMI XCH4"),
]
DIAGNOSTIC_FIELD_NAMES = tuple(name for name, _ in FIELD_LAYOUT_DIAGNOSTICS)


CADENCE_PRESETS: dict[str, dict[str, str]] = {
    "weekly":   {"singular": "week",   "plural": "weeks",   "adj": "Weekly"},
    "monthly":  {"singular": "month",  "plural": "months",  "adj": "Monthly"},
    "biweekly": {"singular": "period", "plural": "periods", "adj": "Biweekly"},
    "daily":    {"singular": "day",    "plural": "days",    "adj": "Daily"},
    "annual":   {"singular": "year",   "plural": "years",   "adj": "Annual"},
    "custom":   {"singular": "period", "plural": "periods", "adj": "Periodic"},
}


# Standard IMI (HEMCO) sector variables, in display order, with the label,
# short label, colour and primary flag used when the config lists no sectors
# and for the fields a listed standard sector leaves out.
# Primary sectors get their own pill; the rest sit behind "more".
# EmisCH4_Total_ExclSoilAbs is deliberately absent: it is a derived total.
# The total's imi_var here only marks that the run has a total; unless the
# config names one, the variable its map is read from is chosen from the data
# (defaults.total_sector_var), to match the statistics CSV's totals.
DEFAULT_SECTOR_TABLE: tuple[tuple[str, str, str, str, str, bool], ...] = (
    # id, imi_var, label, short, color, primary
    ("total", "EmisCH4_Total", "Total", "Total", "var(--brand-primary)", True),
    ("oil", "EmisCH4_Oil", "Oil", "Oil", "var(--arc-orange)", True),
    ("gas", "EmisCH4_Gas", "Gas", "Gas", "#7A4E9E", True),
    ("coal", "EmisCH4_Coal", "Coal", "Coal", "var(--arc-red)", True),
    ("livestock", "EmisCH4_Livestock", "Livestock", "Livestock", "var(--arc-lime)", True),
    ("landfills", "EmisCH4_Landfills", "Landfills", "Landfills", "var(--arc-yellow)", True),
    ("wastewater", "EmisCH4_Wastewater", "Wastewater", "Wastewater", "#A0826D", True),
    ("rice", "EmisCH4_Rice", "Rice", "Rice", "#5E8C31", False),
    ("other_anth", "EmisCH4_OtherAnth", "Other anthropogenic", "Other anth.", "#6B7280", False),
    ("wetlands", "EmisCH4_Wetlands", "Wetlands", "Wetlands", "var(--arc-blue)", False),
    ("biomass", "EmisCH4_BiomassBurn", "Biomass burning", "Biomass", "#9B4A6E", False),
    ("reservoirs", "EmisCH4_Reservoirs", "Reservoirs", "Reservoirs", "#3D7EA6", False),
    ("lakes", "EmisCH4_Lakes", "Lakes", "Lakes", "#7FB3D5", False),
    ("seeps", "EmisCH4_Seeps", "Geological seeps", "Seeps", "#4B5563", False),
    ("termites", "EmisCH4_Termites", "Termites", "Termites", "#B7950B", False),
    ("soil_absorb", "EmisCH4_SoilAbsorb", "Soil absorption", "Soil sink", "#8A9A5B", False),
)
_DERIVED_SECTOR_VARS = {"EmisCH4_Total_ExclSoilAbs"}
_FALLBACK_SECTOR_COLOR = "#9CA3AF"
_STANDARD_SECTORS = {row[0]: row for row in DEFAULT_SECTOR_TABLE}


class SectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    imi_var: str | None = None
    label: str
    # A shorter label, written to config.json but not shown by the current
    # web app. Default: the standard one for a standard id, else the label
    # (always set once validated).
    short: str = ""
    color: str
    primary: bool = False

    @model_validator(mode="before")
    @classmethod
    def _fill_standard_sector(cls, data):
        # A standard sector id (one in DEFAULT_SECTOR_TABLE) takes the fields
        # it leaves out from that table, so `- id: coal` is a complete entry.
        # Any other id must give label, color and imi_var itself; its short
        # label is the label unless given (_default_short).
        if isinstance(data, dict) and data.get("id") in _STANDARD_SECTORS:
            _, imi_var, label, short, color, primary = _STANDARD_SECTORS[data["id"]]
            defaults = {"imi_var": imi_var, "label": label, "short": short, "color": color, "primary": primary}
            if data["id"] == "total":
                # Left unset: chosen from the data by fill_defaults_from_data.
                del defaults["imi_var"]
            data = {**defaults, **{k: v for k, v in data.items() if v is not None}}
        return data

    @model_validator(mode="after")
    def _default_short(self) -> "SectorConfig":
        if not self.short:
            self.short = self.label
        return self

    @model_validator(mode="after")
    def _require_imi_var(self) -> "SectorConfig":
        if not self.imi_var and self.id != "total":
            raise ValueError(f"sector {self.id!r}: imi_var is required for an id that is not a standard sector")
        return self

    @model_validator(mode="after")
    def _id_not_a_diagnostic(self) -> "SectorConfig":
        # Sector and diagnostic fields share one namespace in the period
        # binaries and the manifest, so a sector named like a diagnostic map
        # would replace that map.
        if self.id in DIAGNOSTIC_FIELD_NAMES:
            raise ValueError(
                f"sector id {self.id!r} is the name of a diagnostic map "
                f"({', '.join(DIAGNOSTIC_FIELD_NAMES)}); choose another id"
            )
        return self


def default_sectors(available_vars: Iterable[str]) -> list["SectorConfig"]:
    """Sector list used when the config gives none.

    ``available_vars`` are the ``EmisCH4_*`` variables usable for this run
    (the caller intersects posterior.nc variables with the viz CSV columns).
    Known sectors come in ``DEFAULT_SECTOR_TABLE`` order; any other
    ``EmisCH4_*`` variable follows alphabetically as a non-primary sector
    labelled by its name.
    """
    avail = set(available_vars)
    out: list[SectorConfig] = []
    known = set()
    for sid, var, label, short, color, primary in DEFAULT_SECTOR_TABLE:
        known.add(var)
        if var in avail:
            # The total's imi_var stays unset, as in a config's `- id: total`.
            out.append(SectorConfig(id=sid, imi_var=None if sid == "total" else var, label=label, short=short, color=color, primary=primary))
    for var in sorted(avail - known - _DERIVED_SECTOR_VARS):
        if not var.startswith("EmisCH4_"):
            continue
        stem = var.removeprefix("EmisCH4_")
        out.append(SectorConfig(id=stem.lower(), imi_var=var, label=stem, short=stem,
                                color=_FALLBACK_SECTOR_COLOR, primary=False))
    return out


def cadence_from_days(days: float | None) -> "Cadence":
    """Map one period's length (EndDate - StartDate, days; IMI's EndDate is
    exclusive) to a cadence preset. Anything unrecognised is ``custom``."""
    if days is None:
        return "custom"
    if days == 1:
        return "daily"
    if 6 <= days <= 8:
        return "weekly"
    if 13 <= days <= 15:
        return "biweekly"
    if 28 <= days <= 31:
        return "monthly"
    if 365 <= days <= 366:
        return "annual"
    return "custom"


# OpenStreetMap's standard tile server and the attribution its usage policy
# requires on every map (https://operations.osmfoundation.org/policies/tiles/).
OSM_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OSM_ATTRIBUTION = "© OpenStreetMap contributors"


class MapConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ramp: RampName = "reds"
    scale: Literal["linear", "log"] = "linear"

    # Percentile (0–100) used to pick the top of the emission colorbar instead
    # of the raw global maximum. A single hot cell no longer stretches the whole
    # scale; 100 reproduces the old "use the max" behaviour.
    max_percentile: float = Field(default=98.0, ge=0.0, le=100.0)

    # Draw the on-map colorbar over a partially transparent background so values
    # sitting behind the legend stay visible. False = solid white.
    colorbar_transparent_bg: bool = True

    # The base map: a raster tile address template ({z}/{x}/{y}, or {quadkey}
    # or {bbox-epsg-3857}, as MapLibre accepts), or "none" for no base map
    # (the data on a plain background). The default is OpenStreetMap's own
    # tile server, whose usage policy allows normal interactive viewing only.
    tiles: str = OSM_TILES
    # The attribution shown on every map. Defaults to OpenStreetMap's for the
    # default tiles; required with any other tile address.
    tiles_attribution: str | None = None
    tiles_max_zoom: int = Field(default=19, ge=0, le=24)
    tile_size: Literal[256, 512] = 256
    # MapLibre's compact attribution control: a small box with an (i) button,
    # which collapses to the button alone once the overview map is dragged.
    # False keeps the attribution text always visible.
    attribution_compact: bool = True

    @model_validator(mode="after")
    def _check_tiles(self) -> "MapConfig":
        if self.tiles == "none":
            return self
        if not self.tiles.startswith(("https://", "http://")):
            raise ValueError(f"map.tiles must be an http(s) address template or 'none', not {self.tiles!r}")
        if not (
            all(p in self.tiles for p in ("{z}", "{x}", "{y}"))
            or "{quadkey}" in self.tiles
            or "{bbox-epsg-3857}" in self.tiles
        ):
            raise ValueError(
                "map.tiles must contain {z}, {x} and {y} (or {quadkey}, or {bbox-epsg-3857}): " + self.tiles
            )
        if self.tiles != OSM_TILES and not self.tiles_attribution:
            raise ValueError("map.tiles_attribution is required with a map.tiles other than OpenStreetMap's")
        return self

    def resolved_tiles(self) -> dict[str, object]:
        """The base map as the web app reads it (config.json's `map`)."""
        if self.tiles == "none":
            return {"tiles": None, "tilesAttribution": None, "attributionCompact": self.attribution_compact}
        return {
            "tiles": self.tiles,
            # The validator above makes tiles_attribution present for any other tiles.
            "tilesAttribution": self.tiles_attribution or OSM_ATTRIBUTION,
            "tilesMaxZoom": self.tiles_max_zoom,
            "tileSize": self.tile_size,
            "attributionCompact": self.attribution_compact,
        }


class ChartConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_style: ChartStyle = "candle"
    default_speed: int = 3


class TextConfig(BaseModel):
    """All user-facing copy is overridable.

    `cadence` is the single switch that drives every cadence-derived default.
    Left unset, the converter infers it from the period length. All other
    fields are optional and fall back to the cadence defaults.
    """

    model_config = ConfigDict(extra="forbid")

    cadence: Cadence | None = None
    cadence_singular: str | None = None
    cadence_plural: str | None = None

    heading_context: str | None = None
    overview_heading: str | None = None
    diagnostics_heading: str | None = None

    heading_context_timeseries: str | None = None
    heading_context_single: str | None = None
    heading_context_diagnostics: str | None = None

    subtitle_timeseries: str | None = None
    subtitle_single: str | None = None
    subtitle_diagnostics_timeseries: str | None = None
    subtitle_diagnostics_single: str | None = None

    chart_title_split: str | None = None
    chart_subtitle_split: str | None = None
    chart_title_chart_first: str | None = None
    chart_subtitle_chart_first: str | None = None

    speed_unit: str | None = None
    diagnostics_spatial_label: str | None = None
    diagnostics_scalar_label: str | None = None

    footer_attribution: str | None = None
    footer_disclaimer: str | None = None
    footer_version: str | None = None

    def resolved(
        self,
        *,
        area_name: str | None,
        overview_heading_fallback: str,
        default_cadence: Cadence = "custom",
    ) -> dict[str, str]:
        """Return a dict with every key filled in.

        Cadence-derived defaults are applied for any field left as ``None``.
        The returned dict is what the converter writes into ``config.json``.
        The frontend fills the ``{count}``, ``{date}``, ``{date_start}``,
        ``{date_end}``, ``{years}``, ``{cadence_singular}`` and
        ``{cadence_plural}`` placeholders in the ``subtitle_*`` and
        ``chart_*`` values and in ``footer_attribution``.
        """
        cadence = self.cadence or default_cadence
        preset = CADENCE_PRESETS[cadence]
        sing = self.cadence_singular or preset["singular"]
        plur = self.cadence_plural or preset["plural"]
        adj = preset["adj"]  # "Weekly" / "Monthly" / etc.

        return {
            "cadence": cadence,
            "cadence_singular": sing,
            "cadence_plural": plur,
            "heading_context": self.heading_context or "Integrated Methane Inversion",
            "overview_heading": self.overview_heading or overview_heading_fallback,
            "diagnostics_heading": self.diagnostics_heading or "Inversion diagnostics",
            "heading_context_timeseries": self.heading_context_timeseries or f"{adj} timeseries",
            "heading_context_single": self.heading_context_single or "Single inversion",
            "heading_context_diagnostics": self.heading_context_diagnostics or "Diagnostics",
            "subtitle_timeseries": (
                self.subtitle_timeseries
                or f"{adj} Kalman-filter inversion · {{date_start}} – {{date_end}}"
            ),
            "subtitle_single": (
                self.subtitle_single
                or "Single period inversion · {date_start} → {date_end}"
            ),
            "subtitle_diagnostics_timeseries": (
                self.subtitle_diagnostics_timeseries
                or f"Convergence, observation coverage, and run-quality checks · {{count}} {plur} inversions"
            ),
            "subtitle_diagnostics_single": (
                self.subtitle_diagnostics_single
                or "Convergence, observation coverage, and run-quality checks · single inversion"
            ),
            "chart_title_split": (
                self.chart_title_split
                or (f"{adj} emissions over {area_name}" if area_name else f"{adj} methane emissions")
            ),
            "chart_subtitle_split": (
                self.chart_subtitle_split
                or f"Drag the playhead or click any {sing} to inspect that period on the map."
            ),
            "chart_title_chart_first": (
                self.chart_title_chart_first or f"{adj} methane emissions"
            ),
            "chart_subtitle_chart_first": (
                self.chart_subtitle_chart_first
                or f"{{count}} consecutive Kalman-filter inversions · click any {sing} to see its spatial map below"
            ),
            "speed_unit": self.speed_unit or f"{plur}/sec",
            "diagnostics_spatial_label": self.diagnostics_spatial_label or "Spatial diagnostics",
            "diagnostics_scalar_label": self.diagnostics_scalar_label or "Scalar diagnostics",
            "footer_attribution": (
                self.footer_attribution
                if self.footer_attribution is not None
                else (
                    "Methane inversions were created through Harvard's "
                    "Integrated Methane Inversion (IMI) framework, an "
                    "open-source tool developed by the Atmospheric Chemistry "
                    "Modeling Group at Harvard University. Satellite "
                    "observations from the TROPOMI instrument aboard ESA's "
                    "Sentinel-5P; contains modified Copernicus Sentinel data "
                    "({years}). Prior emissions from EDGAR v8 and GFEI v3 "
                    "and other inventories (cf. official docs for a full list)."
                )
            ),
            "footer_disclaimer": (
                self.footer_disclaimer
                if self.footer_disclaimer is not None
                else (
                    "Estimates are derived from atmospheric inverse modeling "
                    "and carry uncertainty beyond the bounds reported with "
                    "each value. Results are intended for research and "
                    "evaluation alongside other data sources, like bottom up "
                    "inventories and facility-level emission detections. "
                    "Boundaries shown are approximate and do not imply "
                    "endorsement of any territorial claim."
                )
            ),
            "footer_version": (
                self.footer_version
                if self.footer_version is not None
                else "Integrated Methane Inversion"
            ),
        }


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The name of the area the run covers, used in titles ("Monthly
    # emissions over Romania").
    area_name: str | None = None
    # The dashboard's name: the browser tab's title, the title of the
    # dashboard's README and the overview page's heading (which
    # text.overview_heading changes alone). Default: "Methane emissions over
    # <area_name>", or "Methane emissions" without one.
    display_name: str = "Methane emissions"

    # Spatial extent written to the dashboard:
    #   "domain" — the complete inversion region: every cell, including the
    #              buffer elements around the region of interest.
    #   "roi"    — the region of interest only: buffer elements are dropped
    #              (their cells masked out) and the grid is cropped to the
    #              tightest box around what remains.
    # A CLI --region flag overrides this when given. Without a state vector,
    # roi falls back to domain.
    region: Literal["domain", "roi"] = "roi"

    # "terra": Terra Arc's logo and name in the header. "custom": the
    # header shows logo, brand_name and brand_tagline, whichever are
    # set (none: no brand at all), and the footer credits the software,
    # "Made with IMI Dashboard by Terra Arc", with a link to the project.
    header_brand: HeaderBrand = "terra"
    logo: Path | None = None
    brand_name: str | None = None
    brand_tagline: str | None = None

    # Empty = every sector found in the IMI output (see default_sectors).
    sectors: list[SectorConfig] = Field(default_factory=list)
    map: MapConfig = Field(default_factory=MapConfig)
    chart: ChartConfig = Field(default_factory=ChartConfig)
    text: TextConfig = Field(default_factory=TextConfig)

    @model_validator(mode="before")
    @classmethod
    def _default_display_name(cls, data):
        if isinstance(data, dict) and not data.get("display_name") and data.get("area_name"):
            data = {**data, "display_name": f"Methane emissions over {data['area_name']}"}
        return data

    @model_validator(mode="after")
    def _unique_sector_ids(self) -> "Config":
        ids = [s.id for s in self.sectors]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate sector ids: {', '.join(dupes)}")
        return self


def load_config(path: Path | None) -> Config:
    """Load + validate a YAML config file; ``None`` gives the defaults.

    A relative ``logo`` is resolved against the config file's folder,
    so the config works from any current directory. (A Config built in code
    keeps the path as given, relative to the current directory.)"""
    if path is None:
        return Config()
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cfg = Config.model_validate(raw)
    if cfg.logo is not None and not cfg.logo.is_absolute():
        cfg = cfg.model_copy(update={"logo": Path(path).parent / cfg.logo})
    return cfg
