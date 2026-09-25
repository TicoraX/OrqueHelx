import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orquehelx import cuota

RESET_5H = datetime(2026, 9, 26, 5, 59, tzinfo=timezone.utc)
RESET_SEMANA = datetime(2026, 9, 28, 23, 59, tzinfo=timezone.utc)
AGY = "antigravity-subscription-directsdk"
CLAUDE = "claude-subscription-directsdk-experimental"


@pytest.fixture(autouse=True)
def limpio(monkeypatch):
    monkeypatch.setattr(cuota, "_vistos", {})


def _medidor(monkeypatch, prefijo, resultado):
    def medir():
        if isinstance(resultado, Exception):
            raise resultado
        return resultado
    monkeypatch.setitem(cuota.MEDIDORES, prefijo, medir)


def _falla(provider, mensaje, status=None, model=""):
    # Forma real del payload de Hermes (agent/api_request_hooks.py): el texto viaja en error["message"].
    cuota.al_fallar(provider=provider, model=model, status_code=status,
                    error={"type": "RuntimeError", "message": mensaje})


def test_agy_se_reconoce_por_texto_y_sin_reinicio_medido():
    _falla(AGY, "Antigravity model error: RESOURCE_EXHAUSTED: Individual quota reached")
    ag = cuota.consultar(AGY, desde=0)
    assert ag is not None and ag.reinicio is None


def test_claude_generico_se_confirma_con_la_ventana_medida(monkeypatch):
    _medidor(monkeypatch, "claude-subscription", [("Current session", 100.0, RESET_5H), ("Current week", 40.0, None)])
    _falla(CLAUDE, "Native request failed: error_during_execution", model="claude-haiku-4-5")
    assert cuota.consultar(CLAUDE, desde=0).reinicio == RESET_5H


def test_varias_ventanas_agotadas_reporta_la_ultima(monkeypatch):
    _medidor(monkeypatch, "claude-subscription",
             [("Current session", 100.0, RESET_5H), ("Current week", 100.0, RESET_SEMANA)])
    _falla(CLAUDE, "Native request failed: x", model="claude-haiku-4-5")
    assert cuota.consultar(CLAUDE, desde=0).reinicio == RESET_SEMANA


def test_ventana_agotada_sin_hora_deja_reinicio_sin_dato(monkeypatch):
    _medidor(monkeypatch, "claude-subscription",
             [("Current session", 100.0, RESET_5H), ("Current week", 100.0, None)])
    _falla(CLAUDE, "Native request failed: x", model="claude-haiku-4-5")
    ag = cuota.consultar(CLAUDE, desde=0)
    assert ag is not None and ag.reinicio is None


@pytest.mark.parametrize("modelo, agotado", [("claude-haiku-4-5", False), ("claude-opus-5-5", True)])
def test_ventana_de_otro_modelo_no_bloquea_la_ruta(monkeypatch, modelo, agotado):
    _medidor(monkeypatch, "claude-subscription", [("Current session", 20.0, RESET_5H), ("Opus week", 100.0, RESET_SEMANA)])
    _falla(CLAUDE, "Native request failed: x", model=modelo)
    assert (cuota.consultar(CLAUDE, desde=0) is not None) is agotado


@pytest.mark.parametrize("medicion", [[("Current session", 30.0, RESET_5H)], [], ConnectionError("sin red")])
def test_claude_sin_ventana_agotada_no_se_afirma_cuota(monkeypatch, medicion):
    _medidor(monkeypatch, "claude-subscription", medicion)
    _falla(CLAUDE, "Native request failed: error_during_execution")
    assert cuota.consultar(CLAUDE, desde=0) is None


def test_429_con_limite_de_uso_en_cualquier_proveedor():
    _falla("otro", "Usage limit reached, resets at 14:30", status=429)
    assert cuota.consultar("otro", desde=0) is not None


@pytest.mark.parametrize("provider, mensaje, status", [
    (AGY, "Antigravity CLI is not authenticated", None),
    ("otro", "Usage limit reached", 500),
    (CLAUDE, "connection reset", None),
    ("", "quota", 429),
])
def test_errores_que_no_son_cuota(monkeypatch, provider, mensaje, status):
    _medidor(monkeypatch, "claude-subscription", [("Current session", 100.0, RESET_5H)])
    _falla(provider, mensaje, status)
    assert cuota.consultar(provider, desde=0) is None


def test_payload_sin_error_no_rompe_el_hook():
    cuota.al_fallar(provider=AGY)
    cuota.al_fallar(provider=AGY, error="RESOURCE_EXHAUSTED quota exceeded")
    assert cuota.consultar(AGY, desde=0) is not None


def test_consultar_ignora_agotamientos_anteriores():
    _falla(AGY, "RESOURCE_EXHAUSTED quota exceeded")
    assert cuota.consultar(AGY, desde=time.time() + 1) is None


def test_hermes_invoca_el_hook_con_su_payload_real(hermes_home, monkeypatch):
    # Llama al metodo de Hermes que arma el payload (no a al_fallar directo): si Hermes cambia la
    # forma del payload, este test falla.
    from types import SimpleNamespace

    import hermes_cli.plugins as hp
    from agent.api_request_hooks import ApiRequestHooksMixin
    from hermes_cli.plugins import PluginManager

    shutil.copytree(Path(__file__).resolve().parents[1] / "orquehelx", hermes_home / "plugins" / "orquehelx")
    (hermes_home / "config.yaml").write_text(
        "plugins:\n  enabled: [orquehelx]\n  entries:\n    orquehelx:\n      settings:\n"
        "        rutas:\n          agy: {provider: antigravity-subscription-directsdk}\n", encoding="utf-8")
    manager = PluginManager()
    manager.discover_and_load()
    monkeypatch.setattr(hp, "_plugin_manager", manager, raising=False)
    agente = SimpleNamespace(session_id="s", platform="cli", model="flash", provider=AGY, base_url="", api_mode="x",
                             _api_request_payload_for_hook=lambda _: {})
    ApiRequestHooksMixin._invoke_api_request_error_hook(
        agente, task_id="t", turn_id="u", api_request_id="a", api_call_count=1, api_start_time=time.time(),
        api_kwargs=None, error_type="RuntimeError",
        error_message="Antigravity model error: RESOURCE_EXHAUSTED: Individual quota reached")
    cargado = sys.modules["hermes_plugins.orquehelx.cuota"]
    assert cargado.consultar(AGY, desde=0) is not None
