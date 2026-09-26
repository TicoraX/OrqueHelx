import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orquehelx import cuota

RAIZ = Path(__file__).resolve().parents[1] / "orquehelx"
RESET = datetime(2026, 9, 26, 5, 59, tzinfo=timezone.utc)


def _config(hermes_home, rutas_yaml):
    hermes_home.mkdir(parents=True, exist_ok=True)
    (hermes_home / "config.yaml").write_text(
        "plugins:\n  enabled: [orquehelx]\n  entries:\n    orquehelx:\n      settings:\n" + rutas_yaml, encoding="utf-8")


@pytest.fixture
def cliente(monkeypatch):
    # El dashboard importa plugin_api.py por ruta, suelto; aca igual.
    import importlib.util

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    spec = importlib.util.spec_from_file_location("orquehelx_plugin_api_test", RAIZ / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    # Reusa el modulo cuota ya importado por los tests: mismo registro de agotamientos.
    monkeypatch.setattr(api, "_modulo", lambda nombre: __import__(f"orquehelx.{nombre}", fromlist=[nombre]))
    monkeypatch.setattr(cuota, "_vistos", {})
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/orquehelx")
    return TestClient(app)


def test_manifest_declara_la_pestana_y_el_backend():
    manifest = json.loads((RAIZ / "dashboard" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "orquehelx"
    assert manifest["tab"]["path"] == "/orquehelx"
    assert manifest["entry"] == "dist/index.js" and manifest["css"] == "dist/style.css"
    assert manifest["api"] == "plugin_api.py"
    assert (RAIZ / "dashboard" / manifest["entry"]).is_file(), "falta el build de la UI (npm run build en ui/)"


def test_estado_devuelve_rutas_con_cuota_medida_y_sin_dato(cliente, hermes_home, monkeypatch):
    _config(hermes_home, "        rutas:\n"
            "          claude: {provider: claude-subscription-directsdk-experimental, model: claude-haiku-4-5}\n"
            "          opencode: {acp: [opencode, acp]}\n")
    monkeypatch.setitem(cuota.MEDIDORES, "claude-subscription", lambda: [("Current session", 56.0, RESET)])
    datos = cliente.get("/api/plugins/orquehelx/estado").json()
    assert datos["error"] is None
    claude, opencode = datos["rutas"]
    assert claude == {
        "nombre": "claude", "proveedor": "claude-subscription-directsdk-experimental", "modelo": "claude-haiku-4-5",
        "acp": False, "agotada": None,
        "medicion": {"estado": "medida", "error": None,
                     "ventanas": [{"etiqueta": "Current session", "usado": 56.0, "reinicio": RESET.isoformat()}]},
    }
    assert opencode["acp"] is True and opencode["modelo"] is None
    assert opencode["medicion"] == {"estado": "sin_dato", "error": None, "ventanas": []}


def test_estado_informa_medicion_fallida_y_agotamiento_vigente(cliente, hermes_home, monkeypatch):
    _config(hermes_home, "        rutas:\n          claude: {provider: claude-subscription-directsdk-experimental}\n"
            "          agy: {provider: antigravity-subscription-directsdk}\n")

    def falla():
        raise ConnectionError("sin red")
    monkeypatch.setitem(cuota.MEDIDORES, "claude-subscription", falla)
    cuota._vistos["antigravity-subscription-directsdk"] = cuota.Agotamiento(
        "antigravity-subscription-directsdk", "RESOURCE_EXHAUSTED", None, time.time())
    claude, agy = cliente.get("/api/plugins/orquehelx/estado").json()["rutas"]
    assert claude["medicion"] == {"estado": "error", "error": "sin red", "ventanas": []}
    assert agy["agotada"]["reinicio"] is None and agy["agotada"]["desde"] > 0


def test_estado_con_config_invalida_devuelve_el_error_sin_rutas(cliente, hermes_home):
    _config(hermes_home, "        rutas:\n          Mal: {provider: p}\n")
    datos = cliente.get("/api/plugins/orquehelx/estado").json()
    assert datos["rutas"] == [] and "Mal" in datos["error"]
