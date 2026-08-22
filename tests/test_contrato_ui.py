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
un board de verdad. Si el servidor renombra un campo, el mock queda viejo y
esto falla; el navegador, por su lado, barre el texto visible buscando
`undefined`. Las dos mitades juntas son la prueba.

    uv run --python 3.11 --with jsonschema --with pyyaml python tests/test_contrato_ui.py
"""
import json, os, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "hermes-agent"))
sys.path.insert(0, str(RAIZ / "compiler"))
import compile as c
import hermes_cli.kanban_db as k

PUERTO = 8799
TOKEN = "token-de-prueba-contrato"
BOARD = f"contrato-{int(time.time()) % 100000}"
FIJAS = json.loads((RAIZ / "tests" / "fixtures" / "respuestas_ui.json")
                   .read_text(encoding="utf-8"))

# Diccionarios cuyas CLAVES son datos, no contrato: ids de tarea, nombres de
# estado. Comparar sus claves diria que `t_a` != `t_5ed7a52b`, que es cierto y
# no significa nada. De estos se compara la forma de los VALORES.
CLAVES_LIBRES = {"tareas", "por_nodo", "estados"}


def comparar(fija, viva, ruta=""):
    """Toda clave que el mock promete tiene que existir en la respuesta viva.

    En una sola direccion a proposito: que el servidor mande ademas campos que
    el mock no copia es normal --el mock es un recorte-- y exigir igualdad
    obligaria a actualizarlo por cada campo nuevo. Lo que rompe la UI es la
    direccion contraria, que es la que se revisa.
    """
    faltan = []
    if isinstance(fija, dict) and isinstance(viva, dict):
        libre = ruta.rsplit(".", 1)[-1] in CLAVES_LIBRES
        if libre:
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


def pedir(ruta):
    req = urllib.request.Request(f"http://127.0.0.1:{PUERTO}{ruta}",
                                 headers={"X-Orquester-Token": TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
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
    c.compilar({"board": BOARD, "aristas": [["a", "b"]], "nodos": [
        {"id": "a", "titulo": "uno", "runtime": "opencode"},
        {"id": "b", "titulo": "dos", "runtime": "opencode"}]}, board=BOARD)

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

    # --- 2. Y lo que la UI lee de esas respuestas existe -----------------------
    # Las claves anidadas que el modo App toca en cada repintado. Escritas a
    # mano y no sacadas del HTML con una regex: parsear JavaScript con
    # expresiones regulares da falsos positivos en cada cierre, y una lista
    # corta que se lee de un vistazo vale mas que un extractor que miente.
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

    # --- 3. El `assignee` trae el runtime que el timeline muestra --------------
    # No es un campo suelto: el timeline saca el runtime partiendo el
    # `orquester-external:<runtime>`. Si el prefijo cambia, la UI no falla:
    # muestra la cadena entera y nadie se entera hasta verlo.
    assert t["assignee"].startswith("orquester-external:"), t["assignee"]
    assert t["assignee"].split(":", 1)[1] == "opencode", t["assignee"]
    print("3. el `assignee` mantiene el formato del que sale el runtime: OK")

    # --- 4. `progreso_pct` viene en 0-100, que es lo que la barra asume --------
    # La barra hace `scaleX(pct / 100)`. Si el servidor pasara a mandar 0-1, la
    # barra no romperia: se quedaria en 0.5% para siempre.
    pct = tel["resumen"]["progreso_pct"]
    assert isinstance(pct, (int, float)) and 0 <= pct <= 100, pct
    assert tel["resumen"]["total"] == 2, tel["resumen"]
    print(f"4. `progreso_pct` es un porcentaje 0-100 ({pct}): OK")

    print("\nOK: los mocks del navegador y las claves del modo App "
          "coinciden con el Studio de verdad.")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    _cerrar_log()
    try:
        k.connect(board=BOARD).close()
    except Exception:
        pass
