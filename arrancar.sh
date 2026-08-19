#!/bin/sh
# Levanta ORQUESTER. Sin argumentos arranca lo minimo; con --multi agrega el
# plano de control (Postgres + API de NestJS).
#
#     sh arrancar.sh              # motor + Studio
#     sh arrancar.sh --multi      # + Postgres + API multiusuario
#     sh arrancar.sh --parar      # baja todo lo que este script levanto
#
# Funciona en Git Bash y en Linux. Las rutas se derivan, no se escriben.
set -e
RAIZ=$(cd "$(dirname "$0")" && pwd)
cd "$RAIZ"
LOGS="$RAIZ/.logs"
mkdir -p "$LOGS"

# --- Token estable, generado una vez ---------------------------------------
# En un archivo y no en el script: un secreto hardcodeado en el repo termina
# en GitHub. El archivo esta en .gitignore.
#
# Vive en `.env` con forma KEY=VALOR y no en `.orquester-token` pelado para que
# lo pueda leer cualquier otra herramienta que arranque esto: PortMaster carga
# `env_file: [.env]` y de ahi saca el token para la URL del Studio. Un token
# pelado obliga a cada una a saber que ese archivo es un token y no un .env.
ARCHIVO_ENV="$RAIZ/.env"
VIEJO="$RAIZ/.orquester-token"
if [ ! -f "$ARCHIVO_ENV" ]; then
  if [ -f "$VIEJO" ]; then
    # Migracion del formato viejo. Se conserva el token: regenerarlo invalidaria
    # la URL del Studio que ya tenias abierta o guardada.
    echo "ORQUESTER_TOKEN=$(cat "$VIEJO")" > "$ARCHIVO_ENV"
    echo "token migrado de .orquester-token a .env"
  else
    python -c "import secrets,sys; sys.stdout.write('ORQUESTER_TOKEN=' + secrets.token_urlsafe(24))" > "$ARCHIVO_ENV"
    echo "token nuevo generado en .env"
  fi
fi
ORQUESTER_TOKEN=$(sed -n 's/^ORQUESTER_TOKEN=//p' "$ARCHIVO_ENV")
export ORQUESTER_TOKEN

parar() {
  for f in "$LOGS"/motor.pid "$LOGS"/api.pid; do
    [ -f "$f" ] || continue
    pid=$(cat "$f")
    kill "$pid" 2>/dev/null && echo "detenido $(basename "$f" .pid) (pid $pid)" || true
    rm -f "$f"
  done
  # Postgres se deja corriendo a proposito: es un contenedor con datos y
  # bajarlo por reflejo sorprende a quien lo tenia para otra cosa.
  echo "Postgres sigue arriba. Para bajarlo: docker compose down"
}

[ "$1" = "--parar" ] && { parar; exit 0; }

# --- Binarios de los agentes ------------------------------------------------
# `agy` se instala fuera del PATH del sistema en Windows; se agrega si esta.
[ -d "$LOCALAPPDATA/agy/bin" ] && PATH="$LOCALAPPDATA/agy/bin:$PATH"
export PATH

# Hermes solo hace falta para nodos `runtime: hermes`.
if [ -z "$ORQUESTER_HERMES_BIN" ]; then
  if command -v hermes > /dev/null 2>&1; then
    ORQUESTER_HERMES_BIN=$(command -v hermes)
  elif [ -x "$LOCALAPPDATA/hermes/hermes-agent/venv/Scripts/hermes.exe" ]; then
    ORQUESTER_HERMES_BIN="$LOCALAPPDATA/hermes/hermes-agent/venv/Scripts/hermes.exe"
  fi
fi
export ORQUESTER_HERMES_BIN

echo "ejecutores disponibles:"
for cli in claude opencode agy hermes; do
  if command -v "$cli" > /dev/null 2>&1; then
    echo "  $cli: si"
  elif [ "$cli" = "hermes" ] && [ -n "$ORQUESTER_HERMES_BIN" ]; then
    echo "  hermes: si (fuera del PATH)"
  else
    # No es fatal: un flujo que no use ese runtime anda igual, y el compilador
    # avisa antes de correr si lo pedis (preflight de capacidades).
    echo "  $cli: NO — los nodos de ese runtime van a fallar como 'capability'"
  fi
done

# --- Motor + Studio ---------------------------------------------------------
if [ -f "$LOGS/motor.pid" ] && kill -0 "$(cat "$LOGS/motor.pid")" 2>/dev/null; then
  echo "motor: ya estaba corriendo (pid $(cat "$LOGS/motor.pid"))"
else
  uv run --python 3.11 --with jsonschema python ui/server.py 8765 \
    > "$LOGS/motor.log" 2>&1 &
  echo $! > "$LOGS/motor.pid"
  sleep 5
  kill -0 "$(cat "$LOGS/motor.pid")" 2>/dev/null \
    || { echo "el motor no arranco. Log:"; tail -15 "$LOGS/motor.log"; exit 1; }
fi

# --- Plano de control, solo con --multi -------------------------------------
if [ "$1" = "--multi" ]; then
  docker compose up -d db > /dev/null 2>&1 || {
    echo "no se pudo levantar Postgres (¿Docker esta corriendo?)"; exit 1; }
  # Esperar el healthcheck: arrancar el API contra una base que todavia no
  # acepta conexiones falla en el primer query, no al arrancar, y confunde.
  i=0
  while [ $i -lt 30 ]; do
    docker compose ps --format '{{.Status}}' 2>/dev/null | grep -q healthy && break
    i=$((i+1)); sleep 1
  done
  [ -f apps/api/dist/main.js ] || (cd apps/api && npm install --silent && npm run build)

  if [ -f "$LOGS/api.pid" ] && kill -0 "$(cat "$LOGS/api.pid")" 2>/dev/null; then
    echo "api: ya estaba corriendo (pid $(cat "$LOGS/api.pid"))"
  else
    (cd apps/api && PORT=3000 \
      DATABASE_URL="postgresql://orquester:orquester-local@localhost:5433/orquester?schema=public" \
      ORQUESTER_ENGINE_URL="http://127.0.0.1:8765" \
      node dist/main.js > "$LOGS/api.log" 2>&1 & echo $! > "$LOGS/api.pid")
    sleep 4
  fi
  echo "API multiusuario: http://127.0.0.1:3000/api"
fi

echo
echo "  Studio:  http://127.0.0.1:8765/?token=$ORQUESTER_TOKEN"
echo
echo "  logs en .logs/   ·   para bajar todo:  sh arrancar.sh --parar"
