"""Dos dispatchers sobre el mismo kanban: el de Hermes y el de ORQUESTER.

Prerrequisito de ARQUITECTURA.md SS12. Verifica que un loop externo pueda
reclamar cards de su propio carril sin que el dispatcher de Hermes se las pise,
y sin que ninguna card se ejecute dos veces.

Sin LLM y sin spawns reales: `spawn_fn` es un espia que solo anota a quien
habrian lanzado.

    uv run --python 3.11 python ..\\tests\\test_dos_dispatchers.py
"""
import sys, tempfile, threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hermes-agent"))
import hermes_cli.kanban_db as k

LANE = "orquester-external"   # deliberadamente NO es un perfil Hermes
db = Path(tempfile.mkdtemp()) / "dos.db"
k.init_db(db_path=db)
conn = k.connect(db_path=db)

spawned = []          # (task_id, assignee) que el dispatcher de Hermes lanzaria
spy = lambda task, workspace, board=None: (spawned.append((task.id, task.assignee)), 4242)[1]

print(f"journal_mode efectivo: {conn.execute('PRAGMA journal_mode').fetchone()[0]}")

# --- 1. El carril externo es invisible para el dispatcher de Hermes ---
ext = k.create_task(conn, title="Nodo runtime=opencode", assignee=LANE)
nat = k.create_task(conn, title="Nodo runtime=hermes", assignee="default")

r = k.dispatch_once(conn, spawn_fn=spy)
print(f"1. spawned={[t for t, _ in spawned]} nonspawnable={r.skipped_nonspawnable}")
assert ext in r.skipped_nonspawnable, "el carril externo debe caer en nonspawnable"
assert ext not in [t for t, _ in spawned], "Hermes NUNCA debe lanzar el carril externo"

# --- 2. La trampa: assignee=NULL SI lo secuestra default_assignee ---
# Por eso el carril lleva nombre y no se deja en NULL (SS12).
huerfana = k.create_task(conn, title="Nodo sin assignee")
r2 = k.dispatch_once(conn, spawn_fn=spy, default_assignee="default")
print(f"2. auto_assigned={r2.auto_assigned_default} (assignee=NULL queda expuesto)")
assert huerfana in r2.auto_assigned_default, "default_assignee reclama las cards sin assignee"
assert ext not in r2.auto_assigned_default, "el carril con nombre resiste default_assignee"

# --- 3. claim_task bajo carrera: exactamente un ganador ---
race = k.create_task(conn, title="Nodo disputado", assignee=LANE)
ganadores, barrera = [], threading.Barrier(8)

def competir(n):
    c = k.connect(db_path=db)          # conexion propia, como un proceso aparte
    barrera.wait()
    if k.claim_task(c, race, claimer=f"claimer-{n}") is not None:
        ganadores.append(n)

hilos = [threading.Thread(target=competir, args=(i,)) for i in range(8)]
[h.start() for h in hilos]
[h.join() for h in hilos]
print(f"3. 8 claimers compitieron, ganaron {len(ganadores)}: {ganadores}")
assert len(ganadores) == 1, f"claim_task no es atomico: {len(ganadores)} ganadores"

# --- 4. Ejecucion cruzada: ninguna card corre dos veces ---
# El dispatcher de Hermes tickea mientras ORQUESTER reclama su carril.
spawned.clear()
mios = [k.create_task(conn, title=f"externo {i}", assignee=LANE) for i in range(4)]
suyos = [k.create_task(conn, title=f"nativo {i}", assignee="default") for i in range(4)]
reclamados = []

def orquester():
    c = k.connect(db_path=db)
    for t in mios:
        if k.claim_task(c, t, claimer="orquester") is not None:
            reclamados.append(t)

def hermes():
    c = k.connect(db_path=db)
    for _ in range(3):
        k.dispatch_once(c, spawn_fn=spy)

h1, h2 = threading.Thread(target=orquester), threading.Thread(target=hermes)
h1.start(); h2.start(); h1.join(); h2.join()

lanzados = [t for t, _ in spawned]
print(f"4. ORQUESTER reclamo {len(reclamados)}/4; Hermes lanzo {len(lanzados)}")
assert set(reclamados) == set(mios), "ORQUESTER debe reclamar sus 4 cards"
assert not (set(lanzados) & set(mios)), "SOLAPAMIENTO: Hermes lanzo una card del carril externo"
assert len(lanzados) == len(set(lanzados)), "Hermes lanzo la misma card dos veces"

print("\nOK: los dos dispatchers coexisten sin pisarse.")
