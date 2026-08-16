"""El dispatcher externo de ORQUESTER (ARQUITECTURA.md SS12).

Reclama las cards de su propio carril y las ejecuta invocando el binario del
agente externo. **No programa nada**: el kanban de Hermes resuelve las
dependencias y promueve a `ready`; esto solo levanta trabajo ya programado.

Corre en Python, no en TypeScript, a proposito: usa `kanban_db` como libreria
en vez de reimplementar el protocolo de claim contra la misma SQLite.
"""
import sys, threading, time

sys.path.insert(0, r"A:/Proyectos/orquester/hermes-agent")
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
        while not latido.wait(_HEARTBEAT_S):
            k.heartbeat_claim(c, task_id, claimer=CLAIMER)

    hilo = threading.Thread(target=_latir, daemon=True)
    hilo.start()
    try:
        ctx = k.build_worker_context(conn, task_id)   # summaries de los padres
        salida = run_backend(_runtime_de(task), ctx, timeout=timeout)
    except BackendError as e:
        # Un nodo que falla NO se cierra: se bloquea. Si se cerrara con
        # `complete_task`, el kanban lo veria 'done' y **promoveria a sus
        # hijos**, que arrancarian sobre el mensaje de error como si fuera el
        # resultado del padre. Observado en el board `mixto-4`: dos nodos
        # fallaron, cerraron igual, y el hijo corrio sobre la basura.
        latido.set()
        k.block_task(conn, task_id, reason=str(e)[:2000])
        return {"status": "failure", "summary": str(e)[:2000]}
    finally:
        latido.set()

    k.complete_task(
        conn, task_id,
        summary=salida["summary"],
        result=salida["summary"],
        metadata={"orquester_status": salida["status"], "claimer": CLAIMER},
    )
    return salida


def tick(conn, *, timeout: int = 600) -> list[tuple[str, dict]]:
    """Una pasada: ejecutar todas las cards listas del carril propio."""
    # Una consulta por carril en vez de listar todo y filtrar por prefijo:
    # `list_tasks` filtra por assignee en SQL y son 3 backends, no 300.
    listas = [t for rt in BACKENDS
              for t in k.list_tasks(conn, status="ready", assignee=carril(rt))]
    return [(t.id, ejecutar_una(conn, t.id, timeout=timeout)) for t in listas]


def correr(board: str, *, intervalo: int = 5, hasta_vacio: bool = True) -> None:
    """Loop principal. Con `hasta_vacio`, termina cuando no queda trabajo."""
    conn = k.connect(board=board)
    while True:
        hechas = tick(conn)
        for tid, out in hechas:
            print(f"  {tid} -> {out['status']}: {out['summary'][:90]}")
        if hasta_vacio and not hechas:
            pendientes = [
                t for rt in BACKENDS
                for t in k.list_tasks(conn, assignee=carril(rt))
                if t.status in ("todo", "blocked", "running")
            ]
            if not pendientes:
                return
        time.sleep(intervalo)


if __name__ == "__main__":
    correr(sys.argv[1] if len(sys.argv) > 1 else "orquester-test")
