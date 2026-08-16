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

from backends import run_backend, BackendError

CARRIL = "orquester-external"   # deliberadamente NO es un perfil Hermes
CLAIMER = "orquester"
_HEARTBEAT_S = 120              # el TTL del claim es 15 min; con margen


def _runtime_de(task) -> str:
    """El runtime del nodo viaja en `skills`. Es metadato, no selector (SS4.1)."""
    for s in (task.skills or []):
        if s in ("claude-code", "opencode", "antigravity"):
            return s
    raise BackendError(f"la card {task.id} no declara runtime externo")


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
        # ponytail: un fallo duro cierra la card con status=failure en vez de
        # marcarla 'failed'. El estado real vive en `_record_task_failure`, que
        # es privado; acoplarnos a el es peor que perder el matiz. Upgrade:
        # pedir API publica upstream, o el circuit breaker no cuenta estos.
        salida = {"status": "failure", "summary": str(e)[:2000]}
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
    listas = k.list_tasks(conn, status="ready", assignee=CARRIL)
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
                t for t in k.list_tasks(conn, assignee=CARRIL)
                if t.status in ("todo", "blocked", "running")
            ]
            if not pendientes:
                return
        time.sleep(intervalo)


if __name__ == "__main__":
    correr(sys.argv[1] if len(sys.argv) > 1 else "orquester-test")
