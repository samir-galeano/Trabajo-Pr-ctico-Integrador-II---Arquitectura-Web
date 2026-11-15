#!/usr/bin/env bash
# C2 (F7-E6): compara la búsqueda del monolito con /v2/search de search-svc
# con c=10 (concurrencia de la Fase 4) y c=50, y arma la tabla contra la
# línea base del TP I (~50 req/s, 0 errores).
#
# Corre k6 en un contenedor (no hace falta instalarlo). Conviene ejecutarlo
# desde el anfitrión o una máquina distinta de VM-1/VM-2 para no competir por
# CPU con lo que se mide. Mientras corre, en VM-2:
#   ./cna/perf/capture-stats.sh 300        (F7-E8)
#
#   VM1_IP=192.168.100.23 ./cna/perf/run-k6.sh
#
# Si la búsqueda del monolito exige sesión:
#   BOOKSTACK_EMAIL=... BOOKSTACK_PASSWORD=... ./cna/perf/run-k6.sh
#
# Nota: /v2/ tiene rate limiting (limit_req) en el gateway; con c=50 es
# esperable ver 429. Se cuentan aparte (columna 429) y son un hallazgo
# válido: el gateway protege al servicio.

source "$(dirname "$0")/../chaos/lib.sh"
require docker

VM1_IP="${VM1_IP:-192.168.100.23}"
DURATION="${DURATION:-60s}"
K6_IMAGE="grafana/k6:1.3.0"
OUT_DIR="$RESULTS_DIR/F7-E6-k6-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT_DIR"

start_evidence F7-E6-k6 "$@"

TABLE="$OUT_DIR/comparativa.md"
{
  echo "# C2 - Búsqueda: monolito vs. search-svc (por el gateway, HTTPS)"
  echo
  echo "Duración por corrida: $DURATION. Línea base Fase 4: ~50 req/s, 0 errores (ab -n 1000 -c 10)."
  echo
  echo "| Destino | VUs | Peticiones | req/s | Errores | 429 | Prom. (ms) | p95 (ms) | p99 (ms) |"
  echo "|---|---|---|---|---|---|---|---|---|"
  echo "| Línea base Fase 4 (monolito, ab) | 10 | 1000 | ~50 | 0 % | - | 23,7 (TLS + respuesta) | - | - |"
} > "$TABLE"

for vus in 10 50; do
  for target in monolith service; do
    name="$target-c$vus"
    log ">>> k6: $name"
    MSYS_NO_PATHCONV=1 docker run --rm -i --user "$(id -u):$(id -g)" \
      --add-host "bookstack.local:$VM1_IP" \
      --add-host "api.bookstack.local:$VM1_IP" \
      -v "$REPO_ROOT/cna/perf/k6:/scripts:ro" \
      -v "$OUT_DIR:/results" \
      -e TARGET="$target" -e VUS="$vus" -e DURATION="$DURATION" \
      -e BOOKSTACK_EMAIL="${BOOKSTACK_EMAIL:-}" -e BOOKSTACK_PASSWORD="${BOOKSTACK_PASSWORD:-}" \
      -e API_TOKEN="$API_TOKEN" -e OUT="/results/$name.json" \
      "$K6_IMAGE" run --quiet /scripts/search.js 2>&1 | tee -a "$LOG_FILE"
    cat "$OUT_DIR/$name.row.md" >> "$TABLE"
    sleep 10
  done
done

log "Tabla comparativa:"
tee -a "$LOG_FILE" < "$TABLE"
log "Resultados: $OUT_DIR"
