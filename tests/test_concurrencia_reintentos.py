"""Concurrencia dentro del carril y reintentos de nodos fallados.

Sin LLM: los backends se reemplazan por procesos falsos que duermen o fallan,
asi el test mide el mecanismo y no la latencia de un modelo.

    uv run --python 3.11 --with jsonschema python ..\\tests\\test_concurrencia_reintentos.py
"""
import sys, tempfile, time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "dispatcher"))
sys.path.insert(0, str(RAIZ / "hermes-agent"))

import backends as b
import loop
import hermes_cli.kanban_db as k

db = Path(tempfile.mkdtemp()) / "conc.db"
k.init_db(db_path=db)
conn = k.connect(db_path=db)  # antes del parche de abajo

# Todas las conexiones de los hilos apuntan a la misma base temporal.
# `loop.k` y `k` son el MISMO objeto de modulo, asi que hay que guardar el
# original antes de reemplazarlo o el parche se llama a si mismo.
_connect = k.connect
k.connect = lambda **kw: _connect(db_path=db)

DORMIR = 'import time,json; time.sleep(2); print(json.dumps({"status":"success","summary":"ok"}))'
b.BACKENDS["opencode"] = (lambda g, e: ["python", "-c", DORMIR], b._primer_objeto)

# --- 0. Cuanto tarda UN nodo aca ---
# El umbral se calibra en esta maquina en vez de escribir un numero: un
# contenedor lento hacia fallar un absoluto de 4.5s con 4.9s, y el test pasaba
# a medir la maquina en vez del paralelismo.
# Calentamiento descartado: el PRIMER nodo paga el arranque en frio del
# interprete y de los imports. En un contenedor medimos 4.6s el primero y ~2.1s
# los siguientes; calibrar con el primero inflaba el umbral y hacia fallar el
# tope de paralelismo por ser "demasiado rapido".
k.create_task(conn, title="calentamiento", assignee=loop.carril("opencode"))
loop.tick(conn, timeout=60)

k.create_task(conn, title="calibracion", assignee=loop.carril("opencode"))
t0 = time.monotonic()
loop.tick(conn, timeout=60)
UNO = time.monotonic() - t0
print(f"0. un nodo tarda {UNO:.1f}s en esta maquina (serial de 3 seria ~{UNO*3:.1f}s)")

# --- 1. Paralelismo: 3 nodos no pueden costar 3 nodos de tiempo ---
ids = [k.create_task(conn, title=f"n{i}", assignee=loop.carril("opencode")) for i in range(3)]
t0 = time.monotonic()
res = loop.tick(conn, timeout=60)
dur = time.monotonic() - t0
print(f"1. 3 nodos en paralelo: {dur:.1f}s (limite {UNO*2:.1f}s)")
assert all(o["status"] == "success" for _, o in res), res
assert dur < UNO * 2, f"no corrieron en paralelo: {dur:.1f}s con un nodo en {UNO:.1f}s"
assert all(k.get_task(conn, i).status == "done" for i in ids)

# --- 2. El tope de paralelismo se respeta ---
loop.MAX_PARALELO = 2
ids2 = [k.create_task(conn, title=f"m{i}", assignee=loop.carril("opencode")) for i in range(4)]
t0 = time.monotonic()
loop.tick(conn, timeout=60)
dur2 = time.monotonic() - t0
# Con tope 2, cuatro nodos son DOS tandas: ni una (seria ignorar el tope) ni
# cuatro (seria serial). Se compara contra el costo medido de un nodo.
print(f"2. 4 nodos con MAX_PARALELO=2: {dur2:.1f}s (esperado entre {UNO*1.4:.1f} y {UNO*3:.1f})")
assert UNO * 1.4 < dur2 < UNO * 3, f"el tope no se respeto: {dur2:.1f}s con un nodo en {UNO:.1f}s"
loop.MAX_PARALELO = 3

# --- 3. Fallo transitorio: bloquea como `transient` y se reintenta ---
FALLA = 'import sys; print("sin contrato"); sys.exit(0)'
b.BACKENDS["opencode"] = (lambda g, e: ["python", "-c", FALLA], b._primer_objeto)
t = k.create_task(conn, title="falla siempre", assignee=loop.carril("opencode"))
loop.ejecutar_una(conn, t, timeout=60)
tarea = k.get_task(conn, t)
print(f"3. tras fallar: estado={tarea.status} kind={tarea.block_kind}")
assert tarea.status == "blocked" and tarea.block_kind == "transient", tarea

reabiertas = loop.reintentar(conn)
assert t in reabiertas, "un transient con intentos disponibles debe reabrirse"
assert k.get_task(conn, t).status == "ready"
print("4. el fallo transitorio se reabre para reintentar: OK")

# --- 4. El reintento se agota y no gira para siempre ---
for _ in range(loop.MAX_INTENTOS + 2):
    if k.get_task(conn, t).status == "ready":
        loop.ejecutar_una(conn, t, timeout=60)
    loop.reintentar(conn)
intentos = len(k.list_runs(conn, t, include_active=False))
final = k.get_task(conn, t)
print(f"5. tras agotarse: estado={final.status}, intentos={intentos}")
assert final.status != "ready", "no puede quedar reintentando sin fin"
assert intentos <= loop.MAX_INTENTOS + 1, f"demasiados intentos: {intentos}"
assert not loop._queda_trabajo(conn), "un fallo agotado no es trabajo pendiente"

# --- 5. Fallo permanente: no se reintenta nunca ---
b.BACKENDS["opencode"] = (lambda g, e: ["binario-que-no-existe-jamas"], b._primer_objeto)
p = k.create_task(conn, title="binario ausente", assignee=loop.carril("opencode"))
loop.ejecutar_una(conn, p, timeout=60)
tp = k.get_task(conn, p)
print(f"6. fallo permanente: estado={tp.status} kind={tp.block_kind}")
assert tp.status == "blocked" and tp.block_kind == "capability", tp
assert p not in loop.reintentar(conn), "un `capability` NO se reintenta"

# --- 6. El clasificador coincide con los mensajes que backends produce ---
# Se desincronizaron una vez: el mensaje cambio de "binario no encontrado" a
# "no se pudo lanzar" y `_PERMANENTES` quedo buscando una cadena que ya nadie
# emitia, asi que un fallo permanente se reintentaba dos veces al pedo.
import inspect
fuente = inspect.getsource(b.run_backend) + inspect.getsource(b._resolver_argv)
for frase in loop._PERMANENTES:
    assert frase in fuente, f"'{frase}' no lo emite nadie en backends.py"
print("7. cada frase de _PERMANENTES existe de verdad en backends: OK")

# Y un cwd inexistente (OSError, no FileNotFoundError) debe ser permanente.
b.BACKENDS["opencode"] = (lambda g, e: ["python", "-c", "print(1)"], b._primer_objeto)
w = k.create_task(conn, title="workspace fantasma", assignee=loop.carril("opencode"),
                  workspace_kind="dir", workspace_path="Z:/no/existe/jamas")
loop.ejecutar_una(conn, w, timeout=60)
tw = k.get_task(conn, w)
print(f"8. cwd inexistente: estado={tw.status} kind={tw.block_kind}")
assert tw.block_kind == "capability", f"un cwd invalido no se arregla reintentando: {tw.block_kind}"

print("\nOK: paralelismo acotado y reintentos que terminan.")
