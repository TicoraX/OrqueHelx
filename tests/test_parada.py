"""Parar una corrida corta ESA corrida, no la de al lado.

`/api/parar` paso de "dejar de levantar trabajo nuevo" a "matar lo que este en
vuelo", que es lo que el usuario espera del boton. Pero el registro de procesos
no tenia clave de board, y ORQUESTER corre boards en paralelo a proposito
(ARQUITECTURA SS12, `test_dos_dispatchers`): parar uno mataba los agentes del
otro, que se enteraba como un CLI muerto sin explicacion.

Se prueba con subprocesos de verdad --dormir es barato y no miente-- porque lo
que esta en discusion es justamente si el proceso sigue vivo.

    uv run --python 3.11 --with jsonschema --with pyyaml python tests/test_parada.py
"""
import subprocess, sys, threading, time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "dispatcher"))
sys.path.insert(0, str(RAIZ / "hermes-agent"))
import backends as b

DORMIR = [sys.executable, "-c", "import time; time.sleep(30)"]
abiertos = []


def lanzar(board):
    """Un agente del `board`, fichado como lo fichara el hilo del pool."""
    b.marcar_board(board)
    p = subprocess.Popen(DORMIR, stdin=subprocess.DEVNULL)
    b.registrar_proceso(p)
    b.marcar_board(None)
    abiertos.append(p)
    return p


def vivo(p, espera=2.0):
    """Si murio, murio rapido: `kill` no negocia. Se espera un poco igual
    porque en Windows el estado tarda un instante en verse."""
    fin = time.time() + espera
    while time.time() < fin:
        if p.poll() is not None:
            return False
        time.sleep(0.05)
    return p.poll() is None


try:
    # --- 1. Parar un board no toca los procesos del otro ----------------------
    a1, a2 = lanzar("board-a"), lanzar("board-a")
    b1 = lanzar("board-b")
    assert vivo(a1, 0.3) and vivo(a2, 0.3) and vivo(b1, 0.3), "no arrancaron"

    muertos = b.matar_procesos_activos("board-a")
    assert muertos == 2, f"decia haber matado {muertos}, esperaba 2"
    assert not vivo(a1) and not vivo(a2), "quedo vivo un agente del board parado"
    assert vivo(b1), ("parar 'board-a' mato el agente de 'board-b': el registro "
                      "volvio a ser global")
    print("1. parar un board mata sus agentes y no los del otro: OK")

    # --- 2. Sin board se mata todo: es el apagado, no la parada de una corrida -
    b.matar_procesos_activos()
    assert not vivo(b1), "el apagado general dejo un proceso vivo"
    print("2. sin board, `matar_procesos_activos` corta todo: OK")

    # --- 3. Un hilo sin marcar no pertenece a ningun board --------------------
    # Es el chat del Studio: corre en el hilo del handler HTTP, no es parte de
    # ninguna corrida, y parar un board no tiene por que cortarlo.
    suelto = lanzar(None)
    b.matar_procesos_activos("board-a")
    assert vivo(suelto), "parar un board mato un proceso que no era de ningun board"
    print("3. un proceso sin board no lo alcanza la parada de un board: OK")
    b.matar_procesos_activos()

    # --- 4. El hilo del pool no se lleva su marca al siguiente tick ------------
    # Los hilos de `ThreadPoolExecutor` se reciclan. Si la marca quedara puesta,
    # el proximo proceso que abra ese hilo se fichara en el board anterior y una
    # parada se llevaria puesto trabajo ajeno.
    visto = {}

    def _trabajo():
        b.marcar_board("board-viejo")
        b.marcar_board(None)                     # como hace `_uno` en su finally
        visto["queda"] = getattr(b._HILO, "board", "sin atributo")

    h = threading.Thread(target=_trabajo)
    h.start(); h.join()
    assert visto["queda"] is None, f"la marca quedo en {visto['queda']!r}"
    print("4. el hilo suelta su board al terminar la card: OK")

    # --- 5. Y el registro no crece con procesos muertos ------------------------
    # `desregistrar_proceso` lo llama `_correr` en su `finally`. Sin eso, el set
    # junta un Popen muerto por cada nodo ejecutado en la vida del proceso.
    # (los de arriba se desfichan aca como lo hace `_correr` en su `finally`:
    # matar no desficha, lo desficha el hilo cuando su `communicate` vuelve)
    for viejo in abiertos:
        b.desregistrar_proceso(viejo)
    p1, p2 = lanzar("board-c"), lanzar("board-c")
    b.desregistrar_proceso(p1)
    assert "board-c" in b._PROCESOS_ACTIVOS, "se fue la clave con un proceso vivo"
    b.desregistrar_proceso(p2)
    assert b._PROCESOS_ACTIVOS == {}, (
        f"el fichero no quedo vacio: {b._PROCESOS_ACTIVOS}")
    print("5. desregistrar saca el proceso y la clave del board vacia: OK")

    # --- 6. Matar un proceso especifico por task_id ----------------------------
    def lanzar_con_task(board, task_id):
        b.marcar_board(board)
        b.marcar_tarea(task_id)
        p = subprocess.Popen(DORMIR, stdin=subprocess.DEVNULL)
        b.registrar_proceso(p)
        b.marcar_tarea(None)
        b.marcar_board(None)
        abiertos.append(p)
        return p

    pt1 = lanzar_con_task("board-d", "t_1")
    pt2 = lanzar_con_task("board-d", "t_2")
    assert vivo(pt1, 0.3) and vivo(pt2, 0.3), "no arrancaron con task"

    matado = b.matar_proceso_task("board-d", "t_1")
    assert matado is True, "no mato la tarea t_1"
    assert not vivo(pt1), "quedo viva la tarea t_1"
    assert vivo(pt2), "mato la tarea t_2 indebidamente"
    b.matar_procesos_activos("board-d")
    print("6. matar_proceso_task corta solo la tarea indicada: OK")

    # --- 7. Parar un nodo que no existe lo DICE ------------------------------
    # `_parar_nodo` devolvia `ok: True` fijo: sobre un task_id inexistente --o
    # vacio, que es lo que llega si el cuerpo no lo trae-- el Studio anunciaba
    # "nodo detenido" sin haber tocado nada. Su hermano `_parar_board` ya lo
    # decia bien; esto es la misma honestidad, propagada.
    sys.path.insert(0, str(RAIZ / "ui"))
    sys.path.insert(0, str(RAIZ / "compiler"))
    import server as srv

    for _tid, _esperado in (("t_no_existe", "no existe"), ("", "falta el task_id")):
        _r = srv._parar_nodo("orquester", _tid)
        assert _r["ok"] is False, f"dijo que paro algo que no existe: {_r}"
        assert _esperado in _r["motivo"], f"el motivo no explica nada: {_r}"
    print("7. parar un nodo inexistente devuelve ok=False con motivo: OK")

    print("\nOK: parar una corrida corta esa corrida y ninguna otra.")
finally:
    for p in abiertos:
        try:
            p.kill()
        except Exception:
            pass
