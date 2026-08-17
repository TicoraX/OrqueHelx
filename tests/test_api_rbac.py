"""Multiusuario: sesiones, roles y aislamiento entre organizaciones.

Levanta la API de NestJS contra el Postgres del compose y ejerce los caminos
que importan. Lo que se prueba no es que funcione el camino feliz: es que un
VIEWER no pueda ejecutar y que una org no vea la de al lado.

Requiere `docker compose up -d db` y `npm run build` en apps/api.

    uv run --python 3.11 python ..\\tests\\test_api_rbac.py
"""
import json, os, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
API = RAIZ / "apps" / "api"
PUERTO = 3999
BASE = f"http://127.0.0.1:{PUERTO}/api"
CLAVE = "clave-larga-de-prueba-123"

# Preflight: sin Postgres el API no arranca y el test moria con un URLError
# ilegible. Mejor decir que falta y como levantarlo.
import socket
def _puerto_abierto(host, puerto):
    with socket.socket() as s:
        s.settimeout(2)
        return s.connect_ex((host, puerto)) == 0

if not _puerto_abierto("127.0.0.1", 5433):
    print("OMITIDO: no hay Postgres en 127.0.0.1:5433.")
    print("  Levantalo con:  docker compose up -d db")
    raise SystemExit(0)
if not (API / "dist" / "main.js").is_file():
    print("OMITIDO: falta el build del API.")
    print("  Compilalo con:  cd apps/api && npm run build")
    raise SystemExit(0)

env = {**os.environ,
       "PORT": str(PUERTO),
       "DATABASE_URL": "postgresql://orquester:orquester-local@localhost:5433/orquester?schema=public",
       # El motor no hace falta para probar RBAC: se apunta a un puerto muerto y
       # los tests evitan las rutas que lo necesitan.
       "ORQUESTER_ENGINE_URL": "http://127.0.0.1:1"}
proc = subprocess.Popen(["node", str(API / "dist" / "main.js")], env=env, cwd=str(API),
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace")


def pedir(metodo, ruta, token=None, cuerpo=None):
    req = urllib.request.Request(
        BASE + ruta, method=metodo,
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers={**({"Authorization": f"Bearer {token}"} if token else {}),
                 **({"Content-Type": "application/json"} if cuerpo is not None else {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        cuerpo_err = e.read().decode()
        try:
            return e.code, json.loads(cuerpo_err or "{}")
        except json.JSONDecodeError:
            return e.code, {"raw": cuerpo_err[:200]}


def unico(p):
    return f"{p}-{int(time.time()*1000)%10**9}@test.local"


try:
    for _ in range(80):
        try:
            pedir("POST", "/auth/login", cuerpo={})
            break
        except Exception:
            time.sleep(0.4)

    # --- 1. Registro: la contraseña corta se rechaza ---
    ana, beto = unico("ana"), unico("beto")
    cod, _ = pedir("POST", "/auth/registro", cuerpo={"email": ana, "password": "corta"})
    assert cod == 400, cod
    cod, _ = pedir("POST", "/auth/registro", cuerpo={"email": ana, "password": CLAVE})
    assert cod == 201, cod
    cod, _ = pedir("POST", "/auth/registro", cuerpo={"email": ana, "password": CLAVE})
    assert cod == 409, f"el email repetido debe dar 409, dio {cod}"
    pedir("POST", "/auth/registro", cuerpo={"email": beto, "password": CLAVE})
    print("1. registro: clave corta 400, email repetido 409: OK")

    # --- 2. Login ---
    cod, r = pedir("POST", "/auth/login", cuerpo={"email": ana, "password": "otra-clave-larga"})
    assert cod == 401, cod
    cod, r = pedir("POST", "/auth/login", cuerpo={"email": unico("fantasma"), "password": CLAVE})
    assert cod == 401, "un email inexistente no puede distinguirse de una clave mala"
    _, ses_ana = pedir("POST", "/auth/login", cuerpo={"email": ana, "password": CLAVE})
    _, ses_beto = pedir("POST", "/auth/login", cuerpo={"email": beto, "password": CLAVE})
    t_ana, t_beto = ses_ana["token"], ses_beto["token"]
    print("2. login: clave mala y usuario inexistente dan el mismo 401: OK")

    # --- 3. Sin token no se entra a ningun lado ---
    for metodo, ruta in [("GET", "/auth/yo"), ("GET", "/orgs"), ("POST", "/orgs")]:
        cod, _ = pedir(metodo, ruta, cuerpo={} if metodo == "POST" else None)
        assert cod == 401, f"{ruta} respondio {cod} sin token"
    cod, _ = pedir("GET", "/auth/yo", token="token-inventado")
    assert cod == 401, cod
    print("3. sin token o con token falso: 401 en todas: OK")

    # --- 4. El que crea la org queda OWNER ---
    _, org = pedir("POST", "/orgs", token=t_ana, cuerpo={"nombre": "Equipo de Ana"})
    orgId = org["id"]
    _, mias = pedir("GET", "/orgs", token=t_ana)
    assert any(o["id"] == orgId and o["rol"] == "OWNER" for o in mias), mias
    print(f"4. org creada, el creador es OWNER: OK")

    # --- 5. Aislamiento: Beto no ve la org de Ana ---
    _, suyas = pedir("GET", "/orgs", token=t_beto)
    assert not any(o["id"] == orgId for o in suyas), suyas
    # Y al pedirla directo recibe 404, NO 403: un 403 confirmaria que existe.
    cod, _ = pedir("GET", f"/orgs/{orgId}/auditoria", token=t_beto)
    assert cod == 404, f"un extraño debe recibir 404, recibio {cod}"
    cod, _ = pedir("GET", f"/orgs/{orgId}/grafos", token=t_beto)
    assert cod == 404, cod
    print("5. un extraño recibe 404 (no 403): la org no se filtra: OK")

    # --- 6. Roles: VIEWER mira, no ejecuta ---
    cod, _ = pedir("POST", f"/orgs/{orgId}/miembros", token=t_beto,
                   cuerpo={"email": beto, "rol": "OWNER"})
    assert cod == 404, "un extraño no puede auto-invitarse"
    cod, _ = pedir("POST", f"/orgs/{orgId}/miembros", token=t_ana,
                   cuerpo={"email": beto, "rol": "VIEWER"})
    assert cod == 201, cod
    cod, _ = pedir("GET", f"/orgs/{orgId}/grafos", token=t_beto)
    assert cod == 200, f"un VIEWER debe poder listar: {cod}"
    cod, r = pedir("POST", f"/orgs/{orgId}/grafos", token=t_beto,
                   cuerpo={"nombre": "x", "spec": {"nodos": []}})
    assert cod == 403, f"un VIEWER no puede crear grafos: {cod} {r}"
    print("6. VIEWER lista pero no crea (403): OK")

    # --- 7. Ascender a EDITOR cambia lo que puede ---
    pedir("POST", f"/orgs/{orgId}/miembros", token=t_ana,
          cuerpo={"email": beto, "rol": "EDITOR"})
    cod, r = pedir("POST", f"/orgs/{orgId}/grafos", token=t_beto,
                   cuerpo={"nombre": "flujo de beto", "spec": {"nodos": []}})
    # 502 = paso el RBAC y murio al llamar al motor (que aca no existe).
    # Es el resultado correcto: lo que se prueba es el permiso, no el motor.
    assert cod == 502, f"un EDITOR debe pasar el RBAC y llegar al motor: {cod} {r}"
    print("7. tras ascender a EDITOR, el RBAC lo deja pasar (502 del motor): OK")

    # --- 8. Solo OWNER administra miembros ---
    cod, _ = pedir("POST", f"/orgs/{orgId}/miembros", token=t_beto,
                   cuerpo={"email": ana, "rol": "VIEWER"})
    assert cod == 403, f"un EDITOR no administra miembros: {cod}"
    print("8. un EDITOR no puede tocar miembros (403): OK")

    # --- 9. La auditoria registro todo esto ---
    cod, log = pedir("GET", f"/orgs/{orgId}/auditoria", token=t_ana)
    acciones = [e["accion"] for e in log]
    assert "org.crear" in acciones and "miembro.invitar" in acciones, acciones
    print(f"9. auditoria con {len(log)} entradas: {sorted(set(acciones))}: OK")

    # --- 10. Logout invalida la sesion ---
    pedir("POST", "/auth/logout", token=t_beto)
    cod, _ = pedir("GET", "/auth/yo", token=t_beto)
    assert cod == 401, f"la sesion cerrada no puede seguir sirviendo: {cod}"
    print("10. logout invalida el token: OK")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

print("\nOK: multiusuario con roles y organizaciones aisladas.")
