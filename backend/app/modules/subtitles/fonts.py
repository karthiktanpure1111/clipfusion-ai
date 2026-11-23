"""Built-in subtitle fonts: catalogue, download (Google Fonts, OFL) and resolution.

* :data:`BUILTIN_FONTS` — the catalogue shown in the Subtitle Studio. Each entry is a dict
  ``{"family", "category", "weights", "italic", "source", "available", "note"}`` where
  ``source`` is ``google`` (downloaded OFL font files), ``system`` (proprietary font used
  from the operating system when installed: Arial, Impact) or ``fallback`` (a system font
  that is not installed and is rendered with a documented substitute). ``available`` and
  ``note`` reflect the files currently on disk (refreshed by :func:`ensure_builtin_fonts`).
* :func:`ensure_builtin_fonts` downloads static TTF files into ``settings.fonts_dir``
  (``<Family>-<weight>[-italic].ttf``, e.g. ``OpenSans-700-italic.ttf``) and writes
  ``fonts_dir/manifest.json``.
* :func:`resolve_font` maps ``(family, weight, italic)`` to a font file, preferring user
  uploaded :class:`~app.schemas.render.FontFile` entries, with CSS-like nearest-weight
  matching and documented fallbacks (Arial -> Liberation Sans -> Inter, Impact -> Anton).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import DependencyMissing
from app.schemas.render import FontFile

log = logging.getLogger(__name__)

GOOGLE_CSS_URL = "https://fonts.googleapis.com/css2"
# An old non-browser user agent makes the CSS API answer with plain TTF URLs.
GOOGLE_CSS_USER_AGENT = "Wget/1.12 (linux-gnu)"
DOWNLOAD_TIMEOUT_S = 30.0
MAX_FONT_BYTES = 8 * 1024 * 1024
DEFAULT_FAMILY = "Inter"
_TTF_MAGIC = (b"\x00\x01\x00\x00", b"true", b"OTTO")

_WIN = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
_LINUX_MS = Path("/usr/share/fonts/truetype/msttcorefonts")
_MAC = Path("/System/Library/Fonts/Supplemental")
_MAC_LIB = Path("/Library/Fonts")
_LIBERATION_DIRS = (Path("/usr/share/fonts/truetype/liberation"), Path("/usr/share/fonts/liberation-sans"),
                    Path("/usr/share/fonts/truetype/liberation2"))


@dataclass(frozen=True)
class _FontDef:
    family: str
    category: str
    weights: tuple[int, ...]
    italic: bool = False
    source: str = "google"                  # google | system
    # (weight, italic) -> candidate system file paths (system fonts only)
    system_files: dict[tuple[int, bool], tuple[Path, ...]] = field(default_factory=dict)
    fallbacks: tuple[str, ...] = ()        # tried in order when the family is not installed


def _sys(*names: str) -> tuple[Path, ...]:
    return tuple(Path(n) for n in names)


_ARIAL_FILES = {
    (400, False): _sys(_WIN / "arial.ttf", _LINUX_MS / "Arial.ttf", _MAC / "Arial.ttf", _MAC_LIB / "Arial.ttf"),
    (700, False): _sys(_WIN / "arialbd.ttf", _LINUX_MS / "Arial_Bold.ttf", _MAC / "Arial Bold.ttf"),
    (400, True): _sys(_WIN / "ariali.ttf", _LINUX_MS / "Arial_Italic.ttf", _MAC / "Arial Italic.ttf"),
    (700, True): _sys(_WIN / "arialbi.ttf", _LINUX_MS / "Arial_Bold_Italic.ttf", _MAC / "Arial Bold Italic.ttf"),
}  # Arial Black is a separate family in browsers, so it is deliberately not mapped to 900.
_IMPACT_FILES = {
    (400, False): _sys(_WIN / "impact.ttf", _LINUX_MS / "Impact.ttf", _MAC / "Impact.ttf", _MAC_LIB / "Impact.ttf"),
}
_LIBERATION_FILES = {
    (400, False): tuple(d / "LiberationSans-Regular.ttf" for d in _LIBERATION_DIRS),
    (700, False): tuple(d / "LiberationSans-Bold.ttf" for d in _LIBERATION_DIRS),
    (400, True): tuple(d / "LiberationSans-Italic.ttf" for d in _LIBERATION_DIRS),
    (700, True): tuple(d / "LiberationSans-BoldItalic.ttf" for d in _LIBERATION_DIRS),
}

_FONT_DEFS: tuple[_FontDef, ...] = (
    _FontDef("Arial", "sans-serif", (400, 700), True, "system", _ARIAL_FILES, ("Liberation Sans", "Inter")),
    _FontDef("Roboto", "sans-serif", (400, 700, 800, 900), True),
    _FontDef("Poppins", "sans-serif", (400, 700, 800, 900), True),
    _FontDef("Montserrat", "sans-serif", (400, 700, 800, 900), True),
    _FontDef("Inter", "sans-serif", (400, 600, 700, 800, 900), True),
    _FontDef("Open Sans", "sans-serif", (400, 700, 800), True),
    _FontDef("Anton", "display", (400,)),
    _FontDef("Bebas Neue", "display", (400,)),
    _FontDef("Oswald", "sans-serif", (400, 700)),
    _FontDef("Impact", "display", (400,), False, "system", _IMPACT_FILES, ("Anton",)),
    _FontDef("Lato", "sans-serif", (400, 700, 900), True),
    _FontDef("Bangers", "display", (400,)),
    _FontDef("Archivo Black", "sans-serif", (400,)),
    _FontDef("Nunito", "sans-serif", (400, 700, 800, 900), True),
    _FontDef("Raleway", "sans-serif", (400, 700, 800, 900), True),
    _FontDef("Permanent Marker", "handwriting", (400,)),
    _FontDef("Luckiest Guy", "display", (400,)),
)
# Not listed in the catalogue; only used as a substitute for Arial on Linux.
_HIDDEN_DEFS = (_FontDef("Liberation Sans", "sans-serif", (400, 700), True, "system", _LIBERATION_FILES, ()),)
_DEFS_BY_KEY = {d.family.lower(): d for d in (*_FONT_DEFS, *_HIDDEN_DEFS)}
_lock = threading.Lock()


# ------------------------------------------------------------------ file layout
def _file_name(family: str, weight: int, italic: bool) -> str:
    return f"{family.replace(' ', '')}-{weight}{'-italic' if italic else ''}.ttf"


def _google_path(fonts_dir: Path, family: str, weight: int, italic: bool) -> Path:
    return fonts_dir / _file_name(family, weight, italic)


def _wanted_styles(d: _FontDef) -> list[tuple[int, bool]]:
    styles = [(w, False) for w in d.weights]
    if d.italic:
        styles += [(w, True) for w in d.weights if w in (400, 700)]
    return styles


def _installed_files(d: _FontDef, fonts_dir: Path) -> dict[tuple[int, bool], Path]:
    """(weight, italic) -> existing file for a family (downloaded or system)."""
    found: dict[tuple[int, bool], Path] = {}
    if d.source == "system":
        for key, candidates in d.system_files.items():
            hit = next((p for p in candidates if p.is_file()), None)
            if hit:
                found[key] = hit
        return found
    for w, it in _wanted_styles(d):
        p = _google_path(fonts_dir, d.family, w, it)
        if p.is_file() and p.stat().st_size > 1000:
            found[(w, it)] = p
    return found


# ------------------------------------------------------------------ catalogue
def _catalogue_entry(d: _FontDef, fonts_dir: Path) -> dict:
    files = _installed_files(d, fonts_dir)
    entry = {"family": d.family, "category": d.category, "weights": list(d.weights), "italic": d.italic,
             "source": d.source, "available": bool(files), "note": None}
    if d.source == "google":
        if not files:
            entry["note"] = "Not downloaded yet (run ensure_builtin_fonts)."
        return entry
    if files:
        entry["note"] = "Uses the font installed on this system."
        entry["weights"] = sorted({w for w, _ in files})
        return entry
    for fb in d.fallbacks:
        fb_def = _DEFS_BY_KEY.get(fb.lower())
        if fb_def and _installed_files(fb_def, fonts_dir):
            entry.update(source="fallback", available=True,
                         note=f"{d.family} is not installed on this server; rendered with {fb}.")
            return entry
    entry.update(source="fallback", note=f"{d.family} is not installed and no fallback font is available.")
    return entry


def font_catalogue() -> list[dict]:
    """Fresh catalogue with the current on-disk availability."""
    fonts_dir = get_settings().fonts_dir
    return [_catalogue_entry(d, fonts_dir) for d in _FONT_DEFS]


BUILTIN_FONTS: list[dict] = font_catalogue()


def _refresh_catalogue() -> None:
    BUILTIN_FONTS[:] = font_catalogue()
    _family_index.cache_clear()


# ------------------------------------------------------------------ download
def _google_css_url(d: _FontDef) -> str:
    styles = sorted(_wanted_styles(d), key=lambda s: (s[1], s[0]))
    family = d.family.replace(" ", "+")
    if d.italic:
        axis = ";".join(f"{int(it)},{w}" for w, it in styles)
        return f"{GOOGLE_CSS_URL}?family={family}:ital,wght@{axis}"
    return f"{GOOGLE_CSS_URL}?family={family}:wght@{';'.join(str(w) for w, _ in styles)}"


_FACE_RE = re.compile(r"@font-face\s*{(?P<body>[^}]*)}", re.S)


def parse_google_css(css: str) -> dict[tuple[int, bool], str]:
    """Extract ``(weight, italic) -> TTF url`` from a Google Fonts CSS response."""
    out: dict[tuple[int, bool], str] = {}
    for m in _FACE_RE.finditer(css):
        body = m.group("body")
        weight = re.search(r"font-weight:\s*(\d+)", body)
        style = re.search(r"font-style:\s*(\w+)", body)
        url = re.search(r"url\((https://fonts\.gstatic\.com/[^)]+)\)", body)
        if weight and url:
            out[(int(weight.group(1)), bool(style and style.group(1) == "italic"))] = url.group(1)
    return out


def _download(client, url: str, dest: Path) -> None:  # noqa: ANN001 - httpx.Client
    resp = client.get(url)
    resp.raise_for_status()
    data = resp.content
    if len(data) > MAX_FONT_BYTES or not data.startswith(_TTF_MAGIC):
        raise ValueError(f"unexpected font payload from {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def _fetch_family(client, d: _FontDef, fonts_dir: Path) -> list[str]:  # noqa: ANN001
    """Download the missing styles of one Google family; returns error messages."""
    missing = [s for s in _wanted_styles(d) if not _google_path(fonts_dir, d.family, *s).is_file()]
    if not missing:
        return []
    resp = client.get(_google_css_url(d), headers={"User-Agent": GOOGLE_CSS_USER_AGENT})
    if resp.status_code != 200:
        return [f"Google Fonts CSS request failed ({resp.status_code})"]
    urls = parse_google_css(resp.text)
    errors: list[str] = []
    for w, it in missing:
        url = urls.get((w, it))
        if not url:
            errors.append(f"style {w}{' italic' if it else ''} not offered")
            continue
        try:
            _download(client, url, _google_path(fonts_dir, d.family, w, it))
        except Exception as exc:  # noqa: BLE001 - reported per style
            errors.append(f"style {w}{' italic' if it else ''}: {exc}")
    return errors


def ensure_builtin_fonts() -> dict:
    """Download the OFL catalogue fonts into ``settings.fonts_dir`` (idempotent) and write
    ``manifest.json``. Network failures are reported per family (never raised) so the
    catalogue honestly shows which fonts are available. Returns the manifest dict."""
    import httpx

    fonts_dir = get_settings().fonts_dir
    fonts_dir.mkdir(parents=True, exist_ok=True)
    errors: dict[str, list[str]] = {}
    with _lock, httpx.Client(timeout=DOWNLOAD_TIMEOUT_S, follow_redirects=True) as client:
        for d in _FONT_DEFS:
            if d.source != "google":
                continue
            try:
                errs = _fetch_family(client, d, fonts_dir)
            except Exception as exc:  # noqa: BLE001 - network errors are reported, not raised
                errs = [f"download failed: {exc}"]
            if errs:
                log.warning("Font %s: %s", d.family, "; ".join(errs))
                errors[d.family] = errs
        _refresh_catalogue()
        manifest = _build_manifest(fonts_dir, errors)
        tmp = fonts_dir / "manifest.json.part"
        tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(tmp, fonts_dir / "manifest.json")
    return manifest


def _build_manifest(fonts_dir: Path, errors: dict[str, list[str]]) -> dict:
    fonts: dict[str, dict] = {}
    for d, entry in zip(_FONT_DEFS, BUILTIN_FONTS, strict=True):
        files = _installed_files(d, fonts_dir)
        rel = {f"{w}{'i' if it else ''}": (p.relative_to(fonts_dir).as_posix() if p.is_relative_to(fonts_dir)
                                          else p.as_posix())
               for (w, it), p in sorted(files.items())}
        fonts[d.family] = {**entry, "files": rel, "errors": errors.get(d.family, [])}
    return {"version": 1, "generated_at": datetime.now(UTC).isoformat(), "fonts": fonts}


# ------------------------------------------------------------------ resolution
@lru_cache(maxsize=64)
def _family_index(family_key: str) -> tuple[tuple[int, bool, str], ...]:
    d = _DEFS_BY_KEY.get(family_key)
    if d is None:
        return ()
    return tuple((w, it, str(p)) for (w, it), p in _installed_files(d, get_settings().fonts_dir).items())


def _pick(options: Sequence[tuple[int, bool, str]], weight: int, italic: bool) -> str | None:
    """CSS-like matching: same style first, then nearest weight (heavier preferred for
    requests >= 500, lighter otherwise)."""
    if not options:
        return None
    heavier_first = weight >= 500

    def key(o: tuple[int, bool, str]) -> tuple[int, int, int]:
        w, it, _ = o
        direction = 0 if (w >= weight) == heavier_first or w == weight else 1
        return (int(it != italic), abs(w - weight), direction)

    return min(options, key=key)[2]


def _resolve_family(family_key: str, weight: int, italic: bool, seen: set[str]) -> str | None:
    if family_key in seen:
        return None
    seen.add(family_key)
    hit = _pick(_family_index(family_key), weight, italic)
    if hit:
        return hit
    d = _DEFS_BY_KEY.get(family_key)
    for fb in d.fallbacks if d else ():
        hit = _resolve_family(fb.lower(), weight, italic, seen)
        if hit:
            return hit
    return None


def resolve_font(family: str, weight: int, italic: bool, custom: Sequence[FontFile] = ()) -> Path:
    """Font file for ``family`` at ``weight``/``italic``.

    Order: user fonts (``custom``) of that family -> built-in/system files -> the family's
    documented fallbacks -> :data:`DEFAULT_FAMILY` -> any downloaded font. Raises
    :class:`DependencyMissing` when no font file exists at all."""
    key = family.strip().lower()
    customs = [(f.weight, f.italic, str(f.path)) for f in custom
               if f.family.strip().lower() == key and Path(f.path).is_file()]
    hit = _pick(customs, weight, italic) or _resolve_family(key, weight, italic, set())
    if hit is None and key != DEFAULT_FAMILY.lower():
        log.debug("Font %r unavailable, using %s", family, DEFAULT_FAMILY)
        hit = _resolve_family(DEFAULT_FAMILY.lower(), weight, italic, set())
    if hit is None:
        any_font = next(iter(sorted(get_settings().fonts_dir.glob("*.ttf"))), None)
        if any_font is None:
            raise DependencyMissing("No subtitle fonts are installed. Run ensure_builtin_fonts() "
                                    "(downloads the OFL fonts) or upload a custom font.")
        hit = str(any_font)
    return Path(hit)


def builtin_family_names() -> list[str]:
    """Display names of the catalogue families."""
    return [d.family for d in _FONT_DEFS]
