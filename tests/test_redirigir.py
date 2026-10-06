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


@pytest.mark.parametrize("args", [{"goal": "x"}, {"tasks": [{"goal": "x"}]}, {"goal": "x", "action": "spawn"}])
def test_delegate_task_nativo_se_redirige_a_delegar(args):
    veredicto = delegar.redirigir(RUTAS, tool_name="delegate_task", args=args)
    assert veredicto["action"] == "block"
    assert "delegate_to" in veredicto["message"]
    assert "claude" in veredicto["message"] and "opencode" in veredicto["message"]


@pytest.mark.parametrize("tool_name, args", [
    ("delegate_task", {"action": "list"}),
    ("delegate_task", {"action": "steer", "subagent_id": "sa-1", "message": "m"}),
    ("delegate_task", {"action": "stop", "subagent_id": "sa-1"}),
    ("terminal", {"command": "ls"}),
    ("delegate_to", {"route": "claude", "goal": "x"}),
])
def test_control_y_otras_herramientas_pasan(tool_name, args):
    assert delegar.redirigir(RUTAS, tool_name=tool_name, args=args) is None


def test_seccion_de_prompt_explica_las_rutas():
    texto = delegar.seccion_prompt(RUTAS)
    assert "delegate_to" in texto
    assert "claude (claude-haiku-4-5)" in texto and "opencode (ohx-opencode)" in texto
    assert "delegate_task" in texto


def test_registrar_engancha_hook_y_seccion():
    registros = {}
    ctx = SimpleNamespace(
        get_config=lambda clave, defecto=None: {"routes": {"claude": {"provider": "p"}}}.get(clave, defecto),
        register_tool=lambda *_, **__: None,
        register_command=lambda *_, **__: None,
        register_hook=lambda nombre, fn: registros.setdefault("hooks", {}).update({nombre: fn}),
        register_system_prompt_section=lambda id, contenido, **_: registros.update({"seccion": (id, contenido)}),
    )
    delegar.registrar(ctx)
    assert "pre_tool_call" in registros["hooks"] and "api_request_error" in registros["hooks"]
    assert registros["seccion"][0] == "orquehelx.rutas"
    veredicto = registros["hooks"]["pre_tool_call"](tool_name="delegate_task", args={"goal": "x"}, task_id="t")
    assert veredicto["action"] == "block"


def test_hermes_veta_delegate_task_con_el_plugin_cargado(hermes_home, monkeypatch):
    # El mismo camino que recorre el executor antes de despachar una herramienta.
    import hermes_cli.plugins as hp
    from hermes_cli.plugins import PluginManager, _dispatch_pre_tool_call_hooks

    shutil.copytree(Path(__file__).resolve().parents[1] / "orquehelx", hermes_home / "plugins" / "orquehelx")
    (hermes_home / "config.yaml").write_text(
        "plugins:\n  enabled: [orquehelx]\n  entries:\n    orquehelx:\n      settings:\n"
        "        routes:\n          agy: {provider: antigravity-subscription-directsdk}\n", encoding="utf-8")
    manager = PluginManager()
    manager.discover_and_load()
    monkeypatch.setattr(hp, "_plugin_manager", manager, raising=False)
    bloqueo, _ = _dispatch_pre_tool_call_hooks("delegate_task", {"goal": "x"}, task_id="t", session_id="s")
    assert bloqueo and "delegate_to" in bloqueo
    permitido, _ = _dispatch_pre_tool_call_hooks("delegate_task", {"action": "list"}, task_id="t", session_id="s")
    assert permitido is None
