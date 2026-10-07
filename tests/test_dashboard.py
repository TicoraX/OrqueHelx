import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from orquehelx import quota

ROOT = Path(__file__).resolve().parents[1] / "orquehelx"
RESET = datetime(2026, 9, 26, 5, 59, tzinfo=timezone.utc)
API = "/api/plugins/orquehelx"


def _config(hermes_home, routes_yaml):
    hermes_home.mkdir(parents=True, exist_ok=True)
    (hermes_home / "config.yaml").write_text(
        "plugins:\n  enabled: [orquehelx]\n  entries:\n    orquehelx:\n      settings:\n" + routes_yaml, encoding="utf-8")


def _api_module():
    # The dashboard imports plugin_api.py by path, on its own; same here.
    import importlib.util
    spec = importlib.util.spec_from_file_location("orquehelx_plugin_api_test", ROOT / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    return api


@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    api = _api_module()
    # Reuse the quota module the tests already imported: same exhaustion registry.
    monkeypatch.setattr(api, "_module", lambda name: __import__(f"orquehelx.{name}", fromlist=[name]))
    monkeypatch.setattr(quota, "_seen", {})
    app = FastAPI()
    app.include_router(api.router, prefix=API)
    return TestClient(app)


def test_manifest_declares_the_tab_and_the_backend():
    manifest = json.loads((ROOT / "dashboard" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "orquehelx"
    assert manifest["tab"]["path"] == "/orquehelx"
    assert manifest["entry"] == "dist/index.js" and manifest["css"] == "dist/style.css"
    assert manifest["api"] == "plugin_api.py"
    assert (ROOT / "dashboard" / manifest["entry"]).is_file(), "UI build missing (npm run build in ui/)"


def test_loaded_on_its_own_it_finds_the_plugin_modules():
    # Without the plugin loaded in the process, plugin_api loads it as a package: relative imports resolve.
    routes = _api_module()._module("routes")
    assert routes.load({"x": {"provider": "p"}})["x"].provider == "p"


def test_status_returns_routes_with_measured_and_missing_quota(client, hermes_home, monkeypatch):
    _config(hermes_home, "        routes:\n"
            "          claude: {provider: claude-subscription-directsdk-experimental, model: claude-haiku-4-5}\n"
            "          opencode: {acp: [opencode, acp]}\n")
    monkeypatch.setitem(quota.METERS, "claude-subscription", lambda: [("Current session", 56.0, RESET)])
    data = client.get(f"{API}/status").json()
    assert data["error"] is None
    claude, opencode = data["routes"]
    assert claude == {
        "name": "claude", "provider": "claude-subscription-directsdk-experimental", "model": "claude-haiku-4-5",
        "acp": False, "exhausted": None,
        "measurement": {"state": "measured", "error": None,
                        "windows": [{"label": "Current session", "used": 56.0, "reset_at": RESET.isoformat()}]},
    }
    assert opencode["acp"] is True and opencode["model"] is None
    assert opencode["measurement"] == {"state": "no_data", "error": None, "windows": []}


def test_status_reads_the_v01_spanish_key(client, hermes_home):
    _config(hermes_home, "        rutas:\n          claude: {provider: claude-subscription-directsdk-experimental}\n")
    assert [r["name"] for r in client.get(f"{API}/status").json()["routes"]] == ["claude"]


def test_status_reports_failed_measurement_and_current_exhaustion(client, hermes_home, monkeypatch):
    _config(hermes_home, "        routes:\n          claude: {provider: claude-subscription-directsdk-experimental}\n"
            "          agy: {provider: antigravity-subscription-directsdk}\n")

    def fails():
        raise ConnectionError("no network")
    monkeypatch.setitem(quota.METERS, "claude-subscription", fails)
    quota._seen["antigravity-subscription-directsdk"] = quota.Exhaustion(
        "antigravity-subscription-directsdk", "RESOURCE_EXHAUSTED", None, time.time())
    claude, agy = client.get(f"{API}/status").json()["routes"]
    assert claude["measurement"] == {"state": "error", "error": "no network", "windows": []}
    assert agy["exhausted"]["reset_at"] is None and agy["exhausted"]["since"] > 0


def test_status_with_invalid_config_returns_the_error_and_no_routes(client, hermes_home):
    _config(hermes_home, "        routes:\n          Bad: {provider: p}\n")
    data = client.get(f"{API}/status").json()
    assert data["routes"] == [] and "Bad" in data["error"]


def _exhaust(provider, reset_at):
    quota._seen[provider] = quota.Exhaustion(provider, "usage limit", reset_at, time.time())


def test_pause_only_with_a_recorded_exhaustion(client, hermes_home):
    body = {"session": "s1", "provider": "claude-subscription-directsdk-experimental", "model": "claude-haiku-4-5"}
    assert client.post(f"{API}/pauses", json=body).json() == {"pause": None}
    reset_at = datetime.now(timezone.utc) + timedelta(hours=2)
    _exhaust(body["provider"], reset_at)
    pause = client.post(f"{API}/pauses", json=body).json()["pause"]
    assert pause["state"] == "paused" and pause["reset_at"] == reset_at.isoformat()
    assert pause["session"] == "s1" and pause["model"] == "claude-haiku-4-5"
    assert client.get(f"{API}/pauses").json() == {"pauses": [pause]}
    assert (hermes_home / "plugin-data" / "orquehelx" / "pauses.db").is_file()


def test_v01_pauses_are_carried_over(client, hermes_home):
    legacy = hermes_home / "orquehelx" / "pausas.db"
    legacy.parent.mkdir(parents=True)
    with sqlite3.connect(legacy) as con:
        con.executescript(
            "CREATE TABLE pausas (id INTEGER PRIMARY KEY, sesion TEXT NOT NULL, proveedor TEXT NOT NULL, modelo TEXT,"
            " reinicio TEXT, estado TEXT NOT NULL DEFAULT 'pausado', destino TEXT, creado REAL NOT NULL, resuelto REAL);"
            "INSERT INTO pausas (sesion, proveedor, creado) VALUES ('s1', 'claude', 1);"
            "INSERT INTO pausas (sesion, proveedor, estado, destino, creado) VALUES ('s0', 'claude', 'reenviado', 'agy', 0);")
    con.close()
    (pending,) = client.get(f"{API}/pauses").json()["pauses"]
    assert pending["session"] == "s1" and pending["state"] == "paused"
    assert legacy.is_file()


def test_an_expired_exhaustion_does_not_pause(client, hermes_home):
    _exhaust("claude", datetime.now(timezone.utc) - timedelta(minutes=1))
    assert client.post(f"{API}/pauses", json={"session": "s1", "provider": "claude"}).json() == {"pause": None}


def test_resolving_a_pause_is_atomic(client, hermes_home):
    _exhaust("claude", None)
    pause = client.post(f"{API}/pauses", json={"session": "s1", "provider": "claude"}).json()["pause"]
    assert pause["reset_at"] is None
    url = f"{API}/pauses/{pause['id']}"
    assert client.post(f"{url}/resend", json={"target": "agy"}).json() == {"ok": True}
    loser = client.post(f"{url}/resume", json={})
    assert loser.status_code == 409 and loser.json()["detail"] == "already resolved"
    assert client.get(f"{API}/pauses").json() == {"pauses": []}


def test_pauses_validate_their_input(client, hermes_home):
    assert client.post(f"{API}/pauses", json={"session": "", "provider": "x"}).status_code == 422
    assert client.post(f"{API}/pauses", json={"session": "s" * 300, "provider": "x"}).status_code == 422
    assert client.post(f"{API}/pauses/1/delete", json={}).status_code == 422


def test_status_reports_the_configured_main_model(client, hermes_home):
    _config(hermes_home, "        routes:\n          claude: {provider: claude-subscription-directsdk-experimental}\n")
    with (hermes_home / "config.yaml").open("a", encoding="utf-8") as f:
        f.write("model:\n  default: claude-haiku-4-5\n  provider: claude-subscription-directsdk-experimental\n")
    assert client.get(f"{API}/status").json()["main"] == {
        "provider": "claude-subscription-directsdk-experimental", "model": "claude-haiku-4-5"}


def test_status_without_a_configured_model_does_not_invent_one(client, hermes_home):
    _config(hermes_home, "        routes:\n          claude: {provider: claude-subscription-directsdk-experimental}\n")
    assert client.get(f"{API}/status").json()["main"] is None


def test_each_profile_reads_the_quota_of_its_own_plugin_copy(tmp_path, monkeypatch):
    # The dashboard serves several profiles in one process. Hermes loads the plugin once per home: the first as
    # hermes_plugins.orquehelx, the others with a __home_<digest> suffix. Each home must see its own exhaustions.
    import shutil
    import sys

    from hermes_cli.plugins import PluginManager

    homes = [tmp_path / "home-a", tmp_path / "home-b"]
    for home in homes:
        _config(home, "        routes:\n          agy: {provider: antigravity-subscription-directsdk}\n")
        shutil.copytree(ROOT, home / "plugins" / "orquehelx")
        monkeypatch.setenv("HERMES_HOME", str(home))
        PluginManager().discover_and_load()
    copies = {getattr(m, "HOME", None): m for k, m in sys.modules.items()
              if k.startswith("hermes_plugins.orquehelx") and k.endswith(".quota")}
    assert set(copies) >= {quota.home_key(h) for h in homes}
    api = _api_module()
    for home in homes:
        monkeypatch.setenv("HERMES_HOME", str(home))
        assert api._module("quota") is copies[quota.home_key(home)]
