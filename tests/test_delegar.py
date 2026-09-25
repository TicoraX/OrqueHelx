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


def test_el_ejecutor_inline_recibe_el_agente(monkeypatch, llamadas):
    from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS

    monkeypatch.delitem(INLINE_TOOL_EXECUTORS, "delegar", raising=False)
    delegar.registrar(_ctx_falso({"claude": {"provider": "p"}}))
    agente = object()
    INLINE_TOOL_EXECUTORS["delegar"](agente, {"ruta": "claude", "objetivo": "x"}, None)
    assert llamadas[0]["parent_agent"] is agente
    monkeypatch.delitem(INLINE_TOOL_EXECUTORS, "delegar")


def test_el_handler_del_registry_usa_el_parent_agent_que_llegue(monkeypatch, llamadas):
    from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS

    ctx = _ctx_falso({"claude": {"provider": "p"}})
    delegar.registrar(ctx)
    monkeypatch.delitem(INLINE_TOOL_EXECUTORS, "delegar")
    agente = object()
    ctx.herramientas["delegar"]["handler"]({"ruta": "claude", "objetivo": "x"}, parent_agent=agente, task_id="t")
    assert llamadas[0]["parent_agent"] is agente


def test_regresion_carga_en_frio_dentro_del_limite_de_hermes(hermes_home):
    # Regresion: importar model_tools dentro de register() trababa el loader hasta su timeout (10 s)
    # y Hermes descartaba el plugin. En el proceso de pytest no se ve: los modulos ya estan cargados.
    import subprocess
    import sys

    shutil.copytree(Path(__file__).resolve().parents[1] / "orquehelx", hermes_home / "plugins" / "orquehelx")
    (hermes_home / "config.yaml").write_text(
        "plugins:\n  enabled: [orquehelx]\n  entries:\n    orquehelx:\n      settings:\n"
        "        rutas:\n          opencode: {acp: [opencode, acp]}\n", encoding="utf-8")
    codigo = (
        "from hermes_cli.plugins import PluginManager\n"
        "m = PluginManager(); m.discover_and_load()\n"
        "p = [p for p in m.list_plugins() if p['name'] == 'orquehelx'][0]\n"
        "print(p['enabled'], p['tools'], p['hooks'], p['commands'], p['error'])\n"
    )
    salida = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, timeout=120,
                            env={**__import__("os").environ, "HERMES_HOME": str(hermes_home)})
    assert salida.stdout.strip().splitlines()[-1] == "True 1 1 1 None", salida.stdout + salida.stderr


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
        register_hook=lambda nombre, fn: herramientas.update({"hook:" + nombre: fn}),
        register_command=lambda nombre, fn, **_: herramientas.update({"comando:" + nombre: fn}),
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


def _hijo(status):
    return lambda **_: json.dumps({"results": [{"status": status, "summary": None, "error": "fallo"}]})


def _agotar(proveedor, reinicio=None):
    from orquehelx import cuota
    from orquehelx.cuota import Agotamiento
    import time as _t
    cuota._vistos[proveedor] = Agotamiento(proveedor, "RESOURCE_EXHAUSTED", reinicio, _t.time() + 5)


@pytest.fixture
def cuota_limpia(monkeypatch):
    from orquehelx import cuota
    monkeypatch.setattr(cuota, "_vistos", {})


@pytest.mark.parametrize("politica, pedazo", [
    ("preguntar", "preguntale si quiere"),
    ("padre_decide", "puedes reenviar"),
    ("esperar", "no hagas nada"),
])
@pytest.mark.usefixtures("cuota_limpia")
def test_hijo_sin_cuota_devuelve_estado_y_politica(monkeypatch, politica, pedazo):
    from datetime import datetime, timezone
    monkeypatch.setattr("tools.delegate_tool.delegate_task", _hijo("failed"))
    reinicio = datetime(2026, 9, 26, 5, 59, tzinfo=timezone.utc)
    _agotar("claude-subscription-directsdk-experimental", reinicio)
    out = json.loads(delegar.manejar(RUTAS, {"ruta": "claude", "objetivo": "x"}, object(), politica=politica))
    assert out["estado"] == "sin_cuota"
    assert out["ruta"] == "claude"
    assert out["reinicio"] == "2026-09-26T05:59:00+00:00"
    assert out["otras_rutas"] == ["opencode"]
    assert pedazo in out["instruccion"].lower()


@pytest.mark.usefixtures("cuota_limpia")
def test_sin_cuota_sin_reinicio_medido_lo_dice(monkeypatch):
    monkeypatch.setattr("tools.delegate_tool.delegate_task", _hijo("failed"))
    _agotar("ohx-opencode")
    out = json.loads(delegar.manejar(RUTAS, {"ruta": "opencode", "objetivo": "x"}, object()))
    assert out["estado"] == "sin_cuota" and out["reinicio"] is None
    assert "sin dato" in out["instruccion"].lower()


@pytest.mark.usefixtures("cuota_limpia")
def test_fallo_que_no_es_cuota_pasa_tal_cual(monkeypatch):
    monkeypatch.setattr("tools.delegate_tool.delegate_task", _hijo("failed"))
    out = json.loads(delegar.manejar(RUTAS, {"ruta": "claude", "objetivo": "x"}, object()))
    assert out["results"][0]["status"] == "failed"


@pytest.mark.usefixtures("cuota_limpia")
def test_hijo_completado_ignora_agotamientos_viejos(monkeypatch):
    monkeypatch.setattr("tools.delegate_tool.delegate_task", _hijo("completed"))
    _agotar("claude-subscription-directsdk-experimental")
    out = json.loads(delegar.manejar(RUTAS, {"ruta": "claude", "objetivo": "x"}, object()))
    assert out["results"][0]["status"] == "completed"


def test_politica_invalida_falla_al_cargar():
    from orquehelx.rutas import ErrorDeConfig
    ctx = _ctx_falso({"claude": {"provider": "p"}})
    ctx.get_config = lambda clave, defecto=None: {"rutas": {"claude": {"provider": "p"}}, "politica": "auto"}.get(clave, defecto)
    with pytest.raises(ErrorDeConfig, match="politica"):
        delegar.registrar(ctx)


@pytest.mark.parametrize("politica", [["preguntar"], {"a": 1}, 3])
def test_politica_con_tipo_invalido_falla_nombrando_la_clave(politica):
    from orquehelx.rutas import ErrorDeConfig
    ctx = _ctx_falso({"claude": {"provider": "p"}})
    ctx.get_config = lambda clave, defecto=None: {"rutas": {"claude": {"provider": "p"}}, "politica": politica}.get(clave, defecto)
    with pytest.raises(ErrorDeConfig, match="politica"):
        delegar.registrar(ctx)


def _agente(turno):
    return SimpleNamespace(_current_turn_id=turno)


@pytest.mark.usefixtures("cuota_limpia")
@pytest.mark.parametrize("politica", ["preguntar", "esperar"])
def test_sin_cuota_bloquea_otra_delegacion_en_el_mismo_turno(monkeypatch, politica):
    # D2: sin permiso del usuario no hay cambio de ruta; el permiso llega con su proximo mensaje (turno nuevo).
    llamadas = []
    monkeypatch.setattr("tools.delegate_tool.delegate_task", lambda **kw: llamadas.append(kw) or _hijo("failed")())
    _agotar("claude-subscription-directsdk-experimental")
    padre = _agente("turno-1")
    primera = json.loads(delegar.manejar(RUTAS, {"ruta": "claude", "objetivo": "x"}, padre, politica))
    assert primera["estado"] == "sin_cuota"

    bloqueada = json.loads(delegar.manejar(RUTAS, {"ruta": "opencode", "objetivo": "x"}, padre, politica))
    assert "autorice" in bloqueada["error"]
    assert len(llamadas) == 1

    padre._current_turn_id = "turno-2"
    delegar.manejar(RUTAS, {"ruta": "opencode", "objetivo": "x"}, padre, politica)
    assert len(llamadas) == 2


@pytest.mark.usefixtures("cuota_limpia")
def test_padre_decide_puede_cambiar_de_ruta_en_el_mismo_turno(monkeypatch):
    llamadas = []
    monkeypatch.setattr("tools.delegate_tool.delegate_task", lambda **kw: llamadas.append(kw) or _hijo("failed")())
    _agotar("claude-subscription-directsdk-experimental")
    padre = _agente("turno-1")
    delegar.manejar(RUTAS, {"ruta": "claude", "objetivo": "x"}, padre, "padre_decide")
    delegar.manejar(RUTAS, {"ruta": "opencode", "objetivo": "x"}, padre, "padre_decide")
    assert len(llamadas) == 2
