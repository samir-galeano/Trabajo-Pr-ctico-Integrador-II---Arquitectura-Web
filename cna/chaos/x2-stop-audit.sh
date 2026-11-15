#!/usr/bin/env bash
# X2 (F7-E2): audit-svc detenido 2 minutos mientras se editan 20 páginas.
#
# Hipótesis: los eventos publicados durante la caída quedan en el stream
# durable de JetStream; al volver, audit-svc los procesa todos (cero pérdida,
# CA-6).
#
# Se ejecuta en VM-2, desde la raíz del repo.
#
# Modo automático (recomendado): crea 20 páginas de prueba en un libro y las
# edita por la API con un token de un usuario EDITOR (no el de servicio, que
# es de solo lectura):
#   EDITOR_TOKEN=<id>:<secreto> BOOK_ID=<id-libro> ./cna/chaos/x2-stop-audit.sh
#
# Modo manual: sin EDITOR_TOKEN, el script pide editar 20 páginas a mano en
# BookStack durante los 2 minutos.

source "$(dirname "$0")/lib.sh"
require curl docker

PAGES="${PAGES:-20}"
DOWN_SECONDS="${DOWN_SECONDS:-120}"
AUDIT="http://$SERVICES_HOST:8002"
PROMETHEUS="http://$SERVICES_HOST:9090"
BOOKSTACK_HOST_IP="$(grep -E '^BOOKSTACK_HOST_IP=' "$REPO_ROOT/.env" | cut -d= -f2 || true)"

api() {
  # api <método> <ruta> [json]
  curl -sk --max-time 15 -X "$1" \
    ${BOOKSTACK_HOST_IP:+--resolve "bookstack.local:443:$BOOKSTACK_HOST_IP"} \
    -H "Authorization: Token $EDITOR_TOKEN" -H 'Content-Type: application/json' \
    ${3:+-d "$3"} "https://bookstack.local$2"
}

pending() {
  curl -s --get "$PROMETHEUS/api/v1/query" \
    --data-urlencode 'query=jetstream_consumer_num_pending{consumer_name="AUDIT_CONSUMER"}' \
    | grep -o '"value":\[[^]]*\]' | grep -o '"[0-9]*"\]' | tr -d '"]' || echo "?"
}

audit_count_since() {
  api_curl --get "$AUDIT/audit" --data-urlencode "start=$1" --data-urlencode "limit=1000" \
    | grep -o '"count":[0-9]*' | cut -d: -f2
}

start_evidence F7-E2-X2 "$@"

page_ids=()
if [ -n "${EDITOR_TOKEN:-}" ]; then
  [ -n "${BOOK_ID:-}" ] || die "falta BOOK_ID"
  log "Creando $PAGES páginas de prueba en el libro $BOOK_ID (audit-svc funcionando)"
  for i in $(seq -w 1 "$PAGES"); do
    id="$(api POST /api/pages "{\"book_id\":$BOOK_ID,\"name\":\"Caos X2 - página $i\",\"html\":\"<p>Página de prueba del experimento X2</p>\"}" \
      | grep -o '"id":[0-9]*' | head -1 | cut -d: -f2)"
    [ -n "$id" ] || die "no se pudo crear la página $i"
    page_ids+=("$id")
  done
  log "Páginas creadas: ${page_ids[*]}"
  sleep 5
fi

t_stop="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
svc_stop audit-svc
log ">>> audit-svc detenido ($t_stop). Pendientes en AUDIT_CONSUMER: $(pending)"

if [ "${#page_ids[@]}" -gt 0 ]; then
  for id in "${page_ids[@]}"; do
    api PUT "/api/pages/$id" "{\"html\":\"<p>Editada durante X2 a las $(ts)</p>\"}" >/dev/null
    log "Página $id editada"
  done
else
  log "Editar AHORA $PAGES páginas en BookStack (hay ${DOWN_SECONDS}s)."
fi

end=$(( $(date +%s) + DOWN_SECONDS ))
while [ "$(date +%s)" -lt "$end" ]; do
  log "audit-svc caído; pendientes en AUDIT_CONSUMER: $(pending)"
  sleep 15
done

expected="$(pending)"
log "Eventos pendientes al reanudar: $expected"
t_start="$(now_ms)"
svc_start audit-svc
log ">>> audit-svc iniciado"

wait_until 180 bash -c "[ \"\$(curl -s --get '$PROMETHEUS/api/v1/query' --data-urlencode 'query=jetstream_consumer_num_pending{consumer_name=\"AUDIT_CONSUMER\"}' | grep -o '\"[0-9]*\"\]' | tr -d '\"]')\" = 0 ]" \
  || die "audit-svc no vació la cola en 180 s"
catchup=$(( $(now_ms) - t_start ))

sleep 3
registered="$(audit_count_since "$t_stop")"
log "=== Resultado X2 ==="
log "Eventos publicados durante la caída: $expected"
log "Registros de auditoría desde la caída: $registered"
log "Tiempo hasta vaciar la cola: ${catchup}ms (incluye el arranque de audit-svc)"
if [ "$registered" = "$expected" ]; then
  log "Cero pérdida de eventos: OK"
else
  log "ATENCIÓN: no coinciden (revisar si hubo otras ediciones durante la prueba)"
fi
api_curl --get "$AUDIT/audit" --data-urlencode "start=$t_stop" --data-urlencode "limit=1000" \
  | grep -o '"entity_id":"[0-9]*","entity_name":"[^"]*"' | sort | uniq -c | tee -a "$LOG_FILE"

if [ "${#page_ids[@]}" -gt 0 ] && [ "${CLEANUP:-1}" = "1" ]; then
  for id in "${page_ids[@]}"; do api DELETE "/api/pages/$id" >/dev/null; done
  log "Páginas de prueba eliminadas (CLEANUP=0 para conservarlas)"
fi
log "Evidencia: $LOG_FILE"
