import pytest

from orquehelx.rutas import ErrorDeRuta, cargar, credenciales


def test_ruta_de_proveedor_con_modelo():
    rutas = cargar({"claude": {"provider": "claude-subscription-directsdk-experimental", "model": "claude-haiku-4-5"}})
    assert rutas["claude"].proveedor == "claude-subscription-directsdk-experimental"
    assert rutas["claude"].modelo == "claude-haiku-4-5"
    assert rutas["claude"].acp is None


def test_ruta_acp_genera_su_propio_proveedor():
    rutas = cargar({"opencode": {"acp": ["opencode", "acp"]}})
    assert rutas["opencode"].acp == ("opencode", "acp")
    assert rutas["opencode"].proveedor == "ohx-opencode"


def test_credenciales_apagan_el_fallback():
    # D2: nunca cambio automatico de proveedor; [] apaga la cadena nativa de Hermes (#65038).
    ruta = cargar({"agy": {"provider": "antigravity-subscription-directsdk", "model": "flash"}})["agy"]
    assert credenciales(ruta) == {
        "provider": "antigravity-subscription-directsdk", "model": "flash", "fallback_providers": [],
    }
    sin_modelo = cargar({"opencode": {"acp": ["opencode", "acp"]}})["opencode"]
    assert credenciales(sin_modelo) == {"provider": "ohx-opencode", "fallback_providers": []}


@pytest.mark.parametrize("config, pedazo", [
    ({"x": {}}, "'x'"),
    ({"x": {"provider": "a", "acp": ["b"]}}, "'x'"),
    ({"x": {"acp": []}}, "'x'"),
    ({"x": {"acp": ["opencode", 3]}}, "'x'"),
    ({"x": {"provider": ""}}, "'x'"),
    ({"x": {"provider": "a", "extra": 1}}, "extra"),
    ({"Mal Nombre": {"provider": "a"}}, "Mal Nombre"),
    ({"x": "claude"}, "'x'"),
])
def test_config_invalida_falla_nombrando_la_ruta(config, pedazo):
    with pytest.raises(ErrorDeRuta, match=pedazo):
        cargar(config)


def test_sin_rutas_es_un_error_explicito():
    with pytest.raises(ErrorDeRuta, match="plugins.entries.orquehelx.settings.rutas"):
        cargar(None)
    with pytest.raises(ErrorDeRuta, match="plugins.entries.orquehelx.settings.rutas"):
        cargar({})
