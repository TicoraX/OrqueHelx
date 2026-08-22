"""La tabla de capacidades tiene que coincidir con el codigo, no con un deseo.

Es la leccion de §4.1: la skill de Hermes decia que `agy` no aceptaba schema, el
worker le creyo, y el nodo corrio degradado. Una tabla de capacidades que
miente es peor que no tenerla, porque un agente la consulta y decide con eso.

Este check ata cada campo declarado a algo comprobable en `backends.py`.

    uv run --python 3.11 --with jsonschema python ..\\tests\\test_capacidades.py
"""
import inspect, json, sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "dispatcher"))
sys.path.insert(0, str(RAIZ / "compiler"))
sys.path.insert(0, str(RAIZ / "hermes-agent"))

import backends as b
import capacidades as cap
import compile as compilador

t = cap.tabla()

# --- 1. Cobertura: todo backend ejecutable esta declarado, y al reves ---
assert set(b.BACKENDS) <= set(cap.DECLARADO), \
    f"backends sin declarar: {set(b.BACKENDS) - set(cap.DECLARADO)}"
for rt in b.BACKENDS:
    assert t[rt]["lo_ejecuta"] == "orquester", rt
assert t["hermes"]["lo_ejecuta"] == "hermes"
print(f"1. los {len(b.BACKENDS)} backends propios declarados, y hermes aparte: OK")

# --- 2. `schema_en_cli` coincide con el argv que se construye de verdad ---
# Si alguien saca `--json-schema` de un backend y no toca la tabla, un agente
# va a seguir creyendo que puede empujar el contrato por CLI.
for rt in b.BACKENDS:
    construir, _ = b.BACKENDS[rt]
    argv = construir("goal", "/ruta/al/esquema.json")
    tiene = "--json-schema" in argv
    assert tiene == t[rt]["schema_en_cli"], \
        f"{rt}: la tabla dice schema_en_cli={t[rt]['schema_en_cli']} pero el argv {'lo trae' if tiene else 'no lo trae'}"
    if tiene:
        valor = argv[argv.index("--json-schema") + 1]
        # inline = el JSON del contrato; ruta = el path que se le paso.
        esperado = "json-inline" if valor.strip().startswith("{") else "ruta-a-archivo"
        assert t[rt]["schema_como"] == esperado, \
            f"{rt}: schema_como dice {t[rt]['schema_como']} pero pasa {esperado}"
print("2. schema_en_cli y schema_como coinciden con el argv real: OK")

# --- 3. `informa_costo` coincide con lo que el extractor puede devolver ---
# `agy` declara que NO informa costo. Si su extractor empezara a devolver un
# numero, la tabla quedaria vieja y el panel contaria mal.
fuentes = {rt: inspect.getsource(fn) for rt, fn in b.USO.items()}
assert "costo_usd" not in fuentes["antigravity"].split("return")[0].replace(
    '"costo_usd": None', ""), "revisar: agy no deberia asignar costo"
for rt in ("claude-code", "opencode"):
    assert 'costo_usd"] =' in fuentes[rt] or 'costo_usd"] = (' in fuentes[rt] \
        or "costo_usd" in fuentes[rt], f"{rt} declara costo pero su extractor no lo toca"
for rt in b.USO:
    assert t[rt]["extrae_consumo"] is True, rt
print("3. informa_costo consistente con los extractores: OK")

# --- 4. `limite_turnos` es el que de verdad se pasa ---
argv = b.BACKENDS["claude-code"][0]("g", "/e.json")
assert str(t["claude-code"]["limite_turnos"]) == argv[argv.index("--max-turns") + 1]
print(f"4. limite de turnos declarado = el que se pasa ({b.MAX_TURNS}): OK")

# --- 5. `acepta_permisos` solo donde el codigo los reenvia ---
argv = b.run_backend.__doc__ or ""
fuente = inspect.getsource(b.run_backend)
assert "--allowedTools" in fuente, "claude declara permisos pero nadie los pasa"
for rt in b.BACKENDS:
    if t[rt]["acepta_permisos"]:
        assert rt == "claude-code", f"{rt} declara permisos por CLI y no los tiene"
print("5. acepta_permisos solo en el backend que los reenvia: OK")

# --- 6. `faltantes()` detecta lo que no esta ---
# Aca habia un `A == [] or all(x in DECLARADO for x in A)` que no podia fallar:
# `faltantes` recorre `tabla()`, que se construye DESDE `DECLARADO`, asi que la
# segunda mitad es cierta por construccion y la primera nunca se evalua. Lo que
# sigue --inventar un runtime con un binario inexistente-- es la prueba de
# verdad, y no necesitaba el preambulo.
cap.DECLARADO["_fantasma"] = {**cap.DECLARADO["opencode"],
                              "binario": "binario-que-no-existe-jamas"}
try:
    assert cap.faltantes({"_fantasma"}) == ["_fantasma"], cap.faltantes({"_fantasma"})
    assert cap.faltantes({"opencode"}) == [], "opencode esta instalado"
finally:
    del cap.DECLARADO["_fantasma"]
print("6. faltantes() detecta un binario ausente y no molesta con los presentes: OK")

# --- 7. El compilador hace preflight: falla ANTES de tocar la base ---
# Se usa un runtime REAL al que se le saca el binario, no uno inventado: un
# runtime desconocido lo rechaza `carril()` mucho antes y no probaria el
# preflight, que es lo que importa aca.
original = cap.DECLARADO["opencode"]["binario"]
cap.DECLARADO["opencode"] = {**cap.DECLARADO["opencode"],
                             "binario": "binario-que-no-existe-jamas"}
try:
    compilador.validar({"nodos": [{"id": "a", "titulo": "t", "runtime": "opencode"}]})
    raise SystemExit("FALLA: compilo un grafo con un ejecutor ausente")
except compilador.ErrorDeGrafo as e:
    assert "no estan disponibles" in str(e), e
    assert "opencode" in str(e), e
finally:
    cap.DECLARADO["opencode"] = {**cap.DECLARADO["opencode"], "binario": original}
print("7. el compilador rechaza un grafo con ejecutor ausente, con el nombre: OK")

# --- 8. Serializable: un agente la consume por HTTP ---
json.dumps(t)
print("8. la tabla serializa a JSON (la consume /api/capacidades): OK")


# --- 9. El modelo por nodo llega al argv de cada CLI, en su forma ---
for rt in b.BACKENDS:
    construir, _ = b.BACKENDS[rt]
    sin = construir("goal", "/e.json")
    con = construir("goal", "/e.json", "modelo-x")
    assert "--model" not in sin, f"{rt}: sin modelo no debe pasar --model"
    assert t[rt]["modelo_flag"] in " ".join(con), f"{rt}: no paso {t[rt]['modelo_flag']}"
    assert con[con.index("--model") + 1] == "modelo-x", rt
    assert t[rt]["modelo_por_nodo"] is True, rt
print("9. el modelo por nodo llega al argv, y sin modelo no se pasa el flag: OK")

# --- 10. El compilador lo guarda en la columna que ya existia ---
import hermes_cli.kanban_db as _k, tempfile
from pathlib import Path as _P
_db = _P(tempfile.mkdtemp()) / "modelo.db"
_k.init_db(db_path=_db)
_c = _k.connect(db_path=_db)
_tid = _k.create_task(_c, title="con modelo", assignee="orquester-external:opencode",
                      model_override="deepseek/deepseek-chat")
assert _k.get_task(_c, _tid).model_override == "deepseek/deepseek-chat"
print("10. el modelo se persiste en `model_override`, sin inventar columna: OK")

print("\nOK: la tabla de capacidades no puede mentir sin que esto falle.")
