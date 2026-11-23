"""Subtitle templates and the font catalogue/download/resolution."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.modules.subtitles import fonts
from app.modules.subtitles.templates import TEMPLATES, get_template, list_templates
from app.schemas.render import FontFile
from app.schemas.subtitles import SubtitleStyle

_REAL_TTF = fonts.resolve_font("Inter", 400, False)  # resolved before any fonts_dir monkeypatch
REQUIRED_FAMILIES = {"Arial", "Roboto", "Poppins", "Montserrat", "Inter", "Open Sans", "Anton", "Bebas Neue",
                     "Oswald", "Impact"}


def test_templates_are_valid_distinct_and_use_catalogue_fonts() -> None:
    assert set(TEMPLATES) == {"gaming", "podcast", "minimal", "bold_viral", "professional", "reaction"}
    families = {f["family"] for f in fonts.BUILTIN_FONTS}
    signatures = set()
    for key, (name, style) in TEMPLATES.items():
        assert name and isinstance(style, SubtitleStyle)
        SubtitleStyle.model_validate(style.model_dump())  # round-trips through validation
        assert style.font_family in families, key
        signatures.add((style.font_family, style.animation.type, style.background_mode, style.position.preset))
    assert len(signatures) == len(TEMPLATES)
    assert {s.animation.type for _, s in TEMPLATES.values()} >= {"dynamic_emphasis", "word_highlight", "static",
                                                                  "pop", "fade", "karaoke"}
    assert TEMPLATES["bold_viral"][1].uppercase and TEMPLATES["bold_viral"][1].font_weight == 900
    assert TEMPLATES["professional"][1].background_mode == "box"


def test_get_template_returns_independent_copy() -> None:
    style = get_template("gaming")
    style.font_size = 20
    assert TEMPLATES["gaming"][1].font_size != 20
    with pytest.raises(NotFoundError):
        get_template("nope")
    assert [t["key"] for t in list_templates()] == list(TEMPLATES)


def test_catalogue_covers_required_families() -> None:
    entries = {f["family"]: f for f in fonts.font_catalogue()}
    assert REQUIRED_FAMILIES <= set(entries)
    assert len(entries) >= 15
    for e in entries.values():
        assert set(e) >= {"family", "category", "weights", "italic", "source", "available", "note"}
        assert e["source"] in {"google", "system", "fallback"}
    if not Path("C:/Windows/Fonts/impact.ttf").exists():
        assert entries["Impact"]["source"] == "fallback"


def test_resolve_font_nearest_weight_fallbacks_and_custom(tmp_path: Path) -> None:
    inter = fonts.resolve_font("Inter", 600, False)
    assert inter.is_file()
    heavy = fonts.resolve_font("Montserrat", 850, False)  # >= 500: heavier first -> 900
    assert heavy.name.endswith("-900.ttf") or "900" in heavy.name
    light = fonts.resolve_font("Montserrat", 300, False)  # < 500: lighter first, then nearest -> 400
    assert "400" in light.name
    italic = fonts.resolve_font("Montserrat", 700, True)
    assert "italic" in italic.name.lower()
    assert fonts.resolve_font("Bebas Neue", 900, True).is_file()  # single-style family
    unknown = fonts.resolve_font("Totally Unknown Family", 700, False)
    assert unknown == fonts.resolve_font("Inter", 700, False)
    impact = fonts.resolve_font("Impact", 400, False)
    assert impact.is_file()
    custom_path = tmp_path / "MyFont.ttf"
    shutil.copy(inter, custom_path)
    custom = [FontFile(family="My Brand", weight=500, path=custom_path)]
    assert fonts.resolve_font("my brand", 800, False, custom) == custom_path


def test_parse_google_css() -> None:
    css = """@font-face { font-family: 'X'; font-style: italic; font-weight: 700;
      src: url(https://fonts.gstatic.com/s/x/v1/a.ttf) format('truetype'); }
    @font-face { font-family: 'X'; font-style: normal; font-weight: 400;
      src: url(https://fonts.gstatic.com/s/x/v1/b.ttf) format('truetype'); }"""
    assert fonts.parse_google_css(css) == {(700, True): "https://fonts.gstatic.com/s/x/v1/a.ttf",
                                          (400, False): "https://fonts.gstatic.com/s/x/v1/b.ttf"}


@pytest.fixture()
def temp_fonts_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "fonts_dir", tmp_path / "fonts")
    yield tmp_path / "fonts"
    monkeypatch.undo()
    fonts._refresh_catalogue()


def _css_for(request: httpx.Request) -> httpx.Response:
    family = request.url.params["family"]
    name, _, axes = family.partition(":")
    spec = axes.split("@", 1)[1]
    faces = []
    for part in spec.split(";"):
        ital, weight = part.split(",") if "," in part else ("0", part)
        slug = re.sub(r"\W", "", name.lower())
        faces.append(f"@font-face {{ font-family: '{name}'; font-style: {'italic' if ital == '1' else 'normal'}; "
                     f"font-weight: {weight}; src: url(https://fonts.gstatic.com/s/{slug}/{weight}{ital}.ttf) "
                     "format('truetype'); }")
    return httpx.Response(200, text="\n".join(faces))


def test_ensure_builtin_fonts_downloads_and_writes_manifest(temp_fonts_dir: Path) -> None:
    ttf = _REAL_TTF.read_bytes()
    with respx.mock(assert_all_called=False) as router:
        css = router.get(url__startswith=fonts.GOOGLE_CSS_URL).mock(side_effect=_css_for)
        files = router.get(url__startswith="https://fonts.gstatic.com/").mock(
            return_value=httpx.Response(200, content=ttf))
        manifest = fonts.ensure_builtin_fonts()
        assert css.called and files.call_count >= 30
        assert css.calls[0].request.headers["User-Agent"] == fonts.GOOGLE_CSS_USER_AGENT
    assert (temp_fonts_dir / "manifest.json").is_file()
    assert manifest["fonts"]["Montserrat"]["files"]["800"] == "Montserrat-800.ttf"
    assert manifest["fonts"]["Open Sans"]["files"]["700i"] == "OpenSans-700-italic.ttf"
    assert all(e["available"] for e in manifest["fonts"].values() if e["source"] == "google")
    # idempotent: a second run performs no HTTP request at all
    with respx.mock(assert_all_called=False) as router:
        route = router.route().mock(return_value=httpx.Response(500))
        fonts.ensure_builtin_fonts()
        assert not route.called


def test_ensure_builtin_fonts_reports_network_failures(temp_fonts_dir: Path) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(side_effect=httpx.ConnectError("offline"))
        manifest = fonts.ensure_builtin_fonts()
    inter = manifest["fonts"]["Inter"]
    assert inter["available"] is False and inter["errors"]
    assert manifest["fonts"]["Arial"]["source"] in {"system", "fallback"}
