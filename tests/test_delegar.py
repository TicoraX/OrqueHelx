import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from orquehelx import delegar
from orquehelx.rutas import cargar

RUTAS = cargar({
    "claude": {"provider": "claude-subscription-directsdk-experimental", "model": "claude-haiku-4-5"},
    "opencode": {"acp": ["opencode", "acp"]},
})


@pytest.fixture
def llamadas(monkeypatch):
    registro = []

    def falso_delegate_task(**kw):
        registro.append(kw)
        return json.dumps({"results": [{"status": "completed"}]})

    monkeypatch.setattr("tools.delegate_tool.delegate_task", falso_delegate_task)
    return registro


def test_esquema_ofrece_solo_las_rutas_configuradas():
    esquema = delegar.esquema(RUTAS)
    props = esquema["parameters"]["properties"]
    assert esquema["name"] == "delegar"
    assert props["ruta"]["enum"] == ["claude", "opencode"]
    assert esquema["parameters"]["required"] == ["ruta", "objetivo"]
    assert "claude-haiku-4-5" in esquema["description"]


def test_delegar_fija_proveedor_y_apaga_fallback(llamadas):
    agente = object()
    out = delegar.manejar(RUTAS, {"ruta": "opencode", "objetivo": "di hola", "contexto": "c"}, agente)
    assert json.loads(out)["results"][0]["status"] == "completed"
    (kw,) = llamadas
    assert kw["goal"] == "di hola" and kw["context"] == "c" and kw["parent_agent"] is agente
    assert kw["credentials_cfg"] == {"provider": "ohx-opencode", "fallback_providers": []}


@pytest.mark.parametrize("args, pedazo", [
    ({"ruta": "codex", "objetivo": "x"}, "claude, opencode"),
    ({"ruta": "claude"}, "objetivo"),
    ({"ruta": "claude", "objetivo": "   "}, "objetivo"),
])
def test_argumentos_invalidos_devuelven_error_legible(llamadas, args, pedazo):
    out = json.loads(delegar.manejar(RUTAS, args, object()))
    assert pedazo in out["error"]
    assert llamadas == []


def test_sin_agente_padre_explica_la_causa(llamadas):
    out = json.loads(delegar.manejar(RUTAS, {"ruta": "claude", "objetivo": "x"}, None))
    assert "agente padre" in out["error"]
    assert llamadas == []


def test_hermes_con_parent_agent_nativo_no_usa_el_parche(monkeypatch):
    from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS

    monkeypatch.setattr(delegar, "_hermes_pasa_parent_agent", lambda: True)
    monkeypatch.delitem(INLINE_TOOL_EXECUTORS, "delegar", raising=False)
    ctx = _ctx_falso({"claude": {"provider": "p"}})
    delegar.registrar(ctx)
    assert "delegar" not in INLINE_TOOL_EXECUTORS
    assert ctx.herramientas["delegar"]["handler"].__name__ == "handler"


def test_hermes_sin_parent_agent_usa_el_parche(monkeypatch, llamadas):
    from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS

    monkeypatch.setattr(delegar, "_hermes_pasa_parent_agent", lambda: False)
    monkeypatch.delitem(INLINE_TOOL_EXECUTORS, "delegar", raising=False)
    delegar.registrar(_ctx_falso({"claude": {"provider": "p"}}))
    agente = object()
    INLINE_TOOL_EXECUTORS["delegar"](agente, {"ruta": "claude", "objetivo": "x"}, None)
    assert llamadas[0]["parent_agent"] is agente
    monkeypatch.delitem(INLINE_TOOL_EXECUTORS, "delegar")


def test_deteccion_de_parent_agent_lee_la_firma_real():
    import inspect

    import model_tools

    esperado = "parent_agent" in inspect.signature(model_tools.handle_function_call).parameters
    assert delegar._hermes_pasa_parent_agent() is esperado


def test_plugin_carga_en_el_plugin_manager_real(hermes_home):
    # Instala el plugin como lo haria `hermes plugins install`, con la config del usuario.
    destino = hermes_home / "plugins" / "orquehelx"
    shutil.copytree(Path(__file__).resolve().parents[1] / "orquehelx", destino)
    (hermes_home / "config.yaml").write_text(
        "plugins:\n"
        "  enabled: [orquehelx]\n"
        "  entries:\n"
        "    orquehelx:\n"
        "      settings:\n"
        "        rutas:\n"
        "          opencode: {acp: [opencode, acp]}\n",
        encoding="utf-8",
    )
    from hermes_cli.plugins import PluginManager
    from providers import get_provider_profile
    from tools.registry import registry

    PluginManager().discover_and_load()
    assert registry.get_entry("delegar") is not None
    assert get_provider_profile("ohx-opencode").process_command == "opencode"


def _ctx_falso(rutas_config):
    herramientas = {}
    return SimpleNamespace(
        herramientas=herramientas,
        get_config=lambda clave, defecto=None: rutas_config if clave == "rutas" else defecto,
        register_tool=lambda name, toolset, schema, handler, **_: herramientas.update(
            {name: {"toolset": toolset, "schema": schema, "handler": handler}}),
    )


@pytest.mark.parametrize("args, pedazo", [
    ({"ruta": "claude", "objetivo": 42}, "objetivo"),
    ({"ruta": ["claude"], "objetivo": "x"}, "claude, opencode"),
])
def test_tipos_inesperados_del_modelo_no_rompen_el_handler(llamadas, args, pedazo):
    out = json.loads(delegar.manejar(RUTAS, args, object()))
    assert pedazo in out["error"]
    assert llamadas == []


@pytest.mark.parametrize("contexto, esperado", [(" datos ", "datos"), ("   ", None), ({"a": 1}, None), (None, None)])
def test_contexto_se_normaliza(llamadas, contexto, esperado):
    delegar.manejar(RUTAS, {"ruta": "claude", "objetivo": "x", "contexto": contexto}, object())
    assert llamadas[0]["context"] == esperado
