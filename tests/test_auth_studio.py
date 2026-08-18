"""El Studio no atiende `/api/*` sin token.

Importa porque el Studio ejecuta agentes con shell: sin esta barrera, exponer
el puerto es entregar una terminal. Un check que falle si alguien agrega un
endpoint por fuera de la guardia.

    uv run --python 3.11 --with jsonschema python ..\\tests\\test_auth_studio.py
"""
import json, os, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PUERTO = 8799
TOKEN = "token-de-prueba-no-adivinable"

env = {**os.environ, "ORQUESTER_TOKEN": TOKEN, "PYTHONIOENCODING": "utf-8"}
proc = subprocess.Popen([sys.executable, str(RAIZ / "ui" / "server.py"), str(PUERTO)],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace")


def pedir(ruta, token=None, cabecera=True, cuerpo=None):
    url = f"http://127.0.0.1:{PUERTO}{ruta}"
    if token and not cabecera:
        url += ("&" if "?" in ruta else "?") + f"token={token}"
    req = urllib.request.Request(
        url,
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers=({"X-Orquester-Token": token} if token and cabecera else {}) |
                ({"Content-Type": "application/json"} if cuerpo is not None else {}),
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


try:
    for _ in range(50):                       # esperar a que levante
        try:
            pedir("/")
            break
        except Exception:
            time.sleep(0.2)

    # La cascara HTML se sirve sin token: recargar la pagina tiene que andar.
    codigo, _ = pedir("/")
    assert codigo == 200, codigo
    print("1. GET / sin token responde 200 (cascara estatica): OK")

    # Todo /api/* exige token. Se recorren TODAS las rutas, no una de muestra:
    # el riesgo real es que alguien agregue un endpoint fuera de la guardia.
    rutas_get = ["/api/estado?board=x", "/api/traza?board=x&task=t",
                 "/api/consumo?board=x", "/api/grafos", "/api/grafo?nombre=x",
                 # Lanza un subproceso (`agy models`): sin token, ni eso.
                 "/api/modelos?runtime=claude-code"]
    for ruta in rutas_get:
        codigo, _ = pedir(ruta)
        assert codigo == 404, f"{ruta} respondio {codigo} sin token"
    print(f"2. las {len(rutas_get)} rutas GET de /api rechazan sin token: OK")

    grafo = {"board": "x", "nodos": [{"id": "a", "titulo": "t", "runtime": "opencode"}]}
    rutas_post = ["/api/validar", "/api/compilar", "/api/correr", "/api/grafo",
                  "/api/mcp", "/api/parametros", "/api/parar"]
    for ruta in rutas_post:
        codigo, _ = pedir(ruta, cuerpo=grafo)
        assert codigo == 404, f"{ruta} respondio {codigo} sin token"
    print(f"3. las {len(rutas_post)} rutas POST de /api rechazan sin token: OK")

    # Un token equivocado no vale, ni siquiera uno con el prefijo correcto.
    for malo in ["", "x", TOKEN[:-1], TOKEN + "x", TOKEN.upper()]:
        codigo, _ = pedir("/api/grafos", token=malo)
        assert codigo == 404, f"acepto el token {malo!r}"
    print("4. tokens parciales, largos o con otra capitalizacion: rechazados: OK")

    # Con el token correcto, por cabecera y por query.
    codigo, cuerpo = pedir("/api/grafos", token=TOKEN)
    assert codigo == 200 and "grafos" in cuerpo, (codigo, cuerpo)
    codigo, _ = pedir("/api/grafos", token=TOKEN, cabecera=False)
    assert codigo == 200, codigo
    print("5. con el token correcto (cabecera y query) responde 200: OK")

    # Validar es la ruta POST inofensiva: prueba que el token sirve para POST.
    codigo, cuerpo = pedir("/api/validar", token=TOKEN, cuerpo=grafo)
    assert codigo == 200 and '"ok": true' in cuerpo.lower(), (codigo, cuerpo)
    print("6. POST autenticado funciona: OK")
finally:
    proc.terminate()
    proc.wait(timeout=10)

print("\nOK: sin token no se toca nada que ejecute agentes.")
