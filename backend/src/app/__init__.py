"""EVBro — Telemetría, analítica y control autoalojado para vehículos Ebro."""
import os

__version__ = "0.1.0"                       # keep in step with backend/pyproject.toml (a test checks it)


def build_commit() -> str | None:
    """Commit the image was built from (`EVBRO_COMMIT` build argument), or None when unknown."""
    value = (os.environ.get("EVBRO_COMMIT") or "").strip()
    return value[:40] or None
