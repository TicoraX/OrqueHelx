"""OrqueHelx dashboard backend, mounted by Hermes at /api/plugins/orquehelx/.

Hermes imports this file by path and on its own, so there are no relative imports: it uses the plugin modules
already loaded in the process (the dashboard's agent runs here, so the exhaustion registry is the same one)
and, if they are not loaded, loads them by path.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

_ROOT = Path(__file__).resolve().parents[1]
router = APIRouter()


_PACKAGE = "_orquehelx_dashboard"
# Hermes loads the plugin once per home: hermes_plugins.orquehelx for the first, with __home_<digest> for others.
_LOADED_QUOTA = re.compile(r"^(hermes_plugins\.orquehelx(?:__home_[0-9a-f]+)?)\.quota$")


def _module(name: str):
    """The plugin's module for the home of this request: the copy the agent loaded for that home (same
    exhaustion registry), or a copy of our own when the plugin is not loaded for it in this process."""
    from hermes_constants import get_hermes_home
    for key, module in list(sys.modules.items()):
        match = _LOADED_QUOTA.match(key)
        # getattr: a copy from before v0.2.0 (still loaded during an upgrade) has no HOME.
        if match and getattr(module, "HOME", None) and module.HOME == module.home_key(get_hermes_home()):
            return importlib.import_module(f"{match[1]}.{name}")
    if _PACKAGE not in sys.modules:
        # Load the plugin as a package (not file by file) so its relative imports resolve.
        spec = importlib.util.spec_from_file_location(_PACKAGE, _ROOT / "__init__.py",
                                                      submodule_search_locations=[str(_ROOT)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[_PACKAGE] = package
        spec.loader.exec_module(package)
    return importlib.import_module(f"{_PACKAGE}.{name}")


def _config():
    from hermes_cli.config import load_config_readonly
    return load_config_readonly() or {}


def _settings(config: dict) -> dict:
    entries = (config.get("plugins") or {}).get("entries") or {}
    return (entries.get("orquehelx") or {}).get("settings") or {}


def _main(config: dict) -> dict | None:
    """Default model of the main agent per Hermes' config; None if none is configured.
    The live session can change it (session.info wins): this is only what shows before the first turn."""
    model = config.get("model")
    if isinstance(model, str):
        return {"provider": None, "model": model} if model else None
    if not isinstance(model, dict):
        return None
    name = model.get("default") or model.get("model")
    provider = model.get("provider")
    if not name and not provider:
        return None
    return {"provider": provider or None, "model": name or None}


def _measurement(windows, error) -> dict:
    if error is not None:
        return {"state": "error", "error": str(error), "windows": []}
    if windows is None:
        return {"state": "no_data", "error": None, "windows": []}
    return {"state": "measured", "error": None, "windows": [
        {"label": label, "used": used, "reset_at": reset_at.isoformat() if reset_at else None}
        for label, used, reset_at in windows]}


def _current(quota, provider: str):
    """The provider's exhaustion if its reset has not come yet (with no known reset, it is still current)."""
    ex = quota.lookup(provider, since=time.time() - 24 * 3600)
    return ex if ex is not None and (ex.reset_at is None or ex.reset_at.timestamp() > time.time()) else None


@router.get("/status")
def status() -> dict:
    """Configured routes, measured quota of each one (one measurement per provider) and current exhaustions."""
    routes_mod, quota = _module("routes"), _module("quota")
    config = _config()
    main = _main(config)
    try:
        routes = routes_mod.load(routes_mod.setting(_settings(config).get, "routes", "rutas"))
    except routes_mod.ConfigError as exc:
        return {"routes": [], "main": main, "error": str(exc)}
    measured = {p: quota.measure_safe(p) for p in dict.fromkeys(r.provider for r in routes.values())}
    out = []
    for r in routes.values():
        ex = _current(quota, r.provider)
        out.append({
            "name": r.name, "provider": r.provider, "model": r.model, "acp": r.acp is not None,
            "measurement": _measurement(*measured[r.provider]),
            "exhausted": {"since": ex.seen, "reset_at": ex.reset_at.isoformat() if ex.reset_at else None}
            if ex else None,
        })
    return {"routes": out, "main": main, "error": None}


def _pauses():
    from hermes_constants import get_hermes_home
    pauses = _module("pauses")
    home = get_hermes_home()
    return pauses, pauses.open_db(home / "plugin-data" / "orquehelx" / "pauses.db",
                                  legacy=home / "orquehelx" / "pausas.db")


class NewPause(BaseModel):
    session: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=200)
    model: str | None = Field(default=None, max_length=200)


class Resolution(BaseModel):
    target: str | None = Field(default=None, max_length=64)


@router.post("/pauses")
def pause(body: NewPause) -> dict:
    """Pause the session only if the provider has a recorded, current exhaustion; otherwise it is not quota."""
    ex = _current(_module("quota"), body.provider)
    if ex is None:
        return {"pause": None}
    pauses, con = _pauses()
    try:
        return {"pause": pauses.create(con, body.session, body.provider, body.model, ex.reset_at)}
    finally:
        con.close()


@router.get("/pauses")
def pending() -> dict:
    pauses, con = _pauses()
    try:
        return {"pauses": pauses.pending(con)}
    finally:
        con.close()


@router.post("/pauses/{pause_id}/{action}")
def resolve(pause_id: int, action: Literal["resume", "resend", "cancel"], body: Resolution) -> dict:
    pauses, con = _pauses()
    try:
        won = pauses.resolve(con, pause_id, action, body.target)
    finally:
        con.close()
    if not won:
        raise HTTPException(status_code=409, detail="already resolved")
    return {"ok": True}
