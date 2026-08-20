"""El chat: un turno de conversacion con sesion, sin quemar plata.

Los backends se reemplazan por procesos falsos. Lo que se prueba es el
mecanismo —que el id de sesion viaje, que las barandas sigan puestas, que un
turno vacio no pase por bueno—, no que el modelo conteste bien.

La memoria de verdad (que `--resume` recupere el turno anterior) se verifico
contra los CLIs reales y quedo en ARQUITECTURA.md; repetirlo aca costaria
dinero en cada corrida de la suite.

    uv run --python 3.11 --with jsonschema python ..\\tests\\test_chat.py
"""
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "dispatcher"))
sys.path.insert(0, str(RAIZ / "hermes-agent"))

import backends as b
import loop

# Un CLI falso que devuelve el envelope de claude e informa que sesion recibio.
FALSO = (
    "import sys, json;"
    "a = sys.argv[1:];"
    "s = a[a.index('--resume') + 1] if '--resume' in a else 'ses-nueva';"
    "print(json.dumps({'result': 'recibi: ' + a[a.index('-p') + 1],"
    " 'session_id': s, 'usage': {'input_tokens': 10, 'output_tokens': 5},"
    " 'total_cost_usd': 0.01}))"
)
_original = b.CHAT["claude-code"]
b.CHAT["claude-code"] = (
    lambda msg, sesion, modelo: ["python", "-c", FALSO, "-p", msg,
                                 *(["--resume", sesion] if sesion else []),
                                 *(["--model", modelo] if modelo else [])],
    _original[1],
)

# --- 1. Un turno devuelve texto, sesion y consumo ---
r = b.chat_backend("claude-code", "hola", timeout=60)
assert r["texto"] == "recibi: hola", r
assert r["sesion"] == "ses-nueva", r
assert r["uso"]["total"] == 15 and r["uso"]["costo_usd"] == 0.01, r["uso"]
print("1. un turno devuelve texto, sesion y consumo: OK")

# --- 2. La sesion viaja al CLI en el turno siguiente ---
# Sin esto el agente arranca de cero en cada mensaje y el chat es un formulario
# con memoria de pez.
r2 = b.chat_backend("claude-code", "segundo", sesion="ses-abc", timeout=60)
assert r2["sesion"] == "ses-abc", f"no se retomo la sesion: {r2['sesion']}"
print("2. la sesion se pasa al CLI y vuelve intacta: OK")

# --- 3. Si el CLI no informa sesion, se conserva la que habia ---
# Perder el id a mitad de la charla arrancaria una conversacion nueva sin
# avisar, y el usuario veria al agente olvidarse de todo de golpe.
b.CHAT["claude-code"] = (
    lambda msg, sesion, modelo: ["python", "-c",
                                 "import json; print(json.dumps({'result': 'ok'}))"],
    _original[1],
)
r3 = b.chat_backend("claude-code", "x", sesion="ses-vieja", timeout=60)
assert r3["sesion"] == "ses-vieja", r3
print("3. un CLI que no informa sesion no borra la que habia: OK")

# --- 4. Una respuesta vacia es un error, no un turno en blanco ---
b.CHAT["claude-code"] = (
    lambda msg, sesion, modelo: ["python", "-c",
                                 "import json; print(json.dumps({'result': '  '}))"],
    _original[1],
)
try:
    b.chat_backend("claude-code", "x", timeout=60)
    raise SystemExit("FALLA: acepto una respuesta vacia")
except b.BackendError as e:
    assert "no devolvio texto" in str(e), e
print("4. una respuesta vacia falla en vez de pintarse como turno: OK")

# --- 5. Las barandas del grafo valen igual en el chat ---
b.CHAT["claude-code"] = (
    lambda msg, sesion, modelo: ["python", "-c", "print(1)",
                                 "--dangerously-skip-permissions"],
    _original[1],
)
try:
    b.chat_backend("claude-code", "x", timeout=60)
    raise SystemExit("FALLA: el chat se salteo la denylist")
except b.BackendError as e:
    assert "flags de bypass prohibidos" in str(e), e
print("5. la denylist de flags de bypass tambien aplica al chat: OK")

# --- 6. `run_chat` concede los permisos del carril, no todos ---
# El Studio llama a `run_chat` y no a `chat_backend` justamente para esto: un
# chat es un agente con shell igual que un nodo.
visto = {}
b.CHAT["claude-code"] = (
    lambda msg, sesion, modelo: ["python", "-c",
                                 "import json; print(json.dumps({'result': 'ok'}))"],
    _original[1],
)
_chat = b.chat_backend
def espia(runtime, mensaje, **kw):
    visto.update(kw)
    return _chat(runtime, mensaje, **kw)
loop.chat_backend = espia
loop.run_chat("claude-code", "hola", timeout=60)
assert visto.get("herramientas") == loop._HERRAMIENTAS, visto
assert "--dangerously-skip-permissions" not in (visto.get("herramientas") or [])
print("6. run_chat pasa las herramientas del carril: OK")

# --- 7. Un runtime que no puede chatear se rechaza ---
try:
    b.chat_backend("hermes", "hola", timeout=60)
    raise SystemExit("FALLA: hermes no tiene modo chat")
except b.BackendError as e:
    assert "desconocido para chat" in str(e), e
print("7. un runtime sin modo chat se rechaza: OK")

# --- 8. El id de sesion de opencode sale de sus eventos ---
eventos = "\n".join([
    json.dumps({"type": "step_start", "sessionID": "ses_abc123"}),
    json.dumps({"type": "text", "part": {"type": "text", "text": "hola"}}),
])
assert b._sesion_de_jsonl(eventos) == "ses_abc123"
assert b._sesion_de_jsonl("sin json aca") is None
print("8. el sessionID de opencode se extrae de los eventos: OK")

# --- 9. El Studio lee la clave que el chat DEVUELVE ---
# `_optimizar_goal` y `_generar_grafo` leian `res["respuesta"]`, y `chat_backend`
# devuelve `res["texto"]`. Nadie en el repo producia "respuesta". Efecto: el
# optimizador devolvia el goal sin tocar mientras el Studio anunciaba "Goal
# optimizado con exito", y "generar grafo con IA" caia SIEMPRE a una plantilla
# fija de tres nodos. Dos features muertas que se veian vivas.
#
# El falso se interpone en `b.CHAT`, no en `run_chat`: si se mockeara `run_chat`
# el mock elegiria la clave y el test pasaria con el bug puesto. La clave la
# tiene que producir `chat_backend` de verdad.
loop.chat_backend = _chat                      # deshacer el espia del punto 6
# El falso del punto 5 contesta "ok" fijo; aca hace falta uno que devuelva algo
# reconocible para distinguir "leyo la respuesta" de "devolvio el goal sin tocar".
b.CHAT["claude-code"] = (
    lambda msg, sesion, modelo: ["python", "-c", FALSO, "-p", msg],
    _original[1],
)
sys.path.insert(0, str(RAIZ / "compiler"))
sys.path.insert(0, str(RAIZ / "mcp_exporter"))
sys.path.insert(0, str(RAIZ / "ui"))
import capacidades
# Y hay que fingir que el runtime esta instalado: sin esto `_optimizar_goal`
# corta antes de llamar a nadie y el test seria un no-op silencioso que "pasa"
# en cualquier maquina sin CLIs.
capacidades.tabla = lambda: {"claude-code": {"disponible": True}}
import server

r = server._optimizar_goal("contá los tests de {{repo}}", runtime="claude-code")
assert r["degradado"] is False, r
assert r["optimizado"].startswith("recibi:"), ("no leyo lo que el chat devolvio", r)
assert "{{repo}}" in r["optimizado"], ("perdio el marcador", r)
print("9. el optimizador usa la respuesta del agente, no el goal sin tocar: OK")

# --- 10. Y cuando degrada, lo DICE ---
# El fallback es correcto (sin CLI hay que degradar); lo que estaba mal era
# hacerlo en silencio y en verde. Por eso el motivo viaja hasta la UI.
capacidades.tabla = lambda: {}
r = server._optimizar_goal("contá los tests", runtime="claude-code")
assert r["degradado"] is True and "ejecutor" in r["motivo"], r
g = server._generar_grafo("un flujo cualquiera", runtime="claude-code")
assert g["degradado"] is True and g["motivo"], g
assert g["grafo"]["nodos"], g
# Y refinando SIN agente no se pisa el grafo: devolver una plantilla de tres
# nodos porque no se pudo hablar con nadie seria perder el trabajo del usuario.
mio = {"board": "mio", "aristas": [],
       "nodos": [{"id": "unico", "titulo": "lo mio", "runtime": "opencode", "x": 7, "y": 9}]}
g2 = server._generar_grafo("agregale un nodo", runtime="claude-code", actual=mio)
assert g2["degradado"] is True and g2["grafo"] is mio, g2
assert "sin cambios" in g2["motivo"], g2
print("10. sin ejecutor, la degradacion se declara y refinar no pisa el grafo: OK")

# --- 11. Refinar: el grafo se MEZCLA, no se reemplaza ---
# El punto de la feature: "agregale un nodo de tests" tiene que conservar los
# nodos que ya estaban Y donde el usuario los dejo en el lienzo. Re-acomodar
# todo en cada refinamiento tira el arreglo manual, que es lo primero que uno
# hace despues de generar.
capacidades.tabla = lambda: {"claude-code": {"disponible": True}}
GRAFO_NUEVO = {
    "board": "mi-flujo", "reglas": "",
    "nodos": [{"id": "build", "titulo": "compilar", "runtime": "opencode"},
              {"id": "tests", "titulo": "correr los tests", "runtime": "opencode"}],
    "aristas": [["build", "tests"]],
}
# El falso ignora el prompt y devuelve el grafo refinado; lo que se prueba es el
# mecanismo de mezcla, no que el modelo diseñe bien.
b.CHAT["claude-code"] = (
    lambda msg, sesion, modelo: [
        "python", "-c",
        "import json,sys; print(json.dumps({'result': json.dumps(" 
        + repr(GRAFO_NUEVO) + "), 'session_id': 'ses-refina'}))"],
    _original[1],
)
ACTUAL = {"board": "mi-flujo", "aristas": [],
          "nodos": [{"id": "build", "titulo": "compilar", "runtime": "opencode",
                     "x": 777, "y": 888}]}
res = server._generar_grafo("agregale un nodo de tests", runtime="claude-code",
                            actual=ACTUAL, sesion="ses-previa")
assert res["degradado"] is False, res
ids = {n["id"]: n for n in res["grafo"]["nodos"]}
assert set(ids) == {"build", "tests"}, ids
# El que ya estaba, donde estaba.
assert (ids["build"]["x"], ids["build"]["y"]) == (777, 888), ids["build"]
# El nuevo, ubicado y sin pisar al viejo.
assert not (abs(ids["tests"]["x"] - 777) < 196 and abs(ids["tests"]["y"] - 888) < 60), ids
assert res["sesion"] == "ses-refina", res
print("11. refinar conserva los nodos y sus posiciones, y ubica los nuevos: OK")

# --- 12. Y la sesion viaja, asi que dos refinamientos son una conversacion ---
visto_argv = {}
b.CHAT["claude-code"] = (
    lambda msg, sesion, modelo: visto_argv.update(sesion=sesion, msg=msg) or [
        "python", "-c",
        "import json; print(json.dumps({'result': json.dumps("
        + repr(GRAFO_NUEVO) + ")}))"],
    _original[1],
)
server._generar_grafo("y otro mas", runtime="claude-code", actual=ACTUAL,
                      sesion="ses-refina")
assert visto_argv["sesion"] == "ses-refina", visto_argv
# Y el grafo actual viaja en el prompt SIN coordenadas: son del lienzo, el
# agente no las decide, y mandarlas es pagar tokens por ruido.
assert '"id": "build"' in visto_argv["msg"], visto_argv["msg"][:300]
assert "777" not in visto_argv["msg"], "las coordenadas viajaron al agente"
print("12. la sesion encadena refinamientos y las coordenadas no viajan: OK")

print("\nOK: el chat conversa con sesion y con las mismas barandas.")
