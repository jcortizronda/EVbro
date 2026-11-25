"""Optional extensions of the admin console, for one installation's own needs. Empty in the public repository.

A module here may define:
  - `admin_router`: an `APIRouter` the console mounts under `/api`, behind the administrator's session (like its own routes);
  - `ADMIN_SCRIPT`: the name of a `.js` file next to the module, served at `/ext/<module>.js` and loaded by the console page after
    its own script (`frontend/admin/assets/admin.js`), so it can add controls to the existing tabs.

`discover()` lists them; a module that fails to import is logged and skipped, so a broken extension never takes the console down.
"""
from __future__ import annotations

import importlib
import logging
import os
import pkgutil
from dataclasses import dataclass
from typing import Any, Optional

_LOGGER = logging.getLogger("app.extensions")
HERE = os.path.dirname(__file__)


@dataclass
class Extension:
    name: str
    admin_router: Optional[Any] = None
    script_path: Optional[str] = None


def discover() -> list[Extension]:
    found = []
    for info in sorted(pkgutil.iter_modules([HERE]), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{__name__}.{info.name}")
        except Exception as e:          # noqa: BLE001 — one broken extension must not stop the console
            _LOGGER.error("Admin extension %s could not be loaded: %s", info.name, type(e).__name__)
            continue
        script = getattr(module, "ADMIN_SCRIPT", None)
        path = os.path.join(HERE, script) if script else None
        found.append(Extension(info.name, getattr(module, "admin_router", None), path if path and os.path.isfile(path) else None))
    return found
