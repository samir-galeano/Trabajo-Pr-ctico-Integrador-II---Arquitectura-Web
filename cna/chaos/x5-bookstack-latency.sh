#!/usr/bin/env bash
# X5 (F7-E5): 2 s de latencia artificial hacia la API de BookStack.
#
# Hipótesis: search-svc aplica timeout (3 s de conexión), reintentos con
# backoff exponencial y, tras 5 fallos seguidos, abre el circuito: deja de
# llamar a BookStack (responde 503 al instante) y /search sigue respondiendo
# rápido desde el índice local (no se saturan los hilos). Al quitar la
# latencia, el circuito pasa a semiabierto y se cierra con una llamada OK.
#
# Se ejecuta en el nodo donde corre search-svc (VM-2), desde la raíz del
# repo (sudo para tc):
#   ./cna/chaos/x5-bookstack-latency.sh
#
# La latencia se aplica solo a los paquetes de VM-2 hacia VM-1:443 (la API
# de BookStack); las respuestas al gateway y NATS no se ven afectadas.

source "$(dirname "$0")/lib.sh"
require curl tc ip sudo

DELAY="${DELAY:-2000ms}"
DURATION="${DURATION:-90}"
SEARCH="http://$SERVICES_HOST:8000"
BOOKSTACK_IP="${BOOKSTACK_IP:-$(grep -E '^BOOKSTACK_HOST_IP=' "$REPO_ROOT/.env" | cut -d= -f2)}"
IFACE="$(ip route get "$BOOKSTACK_IP" | grep -o 'dev [^ ]*' | cut -d' ' -f2)"

metric() {
  curl -s "$SEARCH/metrics" | grep -E "^$1" | awk '{print $NF}' | paste -sd' ' -
}
circuit() {
  case "$(metric 'bookstack_circuit_state ')" in
    0.0) echo cerrado ;; 1.0) echo semiabierto ;; 2.0) echo ABIERTO ;; *) echo "?" ;;
  esac
}
search_ms() {
  api_curl -o /dev/null -w '%{http_code} %{time_total}' --max-time 5 "$SEARCH/search?q=nginx" \
    | awk '{printf "%s %.0fms", $1, $2 * 1000}'
}

cleanup() {
  sudo tc qdisc del dev "$IFACE" root 2>/dev/null || true
}
trap cleanup EXIT

start_evidence F7-E5-X5 "$@"
require_local search-svc >/dev/null
log "BookStack: $BOOKSTACK_IP:443 por $IFACE"
log "Antes: circuito=$(circuit)  /search=$(search_ms)  $(metric 'bookstack_client_requests_total')"

# Latencia solo hacia BookStack:443 (prio + filtro u32)
sudo tc qdisc add dev "$IFACE" root handle 1: prio
sudo tc qdisc add dev "$IFACE" parent 1:3 handle 30: netem delay "$DELAY"
sudo tc filter add dev "$IFACE" protocol ip parent 1:0 prio 3 u32 \
  match ip dst "$BOOKSTACK_IP"/32 match ip dport 443 0xffff flowid 1:3
log ">>> netem: +$DELAY hacia $BOOKSTACK_IP:443"
sudo tc -s qdisc show dev "$IFACE" | tee -a "$LOG_FILE"

t0="$(now_ms)"
end=$(( $(date +%s) + DURATION ))
while [ "$(date +%s)" -lt "$end" ]; do
  start="$(now_ms)"
  sync="$(api_curl -o /dev/null -w '%{http_code}' --max-time 60 -X POST "$SEARCH/sync" || echo 000)"
  took=$(( $(now_ms) - start ))
  log "t+$(( ($(now_ms) - t0) / 1000 ))s  POST /sync=$sync en ${took}ms  circuito=$(circuit)  /search=$(search_ms)  reintentos=$(metric 'bookstack_client_retries_total')"
  sleep 2
done

log "Contadores: $(curl -s "$SEARCH/metrics" | grep -E '^bookstack_client_requests_total' | tr '\n' ' ')"
cleanup
log ">>> netem quitado"

t1="$(now_ms)"
until [ "$(circuit)" = "cerrado" ]; do
  [ $(( $(now_ms) - t1 )) -lt 120000 ] || die "el circuito no se cerró en 120 s"
  code="$(api_curl -o /dev/null -w '%{http_code}' --max-time 60 -X POST "$SEARCH/sync" || echo 000)"
  log "Recuperación: POST /sync=$code  circuito=$(circuit)"
  sleep 3
done
log "=== Resultado X5 ==="
log "Circuito cerrado otra vez $(( ($(now_ms) - t1) / 1000 ))s después de quitar la latencia"
log "Durante la latencia /search siguió respondiendo desde el índice local (ver columna /search)"
log "Evidencia: $LOG_FILE  (en Grafana: panel 'search-svc → API de BookStack')"
