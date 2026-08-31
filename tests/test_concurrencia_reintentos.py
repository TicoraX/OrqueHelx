"""Concurrencia dentro del carril y reintentos de nodos fallados.

Sin LLM: los backends se reemplazan por procesos falsos que duermen o fallan,
asi el test mide el mecanismo y no la latencia de un modelo.

    uv run --python 3.11 --with jsonschema python ..\\tests\\test_concurrencia_reintentos.py
"""
import os, sys, tempfile, time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "dispatcher"))
sys.path.insert(0, str(RAIZ / "hermes-agent"))

# El backoff de reintentos se apaga para TODO el archivo: lo de abajo prueba la
# mecanica (conteo, tope, permanente vs transitorio) y llama `reintentar()`
# justo despues del fallo, sin esperar. El backoff en si tiene su propia
# seccion al final, que lo prende a mano. Va antes del import de `loop`: la
# constante se lee una sola vez, al importar el modulo.
os.environ["ORQUESTER_RETRY_BACKOFF"] = "0"

import backends as b
import loop
import hermes_cli.kanban_db as k

assert loop.BACKOFF_ACTIVO is False, "el kill switch del backoff no se leyo"

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

# --- 6. Lo permanente lo declara quien levanta el error, no una lista de texto ---
# Antes esto se decidia buscando substrings del mensaje en `loop._PERMANENTES`,
# y se desincronizo dos veces: el mensaje cambio de "binario no encontrado" a
# "no se pudo lanzar" (un fallo permanente se reintentaba dos veces al pedo), y
# despues un `ModuleNotFoundError` nuestro se reintento culpando al agente.
# Ahora es un atributo de la excepcion y no puede quedar apuntando a un texto
# que ya nadie emite.
import inspect
assert issubclass(b.ErrorPermanente, b.BackendError)
assert b.ErrorPermanente("x").permanente is True
assert b.BackendError("x").permanente is False, "el default tiene que ser reintentable"
# Y ningun `raise BackendError` en el modulo puede estar en el lugar de uno
# permanente: los cinco sitios que lo son ya usan la subclase.
fuente = inspect.getsource(b)
assert fuente.count("ErrorPermanente(f") >= 5, "faltan sitios marcados como permanentes"
print("7. lo permanente es un atributo de la excepcion, no una lista de texto: OK")

# Y un cwd inexistente (OSError, no FileNotFoundError) debe ser permanente.
b.BACKENDS["opencode"] = (lambda g, e: ["python", "-c", "print(1)"], b._primer_objeto)
w = k.create_task(conn, title="workspace fantasma", assignee=loop.carril("opencode"),
                  workspace_kind="dir", workspace_path="Z:/no/existe/jamas")
loop.ejecutar_una(conn, w, timeout=60)
tw = k.get_task(conn, w)
print(f"8. cwd inexistente: estado={tw.status} kind={tw.block_kind}")
assert tw.block_kind == "capability", f"un cwd invalido no se arregla reintentando: {tw.block_kind}"


# --- 9. El latido abre la MISMA base que la conexion que reclamo ---
# Abria `k.connect()` a secas, que resuelve al board `default`, y cada board es
# su propia SQLite: en cualquier board que no fuera el default la card no estaba
# ahi, `heartbeat_claim` devolvia False en silencio (nadie miraba el retorno) y
# el latido no latia. Y es la UNICA proteccion que tiene la card: nuestro
# `claim_lock` es "orquester" a secas, no `host:pid`, asi que la extension por
# PID vivo de `release_stale_claims` nunca nos aplica.
h = k.create_task(conn, title="con latido", assignee=loop.carril("opencode"))
assert k.claim_task(conn, h, claimer=loop.CLAIMER) is not None
assert Path(loop._archivo_de(conn)) == db, loop._archivo_de(conn)

c_bien = _connect(db_path=Path(loop._archivo_de(conn)))
assert k.heartbeat_claim(c_bien, h, claimer=loop.CLAIMER) is True, \
    "el latido no encuentra la card en la base que la reclamo"
c_bien.close()

# Y el camino viejo —otra base— falla, callado. Por eso ahora se mira el retorno.
# Sin `k.init_db`: esta parcheado `k.connect` y `init_db` lo llama posicional.
# `connect` auto-inicializa el esquema en la primera conexion, asi que alcanza.
otra = Path(tempfile.mkdtemp()) / "otra.db"
c_mal = _connect(db_path=otra)
assert k.heartbeat_claim(c_mal, h, claimer=loop.CLAIMER) is False, \
    "una base ajena no deberia poder extender este claim"
c_mal.close()
print("9. el latido late contra la base de la card, y avisa si la pierde: OK")

# --- 10. `_queda_trabajo` cuenta las cards `ready` ---
# Contaba `todo`, `running` y `blocked` pero no `ready`. Con el tope de gasto
# agotado, `tick` devuelve [] habiendo cards listas y el loop se cerraba como si
# el flujo hubiera terminado, en vez de por presupuesto.
limpia = Path(tempfile.mkdtemp()) / "ready.db"
c_r = _connect(db_path=limpia)
assert loop._queda_trabajo(c_r) is False, "una base vacia no tiene trabajo"
r = k.create_task(c_r, title="lista para arrancar", assignee=loop.carril("opencode"))
c_r.execute("UPDATE tasks SET status='ready' WHERE id=?", (r,))
c_r.commit()
assert loop._queda_trabajo(c_r) is True, "no conto una card en `ready`"
c_r.close()
print("10. una card `ready` cuenta como trabajo pendiente: OK")

# --- 11. Backoff: un transient no se reabre en la misma pasada del fallo ---
# Sin esto, `tick` (cada ~3s) reabria el nodo en la pasada siguiente al fallo y
# un rate limit se volvia a chocar de inmediato, quemando los intentos.
loop.BACKOFF_ACTIVO = True
try:
    b.BACKENDS["opencode"] = (lambda g, e: ["python", "-c", FALLA], b._primer_objeto)
    bo = k.create_task(conn, title="falla y espera", assignee=loop.carril("opencode"))
    loop.ejecutar_una(conn, bo, timeout=60)
    assert k.get_task(conn, bo).block_kind == "transient", k.get_task(conn, bo)

    assert bo not in loop.reintentar(conn), \
        "con backoff activo no puede reabrirse en el mismo instante del fallo"
    assert k.get_task(conn, bo).status == "blocked"
    print(f"11. con backoff, el fallo recien ocurrido NO se reabre "
          f"(espera {loop.BACKOFF_BASE_S}s): OK")

    # Y cumplido el plazo, si. El margen cubre que `ended_at` esta en segundos
    # enteros y puede haberse redondeado hacia abajo.
    time.sleep(loop.BACKOFF_BASE_S + 1.5)
    assert bo in loop.reintentar(conn), "cumplido el backoff tiene que reabrirse"
    assert k.get_task(conn, bo).status == "ready"
    print("12. cumplido el plazo, el mismo nodo se reabre: OK")

    # --- 12. El re-chequeo bajo lock: si la card ya no es reabrible, se saltea ---
    # `reintentar()` decide sobre una lista leida antes de tomar `_LOCK_RETRY`;
    # el boton manual del Studio (`ui/server.py:_reintentar_nodo`) toma el MISMO
    # lock y puede haber movido la card en el medio. Se simula secuencialmente:
    # la card queda `blocked` para la lista y `ready` para la relectura.
    # Sin backoff: lo que se prueba aca es el re-chequeo, no la espera.
    loop.BACKOFF_ACTIVO = False
    lk = k.create_task(conn, title="la mueven en el medio", assignee=loop.carril("opencode"))
    loop.ejecutar_una(conn, lk, timeout=60)
    assert k.get_task(conn, lk).status == "blocked"

    original = k.get_task
    def _mover_al_releer(c, tid, *a, **kw):
        # Solo la relectura de adentro del lock ve la card ya desbloqueada.
        if tid == lk:
            k.get_task = original
            original(c, tid, *a, **kw)          # el manual gana la carrera
            k.unblock_task(c, tid)
            return original(c, tid, *a, **kw)
        return original(c, tid, *a, **kw)
    k.get_task = _mover_al_releer
    try:
        reab = loop.reintentar(conn)
    finally:
        k.get_task = original
    assert lk not in reab, "no puede contar como reabierta una card que ya movio otro"
    assert k.get_task(conn, lk).status == "ready"
    print("13. el re-chequeo bajo lock saltea la card que ya movio el boton manual: OK")
finally:
    loop.BACKOFF_ACTIVO = False

# --- 14. E0: un assignee mal formado es permanente, no transient ---
# Bug real encontrado revisando el plan de causa tipada: carril()/_runtime_de()
# levantaban BackendError generico (permanente=False por default) para un
# runtime que no existe -- se reintentaba sin sentido, algo que nunca se
# arregla solo.
malo = k.create_task(conn, title="assignee roto",
                     assignee=f"{loop.CARRIL}:runtime-que-no-existe")
loop.ejecutar_una(conn, malo, timeout=60)
tm = k.get_task(conn, malo)
print(f"14. runtime desconocido: estado={tm.status} kind={tm.block_kind}")
assert tm.status == "blocked" and tm.block_kind == "capability", \
    f"un runtime inexistente tiene que ser permanente, no transient: {tm}"
assert malo not in loop.reintentar(conn), "un runtime que no existe no se reintenta"

# --- 15. E1: `causa` queda legible en el run, sobrevive al truncado ---
b.BACKENDS["opencode"] = (lambda g, e: ["python", "-c", "import time; time.sleep(5)"],
                          b._primer_objeto)
lt = k.create_task(conn, title="timeout de verdad", assignee=loop.carril("opencode"))
loop.ejecutar_una(conn, lt, timeout=1)
runs_t = k.list_runs(conn, lt, include_active=False)
assert runs_t[-1].summary.startswith("causa:timeout|"), runs_t[-1].summary
print(f"15. timeout deja causa:timeout| en el run: OK ({runs_t[-1].summary[:24]!r})")

b.BACKENDS["opencode"] = (lambda g, e: ["python", "-c", 'print("no es json")'],
                          b._primer_objeto)
lp = k.create_task(conn, title="parseo roto", assignee=loop.carril("opencode"))
loop.ejecutar_una(conn, lp, timeout=60)
runs_p = k.list_runs(conn, lp, include_active=False)
assert runs_p[-1].summary.startswith("causa:parseo|"), runs_p[-1].summary
print("16. un fallo de parseo deja causa:parseo|: OK")

# El prefijo va DESPUES del truncado a 2000 (loop.py), asi que un mensaje
# larguisimo no se lo come.
b.BACKENDS["opencode"] = (lambda g, e: ["python", "-c", 'print("x" * 5000)'],
                          b._primer_objeto)
ll = k.create_task(conn, title="mensaje larguisimo", assignee=loop.carril("opencode"))
loop.ejecutar_una(conn, ll, timeout=60)
runs_l = k.list_runs(conn, ll, include_active=False)
assert runs_l[-1].summary.startswith("causa:parseo|"), \
    f"el prefijo se corrompio con el truncado: {runs_l[-1].summary[:30]!r}"
assert len(runs_l[-1].summary) <= 2000 + len("causa:parseo|")
print("17. el truncado a 2000 no corrompe el prefijo: OK")

print("\nOK: paralelismo acotado, backoff, reintentos que terminan, y causa tipada.")
