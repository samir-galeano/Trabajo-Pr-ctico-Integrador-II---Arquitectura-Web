#!/usr/bin/env bash
# C2 (F7-E7): ráfaga de 50 exportaciones con N export-worker; mide jobs
# completados por minuto. Se ejecuta en VM-2, desde la raíz del repo:
#
#   ./cna/perf/export-burst.sh 1      # 1 worker
#   ./cna/perf/export-burst.sh 3      # 3 workers
#
# Los jobs se crean contra export-svc directo (puerto 8003), no por el
# gateway, para que el rate limiting de /v2/ no recorte la ráfaga.
# Variables: JOBS (50), PAGE_ID (1), FORMAT (pdf), DELAY (0: segundos de
# demora artificial por job, para que el efecto del escalado se vea aunque
# BookStack exporte muy rápido).

source "$(dirname "$0")/../chaos/lib.sh"
require curl docker

WORKERS="${1:-1}"
JOBS="${JOBS:-50}"
PAGE_ID="${PAGE_ID:-1}"
FORMAT="${FORMAT:-pdf}"
DELAY="${DELAY:-0}"
EXPORT="http://$SERVICES_HOST:8003"

start_evidence "F7-E7-export-${WORKERS}w" "$@"

log "Escalando export-worker a $WORKERS (demora artificial ${DELAY}s)"
svc_scale export-worker "$WORKERS" "$DELAY" 2>&1 | tail -3 | tee -a "$LOG_FILE"
deadline=$(( $(date +%s) + 120 ))
until [ "$(svc_running export-worker)" -ge "$WORKERS" ]; do
  [ "$(date +%s)" -lt "$deadline" ] || die "no quedaron $WORKERS workers healthy"
  sleep 2
done
log "Workers en ejecución: $(svc_running export-worker)$(is_swarm && echo " (nodos: $(nodes_of export-worker))")"

ids=()
t0="$(now_ms)"
for i in $(seq 1 "$JOBS"); do
  id="$(api_curl -X POST "$EXPORT/exports" -H 'Content-Type: application/json' \
    -H "X-Request-ID: burst-${WORKERS}w-$i" -d "{\"page_id\": $PAGE_ID, \"format\": \"$FORMAT\"}" \
    | grep -o '"job_id":"[^"]*"' | cut -d'"' -f4)"
  [ -n "$id" ] || die "falló la creación del job $i"
  ids+=("$id")
done
log "$JOBS jobs encolados en $(( $(now_ms) - t0 ))ms"

while :; do
  done_n=0; failed_n=0
  for id in "${ids[@]}"; do
    case "$(api_curl "$EXPORT/exports/$id" | grep -o '"status":"[a-z]*"' | cut -d'"' -f4)" in
      done) done_n=$((done_n + 1)) ;;
      failed) failed_n=$((failed_n + 1)) ;;
    esac
  done
  elapsed=$(( $(now_ms) - t0 ))
  log "t+$((elapsed / 1000))s  done=$done_n  failed=$failed_n  pendientes=$((JOBS - done_n - failed_n))"
  [ $((done_n + failed_n)) -lt "$JOBS" ] || break
  [ "$elapsed" -lt 900000 ] || die "la ráfaga no terminó en 15 minutos"
  sleep 2
done

per_min="$(awk -v n="$done_n" -v ms="$elapsed" 'BEGIN { printf "%.1f", n / (ms / 60000) }')"
log "=== Resultado: $WORKERS worker(s) ==="
log "$done_n de $JOBS jobs completados en $((elapsed / 1000))s → $per_min jobs/min ($failed_n fallidos)"
echo "| $WORKERS | $JOBS | $done_n | $failed_n | $((elapsed / 1000)) | $per_min |" >> "$RESULTS_DIR/F7-E7-export-tabla.md"
log "Fila agregada a $RESULTS_DIR/F7-E7-export-tabla.md (Workers | Jobs | Done | Fallidos | Segundos | Jobs/min)"
log "Evidencia: $LOG_FILE"
