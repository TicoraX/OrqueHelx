"""Prueba del contrato output_schema de Hermes con el schema AgentAdapterOutput de ORQUESTER."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hermes-agent"))

from tools.delegation_output_schema import (
    coerce_output_schema, append_output_contract, validate_output,
    build_retry_message, MAX_SCHEMA_RETRIES,
)

# AgentAdapterOutput, tal cual ARQUITECTURA.md:108
SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["success", "failure"]},
        "summary": {"type": "string"},
        "rawLogsRef": {"type": "string"},
        "data": {"type": "object"},
    },
    "required": ["status", "summary"],
    "additionalProperties": False,
}

# 1. El modelo puede mandar el schema doble-encodeado como string
s, err = coerce_output_schema(json.dumps(SCHEMA))
assert err is None and s == SCHEMA, f"coerce falló: {err}"
print("1. coerce_output_schema acepta schema doble-encodeado: OK")

# 2. El contrato se inyecta en el context del hijo
ctx = append_output_contract("Refactoriza el modulo de auth.", SCHEMA)
assert "OUTPUT CONTRACT (machine-validated)" in ctx
assert "Refactoriza el modulo de auth." in ctx
print("2. append_output_contract inyecta el contrato en el context: OK")

# 3. Salida valida, envuelta en prosa + code fence (lo que hace un LLM real)
sucio = 'Listo. Aqui va:\n```json\n{"status":"success","summary":"3 archivos tocados"}\n```\nEspero sirva.'
ok, errs = validate_output(sucio, SCHEMA)
assert ok, errs
print("3. validate_output extrae JSON de prosa + fence: OK")

# 4. Salida invalida -> errores con JSON-path
ok, errs = validate_output('{"status":"done","summary":42,"extra":1}', SCHEMA)
assert not ok
print("4. validate_output rechaza y reporta:")
for e in errs:
    print(f"     {e}")

# 5. El retry lleva los errores verbatim y NO re-pega el schema
retry = build_retry_message(errs)
assert all(e in retry for e in errs)
assert "additionalProperties" not in retry.replace(errs[0], "")
assert MAX_SCHEMA_RETRIES == 1
print(f"5. build_retry_message: errores verbatim, sin re-pegar schema, max {MAX_SCHEMA_RETRIES} retry: OK")

# 6. Falta 'required' -> se detecta
ok, errs = validate_output('{"summary":"sin status"}', SCHEMA)
assert not ok and "status" in errs[0]
print(f"6. required faltante detectado: {errs[0]}")

# 7. CAVEAT: sin jsonschema instalado, validate_output acepta cualquier JSON
import jsonschema
print(f"7. jsonschema presente (v{jsonschema.__version__}) — si falta, validate_output devuelve True sin validar")

print("\nTODOS LOS CHECKS PASARON")
