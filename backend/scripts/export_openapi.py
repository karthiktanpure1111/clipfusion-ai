"""Export the FastAPI OpenAPI schema to shared/openapi.json (input for frontend type generation).

Usage:  .venv/Scripts/python.exe scripts/export_openapi.py
Then:   cd ../frontend && npm run gen:api
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("WORKER_MODE", "local")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + (BACKEND / "data" / "openapi-export.db").as_posix())


def main() -> None:
    from app.main import app

    schema = app.openapi()
    out = BACKEND.parent / "shared" / "openapi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
    paths = schema.get("paths", {})
    print(f"Wrote {out} ({len(paths)} paths, {len(schema.get('components', {}).get('schemas', {}))} schemas)")


if __name__ == "__main__":
    main()
