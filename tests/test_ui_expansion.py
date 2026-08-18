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

    # El nombre de la plantilla tambien viene de afuera.
    for malo in ["../../ui/grafos/prueba-ui-expansion", "..", "no-existe"]:
        codigo, _ = pedir("/api/plantilla", {"plantilla": malo, "nombre": "x"})
        assert codigo == 404, f"acepto la plantilla {malo!r}: {codigo}"
    print("12. nombres de plantilla con .. o inexistentes: 404: OK")

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
finally:
    proc.terminate()
    proc.wait(timeout=10)
    (RAIZ / "ui" / "grafos" / f"{NOMBRE}.json").unlink(missing_ok=True)

print("\nOK: abrir un grafo, pasarle parametros y elegir el workspace.")
