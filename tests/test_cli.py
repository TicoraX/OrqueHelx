"""Tests para el CLI autónomo de ORQUESTER (cli.py).

    uv run --python 3.11 --with jsonschema --with pyyaml python tests/test_cli.py
"""
import argparse, io, json, os, sys, tempfile
from pathlib import Path
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import cli

# 1. Doctor
res = cli.cmd_doctor(argparse.Namespace())
assert res == 0, f"cmd_doctor devolvio {res}"
print("1. cmd_doctor: OK")

# 2. Plantillas
res = cli.cmd_plantillas(argparse.Namespace())
assert res == 0, f"cmd_plantillas devolvio {res}"
print("2. cmd_plantillas: OK")

# 3. Skills
res = cli.cmd_skills(argparse.Namespace())
assert res == 0, f"cmd_skills devolvio {res}"
print("3. cmd_skills: OK")

# 4. Validar
pl = str(RAIZ / "plantillas" / "documentar-cambios.json")
args_val = argparse.Namespace(grafo=pl, ignorar_capacidades=True)
res = cli.cmd_validar(args_val)
assert res == 0, f"cmd_validar con plantilla valida devolvio {res}"

# Validar archivo inexistente
args_inv = argparse.Namespace(grafo="archivo-que-no-existe-123.json", ignorar_capacidades=True)
res = cli.cmd_validar(args_inv)
assert res == 1, "cmd_validar debio fallar con archivo inexistente"
print("4. cmd_validar: OK")

print("\nOK: todos los tests de cli.py pasaron exitosamente.")
