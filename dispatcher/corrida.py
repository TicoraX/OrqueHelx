"""El bucle de una corrida completa, fuera del servidor HTTP.

Vivia adentro de `ui/server.py._arrancar`, y una SEGUNDA copia adentro de
`mcp_exporter.ejecutar`. Las dos hacian lo mismo --pinchar al dispatcher de
Hermes, correr un `tick` del carril propio, dormir-- y ya habian empezado a
divergir: la del Studio arreglo que un Hermes colgado frenaba tres minutos el
despacho propio, la del MCP se quedo con el `subprocess.run(timeout=180)`
bloqueante. Esa es la enfermedad de esta rama, diagnosticada siete veces: una
proteccion que existe en un lugar y no se propago al de al lado.

Con esto, ademas, correr un DAG no necesita levantar un servidor HTTP: es lo
que usa `orquester.py run`.

    uv run --python 3.11 --with jsonschema python dispatcher/corrida.py
"""
import os, shutil, subprocess, sys, threading, time, traceback
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "hermes-agent"))
import hermes_cli.kanban_db as k
import loop as dispatcher

# board -> peticion de parada. Lo lee el bucle en cada vuelta; lo escribe quien
# aprieta el boton, desde otro hilo.
_LOCK = threading.Lock()
_PARAR: set[str] = set()

# board -> [proceso de Hermes en vuelo]. La lista de un elemento es el closure
# mas corto para recordar el hijo ENTRE vueltas del bucle.
_HERMES: dict[str, list] = {}


def hermes_bin() -> str | None:
    """El binario de Hermes, si esta. Solo hace falta con nodos `runtime: hermes`."""
    return (os.environ.get("ORQUESTER_HERMES_BIN")
            or shutil.which("hermes")
            or next((str(p) for p in [
                Path(os.environ.get("LOCALAPPDATA", "")) /
                "hermes/hermes-agent/venv/Scripts/hermes.exe"] if p.is_file()), None))


def pedir_parada(board: str) -> None:
    with _LOCK:
        _PARAR.add(board)


def limpiar_parada(board: str) -> None:
    """Antes de arrancar: una corrida anterior pudo dejar el board marcado."""
    with _LOCK:
        _PARAR.discard(board)


def parando(board: str) -> bool:
    with _LOCK:
        return board in _PARAR


def matar_hermes(board: str) -> bool:
    """El hijo de Hermes de este board, si sigue vivo. Devuelve si mato algo."""
    proc = (_HERMES.get(board) or [None])[0]
    if proc is None or proc.poll() is not None:
        return False
    try:
        proc.kill()          # kill a secas: terminate+kill sin espera era decorativo
        return True
    except Exception:
        return False


def _hay_futuro(conn) -> bool:
    """¿Queda alguna card que TODAVIA pueda avanzar?

    La condicion de corte miraba cada card por separado: `loop._queda_trabajo`
    para el carril propio y un conteo de `todo/ready/running` para las de
    Hermes. Las dos contaban como trabajo un `todo` cuyo padre habia quedado
    bloqueado para siempre, y ese hijo nunca se iba a mover: el bucle giraba
    sin fin sobre un flujo terminado.

    Verificado con el CLI antes de escribir esto: nodo A con un runtime que
    falla de forma permanente, nodo B colgando de A. A quedaba `blocked` y B
    `todo`, y la corrida no terminaba nunca.

    Un `todo` solo cuenta si TODOS sus padres pueden llegar a `done`. La
    recursion termina porque el compilador ya rechaza los ciclos, y ademas se
    lleva un conjunto de visitados por las dudas.
    """
    tareas = {t.id: t for t in k.list_tasks(conn)}
    propios = {dispatcher.carril(rt) for rt in dispatcher.BACKENDS}
    memo: dict[str, bool] = {}

    def _pendiente_viva(tid: str, visto=frozenset()) -> bool:
        if tid in memo:
            return memo[tid]
        if tid in visto:
            return False                   # ciclo: nadie lo va a desatar
        t = tareas[tid]
        if t.status in ("ready", "running"):
            valor = True
        elif t.status == "todo":
            # Un padre que ya esta `done` no frena a nadie; uno que todavia
            # puede terminar, tampoco.
            valor = all(tareas[p].status == "done"
                        or _pendiente_viva(p, visto | {tid})
                        for p in k.parent_ids(conn, tid) if p in tareas)
        elif (t.status == "blocked" and t.block_kind == "transient"
              and t.assignee in propios
              and len(k.list_runs(conn, tid, include_active=False))
              < dispatcher.MAX_INTENTOS):
            valor = True                   # el reintento lo hace `loop.reintentar`
        else:
            valor = False                  # done, triage, archived, blocked duro
        memo[tid] = valor
        return valor

    return any(_pendiente_viva(tid) for tid in tareas)


def _pinchar_hermes(binario, board: str, en_vuelo: list) -> None:
    """Lanzar el dispatcher de Hermes sin quedarse esperandolo.

    Era `subprocess.run(..., timeout=180)` adentro del while: un Hermes colgado
    frenaba TRES MINUTOS el despacho de nuestro propio carril, que no tiene
    nada que ver con el suyo. Ahora se lanza y se sigue; en la vuelta siguiente,
    si el anterior no termino, no se lanza otro (dos dispatchers sobre el mismo
    board se pisan el claim).
    """
    if not binario:
        return
    if en_vuelo[0] is not None and en_vuelo[0].poll() is None:
        return                             # el anterior sigue trabajando
    try:
        en_vuelo[0] = subprocess.Popen(
            [binario, "kanban", "--board", board, "dispatch"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            # `stdin=DEVNULL` por el mismo motivo que en `backends.run_backend`:
            # cuando el padre es el servidor MCP, el stdin heredado es el canal
            # JSON-RPC.
            stdin=subprocess.DEVNULL)
    except Exception:
        traceback.print_exc()              # que no tumbe el loop del carril propio


def imprimir(hechas) -> None:
    """`log` para consola: una linea por nodo terminado en la vuelta."""
    for tid, out in hechas:
        print(f"  {tid} -> {out['status']}: {(out.get('summary') or '')[:90]}")


def correr(board: str, *, tope_usd: float = None, listo=None, espera: float = 3.0,
           timeout: float = None, timeout_nodo: int = 600, log=None) -> dict:
    """Despachar el board hasta que no quede trabajo. Bloquea.

    - `tope_usd`: al alcanzarlo se deja de arrancar nodos nuevos. El corte es
      ENTRE nodos: los que ya arrancaron terminan (ver `loop.tick`).
    - `listo(conn) -> bool`: condicion extra de fin. La usa el exportador MCP,
      que espera por SUS cards y no por el board entero.
    - `timeout`: segundos de pared para toda la corrida. `None` = sin limite;
      el bucle igual termina solo cuando no queda trabajo.
    - `log(hechas)`: se llama con la lista de `(task_id, resultado)` de cada
      vuelta que hizo algo. Lo usa el CLI para ir informando.

    Devuelve `{"motivo", "vueltas", "nodos"}`. `motivo` es uno de: `sin
    trabajo` (fin normal, todo cerrado), `trabado` (no queda nada que pueda
    avanzar y hay cards abiertas), `listo`, `parado`, `tope`, `timeout`,
    `error`.
    """
    binario = hermes_bin()
    en_vuelo = [None]
    _HERMES[board] = en_vuelo
    limite = None if timeout is None else time.monotonic() + timeout
    vueltas = nodos = 0
    conn = None
    try:
        conn = k.connect(board=board)
        while True:
            if parando(board):
                return {"motivo": "parado", "vueltas": vueltas, "nodos": nodos}
            if limite is not None and time.monotonic() > limite:
                return {"motivo": "timeout", "vueltas": vueltas, "nodos": nodos}
            if tope_usd is not None and dispatcher.gasto_usd(conn) >= tope_usd:
                # Se anota como si lo hubieran parado a mano: el Studio ya sabe
                # mostrar ese estado, y el motivo se ve en el consumo.
                pedir_parada(board)
                print(f"[{board}] tope de US$ {tope_usd} alcanzado: no se "
                      f"arrancan nodos nuevos")
                return {"motivo": "tope", "vueltas": vueltas, "nodos": nodos}

            _pinchar_hermes(binario, board, en_vuelo)
            hechas = dispatcher.tick(conn, board=board, tope_usd=tope_usd,
                                     timeout=timeout_nodo)
            vueltas += 1
            nodos += len(hechas)
            if hechas and log:
                log(hechas)

            if listo is not None and listo(conn):
                return {"motivo": "listo", "vueltas": vueltas, "nodos": nodos}
            if not hechas and not _hay_futuro(conn):
                # `trabado` y `sin trabajo` son cosas distintas y el llamador
                # decide distinto con cada una: sin trabajo es un flujo que
                # termino, trabado es uno que se corto con cards abiertas.
                abiertas = any(t.status not in ("done", "archived")
               for t in k.list_tasks(conn))
                return {"motivo": "trabado" if abiertas else "sin trabajo",
                        "vueltas": vueltas, "nodos": nodos}
            time.sleep(espera)
    except Exception:
        traceback.print_exc()
        return {"motivo": "error", "vueltas": vueltas, "nodos": nodos}
    finally:
        # El hijo de Hermes no sobrevive a la corrida: si sigue vivo cuando
        # esto termina, queda un dispatcher suelto sobre un board que el
        # llamador ya da por cerrado.
        if en_vuelo[0] is not None and en_vuelo[0].poll() is None:
            en_vuelo[0].kill()
        # Y la referencia: sin esto, `_HERMES` crece una entrada por corrida y
        # guarda para siempre un Popen muerto que `matar_hermes` vuelve a mirar.
        _HERMES.pop(board, None)
        limpiar_parada(board)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    # Autochequeo: el bucle corta cuando no hay trabajo y cuando le piden parar,
    # sin agentes de verdad. Un board vacio no tiene nada que despachar.
    tablero = f"corrida-autocheck-{int(time.time() * 1000) % 10_000_000}"
    k.create_board(tablero)
    try:
        r = correr(tablero, espera=0.01)
        assert r["motivo"] == "sin trabajo", r
        assert r["nodos"] == 0, r
        assert _HERMES == {}, f"quedo registro de Hermes: {_HERMES}"

        pedir_parada(tablero)
        r = correr(tablero, espera=0.01)
        assert r["motivo"] == "parado", r
        assert not parando(tablero), "la parada no se limpio al terminar"

        r = correr(tablero, espera=0.01, listo=lambda c: True)
        assert r["motivo"] == "listo", r
        print("OK: el bucle corta por fin de trabajo, por parada y por `listo`.")
    finally:
        try:
            k.remove_board(tablero, archive=False)
        except Exception:
            traceback.print_exc()
