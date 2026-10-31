"""Where the web files live."""
from __future__ import annotations

from pathlib import Path

from app.config import settings


def frontend_dir() -> str:
    """Carpeta `frontend/` (plantillas + estáticos + consola de administración). `FRONTEND_DIR` manda; si no, se busca subiendo desde este
    fichero (en el repo está en la raíz; en la imagen Docker, en /app/frontend)."""
    if settings.FRONTEND_DIR:
        return settings.FRONTEND_DIR
    for parent in Path(__file__).resolve().parents:
        if (parent / "frontend" / "templates" / "index.html").is_file():
            return str(parent / "frontend")
    raise RuntimeError("No se encuentra la carpeta frontend/ (define FRONTEND_DIR).")
