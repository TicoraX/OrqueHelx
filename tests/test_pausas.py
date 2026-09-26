import threading
from datetime import datetime, timezone

import pytest

from orquehelx import pausas

RESET = datetime(2026, 9, 26, 19, 0, tzinfo=timezone.utc)


@pytest.fixture
def con(tmp_path):
    c = pausas.abrir(tmp_path / "pausas.db")
    yield c
    c.close()


def test_crear_y_listar_la_pausa_pendiente(con):
    p = pausas.crear(con, "sesion-1", "claude-subscription-directsdk-experimental", "claude-haiku-4-5", RESET)
    assert p["estado"] == "pausado"
    assert p["reinicio"] == RESET.isoformat()
    assert pausas.pendientes(con) == [p]


def test_una_sesion_tiene_una_sola_pausa_pendiente(con):
    a = pausas.crear(con, "sesion-1", "claude", None, RESET)
    b = pausas.crear(con, "sesion-1", "claude", None, None)
    assert a == b
    assert len(pausas.pendientes(con)) == 1


def test_resolver_saca_la_pausa_y_registra_la_accion(con):
    p = pausas.crear(con, "sesion-1", "claude", None, None)
    assert pausas.resolver(con, p["id"], "reenviar", "agy") is True
    assert pausas.pendientes(con) == []
    fila = con.execute("SELECT estado, destino, resuelto FROM pausas WHERE id = ?", (p["id"],)).fetchone()
    assert fila[0] == "reenviado" and fila[1] == "agy" and fila[2] is not None


def test_resolver_dos_veces_gana_solo_la_primera(con):
    p = pausas.crear(con, "sesion-1", "claude", None, None)
    assert pausas.resolver(con, p["id"], "cancelar") is True
    assert pausas.resolver(con, p["id"], "reanudar") is False
    assert con.execute("SELECT estado FROM pausas").fetchone()[0] == "cancelado"


def test_accion_desconocida_falla_en_voz_alta(con):
    p = pausas.crear(con, "sesion-1", "claude", None, None)
    with pytest.raises(ValueError):
        pausas.resolver(con, p["id"], "borrar")


def test_carrera_entre_conexiones_resuelve_una_sola(tmp_path):
    # D19: reanudar al reinicio y un clic en otra pestania a la vez; solo uno gasta el turno.
    ruta = tmp_path / "pausas.db"
    with pausas.abrir(ruta) as c:
        pid = pausas.crear(c, "sesion-1", "claude", None, None)["id"]
    ganadores: list[bool] = []
    barrera = threading.Barrier(8)

    def intento():
        c = pausas.abrir(ruta)
        barrera.wait()
        ganadores.append(pausas.resolver(c, pid, "reanudar"))
        c.close()

    hilos = [threading.Thread(target=intento) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert sorted(ganadores) == [False] * 7 + [True]


def test_una_sesion_resuelta_puede_volver_a_pausarse(con):
    p = pausas.crear(con, "sesion-1", "claude", None, None)
    pausas.resolver(con, p["id"], "reanudar")
    q = pausas.crear(con, "sesion-1", "claude", None, None)
    assert q["id"] != p["id"] and q["estado"] == "pausado"


def test_un_error_de_integridad_que_no_es_duplicado_no_se_traga(con):
    with pytest.raises(pausas.sqlite3.IntegrityError):
        pausas.crear(con, "sesion-1", None, None, None)
