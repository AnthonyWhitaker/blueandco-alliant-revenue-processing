"""
Write the API's OpenAPI schema to revenue_engine/openapi.json without starting a server.
The frontend generates its TypeScript types from this file.

    python scripts/export_openapi.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.main import app  # noqa: E402

out = ROOT / "openapi.json"
out.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
print(f"Wrote {out.relative_to(ROOT)}")
