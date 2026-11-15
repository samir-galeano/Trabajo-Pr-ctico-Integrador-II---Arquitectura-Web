#!/usr/bin/env bash
# X4 (F7-E4): apagar VM-2 completa.
#
# Hipótesis: BookStack sigue operativo (CA-5) mientras VM-2 está apagada; el
# gateway responde 503 JSON en /v2/*; al encender VM-2, Docker levanta los
# contenedores (restart: unless-stopped) y el sistema converge sin
# intervención manual.
#
# Se ejecuta en VM-1 (o en el anfitrión), NO en VM-2:
#   ./cna/chaos/x4-vm2-down.sh
# y, cuando el script lo indique, apagar VM-2 desde VMware (Power Off) y
# volver a encenderla.
#
# Opcional: VM2_SSH=usuario@ip-vm2 para listar el estado de los contenedores
# al final.

source "$(dirname "$0")/lib.sh"
require curl

SEARCH="$GATEWAY_URL/v2/search?q=nginx"
STABLE_SECONDS="${STABLE_SECONDS:-30}"

start_evidence F7-E4-X4 "$@"

log "Estado inicial: monolito $(http_code "$MONOLITH_URL/")  /v2/search $(http_code "$SEARCH")"
log ">>> Apagar VM-2 ahora (VMware: Power Off). Luego encenderla. El script mide todo."

t0="$(now_ms)"
monolith_total=0
monolith_fail=0
t_down=""
t_up=""
stable_since=""

while :; do
  elapsed=$(( $(now_ms) - t0 ))
  mono="$(http_code "$MONOLITH_URL/" 5)"
  monolith_total=$((monolith_total + 1))
  [ "$mono" = "200" ] || monolith_fail=$((monolith_fail + 1))
  code="$(http_code "$SEARCH" 5)"
  log "t+$((elapsed / 1000))s  monolito=$mono  /v2/search=$code"

  if [ "$code" != "200" ] && [ -z "$t_down" ]; then
    t_down="$elapsed"
    log "VM-2 fuera de servicio. Respuesta del gateway: $(http_body "$SEARCH" 5)"
  fi

  if [ -n "$t_down" ] && [ "$code" = "200" ]; then
    [ -n "$t_up" ] || { t_up="$elapsed"; log "/v2/search vuelve a responder 200"; }
    stable_since="${stable_since:-$elapsed}"
    if [ $(( elapsed - stable_since )) -ge $(( STABLE_SECONDS * 1000 )) ]; then
      break
    fi
  else
    stable_since=""
  fi
  sleep 2
done

log "=== Resultado X4 ==="
log "VM-2 sin servicio desde t+$((t_down / 1000))s hasta t+$((t_up / 1000))s ($(( (t_up - t_down) / 1000 ))s, incluye el arranque de la VM)"
log "Monolito: $monolith_fail fallos de $monolith_total peticiones (esperado 0)"
log "Estable durante ${STABLE_SECONDS}s sin intervención manual"
if [ -n "${VM2_SSH:-}" ]; then
  ssh "$VM2_SSH" 'docker ps --format "{{.Names}}\t{{.Status}}"' | tee -a "$LOG_FILE"
fi
log "Evidencia: $LOG_FILE"
