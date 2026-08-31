"""Tests para la generación y exportación de reportes ejecutivos (Markdown y HTML).

    uv run --python 3.11 --with jsonschema --with pyyaml python tests/test_reporte.py
"""
import sys, tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "ui"))
sys.path.insert(0, str(RAIZ / "dispatcher"))
sys.path.insert(0, str(RAIZ / "hermes-agent"))

import server
import hermes_cli.kanban_db as k

# Base temporal aislada
db = Path(tempfile.mkdtemp()) / "reportes.db"
k.init_db(db_path=db)
conn = k.connect(db_path=db)

# Crear tareas simuladas
t1 = k.create_task(conn, title="Auditoría de seguridad", assignee="orquester-external:claude-code")
t2 = k.create_task(conn, title="Aprobación del líder", assignee="human", parents=[t1])

k.claim_task(conn, t1, claimer="orquester")
k.complete_task(conn, t1, summary="Se encontraron 0 vulnerabilidades críticas.",
                result="Reporte de seguridad: todo limpio.\n```json\n{\"score\": 100}\n```")

# Parchear conexión del server
_conn_orig = server._conn
server._conn = lambda board: k.connect(db_path=db)

try:
    # 1. Reporte Markdown
    res_md = server._generar_reporte_corrida("test-board", formato="markdown")
    assert res_md.get("ok") is True, f"Error en reporte markdown: {res_md}"
    rep_md = res_md.get("reporte", "")
    assert "# Reporte de Auditoría: test-board" in rep_md
    assert "Auditoría de seguridad" in rep_md
    assert "Se encontraron 0 vulnerabilidades críticas" in rep_md
    print("1. Generación de reporte Markdown: OK")

    # 2. Reporte HTML
    res_html = server._generar_reporte_corrida("test-board", formato="html")
    assert res_html.get("ok") is True, f"Error en reporte html: {res_html}"
    rep_html = res_html.get("reporte", "")
    assert "<!DOCTYPE html>" in rep_html
    assert "Reporte Ejecutivo: test-board" in rep_html
    assert "Auditoría de seguridad" in rep_html
    print("2. Generación de reporte HTML: OK")
finally:
    server._conn = _conn_orig

print("\nOK: todos los tests de reportes pasaron exitosamente.")
