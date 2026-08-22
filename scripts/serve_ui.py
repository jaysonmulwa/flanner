"""Run the local web UI against a seeded catalog, for looking at it."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
# Must be set before flanner.web is imported: the app resolves its home at import time.
os.environ.setdefault("FLANNER_HOME", str(ROOT / ".ui-preview"))

import uvicorn  # noqa: E402

from flanner.web import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8094")), log_level="info")
