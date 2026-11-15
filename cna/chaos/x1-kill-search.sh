#!/usr/bin/env bash
# X1 (F7-E1): caída de search-svc.
#
# Hipótesis: si el proceso de search-svc muere, el monolito sigue
# respondiendo 200, el gateway devuelve 503 JSON en /v2/search y Docker
# vuelve a levantar el contenedor solo (restart: unless-stopped).
#
# Se ejecuta en el nodo donde corre search-svc (VM-2), desde la raíz del repo
# (necesita sudo para el kill). Ese nodo debe resolver bookstack.local y
# api.bookstack.local a VM-1 (/etc/hosts). Funciona con Compose y con Swarm.
#
#   ./cna/chaos/x1-kill-search.sh
#
# Nota: se mata el PROCESO (kill -9 al PID), no "docker kill". Con Docker
# Compose, "docker kill" cuenta como parada manual y no dispara el reinicio.
# En Swarm, el orquestador crea una tarea (contenedor) nueva.

source "$(dirname "$0")/lib.sh"
require curl docker sudo

SERVICE="${SERVICE:-search-svc}"
SEARCH="$GATEWAY_URL/v2/search?q=nginx"
TIMEOUT="${TIMEOUT:-180}"

start_evidence F7-E1-X1 "$@"

victim="$(require_local "$SERVICE")"
restarts_before="$(docker inspect -f '{{.RestartCount}}' "$victim")"
log "Orquestador: $(is_swarm && echo "Swarm (stack $STACK)" || echo "Docker Compose")"
log "Estado inicial: monolito $(http_code "$MONOLITH_URL/")  gateway /v2/search $(http_code "$SEARCH")  contenedor=${victim:0:12}"
[ "$(http_code "$SEARCH")" = "200" ] || die "/v2/search no responde 200 antes del experimento"

t0="$(now_ms)"
crash_container "$victim"
log ">>> kill -9 al proceso de $SERVICE (contenedor ${victim:0:12})"

monolith_fail=0
t_down=""
body_503=""
t_restart=""

while :; do
  elapsed=$(( $(now_ms) - t0 ))
  [ "$elapsed" -lt $(( TIMEOUT * 1000 )) ] || die "no se recuperó en ${TIMEOUT}s"

  mono="$(http_code "$MONOLITH_URL/" 3)"
  [ "$mono" = "200" ] || monolith_fail=$((monolith_fail + 1))

  code="$(http_code "$SEARCH" 3)"
  current="$(container_of "$SERVICE")"
  state="$( [ -n "$current" ] && docker inspect -f '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$current" 2>/dev/null || echo 'sin contenedor')"
  log "t+${elapsed}ms  monolito=$mono  /v2/search=$code  contenedor=$state"

  if [ "$code" != "200" ] && [ -z "$t_down" ]; then
    t_down="$elapsed"
    body_503="$(http_body "$SEARCH" 3)"
    log "Respuesta del gateway durante la caída: $body_503"
  fi

  # Compose reinicia el mismo contenedor; Swarm crea uno nuevo (quizá en otro nodo)
  if [ -z "$t_restart" ]; then
    if [ -n "$current" ] && [ "$current" != "$victim" ]; then
      t_restart="$elapsed"; log "Swarm creó una tarea nueva (${current:0:12}) en t+${elapsed}ms"
    elif [ "$(docker inspect -f '{{.RestartCount}}' "$victim" 2>/dev/null || echo 0)" -gt "$restarts_before" ]; then
      t_restart="$elapsed"; log "Docker reinició el contenedor en t+${elapsed}ms"
    fi
  fi

  if [ -n "$t_down" ] && [ "$code" = "200" ]; then
    log "=== Resultado X1 ==="
    log "Primera respuesta no-200 del gateway: t+${t_down}ms"
    log "Recreado por el orquestador:          t+${t_restart:-?}ms (o en otro nodo: $(is_swarm && nodes_of "$SERVICE"))"
    log "MTTR (kill → /v2/search 200):        ${elapsed}ms"
    log "Fallos del monolito durante la caída: $monolith_fail (esperado 0)"
    log "Evidencia: $LOG_FILE"
    exit 0
  fi
  sleep 0.5
done
