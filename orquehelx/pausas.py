"""Pausas del agente principal por cuota agotada, persistidas en SQLite (D16) con salidas atomicas (D19).

    pausado --reanudar--> reanudado   (mismo proveedor, al reinicio)
            --reenviar--> reenviado   (otra ruta, elegida por el usuario)
            --cancelar--> cancelado

Toda salida es ``UPDATE ... WHERE estado = 'pausado'``: si dos pestanias (o el reinicio y un clic) resuelven
a la vez, SQLite serializa las escrituras y solo una cambia la fila; la otra recibe False.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path

ACCIONES = {"reanudar": "reanudado", "reenviar": "reenviado", "cancelar": "cancelado"}
_COLUMNAS = "id, sesion, proveedor, modelo, reinicio, estado, creado"

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS pausas (
    id INTEGER PRIMARY KEY,
    sesion TEXT NOT NULL,
    proveedor TEXT NOT NULL,
    modelo TEXT,
    reinicio TEXT,
    estado TEXT NOT NULL DEFAULT 'pausado',
    destino TEXT,
    creado REAL NOT NULL,
    resuelto REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS una_pendiente_por_sesion ON pausas (sesion) WHERE estado = 'pausado';
"""


def abrir(ruta: Path) -> sqlite3.Connection:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    # timeout: una escritura concurrente espera su turno en vez de fallar con "database is locked".
    con = sqlite3.connect(ruta, timeout=5)
    con.executescript(_ESQUEMA)
    return con


def _dict(fila) -> dict:
    return dict(zip(("id", "sesion", "proveedor", "modelo", "reinicio", "estado", "creado"), fila))


def crear(con: sqlite3.Connection, sesion: str, proveedor: str, modelo: str | None,
          reinicio: datetime | None) -> dict:
    """Pausa la sesion; si ya tenia una pendiente, devuelve esa (el indice unico lo garantiza)."""
    try:
        with con:
            con.execute("INSERT INTO pausas (sesion, proveedor, modelo, reinicio, creado) VALUES (?, ?, ?, ?, ?)",
                        (sesion, proveedor, modelo, reinicio.isoformat() if reinicio else None, time.time()))
    except sqlite3.IntegrityError:
        pass
    fila = con.execute(f"SELECT {_COLUMNAS} FROM pausas WHERE sesion = ? AND estado = 'pausado'",
                       (sesion,)).fetchone()
    return _dict(fila)


def pendientes(con: sqlite3.Connection) -> list[dict]:
    return [_dict(f) for f in con.execute(f"SELECT {_COLUMNAS} FROM pausas WHERE estado = 'pausado' ORDER BY id")]


def resolver(con: sqlite3.Connection, pausa: int, accion: str, destino: str | None = None) -> bool:
    """True si esta llamada saco la pausa de 'pausado'; False si otra ya la habia resuelto."""
    if accion not in ACCIONES:
        raise ValueError(f"acción desconocida {accion!r}; usa una de: {', '.join(ACCIONES)}")
    with con:
        cursor = con.execute(
            "UPDATE pausas SET estado = ?, destino = ?, resuelto = ? WHERE id = ? AND estado = 'pausado'",
            (ACCIONES[accion], destino, time.time(), pausa))
    return cursor.rowcount == 1
