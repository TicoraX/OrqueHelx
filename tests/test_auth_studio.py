"""El Studio no atiende `/api/*` sin token.

Importa porque el Studio ejecuta agentes con shell: sin esta barrera, exponer
el puerto es entregar una terminal. Un check que falle si alguien agrega un
endpoint por fuera de la guardia.

    uv run --python 3.11 --with jsonschema python ..\\tests\\test_auth_studio.py
"""
import json, os, re, subprocess, sys, time, urllib.error, urllib.request
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

    # Todo /api/* exige token. Las rutas se SACAN DEL FUENTE, no de una lista a
    # mano: la de antes decia "se recorren TODAS" y cubria 18 de 34, porque cada
    # endpoint nuevo habia que acordarse de agregarlo. El riesgo que este test
    # dice cubrir es exactamente ese, y una lista escrita a mano no lo cubre.
    fuente = (RAIZ / "ui" / "server.py").read_text(encoding="utf-8")
    rutas_get = sorted(set(re.findall(r'ruta == "(/api/[^"]*)"', fuente)))
    rutas_post = sorted(set(re.findall(r'self\.path == "(/api/[^"]*)"', fuente)))
    assert len(rutas_get) >= 15 and len(rutas_post) >= 15, (rutas_get, rutas_post)

    # La unica excepcion deliberada: no expone nada del usuario, solo que sabe
    # hacer este motor, y un agente la consulta antes de armar un grafo.
    PUBLICAS = {"/api/capacidades"}

    for ruta in rutas_get:
        if ruta in PUBLICAS:
            continue
        codigo, _ = pedir(ruta)
        assert codigo == 404, f"{ruta} respondio {codigo} sin token"
    print(f"2. las {len(rutas_get) - len(PUBLICAS)} rutas GET de /api "
          f"(sacadas del fuente) rechazan sin token: OK")

    grafo = {"board": "x", "nodos": [{"id": "a", "titulo": "t", "runtime": "opencode"}]}
    for ruta in rutas_post:
        codigo, _ = pedir(ruta, cuerpo=grafo)
        assert codigo == 404, f"{ruta} respondio {codigo} sin token"
    print(f"3. las {len(rutas_post)} rutas POST de /api "
          f"(sacadas del fuente) rechazan sin token: OK")

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
