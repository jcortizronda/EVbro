"""Where the web files live."""
from __future__ import annotations

from pathlib import Path

from app.config import settings


def frontend_dir() -> str:
    """The `frontend/` folder (templates + static files + admin console). `FRONTEND_DIR` wins; otherwise it is looked up going up from this
    file (in the repo it is at the root; in the Docker image, at /app/frontend)."""
    if settings.FRONTEND_DIR:
        return settings.FRONTEND_DIR
    for parent in Path(__file__).resolve().parents:
        if (parent / "frontend" / "templates" / "index.html").is_file():
            return str(parent / "frontend")
    raise RuntimeError("Cannot find the frontend/ folder (set FRONTEND_DIR).")
