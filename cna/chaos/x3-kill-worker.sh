#!/usr/bin/env bash
# X3 (F7-E3): matar un export-worker a mitad de un job.
#
# Hipótesis: el job no queda en "running" para siempre. El lease del worker
# muerto vence (EXPORT_LEASE_TTL, 15 s), otro worker lo devuelve a la cola,
# lo toma y lo termina (attempts = 2).
#
# Se ejecuta en VM-2, desde la raíz del repo (necesita sudo para el kill).
#   PAGE_ID=<id-página-existente> ./cna/chaos/x3-kill-worker.sh
# Funciona con Compose y con Swarm. En Swarm, si el job lo toma un worker de
# otro nodo, el script indica en qué nodo correrlo.

source "$(dirname "$0")/lib.sh"
require curl docker sudo

PAGE_ID="${PAGE_ID:-1}"
DELAY="${DELAY:-30}"
EXPORT="http://$SERVICES_HOST:8003"

field() { grep -o "\"$1\":\"\\?[^\",}]*" | head -1 | sed 's/.*:"\{0,1\}//'; }

start_evidence F7-E3-X3 "$@"

log "Levantando 2 workers con una demora artificial de ${DELAY}s por job"
svc_scale export-worker 2 "$DELAY" 2>&1 | tee -a "$LOG_FILE"
deadline=$(( $(date +%s) + 90 ))
until [ "$(svc_running export-worker)" -ge 2 ]; do
  [ "$(date +%s)" -lt "$deadline" ] || die "los workers no quedaron healthy"
  sleep 2
done
log "Workers en ejecución: $(svc_running export-worker)$(is_swarm && echo " (nodos: $(nodes_of export-worker))")"

job_id="$(api_curl -X POST "$EXPORT/exports" -H 'Content-Type: application/json' \
  -H "X-Request-ID: x3-$(date +%s)" -d "{\"page_id\": $PAGE_ID, \"format\": \"pdf\"}" | field job_id)"
[ -n "$job_id" ] || die "no se pudo crear el job"
log "Job creado: $job_id"

job_running() { api_curl "$EXPORT/exports/$job_id" | grep -q '"status":"running"'; }
wait_until 30 job_running || die "el job no pasó a running"
job="$(api_curl "$EXPORT/exports/$job_id")"
# worker = "<hostname>-<sufijo>" y el hostname de un contenedor es su ID corto
worker="$(echo "$job" | field worker)"
worker="${worker%-*}"
docker inspect "$worker" >/dev/null 2>&1 \
  || die "el worker $worker que tiene el job no está en este nodo$(is_swarm && echo "; nodos de export-worker: $(nodes_of export-worker)")"
log "Estado: $job"
log "Worker que tiene el job: $(docker inspect -f '{{.Name}}' "$worker")"

sleep 3
t0="$(now_ms)"
crash_container "$worker"
log ">>> kill -9 al worker $(docker inspect -f '{{.Name}}' "$worker") con el job en running"

last=""
t_requeue=""
while :; do
  elapsed=$(( $(now_ms) - t0 ))
  [ "$elapsed" -lt 180000 ] || die "el job no terminó en 180 s"
  job="$(api_curl "$EXPORT/exports/$job_id")"
  status="$(echo "$job" | field status)"
  attempts="$(echo "$job" | field attempts)"
  now="$status attempts=$attempts"
  if [ "$now" != "$last" ]; then
    log "t+${elapsed}ms  $now"
    last="$now"
  fi
  if [ -z "$t_requeue" ] && [ "${attempts:-1}" -ge 2 ]; then t_requeue="$elapsed"; fi
  case "$status" in
    done|failed) break ;;
  esac
  sleep 1
done

log "=== Resultado X3 ==="
log "Estado final: $status, intentos: $attempts"
log "Job retomado por otro worker: t+${t_requeue:-?}ms (lease de ${EXPORT_LEASE_TTL:-15}s + revisión cada 5s)"
log "Job terminado: t+${elapsed}ms (incluye ${DELAY}s de demora artificial)"
log "Workers en ejecución: $(svc_running export-worker)"

log "Restaurando 1 worker sin demora"
svc_scale export-worker 1 0 2>&1 | tail -1 | tee -a "$LOG_FILE"
log "Evidencia: $LOG_FILE"
