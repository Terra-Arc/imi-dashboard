import re
from pathlib import Path

import pytest
import yaml

from imi_dashboard.config import Config, cadence_from_days, default_sectors, load_config

from .conftest import EXAMPLE_CONFIG, REPO_ROOT


def _minimal(**overrides) -> dict:
    base: dict = {
        "area_name": "Romania",
        "sectors": [
            {"id": "total", "label": "Total", "short": "Total", "color": "red", "primary": True},
            {"id": "oil", "imi_var": "EmisCH4_Oil", "label": "Oil", "short": "Oil", "color": "orange", "primary": True},
        ],
    }
    base.update(overrides)
    return base


def test_example_config_validates():
    c = load_config(EXAMPLE_CONFIG)
    assert c.area_name == "Romania"
    assert c.header_brand == "terra"
    assert len(c.sectors) >= 2
    assert c.sectors[0].id == "total"
    assert c.sectors[0].imi_var is None  # chosen from the data at conversion
    assert c.sectors[1].imi_var == "EmisCH4_Coal"
    assert c.text.cadence == "monthly"


def test_total_sector_imi_var_optional():
    """A total sector without imi_var is valid; its variable is chosen from
    the data at conversion (defaults.total_sector_var). One that names a
    variable keeps it."""
    c = Config.model_validate(_minimal())
    total = next(s for s in c.sectors if s.id == "total")
    assert total.imi_var is None
    c = Config.model_validate({"sectors": [{"id": "total", "imi_var": "EmisCH4_Total"}]})
    assert c.sectors[0].imi_var == "EmisCH4_Total"


def test_non_standard_sector_requires_imi_var():
    from pydantic import ValidationError

    raw = _minimal(sectors=[{"id": "flaring", "label": "Flaring", "short": "Flaring", "color": "green"}])
    with pytest.raises(ValidationError, match="sector 'flaring': imi_var is required"):
        Config.model_validate(raw)


def test_standard_sector_fills_missing_fields_from_the_table():
    """`- id: coal` is enough: label, short, color, imi_var and primary come
    from DEFAULT_SECTOR_TABLE; a field that is given wins."""
    c = Config.model_validate({"sectors": [{"id": "total"}, {"id": "coal"}, {"id": "rice", "label": "Paddy rice"}]})
    total, coal, rice = c.sectors
    assert (total.imi_var, total.label, total.short, total.color, total.primary) == (
        None, "Total", "Total", "var(--brand-primary)", True)
    assert (coal.imi_var, coal.label, coal.short, coal.color, coal.primary) == (
        "EmisCH4_Coal", "Coal", "Coal", "var(--arc-red)", True)
    assert (rice.imi_var, rice.label, rice.short, rice.primary) == ("EmisCH4_Rice", "Paddy rice", "Rice", False)


def test_non_standard_sector_needs_label_and_color():
    """A sector that is not standard gives its label and colour; its short
    label is the label unless given."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as info:
        Config.model_validate({"sectors": [{"id": "flaring", "imi_var": "EmisCH4_Flaring"}]})
    missing = sorted(e["loc"][-1] for e in info.value.errors() if e["type"] == "missing")
    assert missing == ["color", "label"]
    flaring, venting = Config.model_validate({"sectors": [
        {"id": "flaring", "imi_var": "EmisCH4_Flaring", "label": "Gas flaring", "color": "red"},
        {"id": "venting", "imi_var": "EmisCH4_Venting", "label": "Venting", "short": "Vent", "color": "blue"},
    ]}).sectors
    assert (flaring.label, flaring.short) == ("Gas flaring", "Gas flaring")
    assert (venting.label, venting.short) == ("Venting", "Vent")


def test_custom_brand_needs_no_logo():
    """header_brand: custom shows the user's logo, name and tagline,
    whichever are given; with none of them the header has no brand."""
    c = Config.model_validate(_minimal(header_brand="custom"))
    assert (c.header_brand, c.logo, c.brand_name) == ("custom", None, None)
    c = Config.model_validate(_minimal(header_brand="custom", logo="./logo.png"))
    assert c.logo == Path("./logo.png")


def test_no_config_gives_neutral_defaults():
    """Without a file every key has a default, and none of them names a
    particular area, IMI version or cadence."""
    c = load_config(None)
    assert c == Config()
    assert c.area_name is None
    assert c.region == "roi"
    assert c.sectors == []          # filled from the IMI output at conversion
    assert c.text.cadence is None   # inferred from the period length
    t = c.text.resolved(area_name=c.area_name, overview_heading_fallback=c.display_name)
    assert t["cadence"] == "custom"
    assert t["chart_title_split"] == "Periodic methane emissions"
    assert t["footer_version"] == "Integrated Methane Inversion"
    assert t["overview_heading"] == "Methane emissions"


def test_empty_sectors_allowed():
    assert Config.model_validate({"sectors": []}).sectors == []


def test_duplicate_sector_ids_rejected():
    raw = _minimal(sectors=[
        {"id": "oil", "imi_var": "EmisCH4_Oil", "label": "Oil", "short": "Oil", "color": "orange"},
        {"id": "oil", "imi_var": "EmisCH4_Gas", "label": "Gas", "short": "Gas", "color": "purple"},
    ])
    with pytest.raises(Exception, match="duplicate sector ids: oil"):
        Config.model_validate(raw)


def test_packaged_template_is_all_defaults():
    """The init-config template documents the keys without setting any, so
    loading it untouched is the same as passing no config."""
    template = REPO_ROOT / "tools" / "imi-dashboard" / "imi_dashboard" / "templates" / "config.yaml"
    assert load_config(template) == Config()


def test_packaged_template_documents_every_text_key():
    from imi_dashboard.config import TextConfig
    from imi_dashboard.init_config import render_generic

    template = render_generic()
    undocumented = [key for key in TextConfig.model_fields if key not in template]
    assert undocumented == []
    for placeholder in ("{count}", "{date}", "{date_start}", "{date_end}", "{cadence_singular}", "{cadence_plural}"):
        assert placeholder in template


@pytest.mark.parametrize(
    "days,cadence",
    [(1, "daily"), (7, "weekly"), (14, "biweekly"), (28, "monthly"), (31, "monthly"),
     (365, "annual"), (366, "annual"), (10, "custom"), (None, "custom")],
)
def test_cadence_from_days(days, cadence):
    assert cadence_from_days(days) == cadence


def test_default_sectors_order_and_unknown_variables():
    got = default_sectors([
        "EmisCH4_Wetlands", "EmisCH4_Oil", "EmisCH4_Total", "EmisCH4_Total_ExclSoilAbs", "EmisCH4_Volcano",
    ])
    assert [s.id for s in got] == ["total", "oil", "wetlands", "volcano"]
    assert [s.primary for s in got] == [True, True, False, False]
    assert got[-1].imi_var == "EmisCH4_Volcano" and got[-1].label == "Volcano"


def test_map_tiles_default_to_openstreetmap():
    m = Config().map
    assert m.resolved_tiles() == {
        "tiles": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "tilesAttribution": "© OpenStreetMap contributors",
        "tilesMaxZoom": 19,
        "tileSize": 256,
        "attributionCompact": True,
    }


def test_map_tiles_none_leaves_the_base_map_out():
    m = Config.model_validate({"map": {"tiles": "none", "attribution_compact": False}}).map
    assert m.resolved_tiles() == {"tiles": None, "tilesAttribution": None, "attributionCompact": False}


def test_map_tiles_from_another_provider():
    m = Config.model_validate({"map": {
        "tiles": "https://tiles.example.org/{z}/{x}/{y}@2x.png",
        "tiles_attribution": "© Example Tiles",
        "tiles_max_zoom": 18,
        "tile_size": 512,
    }}).map
    r = m.resolved_tiles()
    assert r["tiles"] == "https://tiles.example.org/{z}/{x}/{y}@2x.png"
    assert (r["tilesAttribution"], r["tilesMaxZoom"], r["tileSize"]) == ("© Example Tiles", 18, 512)


@pytest.mark.parametrize(
    "map_cfg,message",
    [
        ({"tiles": "https://tiles.example.org/{z}/{x}/{y}.png"}, "tiles_attribution is required"),
        ({"tiles": "tiles.example.org/{z}/{x}/{y}.png", "tiles_attribution": "x"}, "http(s) address template or 'none'"),
        ({"tiles": "https://tiles.example.org/map.png", "tiles_attribution": "x"}, "must contain {z}, {x} and {y}"),
        ({"tile_size": 300}, "tile_size"),
    ],
)
def test_map_tiles_invalid(map_cfg, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        Config.model_validate({"map": map_cfg})


def test_unknown_top_level_key_rejected():
    with pytest.raises(Exception):
        Config.model_validate(_minimal(spurious_key="oops"))


def test_text_cadence_weekly():
    c = Config.model_validate(_minimal(text={"cadence": "weekly"}))
    t = c.text.resolved(area_name=c.area_name, overview_heading_fallback=c.display_name)
    assert t["cadence"] == "weekly"
    assert t["cadence_singular"] == "week"
    assert t["cadence_plural"] == "weeks"
    assert t["heading_context_timeseries"] == "Weekly timeseries"
    assert "Weekly Kalman-filter" in t["subtitle_timeseries"]
    assert t["speed_unit"] == "weeks/sec"


def test_text_cadence_monthly_propagates_everywhere():
    c = Config.model_validate(_minimal(text={"cadence": "monthly"}))
    t = c.text.resolved(area_name=c.area_name, overview_heading_fallback=c.display_name)
    assert t["cadence_singular"] == "month"
    assert t["cadence_plural"] == "months"
    assert t["heading_context_timeseries"] == "Monthly timeseries"
    assert "Monthly Kalman-filter" in t["subtitle_timeseries"]
    assert t["speed_unit"] == "months/sec"
    assert "any month to inspect" in t["chart_subtitle_split"]


def test_text_individual_override_wins_over_cadence():
    c = Config.model_validate(_minimal(text={
        "cadence": "monthly",
        "subtitle_timeseries": "Custom subtitle {date}",
    }))
    t = c.text.resolved(area_name=c.area_name, overview_heading_fallback=c.display_name)
    # other monthly defaults still applied
    assert t["heading_context_timeseries"] == "Monthly timeseries"
    # but the explicit override is honoured
    assert t["subtitle_timeseries"] == "Custom subtitle {date}"


def test_overview_heading_falls_back_to_display_name():
    c = Config.model_validate(_minimal(display_name="CH4 over India", area_name="India"))
    t = c.text.resolved(area_name=c.area_name, overview_heading_fallback=c.display_name)
    assert t["overview_heading"] == "CH4 over India"


def test_heading_defaults_to_the_area_name():
    """Without display_name, the heading names the area when one is set,
    and stays "Methane emissions" when not; display_name and then
    text.overview_heading override it."""
    def h1(**raw):
        c = Config.model_validate(raw)
        return c.display_name, c.text.resolved(area_name=c.area_name, overview_heading_fallback=c.display_name)["overview_heading"]

    assert h1(area_name="Romania") == ("Methane emissions over Romania", "Methane emissions over Romania")
    assert h1() == ("Methane emissions", "Methane emissions")
    assert h1(area_name="Romania", display_name="CH4 in Romania") == ("CH4 in Romania", "CH4 in Romania")
    assert h1(area_name="Romania", text={"overview_heading": "Romania 2023"})[1] == "Romania 2023"


def test_footer_attribution_can_be_blanked():
    c = Config.model_validate(_minimal(text={"footer_attribution": ""}))
    t = c.text.resolved(area_name=c.area_name, overview_heading_fallback=c.display_name)
    assert t["footer_attribution"] == ""


@pytest.mark.parametrize("sid", ["scale", "kernel", "tropomi_xch4"])
def test_sector_id_naming_a_diagnostic_map_rejected(sid):
    """Sector and diagnostic fields share one namespace in the period files."""
    from pydantic import ValidationError

    cfg = _minimal()
    cfg["sectors"][1]["id"] = sid
    with pytest.raises(ValidationError, match=f"sector id '{sid}' is the name of a diagnostic map"):
        Config.model_validate(cfg)


def test_relative_logo_resolved_against_the_config_folder(tmp_path: Path):
    folder = tmp_path / "cfg"
    folder.mkdir()
    rel = folder / "rel.yaml"
    rel.write_text("header_brand: custom\nlogo: img/logo.png\n", encoding="utf-8")
    assert load_config(rel).logo == folder / "img" / "logo.png"
    absolute = folder / "abs.yaml"
    absolute.write_text(f"header_brand: custom\nlogo: {tmp_path / 'x.png'}\n", encoding="utf-8")
    assert load_config(absolute).logo == tmp_path / "x.png"
    # A Config built in code keeps the path as given.
    assert Config(header_brand="custom", logo="logo.png").logo == Path("logo.png")
