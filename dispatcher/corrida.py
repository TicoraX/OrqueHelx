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
import os, re, shutil, subprocess, sys, threading, time, traceback
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


# La regla del kanban, copiada a proposito: `kanban_db._normalize_board_slug`
# es privada del clon pineado. `tests/test_cli.py` verifica que las dos digan lo
# mismo, asi que un bump del pin que la cambie se ve en la suite y no en
# produccion.
_SLUG = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,63}$")


def slug(board: str) -> str:
    """El nombre del board como lo guarda el kanban: minusculas y sin espacios.

    `k.connect` normaliza a minusculas por su cuenta, asi que `Mi-Board` y
    `mi-board` son EL MISMO board en disco. Los registros en memoria
    (`_PARAR`, `_HERMES`, y `_corriendo`/`_topes` del Studio) se indexaban por
    el nombre crudo: arrancar `Mi-Board` con `mi-board` corriendo pasaba el
    "ya hay un dispatcher en este board" y dejaba dos escribiendo sobre la
    misma SQLite, y parar uno no paraba el otro.
    """
    limpio = str(board or "").strip().lower()
    if not _SLUG.match(limpio):
        raise ValueError(
            f"board invalido: {board!r} (1-64 caracteres, minusculas, numeros, "
            "guiones y guiones bajos, sin empezar con guion)")
    return limpio


def pedir_parada(board: str) -> None:
    with _LOCK:
        _PARAR.add(slug(board))


def limpiar_parada(board: str) -> None:
    """Antes de arrancar: una corrida anterior pudo dejar el board marcado."""
    with _LOCK:
        _PARAR.discard(slug(board))


def parando(board: str) -> bool:
    with _LOCK:
        return slug(board) in _PARAR


def matar_hermes(board: str) -> bool:
    """El hijo de Hermes de este board, si sigue vivo. Devuelve si mato algo."""
    proc = (_HERMES.get(slug(board)) or [None])[0]
    if proc is None or proc.poll() is not None:
        return False
    try:
        proc.kill()          # kill a secas: terminate+kill sin espera era decorativo
        return True
    except Exception:
        return False


# `assignee` que el compilador le pone a un nodo de aprobacion humana
# (`compile._assignee`). No es un carril nuestro ni un perfil de Hermes: nadie
# lo va a levantar, y esa es exactamente la idea.
GATE = "human"


def gates_esperando(conn) -> list:
    """Los nodos de aprobacion que ya tienen a sus padres cerrados.

    En `ready` y no en `todo`: un gate en `todo` todavia espera a sus padres, y
    de ese lo que hay que mirar es si los padres pueden terminar.
    """
    return [t for t in k.list_tasks(conn)
            if t.assignee == GATE and t.status == "ready"]


def aprobar_gate(conn, task_id: str, *, resultado: str = None,
                 automatico: bool = False) -> str:
    """Cerrar un gate para que sus hijos se promuevan. Devuelve lo que quedo escrito.

    `automatico` no es cosmetico: el registro tiene que distinguir una
    aprobacion humana de una que hizo un flag, o una auditoria a seis meses lee
    "aprobado por el usuario" sobre algo que ningun usuario miro. El pasado no
    se reescribe, asi que mejor que no mienta cuando se escribe.
    """
    texto = (resultado or "").strip() or (
        "Aprobado automaticamente por --aprobar-gates (sin revision humana)"
        if automatico else "Aprobado por el usuario")
    k.complete_task(conn, task_id, summary=texto, result=texto,
                    metadata={"orquester_status": "success",
                              "claimer": "auto" if automatico else "human",
                              "aprobacion": "automatica" if automatico else "humana"})
    return texto


def _hay_futuro(conn, *, esperar_gates: bool = False) -> bool:
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
        if t.assignee == GATE and t.status == "ready" and not esperar_gates:
            # Un gate en `ready` esta esperando a una persona. Nada de lo que
            # este proceso hace lo va a mover, asi que para una corrida
            # headless NO es futuro: es un final, y con nombre propio. Con
            # `esperar_gates` (el Studio, o `--esperar-gates`) si cuenta,
            # porque ahi hay alguien del otro lado.
            valor = False
        elif t.status in ("ready", "running"):
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
           timeout: float = None, timeout_nodo: int = 600, log=None,
           gates: str = "parar") -> dict:
    """Despachar el board hasta que no quede trabajo. Bloquea.

    - `tope_usd`: al alcanzarlo se deja de arrancar nodos nuevos. El corte es
      ENTRE nodos: los que ya arrancaron terminan (ver `loop.tick`).
    - `listo(conn) -> bool`: condicion extra de fin. La usa el exportador MCP,
      que espera por SUS cards y no por el board entero.
    - `timeout`: segundos de pared para toda la corrida. `None` = sin limite;
      el bucle igual termina solo cuando no queda trabajo.
    - `log(hechas)`: se llama con la lista de `(task_id, resultado)` de cada
      vuelta que hizo algo. Lo usa el CLI para ir informando.
    - `gates`: que hacer con un nodo de aprobacion humana que quedo esperando.
        - `parar` (default): terminar con `motivo: "gate"`. El board queda
          intacto y se reanuda despues, desde el Studio o con otra corrida. Es
          lo correcto sin nadie mirando: la espera de una firma dura horas y el
          estado ya es durable, asi que tener el proceso vivo no compra nada.
        - `esperar`: seguir dando vueltas hasta que alguien apruebe. Lo usa el
          Studio, donde el boton esta a la vista. **Acotalo con `timeout`**: una
          espera sin fondo es como se clavan los ejecutores de un CI.
        - `aprobar`: aprobarlos solos y seguir. Para un pipeline que de verdad
          no tiene humano. Queda asentado como automatico en la card.

    Devuelve `{"motivo", "vueltas", "nodos"}`. `motivo` es uno de: `sin
    trabajo` (fin normal, todo cerrado), `gate` (falta una aprobacion humana),
    `trabado` (no queda nada que pueda avanzar y hay cards abiertas), `listo`,
    `parado`, `tope`, `timeout`, `error`.
    """
    board = slug(board)            # el registro en memoria y la SQLite, la
                                   # misma clave: ver `slug`
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
                # Antes se hacia `pedir_parada(board)` aca "para que el Studio
                # muestre el estado de parado". Era un no-op: el `finally` de
                # esta misma salida hace `limpiar_parada`. Lo que informa es el
                # `motivo` que se devuelve.
                print(f"[{board}] tope de US$ {tope_usd} alcanzado: no se "
                      f"arrancan nodos nuevos")
                return {"motivo": "tope", "vueltas": vueltas, "nodos": nodos}

            if gates == "aprobar":
                for g in gates_esperando(conn):
                    aprobar_gate(conn, g.id, automatico=True)
                    print(f"[{board}] gate '{g.title}' aprobado automaticamente")

            _pinchar_hermes(binario, board, en_vuelo)
            hechas = dispatcher.tick(conn, board=board, tope_usd=tope_usd,
                                     timeout=timeout_nodo)
            vueltas += 1
            nodos += len(hechas)
            if hechas and log:
                log(hechas)

            if listo is not None and listo(conn):
                return {"motivo": "listo", "vueltas": vueltas, "nodos": nodos}
            if not hechas and not _hay_futuro(conn, esperar_gates=gates != "parar"):
                # Tres finales distintos, porque el llamador decide distinto con
                # cada uno: `sin trabajo` es un flujo que termino, `gate` es uno
                # que espera una firma y se puede reanudar, `trabado` es uno que
                # se corto con cards que ya no pueden avanzar.
                if gates_esperando(conn):
                    return {"motivo": "gate", "vueltas": vueltas, "nodos": nodos}
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
