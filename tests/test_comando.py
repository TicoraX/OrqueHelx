import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from orquehelx import comando, cuota
from orquehelx.rutas import cargar

RUTAS = cargar({
    "claude": {"provider": "claude-subscription-directsdk-experimental", "model": "claude-haiku-4-5"},
    "agy": {"provider": "antigravity-subscription-directsdk", "model": "flash"},
})
RESET = datetime(2026, 9, 26, 5, 59, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def limpio(monkeypatch):
    monkeypatch.setattr(cuota, "_vistos", {})


def test_rutas_lista_nombre_proveedor_y_modelo():
    salida = comando.ejecutar(RUTAS, "routes")
    assert "claude" in salida and "claude-subscription-directsdk-experimental" in salida
    assert "claude-haiku-4-5" in salida and "agy" in salida


def test_cuota_muestra_ventanas_medidas_y_sin_dato(monkeypatch):
    monkeypatch.setitem(cuota.MEDIDORES, "claude-subscription",
                        lambda: [("Current session", 30.0, RESET), ("Current week", 28.0, None)])
    salida = comando.ejecutar(RUTAS, "quota")
    assert "Current session: 30 %" in salida
    assert RESET.astimezone().strftime("%Y-%m-%d %H:%M") in salida
    assert "Current week: 28 %" in salida
    assert "agy: no data" in salida


def test_cuota_con_medicion_fallida_dice_la_causa(monkeypatch):
    def falla():
        raise ConnectionError("sin red")
    monkeypatch.setitem(cuota.MEDIDORES, "claude-subscription", falla)
    assert "could not measure (sin red)" in comando.ejecutar(RUTAS, "quota")


def test_cuota_informa_un_agotamiento_visto():
    cuota._vistos["antigravity-subscription-directsdk"] = cuota.Agotamiento(
        "antigravity-subscription-directsdk", "RESOURCE_EXHAUSTED", None, time.time())
    assert "agy: out of quota since" in comando.ejecutar(RUTAS, "quota")


@pytest.mark.parametrize("args", ["", "otra", "  "])
def test_subcomando_desconocido_muestra_uso(args):
    assert "usage: /ohx" in comando.ejecutar(RUTAS, args)


def test_registrar_agrega_el_comando_ohx():
    from orquehelx import delegar
    comandos = {}
    ctx = SimpleNamespace(
        get_config=lambda clave, defecto=None: {"routes": {"claude": {"provider": "p"}}}.get(clave, defecto),
        register_tool=lambda *_, **__: None, register_hook=lambda *_, **__: None,
        register_system_prompt_section=lambda *_, **__: None,
        register_command=lambda nombre, fn, **_: comandos.update({nombre: fn}),
    )
    delegar.registrar(ctx)
    assert comandos["ohx"]("routes") == "claude: p"


def test_cuota_mide_una_vez_por_proveedor(monkeypatch):
    rutas = cargar({
        "haiku": {"provider": "claude-subscription-directsdk-experimental", "model": "claude-haiku-4-5"},
        "opus": {"provider": "claude-subscription-directsdk-experimental", "model": "claude-opus-5-5"},
    })
    llamadas = []
    monkeypatch.setitem(cuota.MEDIDORES, "claude-subscription",
                        lambda: llamadas.append(1) or [("Current session", 10.0, RESET)])
    salida = comando.ejecutar(rutas, "quota")
    assert len(llamadas) == 1
    assert "haiku:" in salida and "opus:" in salida


def test_agotamiento_con_reinicio_ya_pasado_no_se_muestra_como_actual():
    from datetime import timedelta
    pasado = datetime.now(timezone.utc) - timedelta(minutes=5)
    cuota._vistos["antigravity-subscription-directsdk"] = cuota.Agotamiento(
        "antigravity-subscription-directsdk", "RESOURCE_EXHAUSTED", pasado, time.time() - 3600)
    assert "out of quota since" not in comando.ejecutar(RUTAS, "quota")


@pytest.mark.parametrize("usado, texto", [(56.0, "56 %"), (99.6, "99.6 %"), (99.96, "99.9 %"), (100.0, "100 %")])
def test_porcentaje_nunca_redondea_hacia_agotado(monkeypatch, usado, texto):
    monkeypatch.setitem(cuota.MEDIDORES, "claude-subscription", lambda: [("Current session", usado, None)])
    assert f"Current session: {texto}" in comando.ejecutar(RUTAS, "quota")


def test_en_espanol_y_con_los_subcomandos_de_v01(monkeypatch):
    monkeypatch.setattr("agent.i18n.get_language", lambda: "es-419")
    assert "agy: sin dato" in comando.ejecutar(RUTAS, "cuota")
    assert "uso: /ohx" in comando.ejecutar(RUTAS, "otra")
