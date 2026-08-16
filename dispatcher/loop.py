"""El dispatcher externo de ORQUESTER (ARQUITECTURA.md SS12).

Reclama las cards de su propio carril y las ejecuta invocando el binario del
agente externo. **No programa nada**: el kanban de Hermes resuelve las
dependencias y promueve a `ready`; esto solo levanta trabajo ya programado.

Corre en Python, no en TypeScript, a proposito: usa `kanban_db` como libreria
en vez de reimplementar el protocolo de claim contra la misma SQLite.
"""
import sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# La raiz sale del propio archivo, no de una constante: el repo tiene que
# correr desde cualquier ruta y en cualquier maquina.
RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "hermes-agent"))
import hermes_cli.kanban_db as k

from backends import run_backend, BackendError, BACKENDS

# El carril va en `assignee`, y el runtime como sufijo: `orquester-external:opencode`.
# Informacion de ruteo en el campo de ruteo. Dos razones para no usar `skills`:
# ya significa otra cosa en Hermes (carga de contexto para un worker nativo, y
# nos confundio una vez, SS4.1), y una card que por error cayera en manos del
# dispatcher de Hermes cargaria una skill que nadie pidio. Hermes solo hace
# lower() sobre el assignee (`profiles.normalize_profile_name`), asi que el
# sufijo sobrevive intacto y sigue sin ser un perfil valido -> nonspawnable.
CARRIL = "orquester-external"
CLAIMER = "orquester"
_HEARTBEAT_S = 120              # el TTL del claim es 15 min; con margen

# Cuantos nodos del carril corren a la vez. Un fan-out ancho se serializaba
# entero antes de esto. El techo lo pone el rate limit del proveedor de cada
# CLI, no la maquina: por eso es bajo y configurable.
MAX_PARALELO = 3

# Reintentos por nodo ante un fallo transitorio. Hermes ademas corta los bucles
# de desbloqueo por su cuenta (`BLOCK_RECURRENCE_LIMIT`), asi que un reintento
# que se obstine termina en `triage` y no girando para siempre.
MAX_INTENTOS = 2

# Fallos que NO se reintentan: no se arreglan solos y reintentarlos solo gasta
# tiempo y cuota. Van como `capability`, que es el tipo que Hermes reserva para
# "a este worker le falta algo", en vez de `transient`.
# Estas cadenas tienen que coincidir con lo que `backends` produce de verdad.
# Ya se desincronizaron una vez: el catch paso de FileNotFoundError a OSError y
# el mensaje cambio a "no se pudo lanzar", pero aca seguia "binario no
# encontrado". Efecto: un workspace inexistente se reintentaba como transitorio
# dos veces antes de rendirse, en vez de fallar de una.
_PERMANENTES = ("no esta en el PATH", "no se pudo lanzar",
                "flags de bypass prohibidos", "runtime desconocido")

# Permisos que se le conceden al agente externo. Lectura y shell: alcanza para
# inspeccionar un repo y correr tests, y NO incluye ningun flag de bypass (esos
# siguen en la denylist de `backends.FLAGS_PROHIBIDOS`). Cuando el Studio deje
# elegir permisos por nodo, esto pasa a ser el default y no la unica opcion.
_HERRAMIENTAS = ["Read", "Grep", "Glob", "Bash"]


def carril(runtime: str) -> str:
    """El `assignee` que le toca a un nodo de este runtime."""
    if runtime not in BACKENDS:
        raise BackendError(f"runtime desconocido: {runtime}")
    return f"{CARRIL}:{runtime}"


def _runtime_de(task) -> str:
    prefijo, _, runtime = (task.assignee or "").partition(":")
    if prefijo != CARRIL or runtime not in BACKENDS:
        raise BackendError(f"la card {task.id} no declara runtime externo: {task.assignee!r}")
    return runtime


def ejecutar_una(conn, task_id: str, *, timeout: int = 600) -> dict:
    """Reclamar, ejecutar y cerrar una card. Devuelve el contrato."""
    task = k.claim_task(conn, task_id, claimer=CLAIMER)
    if task is None:
        return {"status": "skipped", "summary": "ya reclamada por otro"}

    latido = threading.Event()

    def _latir():
        # Sin esto, release_stale_claims nos saca la card en una invocacion
        # larga: `agy` tardo 124.8s en la verificacion, y hay peores.
        c = k.connect()
        try:
            while not latido.wait(_HEARTBEAT_S):
                k.heartbeat_claim(c, task_id, claimer=CLAIMER)
        finally:
            c.close()

    hilo = threading.Thread(target=_latir, daemon=True)
    hilo.start()
    try:
        ctx = k.build_worker_context(conn, task_id)   # summaries de los padres
        salida = run_backend(_runtime_de(task), ctx, timeout=timeout,
                             cwd=task.workspace_path or None,
                             herramientas=_HERRAMIENTAS)
    except BackendError as e:
        # Un nodo que falla NO se cierra: se bloquea. Si se cerrara con
        # `complete_task`, el kanban lo veria 'done' y **promoveria a sus
        # hijos**, que arrancarian sobre el mensaje de error como si fuera el
        # resultado del padre. Observado en el board `mixto-4`: dos nodos
        # fallaron, cerraron igual, y el hijo corrio sobre la basura.
        latido.set()
        msg = str(e)[:2000]
        permanente = any(p in msg for p in _PERMANENTES)
        k.block_task(conn, task_id, reason=msg,
                     kind="capability" if permanente else "transient")
        return {"status": "failure", "summary": msg}
    finally:
        latido.set()

    k.complete_task(
        conn, task_id,
        summary=salida["summary"],
        result=salida["summary"],
        metadata={"orquester_status": salida["status"], "claimer": CLAIMER},
    )
    return salida


def _mis_cards(conn, estado: str) -> list:
    """Cards del carril propio en `estado`.

    Una consulta por carril en vez de listar todo y filtrar por prefijo:
    `list_tasks` filtra por assignee en SQL y son 3 backends, no 300.
    """
    return [t for rt in BACKENDS
            for t in k.list_tasks(conn, status=estado, assignee=carril(rt))]


def reintentar(conn) -> list[str]:
    """Desbloquear los fallos transitorios que todavia tienen intentos."""
    reabiertas = []
    for t in _mis_cards(conn, "blocked"):
        if t.block_kind != "transient":
            continue                      # dependencia o fallo permanente
        # Intentos previos contados desde `task_runs`, no desde memoria: el
        # dispatcher puede reiniciarse y el conteo tiene que sobrevivir.
        intentos = len(k.list_runs(conn, t.id, include_active=False))
        if intentos < MAX_INTENTOS:
            k.unblock_task(conn, t.id)
            reabiertas.append(t.id)
    return reabiertas


def tick(conn, *, timeout: int = 600, board: str = None) -> list[tuple[str, dict]]:
    """Una pasada: reabrir lo reintentable y ejecutar lo listo, en paralelo."""
    reintentar(conn)
    listas = _mis_cards(conn, "ready")
    if not listas:
        return []

    # Una conexion por hilo: los objetos de sqlite3 no se comparten entre
    # hilos, y `claim_task` ya es atomico entre conexiones (verificado en
    # `tests/test_dos_dispatchers.py`), asi que no hace falta lock propio.
    def _uno(t):
        # Cerrar siempre: se abre una conexion por card por tick, y el bucle de
        # `correr` tickea cada pocos segundos. Sin cerrar, un flujo largo se
        # come los descriptores.
        c = k.connect(board=board) if board else k.connect()
        try:
            return (t.id, ejecutar_una(c, t.id, timeout=timeout))
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=MAX_PARALELO) as pool:
        return list(pool.map(_uno, listas))


def correr(board: str, *, intervalo: int = 5, hasta_vacio: bool = True) -> None:
    """Loop principal. Con `hasta_vacio`, termina cuando no queda trabajo."""
    conn = k.connect(board=board)
    while True:
        hechas = tick(conn, board=board)
        for tid, out in hechas:
            print(f"  {tid} -> {out['status']}: {out['summary'][:90]}")
        if hasta_vacio and not hechas and not _queda_trabajo(conn):
            return
        time.sleep(intervalo)


def _queda_trabajo(conn) -> bool:
    """¿Hay algo que este loop pueda llegar a ejecutar?

    Un `blocked` de tipo `capability` no se resuelve solo y un `transient` sin
    intentos tampoco: contarlos como pendientes deja el loop girando para
    siempre. Solo cuenta lo que de verdad puede avanzar.
    """
    for t in (t for rt in BACKENDS for t in k.list_tasks(conn, assignee=carril(rt))):
        if t.status in ("todo", "running"):
            return True
        if t.status == "blocked" and t.block_kind == "transient" \
                and len(k.list_runs(conn, t.id, include_active=False)) < MAX_INTENTOS:
            return True
    return False


if __name__ == "__main__":
    correr(sys.argv[1] if len(sys.argv) > 1 else "orquester-test")
