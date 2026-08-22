"""El contrato entre la UI y el servidor: las claves que una lee y la otra manda.

Es el agujero mas caro de esta rama. `ui/index.html` lee las respuestas del
Studio por nombre de campo --`datos.resumen.progreso_pct`, `t.assignee`-- y
JavaScript no se queja de una clave que no existe: devuelve `undefined` y la
UI lo pinta. Nueve campos inventados del modo App (`hechas`, `total_tareas`,
`status`, `title`, `result`...) convivieron con la suite entera en verde: la
barra de progreso vivia en 0%, todo nodo figuraba pendiente para siempre y el
entregable no aparecia nunca.

El guion del navegador mockea esas respuestas, asi que por si solo no alcanza:
un mock inventado hace pasar a una UI equivocada. Aca se cierra el circulo. Los
mocks viven en `fixtures/respuestas_ui.json` --un solo lugar, que sirve
`ui_navegador.mjs`-- y este test los contrasta contra un Studio de verdad con
un board de verdad. Despues recorre el resto de los endpoints con la tabla de
mas abajo. Si el servidor renombra un campo, el mock queda viejo y esto falla;
el navegador, por su lado, barre el texto visible buscando `undefined`.

    uv run --python 3.11 --with jsonschema --with pyyaml python tests/test_contrato_ui.py
"""
import gc, json, os, re, sqlite3, subprocess, sys, tempfile, time
import urllib.error, urllib.parse, urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "hermes-agent"))
sys.path.insert(0, str(RAIZ / "compiler"))
sys.path.insert(0, str(RAIZ / "dispatcher"))
import compile as c
import hermes_cli.kanban_db as k
import loop as dispatcher

PUERTO = 8799
TOKEN = "token-de-prueba-contrato"
BOARD = f"contrato-{int(time.time()) % 100000}"
# Todo board que este test cree, para borrarlo al final.
_creados = {BOARD, f"{BOARD}-c"}
FIJAS = json.loads((RAIZ / "tests" / "fixtures" / "respuestas_ui.json")
                   .read_text(encoding="utf-8"))

# Diccionarios cuyas CLAVES son datos, no contrato: ids de tarea, nombres de
# estado. Comparar sus claves diria que `t_a` != `t_5ed7a52b`, que es cierto y
# no significa nada. De estos se compara la forma de los VALORES.
CLAVES_LIBRES = {"tareas", "por_nodo", "estados"}

# Un contenedor vacio no puede probar la forma de lo que llevaria adentro.
SIN_DATOS = object()


def comparar(fija, viva, ruta=""):
    """Toda clave que el mock promete tiene que existir en la respuesta viva.

    En una sola direccion a proposito: que el servidor mande ademas campos que
    el mock no copia es normal --el mock es un recorte-- y exigir igualdad
    obligaria a actualizarlo por cada campo nuevo. Lo que rompe la UI es la
    direccion contraria, que es la que se revisa.
    """
    faltan = []
    if isinstance(fija, dict) and isinstance(viva, dict):
        if ruta.rsplit(".", 1)[-1] in CLAVES_LIBRES:
            # Una muestra de cada lado alcanza: son todos la misma forma.
            if fija and viva:
                f, v = next(iter(fija.values())), next(iter(viva.values()))
                faltan += comparar(f, v, f"{ruta}.*")
            return faltan
        for clave, valor in fija.items():
            if clave not in viva:
                faltan.append(f"{ruta}.{clave}".lstrip("."))
            else:
                faltan += comparar(valor, viva[clave], f"{ruta}.{clave}")
    elif isinstance(fija, list) and isinstance(viva, list):
        if fija and viva:
            faltan += comparar(fija[0], viva[0], f"{ruta}[0]")
    return faltan


def recorrer(datos, camino):
    """Seguir un camino de la tabla. True, False, o SIN_DATOS.

    Sintaxis:
        "a.b"      diccionarios anidados
        "a.*.b"    diccionario de claves libres: se mira un valor cualquiera,
                   porque todos tienen la misma forma
        "a[].b"    lista de objetos: se mira el primero
    """
    actual = datos
    for tramo in camino.split("."):
        lista = tramo.endswith("[]")
        tramo = tramo[:-2] if lista else tramo
        if tramo == "*":
            if not isinstance(actual, dict):
                return False
            if not actual:
                return SIN_DATOS
            actual = next(iter(actual.values()))
            continue
        if not isinstance(actual, dict) or tramo not in actual:
            return False
        actual = actual[tramo]
        if lista:
            if not isinstance(actual, list):
                return False
            if not actual:
                return SIN_DATOS
            actual = actual[0]
    return True


def TABLA(grafo, board, repo, tarea, snap, sucio, plantilla, copia):
    """Lo que `ui/index.html` lee de cada respuesta, endpoint por endpoint.

    Escrito a mano, no extraido con una regex. Lo intente: un extractor sobre el
    HTML acertaba 36 de 41 sitios y fallaba distinto en cada arreglo --las
    llamadas multilinea, los `.then()`, las rutas armadas con backticks-- porque
    parsear JavaScript con expresiones regulares es escribir un parser mal. Una
    tabla que se lee de un vistazo vale mas que un extractor que miente, y
    ademas puede decir explicitamente que se decidio NO cubrir (ver `AFUERA`).
    """
    return [
        # --- El panel de diagnostico ------------------------------------------
        # Claves anidadas que solo se pintan al apretar un boton que el guion del
        # navegador nunca aprieta: ni el barrido de `undefined` las ve. Es el
        # rincon menos mirado de la UI.
        ("/api/doctor", None, [
            "plataforma.os", "plataforma.release", "plataforma.python",
            "plataforma.sqlite_version", "plataforma.wal_seguro",
            "binarios.*.disponible", "binarios.*.version", "runtimes_activos",
        ]),
        ("/api/secretos-status", None, [
            "total_configuradas", "total_revisadas",
            "secretos[].presente", "secretos[].enmascarado",
            "secretos[].variable", "secretos[].proveedor",
        ]),
        ("/api/workspaces", None, ["total_workspaces", "tamano_total_humano"]),

        # --- Listas que llenan desplegables -----------------------------------
        ("/api/grafos", None, ["grafos"]),
        ("/api/plantillas", None, [
            "plantillas[].nombre", "plantillas[].descripcion", "plantillas[].nodos",
            "plantillas[].runtimes", "plantillas[].parametros", "plantillas[].faltan",
        ]),
        ("/api/historial", None, [
            "historial[].slug", "historial[].total_nodos", "historial[].completado",
            "historial[].tiene_fallos", "historial[].costo_usd",
        ]),
        # `CAPS[runtime]`: el desplegable de esfuerzo y la pista de modelo salen
        # de aca, y llegan asincronos DESPUES del primer dibujo.
        ("/api/capacidades", None,
         ["*.tope_gasto_flag", "*.modelo_forma", "*.esfuerzos"]),
        ("/api/snapshot", {"board": board, "grafo": grafo, "descripcion": "tabla"}, []),
        (f"/api/snapshots?board={board}", None,
         ["snapshots[].id", "snapshots[].descripcion", "snapshots[].total_nodos"]),

        # --- Lo que se calcula sobre el grafo, sin tocar nada ------------------
        ("/api/validar", grafo, []),
        ("/api/ordenar", grafo, ["posiciones"]),
        ("/api/simular", grafo, [
            "total_nodos", "paralelismo_maximo", "camino_critico_pasos", "runtimes",
            "pasos[].paso", "pasos[].paralelos",
            "pasos[].nodos[].id", "pasos[].nodos[].runtime",
        ]),
        ("/api/parametros", grafo, ["parametros", "faltan"]),
        ("/api/mcp", grafo, ["config", "tool", "parametros"]),
        ("/api/exportar-mermaid", grafo, ["mermaid"]),
        ("/api/exportar-ci", grafo, ["workflow"]),
        ("/api/exportar-python", grafo, ["script"]),
        # `h.tipo.toUpperCase()` no muestra `undefined`: TIRA. Un hallazgo sin
        # `tipo` deja el modal a medio pintar y sin decir por que.
        # Con `sucio` y no con `grafo`: un grafo sano devuelve cero hallazgos y
        # la forma de un hallazgo se quedaria sin mirar. Un nodo aislado es el
        # antipatron mas barato de provocar.
        ("/api/analizar-grafo", sucio, ["hallazgos[].tipo", "hallazgos[].mensaje"]),
        ("/api/trazabilidad-grafo", {"nodo": "a", "grafo": grafo},
         ["ancestros", "descendientes", "impacto_pct"]),
        ("/api/snapshot/diff", {"id": snap, "grafo_actual": grafo}, [
            "identicos", "nodos_agregados", "nodos_eliminados",
            "nodos_modificados", "aristas_agregadas", "aristas_eliminadas",
        ]),

        # --- Lo que depende del board ya compilado ----------------------------
        (f"/api/consumo?board={board}", None,
         ["total.intentos", "total.con_costo", "total.costo_usd",
          "por_nodo", "tope_usd"]),
        (f"/api/reporte-corrida?board={board}", None, ["reporte"]),
        (f"/api/exportar-dataset?board={board}", None,
         ["board", "total_registros", "jsonl"]),
        (f"/api/traza?board={board}&task={tarea}", None, [
            "resultado",
            "intentos[].n", "intentos[].outcome", "intentos[].inicio",
            "intentos[].fin", "intentos[].resumen", "intentos[].error",
            "eventos[].kind", "eventos[].cuando", "eventos[].detalle",
        ]),

        # --- Guardar, abrir, copiar y borrar ----------------------------------
        # `abrirGrafo(datos, nombre)`: el grafo llega en la raiz de la respuesta,
        # no anidado. La UI dibuja `nodos` y `aristas` directo de ahi.
        ("/api/grafo", grafo, []),                      # POST: guardar
        (f"/api/grafo?nombre={board}", None, ["nodos", "aristas"]),
        ("/api/compilar", {**grafo, "board": f"{board}-c"}, ["ids"]),
        ("/api/plantilla", {"plantilla": plantilla, "nombre": copia}, ["grafo"]),
        ("/api/grafo/borrar", {"board": copia}, []),    # se lleva la copia de arriba

        # --- La carpeta que el modo App analiza -------------------------------
        (f"/api/workspace/analizar?ruta={repo}", None, [
            "nombre", "ruta", "es_git", "git_branch", "git_cambios_pendientes",
            "stack", "frameworks", "comando_tests",
        ]),
    ]


# Lo que NO se contrasta, y por que. Se imprime al terminar: una omision dicha
# es una decision; una omision callada es el bug de la proxima tanda.
AFUERA = {
    # Se contrastan igual, sin HTTP y sin agente, en el punto 8.
    "/api/generar-grafo": "por HTTP llamaria a un agente; su forma va en el punto 8",
    "/api/optimizar-goal": "por HTTP llamaria a un agente; su forma va en el punto 8",
    "/api/orquestar-intencion": "por HTTP llamaria a un agente; su forma va en el punto 8",
    # Cubiertos por otro test de la suite. Decir donde vale mas que decir que no.
    "/api/chat": "responde el dict de `run_chat` tal cual; su forma la fija test_chat",
    "/api/workspace/elegir": "abre un dialogo del sistema; sus 4 salidas van en test_modo_app",
    # Sin cubrir, y por que.
    "/api/modelos": "lanza un subproceso que consulta al proveedor",
    "/api/correr": "arranca el dispatcher",
    "/api/parar": "frena una corrida",
    "/api/reintentar-nodo": "reabre una card",
    "/api/workspaces/limpiar": "borra el scratch de todos los boards",
    "/api/snapshot/restaurar": "pisa el grafo abierto",
    "/api/guardar-plantilla": "escribe en el catalogo del usuario y no hay endpoint para borrarla",
    "/api/boards": "no lo lee nadie en la UI (QA-2026-08-20 D3): endpoint muerto",
    "/api/telemetria": "punto 1: su forma se contrasta contra el mock del navegador",
    "/api/estado": "punto 1: su forma se contrasta contra el mock del navegador",
}

env = {**os.environ, "ORQUESTER_TOKEN": TOKEN, "PYTHONIOENCODING": "utf-8"}
# A un archivo y no a `PIPE`: nadie lee ese pipe mientras corre el test, y con
# el buffer del sistema lleno el Studio se cuelga escribiendo su propio log.
_log = tempfile.NamedTemporaryFile("w+", suffix=".log", delete=False,
                                   encoding="utf-8", errors="replace")
proc = subprocess.Popen([sys.executable, str(RAIZ / "ui" / "server.py"), str(PUERTO)],
                        env=env, stdout=_log, stderr=subprocess.STDOUT)


def _cerrar_log():
    _log.flush(); _log.close()
    if sys.exc_info()[0] is not None:
        cola = Path(_log.name).read_text(encoding="utf-8", errors="replace").strip()
        if cola:
            print("--- ultimas lineas del Studio ---")
            for linea in cola.splitlines()[-15:]:
                print("   ", linea)
    for _ in range(10):
        try:
            Path(_log.name).unlink(missing_ok=True)
            break
        except OSError:
            time.sleep(0.2)


def pedir(ruta, cuerpo=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PUERTO}{ruta}",
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers={"X-Orquester-Token": TOKEN} |
                ({"Content-Type": "application/json"} if cuerpo is not None else {}))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


try:
    for _ in range(50):
        try:
            pedir("/api/grafos")
            break
        except Exception:
            time.sleep(0.2)

    # Un board de verdad, con dos nodos y una arista: `_telemetria` divide por
    # el total y `_estado` recorre las cards, asi que un board vacio devolveria
    # las mismas claves sin ejercitar nada.
    GRAFO = {"board": BOARD, "aristas": [["a", "b"]], "nodos": [
        {"id": "a", "titulo": "uno", "runtime": "opencode"},
        {"id": "b", "titulo": "dos", "runtime": "opencode"}]}
    c.compilar(GRAFO, board=BOARD)

    # --- 1. Los mocks del navegador dicen la verdad ---------------------------
    vivos = {}
    for ruta in ("/api/estado", "/api/telemetria"):
        codigo, vivos[ruta] = pedir(f"{ruta}?board={BOARD}")
        assert codigo == 200, (ruta, codigo, vivos[ruta])

    revisados = 0
    for clave, fija in FIJAS.items():
        if clave.startswith("_"):
            continue
        # `/api/telemetria/terminado` es el mismo endpoint con el board ya
        # frenado: mismo contrato, otro momento.
        ruta = clave.rsplit("/", 1)[0] if clave.endswith("/terminado") else clave
        if ruta not in vivos:
            continue
        faltan = comparar(fija, vivos[ruta])
        assert not faltan, (
            f"el mock de {clave} promete claves que {ruta} no manda: {faltan}\n"
            f"  el servidor manda: {sorted(vivos[ruta])}")
        revisados += 1
    assert revisados >= 3, f"solo se contrastaron {revisados} mocks"
    print(f"1. los {revisados} mocks del navegador coinciden con el Studio vivo: OK")

    # --- 2. Y lo que la UI lee de esas respuestas existe ----------------------
    t = next(iter(vivos["/api/estado"]["tareas"].values()))
    tel = vivos["/api/telemetria"]
    lecturas = {
        "estado.corriendo": "corriendo" in vivos["/api/estado"],
        "estado.tareas.*.titulo": "titulo" in t,
        "estado.tareas.*.estado": "estado" in t,
        "estado.tareas.*.assignee": "assignee" in t,
        "estado.tareas.*.resumen": "resumen" in t,
        "telemetria.resumen.total": "total" in tel["resumen"],
        "telemetria.resumen.terminados": "terminados" in tel["resumen"],
        "telemetria.resumen.progreso_pct": "progreso_pct" in tel["resumen"],
        "telemetria.consumo.total.costo_usd": "costo_usd" in tel["consumo"]["total"],
    }
    rotas = [n for n, existe in lecturas.items() if not existe]
    assert not rotas, f"la UI lee claves que el servidor no manda: {rotas}"
    print(f"2. las {len(lecturas)} claves que pinta el modo App existen: OK")

    # --- 3. El `assignee` trae el runtime que el timeline muestra -------------
    # No es un campo suelto: el timeline saca el runtime partiendo el
    # `orquester-external:<runtime>`. Si el prefijo cambia, la UI no falla:
    # muestra la cadena entera y nadie se entera hasta verlo.
    assert t["assignee"].startswith("orquester-external:"), t["assignee"]
    assert t["assignee"].split(":", 1)[1] == "opencode", t["assignee"]
    print("3. el `assignee` mantiene el formato del que sale el runtime: OK")

    # --- 4. `progreso_pct` viene en 0-100, que es lo que la barra asume -------
    # La barra hace `scaleX(pct / 100)`. Si el servidor pasara a mandar 0-1, la
    # barra no romperia: se quedaria en 0.5% para siempre.
    pct = tel["resumen"]["progreso_pct"]
    assert isinstance(pct, (int, float)) and 0 <= pct <= 100, pct
    assert tel["resumen"]["total"] == 2, tel["resumen"]
    print(f"4. `progreso_pct` es un porcentaje 0-100 ({pct}): OK")

    # --- 5. El resto de los endpoints -----------------------------------------
    tarea = next(iter(vivos["/api/estado"]["tareas"]))

    # Un intento de verdad sobre esa card. Sin esto, `/api/traza` devuelve
    # `intentos: []` y las seis claves que pinta el panel de traza --el numero,
    # el outcome, los tiempos, el resumen, el error-- se quedan sin mirar. El
    # backend va stubbeado: lo que hace falta es el REGISTRO del intento, no
    # gastar plata en un agente para conseguirlo.
    conn = k.connect(board=BOARD)
    real = dispatcher.run_backend
    dispatcher.run_backend = lambda runtime, goal, **kw: {
        "status": "success", "summary": "intento de prueba", "uso": {"costo_usd": 0.0}}
    try:
        dispatcher.ejecutar_una(conn, tarea, timeout=30)
    finally:
        dispatcher.run_backend = real
        conn.close()
    repo = urllib.parse.quote(str(RAIZ))

    # Un snapshot de verdad, para que el listado y el diff tengan de que hablar.
    codigo, _ = pedir("/api/snapshot",
                      {"board": BOARD, "grafo": GRAFO, "descripcion": "contrato"})
    assert codigo == 200, codigo
    _, listado = pedir(f"/api/snapshots?board={BOARD}")
    snap = (listado.get("snapshots") or [{}])[0].get("id", "")

    rotos, sin_datos, revisadas, cubiertos = [], [], 0, set()
    SUCIO = {**GRAFO, "nodos": GRAFO["nodos"] + [
        {"id": "c", "titulo": "aislado", "runtime": "opencode"}]}
    # Una plantilla del catalogo, para copiarla y borrar la copia enseguida: es
    # la unica forma de ejercitar `/api/plantilla` sin dejar basura.
    _, cat = pedir("/api/plantillas")
    PLANTILLA = (cat.get("plantillas") or [{}])[0].get("nombre", "")
    assert PLANTILLA, "el catalogo de plantillas vino vacio"
    COPIA = f"{BOARD}-copia"

    for ruta, cuerpo, caminos in TABLA(GRAFO, BOARD, repo, tarea, snap, SUCIO,
                                       PLANTILLA, COPIA):
        codigo, datos = pedir(ruta, cuerpo)
        assert codigo == 200, f"{ruta} devolvio {codigo}: {datos}"
        for camino in caminos:
            r = recorrer(datos, camino)
            if r is SIN_DATOS:
                sin_datos.append(f"{ruta.split('?')[0]} -> {camino}")
            elif not r:
                rotos.append(f"{ruta.split('?')[0]} -> {camino}")
        revisadas += len(caminos)
        cubiertos.add(ruta.split("?")[0])

    assert not rotos, ("la UI lee claves que el servidor no manda:\n  "
                       + "\n  ".join(rotos))
    print(f"5. {revisadas} claves mas, en {len(cubiertos)} endpoints: OK")

    # --- 6. Lo que no se pudo mirar se dice -----------------------------------
    # Un contenedor vacio no prueba la forma de lo que llevaria adentro. Pasar
    # en silencio es exactamente como los nueve campos del modo App
    # sobrevivieron a la suite entera.
    if sin_datos:
        print(f"6. {len(sin_datos)} camino(s) SIN DATOS en esta maquina: el "
              f"contenedor vino vacio y la forma no se pudo mirar:")
        for x in sin_datos:
            print(f"     {x}")
    else:
        print("6. ningun camino quedo sin datos: OK")

    # --- 7. Ninguna ruta del servidor queda sin declarar ----------------------
    # El punto que hace que esto no se pudra. Sin el, la tabla es "me acorde de
    # todos" y nadie se entera de la ruta 45. Se escribio despues de que una
    # comparacion a mano encontrara SEIS rutas que no estaban ni en la tabla ni
    # en AFUERA: no se habian excluido por ningun motivo, se habian perdido.
    #
    # Del fuente y no de una lista escrita al lado: una lista al lado es otra
    # cosa que se queda vieja. Es la misma idea que `test_pin_hermes`.
    fuente = (RAIZ / "ui" / "server.py").read_text(encoding="utf-8")
    del_servidor = set(re.findall(r'(?:ruta|self\.path) == "(/api/[^"]+)"', fuente))
    assert len(del_servidor) >= 40, (
        f"el patron encontro {len(del_servidor)} rutas: cambio la forma de "
        "despachar en server.py y este chequeo dejo de mirar lo que decia mirar")

    declaradas = {r.split("?")[0] for r, _, _ in
                  TABLA(GRAFO, BOARD, repo, tarea, snap, SUCIO, PLANTILLA, COPIA)}
    declaradas |= set(AFUERA)
    huerfanas = sorted(del_servidor - declaradas)
    assert not huerfanas, (
        "rutas del servidor que no estan ni contrastadas ni declaradas en "
        f"AFUERA:\n  " + "\n  ".join(huerfanas) +
        "\n  (agregala a TABLA con lo que la UI le lee, o a AFUERA con el motivo)")

    # Y al reves: una entrada que apunta a una ruta que ya no existe es una
    # cobertura imaginaria, que es peor que ninguna.
    fantasmas = sorted(declaradas - del_servidor)
    assert not fantasmas, (
        f"declaradas pero el servidor ya no las sirve: {fantasmas}")
    print(f"7. las {len(del_servidor)} rutas de server.py estan declaradas: OK")

    # --- 8. Los tres que llaman a un agente, sin llamar a ninguno -------------
    # Los tenia afuera por caros y estaba equivocado: la forma de la respuesta no
    # la arma el agente, la arma este codigo envolviendo lo que el agente
    # devolvio. Es determinista y se puede mirar gratis.
    #
    # En proceso y no por HTTP: lo que se pregunta es la forma de un diccionario,
    # y el HTTP no agrega nada a esa pregunta. Ademas es lo unico que permite
    # stubbear el agente, que en el subproceso del Studio esta fuera de alcance.
    sys.path.insert(0, str(RAIZ / "ui"))
    import server as srv

    GRAFO_DEL_AGENTE = json.dumps({
        "board": "disenado", "nodos": [
            {"id": "n1", "titulo": "leer", "runtime": "opencode"},
            {"id": "n2", "titulo": "escribir", "runtime": "opencode"}],
        "aristas": [["n1", "n2"]]})

    chat_real, rt_real, arrancar_real = (srv.dispatcher.run_chat,
                                         srv._runtime_para_chatear, srv._arrancar)
    srv.dispatcher.run_chat = lambda rt, prompt, **kw: {
        "texto": GRAFO_DEL_AGENTE if "arquitecto" in prompt else "goal optimizado",
        "sesion": "ses-1", "uso": {"costo_usd": 0.0}}
    # Sin esto, en una maquina sin CLIs instalados las dos funciones se van por
    # la rama degradada y el camino con agente nunca se mira.
    srv._runtime_para_chatear = lambda rt: "opencode"
    srv._arrancar = lambda board, tope: {"ok": True, "motivo": ""}
    try:
        # Las dos ramas tienen que devolver LAS MISMAS claves. Si la degradada
        # devolviera de menos, la UI leeria `undefined` justo cuando algo salio
        # mal, que es cuando el usuario mas necesita que le hablen claro.
        for nombre, con_agente, degradado in (
            ("_generar_grafo",
             srv._generar_grafo("armá un flujo de revisión"),
             srv._generar_grafo("armá un flujo de revisión", dry_run=True)),
            ("_optimizar_goal",
             srv._optimizar_goal("revisá el repo"),
             srv._optimizar_goal("revisá el repo", dry_run=True)),
        ):
            assert set(con_agente) == set(degradado), (
                f"{nombre}: la rama degradada devuelve otras claves que la normal: "
                f"con agente {sorted(con_agente)} / degradada {sorted(degradado)}")
            assert degradado["degradado"] is True and con_agente["degradado"] is False, (
                f"{nombre}: `degradado` no distingue las dos ramas")

        g = srv._generar_grafo("armá un flujo de revisión")
        for camino in ("grafo.board", "grafo.nodos", "grafo.aristas",
                       "degradado", "motivo", "sesion"):
            assert recorrer(g, camino) is True, f"_generar_grafo no devuelve {camino}"
        # La UI dibuja los nodos con las coordenadas que vienen del servidor.
        n0 = g["grafo"]["nodos"][0]
        faltan_n = [c for c in ("id", "titulo", "runtime", "x", "y") if c not in n0]
        assert not faltan_n, f"al nodo disenado le faltan {faltan_n}: {n0}"

        o = srv._optimizar_goal("revisá el repo")
        for camino in ("optimizado", "degradado", "motivo"):
            assert recorrer(o, camino) is True, f"_optimizar_goal no devuelve {camino}"

        # Y el mock del modo App, que hasta aca era el unico sin contrastar:
        # `/api/orquestar-intencion` no se puede pedir por HTTP sin gastar, asi
        # que su forma se comparaba contra si misma.
        oi = srv._orquestar_intencion({"workspace": str(RAIZ), "prompt": "revisá el repo",
                                       "ejecutar": True, "tope_usd": "1"})
        _creados.add(oi.get("board", ""))
        faltan_oi = comparar(FIJAS["/api/orquestar-intencion"], oi)
        assert not faltan_oi, (
            "el mock de /api/orquestar-intencion promete claves que la funcion "
            f"no devuelve: {faltan_oi}\n  devuelve: {sorted(oi)}")
        print("8. las 3 funciones que llaman a un agente devuelven lo que la UI "
              "lee, con agente y sin el: OK")
    finally:
        srv.dispatcher.run_chat = chat_real
        srv._runtime_para_chatear = rt_real
        srv._arrancar = arrancar_real

    print(f"\n   afuera del contraste, a proposito ({len(AFUERA)}):")
    for ruta, motivo in sorted(AFUERA.items()):
        print(f"     {ruta:26} {motivo}")

    print(f"\nOK: {revisadas + len(lecturas)} claves de la UI contrastadas contra "
          f"un Studio de verdad.")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    _cerrar_log()
    # Lo que este test escribio, este test lo saca. `ui/grafos/` es la carpeta
    # donde el usuario guarda SUS flujos y `list_boards` es lo que llena el
    # desplegable de corridas previas del Studio: un test que deja tres boards y
    # un .json por corrida le ensucia la interfaz a quien lo corra dos veces.
    for f in (RAIZ / "ui" / "grafos").glob(f"{BOARD}*.json"):
        f.unlink(missing_ok=True)
    # Antes de borrar: soltar las conexiones que este proceso dejo abiertas. En
    # Windows un `.db` con un handle vivo no se puede borrar, y `compilar`,
    # `ejecutar_una` y `_orquestar_intencion` abren la suya adentro sin
    # devolverla. La causa se vio en el error, no se adivino:
    #   PermissionError [WinError 32] ... 'boards\contrato-82636\kanban.db'
    # ponytail: barrido por `gc` en vez de un registro de conexiones. Es teardown
    # de un test; si algun dia hace falta en produccion, ahi si va el registro.
    for objeto in gc.get_objects():
        if isinstance(objeto, sqlite3.Connection):
            try:
                objeto.close()
            except Exception:
                pass

    for slug in sorted(x for x in _creados if x):
        try:
            # `archive=False`: archivar mueve el directorio a `_archived/`, o sea
            # que la basura de un test seguiria ocupando disco del usuario para
            # siempre. Lo que este test creo no vale la pena recuperarlo.
            k.remove_board(slug, archive=False)
        except Exception as e:
            # Dicho, no tragado: un limpiado que falla en silencio es como
            # empezo este problema.
            print(f"   [aviso] no se pudo borrar el board {slug}: "
                  f"{type(e).__name__}: {e}")
