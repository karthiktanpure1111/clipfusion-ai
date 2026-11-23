"""Download the runtime assets ClipFusion needs (idempotent).

* Face-detection models (YuNet ONNX, MediaPipe BlazeFace) -> ``settings.models_dir``
* Built-in OFL subtitle fonts -> ``settings.fonts_dir`` (provided by the subtitles engine)

Each step is independent: a failure is reported and the other steps still run. The exit
code is non-zero only when the primary face model (YuNet) is unavailable.

Usage::

    cd backend && .venv/Scripts/python.exe scripts/download_assets.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _face_models() -> dict:
    from app.modules.faces.models import ensure_face_models

    return ensure_face_models()


def _fonts() -> dict:
    from app.modules.subtitles.fonts import ensure_builtin_fonts  # engineer B2b's module

    return ensure_builtin_fonts()


def main() -> int:
    """Run every download step and print a JSON summary."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    summary: dict[str, object] = {}
    for name, step in (("face_models", _face_models), ("fonts", _fonts)):
        try:
            summary[name] = step()
        except Exception as exc:  # noqa: BLE001 - report and continue with the other assets
            logging.getLogger("download_assets").warning("%s failed: %s", name, exc)
            summary[name] = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(summary, indent=2, default=str))
    faces = summary.get("face_models")
    ok = isinstance(faces, dict) and bool(faces.get("yunet", {}).get("available"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
