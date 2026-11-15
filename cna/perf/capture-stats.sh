#!/usr/bin/env bash
# C2 (F7-E8): registra el consumo de cada contenedor y del nodo durante las
# pruebas de carga. Ejecutar en cada nodo con Docker mientras corre k6 o la
# ráfaga de exportaciones:
#
#   ./cna/perf/capture-stats.sh 300      # 300 segundos, una muestra cada 5 s
#
# Deja un CSV (docker stats) y un log con carga, memoria y top del nodo.
# Además, sacar una captura de htop en el pico de la prueba.

source "$(dirname "$0")/../chaos/lib.sh"
require docker

SECONDS_TOTAL="${1:-300}"
INTERVAL="${INTERVAL:-5}"

start_evidence "F7-E8-stats-$(hostname)" "$@"
CSV="${LOG_FILE%.log}.csv"
echo "timestamp,container,cpu_percent,mem_usage,mem_percent,net_io,block_io" > "$CSV"

end=$(( $(date +%s) + SECONDS_TOTAL ))
while [ "$(date +%s)" -lt "$end" ]; do
  now="$(ts)"
  docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}},{{.NetIO}},{{.BlockIO}}' \
    | sed "s/^/$now,/" >> "$CSV"
  {
    echo "[$now] load: $(cut -d' ' -f1-3 /proc/loadavg)  mem: $(free -m | awk '/^Mem:/ {print $3 "/" $2 " MiB"}')"
    top -b -n1 -o %CPU | sed -n '7,12p'
  } >> "$LOG_FILE"
  sleep "$INTERVAL"
done

log "Pico de CPU por contenedor:"
awk -F, 'NR > 1 { gsub("%", "", $3); if ($3 + 0 > max[$2]) max[$2] = $3 + 0 }
  END { for (c in max) printf "  %-28s %6.1f %%\n", c, max[c] }' "$CSV" | sort -k2 -nr | tee -a "$LOG_FILE"
log "CSV: $CSV"
log "Evidencia: $LOG_FILE"
