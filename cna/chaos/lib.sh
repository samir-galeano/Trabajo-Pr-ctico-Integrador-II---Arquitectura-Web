#!/usr/bin/env bash
# Funciones comunes de los scripts de caos, rendimiento y seguridad (Fase 7).
#
# Cada script deja un log con marcas de tiempo en $RESULTS_DIR. La primera
# línea imita el prompt (usuario@host:directorio$ comando), como piden las
# evidencias del TP.
#
# Variables configurables (con sus valores por defecto):
#   GATEWAY_URL   https://api.bookstack.local      gateway Nginx de VM-1
#   MONOLITH_URL  https://bookstack.local          BookStack
#   SERVICES_HOST 127.0.0.1                        host de los servicios (VM-2)
#   RESULTS_DIR   cna/docs/evidencias/fase7

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESULTS_DIR="${RESULTS_DIR:-$REPO_ROOT/cna/docs/evidencias/fase7}"
GATEWAY_URL="${GATEWAY_URL:-https://api.bookstack.local}"
MONOLITH_URL="${MONOLITH_URL:-https://bookstack.local}"
SERVICES_HOST="${SERVICES_HOST:-127.0.0.1}"

ts() { date '+%Y-%m-%d %H:%M:%S.%3N'; }
now_ms() { date +%s%3N; }

# Inicia el archivo de evidencia: start_evidence <nombre> "$@"
start_evidence() {
  local name="$1"
  shift
  mkdir -p "$RESULTS_DIR"
  LOG_FILE="$RESULTS_DIR/${name}-$(date +%Y%m%d-%H%M%S).log"
  export LOG_FILE
  {
    echo "$(whoami)@$(hostname):$(pwd)\$ $0 $*"
    echo "# inicio: $(ts)"
  } | tee "$LOG_FILE"
}

log() { printf '[%s] %s\n' "$(ts)" "$*" | tee -a "${LOG_FILE:-/dev/null}"; }

die() {
  log "ERROR: $*"
  exit 1
}

# Token Bearer de la API (/v2/* y servicios). Se toma de search-svc/.env.
API_TOKEN="${API_TOKEN:-$(grep -E '^API_AUTH_TOKEN=' "$REPO_ROOT/cna/services/search-svc/.env" 2>/dev/null | cut -d= -f2- || true)}"
AUTH=()
[ -z "$API_TOKEN" ] || AUTH=(-H "Authorization: Bearer $API_TOKEN")

# ¿La URL es de nuestra API? (al monolito no se le manda el token)
is_api() {
  case "$1" in
    "$GATEWAY_URL"*|"http://$SERVICES_HOST"*) return 0 ;;
  esac
  return 1
}

# Código HTTP de una URL (000 si no responde)
http_code() {
  local auth=()
  ! is_api "$1" || auth=("${AUTH[@]}")
  curl -sk "${auth[@]}" -o /dev/null -w '%{http_code}' --max-time "${2:-5}" "$1" || true
}

# Cuerpo de una URL (vacío si no responde)
http_body() {
  local auth=()
  ! is_api "$1" || auth=("${AUTH[@]}")
  curl -sk "${auth[@]}" --max-time "${2:-5}" "$1" || true
}

# curl contra la API con el token: api_curl [opciones de curl] <url>
api_curl() {
  curl -s "${AUTH[@]}" "$@"
}

require() {
  for cmd in "$@"; do
    command -v "$cmd" >/dev/null 2>&1 || die "falta el comando '$cmd'"
  done
}

# ------------------------------------------------------------------
# Orquestador: los scripts funcionan igual con Docker Compose y con el
# stack de Swarm (cna/deploy/swarm). Los servicios se nombran sin prefijo.
# ------------------------------------------------------------------
STACK="${STACK:-cna}"
COMPOSE=(docker compose -f "$REPO_ROOT/compose.yaml")

is_swarm() { docker service inspect "${STACK}_search-svc" >/dev/null 2>&1; }

# IDs de los contenedores en ejecución de un servicio, en ESTE nodo
containers_of() {
  if is_swarm; then
    docker ps -q --filter "label=com.docker.swarm.service.name=${STACK}_$1"
  else
    docker ps -q --filter "label=com.docker.compose.service=$1"
  fi
}
container_of() { containers_of "$1" | head -1; }

# Nodos donde corre un servicio (Swarm)
nodes_of() {
  docker service ps "${STACK}_$1" --filter desired-state=running --format '{{.Node}}' | sort -u | paste -sd' ' -
}

# Exige que el servicio tenga un contenedor en este nodo (para kill / tc)
require_local() {
  local id
  id="$(container_of "$1")"
  if [ -z "$id" ]; then
    if is_swarm; then
      die "$1 no corre en este nodo; ejecutar el script en: $(nodes_of "$1")"
    fi
    die "$1 no está corriendo"
  fi
  echo "$id"
}

svc_stop() {
  if is_swarm; then
    docker service scale --detach=false "${STACK}_$1=0" >/dev/null
  else
    "${COMPOSE[@]}" stop "$1"
  fi
}

svc_start() {
  if is_swarm; then
    docker service scale --detach=false "${STACK}_$1=${2:-1}" >/dev/null
  else
    "${COMPOSE[@]}" start "$1"
  fi
}

# svc_scale <servicio> <réplicas> [demora artificial de export-worker]
svc_scale() {
  local svc="$1" replicas="$2" delay="${3:-0}"
  if is_swarm; then
    docker service update --detach=false --quiet --replicas "$replicas" \
      --env-add "EXPORT_SIMULATED_DELAY=$delay" "${STACK}_$svc" >/dev/null
  else
    EXPORT_SIMULATED_DELAY="$delay" "${COMPOSE[@]}" up -d --scale "$svc=$replicas" "$svc"
  fi
}

# Réplicas en ejecución (y healthy) de un servicio en todo el clúster
svc_running() {
  if is_swarm; then
    docker service ls --filter "name=${STACK}_$1" --format '{{.Replicas}}' | cut -d/ -f1
  else
    docker ps -q --filter "label=com.docker.compose.service=$1" --filter health=healthy | wc -l
  fi
}

# PID del proceso principal de un contenedor (en el namespace del host)
container_pid() {
  docker inspect -f '{{.State.Pid}}' "$1"
}

# Mata el proceso del contenedor con SIGKILL, como una caída real.
# (Con Docker Compose, "docker kill" cuenta como parada manual y la
#  política restart: unless-stopped NO lo vuelve a levantar. En Swarm el
#  orquestador crea una tarea nueva en cualquiera de los dos casos.)
crash_container() {
  local pid
  pid="$(container_pid "$1")"
  [ "$pid" -gt 0 ] || die "el contenedor $1 no está corriendo"
  sudo kill -9 "$pid"
}

# Espera hasta que un comando tenga éxito o venza el plazo (segundos)
wait_until() {
  local timeout="$1"
  shift
  local deadline=$(( $(date +%s) + timeout ))
  until "$@"; do
    [ "$(date +%s)" -lt "$deadline" ] || return 1
    sleep 0.5
  done
}
