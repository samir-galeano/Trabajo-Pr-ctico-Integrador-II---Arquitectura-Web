#!/usr/bin/env bash
# F7-E14: rota el token de la API de BookStack sin reconstruir imágenes y sin
# cortar el servicio. Se ejecuta en VM-2 (manager, si es Swarm), desde la raíz
# del repositorio.
#
# Antes de correrlo: en BookStack, perfil del usuario de servicio → API Tokens,
# crear un token NUEVO (sin borrar el anterior todavía).
#
#   ./cna/security/rotate-bookstack-token.sh
#
# Detecta cómo está desplegado:
#   - Docker Swarm (stack "cna"): crea el Docker secret bookstack_token_vN+1 y
#     lo cambia en search-svc, export-svc y export-worker con una
#     actualización rodante start-first (la tarea nueva queda healthy antes
#     de bajar la vieja); al final borra el secreto viejo.
#   - Docker Compose: escribe cna/deploy/secrets/token, que los servicios
#     vuelven a leer en caliente (app/credentials.py) sin reiniciarse.
# En los dos casos actualiza también los .env.
#
# Durante toda la rotación una sonda llama a /search cada 0,5 s e informa
# cuántas peticiones fallaron (esperado: 0).
#
# Después: borrar el token viejo en BookStack y editar una página para
# comprobar que la reindexación sigue funcionando con el token nuevo.

source "$(dirname "$0")/../chaos/lib.sh"
require curl docker

STACK="${STACK:-cna}"
TOKEN_FILE="$REPO_ROOT/cna/deploy/secrets/token"
ENV_FILES=("$REPO_ROOT/cna/services/search-svc/.env" "$REPO_ROOT/cna/services/export-svc/.env")
TOKEN_SERVICES=(search-svc export-svc export-worker)
SEARCH_PROBE="http://$SERVICES_HOST:8000/search?q=a"

if docker service inspect "${STACK}_search-svc" >/dev/null 2>&1; then
  MODE=swarm
else
  MODE=compose
fi

start_evidence F7-E14-rotacion-token "$@"
log "Despliegue detectado: $MODE"

read -rp "ID del token NUEVO: " NEW_ID
read -rsp "Secreto del token NUEVO: " NEW_SECRET
echo
[ -n "$NEW_ID" ] && [ -n "$NEW_SECRET" ] || die "ID y secreto son obligatorios"
log "Token nuevo: $NEW_ID (el secreto no se registra)"

# 1. Validar el token nuevo contra BookStack antes de usarlo
BOOKSTACK_HOST_IP="$(grep -E '^BOOKSTACK_HOST_IP=' "$REPO_ROOT/.env" | cut -d= -f2 || true)"
code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 \
  ${BOOKSTACK_HOST_IP:+--resolve "bookstack.local:443:$BOOKSTACK_HOST_IP"} \
  -H "Authorization: Token $NEW_ID:$NEW_SECRET" \
  "https://bookstack.local/api/books?count=1")"
[ "$code" = "200" ] || die "BookStack rechazó el token nuevo (HTTP $code); no se cambia nada"
log "BookStack acepta el token nuevo (HTTP 200)"

# 2. Sonda continua sobre /search
PROBE_LOG="$(mktemp)"
(
  while true; do
    printf '%s %s\n' "$(ts)" "$(http_code "$SEARCH_PROBE" 3)" >> "$PROBE_LOG"
    sleep 0.5
  done
) &
PROBE_PID=$!
trap 'kill $PROBE_PID 2>/dev/null || true; rm -f "$PROBE_LOG"' EXIT
log "Sonda iniciada sobre $SEARCH_PROBE"

# 3. Aplicar el token
if [ "$MODE" = "swarm" ]; then
  OLD_SECRET="$(docker service inspect -f '{{range .Spec.TaskTemplate.ContainerSpec.Secrets}}{{.SecretName}}{{end}}' "${STACK}_search-svc")"
  last="$(docker secret ls --format '{{.Name}}' | grep -E '^bookstack_token_v[0-9]+$' | sed 's/.*_v//' | sort -n | tail -1)"
  NEW_SECRET_NAME="bookstack_token_v$(( ${last:-0} + 1 ))"
  printf '%s:%s' "$NEW_ID" "$NEW_SECRET" | docker secret create "$NEW_SECRET_NAME" - >/dev/null
  log "Secreto $NEW_SECRET_NAME creado (reemplaza a $OLD_SECRET)"

  for svc in "${TOKEN_SERVICES[@]}"; do
    log "Actualización rodante de ${STACK}_$svc (start-first)"
    docker service update --detach=false --quiet \
      --secret-rm "$OLD_SECRET" \
      --secret-add "source=$NEW_SECRET_NAME,target=bookstack_token,uid=10001,gid=10001,mode=0400" \
      "${STACK}_$svc" >/dev/null
    log "${STACK}_$svc: $(docker service ls --filter "name=${STACK}_$svc" --format '{{.Replicas}}') réplicas con $NEW_SECRET_NAME"
  done

  docker secret rm "$OLD_SECRET" >/dev/null && log "Secreto viejo $OLD_SECRET eliminado"
  log "deploy.sh usará $NEW_SECRET_NAME en los próximos despliegues (toma el bookstack_token_vN más alto)"
else
  SINCE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  declare -A STARTED_BEFORE
  for c in search-svc export-svc; do
    STARTED_BEFORE[$c]="$(docker inspect -f '{{.State.StartedAt}}' "$c")"
  done

  mkdir -p "$(dirname "$TOKEN_FILE")"
  if [ ! -f "$TOKEN_FILE" ]; then
    install -m 600 /dev/null "$TOKEN_FILE"
    sudo chown 10001:10001 "$TOKEN_FILE"   # los contenedores corren como UID 10001
  fi
  # Mismo inode: el montaje del directorio ve el cambio
  printf '%s:%s\n' "$NEW_ID" "$NEW_SECRET" | sudo tee "$TOKEN_FILE" >/dev/null
  log "Token escrito en ${TOKEN_FILE#"$REPO_ROOT"/}"

  for c in search-svc export-svc; do
    port="$(docker port "$c" | head -1 | sed 's/.*://')"
    wait_until 30 bash -c "curl -s -o /dev/null http://$SERVICES_HOST:$port/readyz; docker logs --since $SINCE $c 2>&1 | grep -q 'Token de BookStack cargado.*$NEW_ID'" \
      || die "$c no recargó el token"
    log "$c: $(docker logs --since "$SINCE" "$c" 2>&1 | grep 'Token de BookStack cargado' | tail -1)"
    if [ "$(docker inspect -f '{{.State.StartedAt}}' "$c")" = "${STARTED_BEFORE[$c]}" ]; then
      log "$c: sin reinicios"
    else
      log "$c: SE REINICIÓ durante la rotación"
    fi
  done
fi

for env_file in "${ENV_FILES[@]}"; do
  sed -i -e "s|^BOOKSTACK_TOKEN_ID=.*|BOOKSTACK_TOKEN_ID=$NEW_ID|" \
         -e "s|^BOOKSTACK_TOKEN_SECRET=.*|BOOKSTACK_TOKEN_SECRET=$NEW_SECRET|" "$env_file"
done
log ".env actualizados"

# 4. Resultado de la sonda
sleep 3
kill $PROBE_PID 2>/dev/null || true
total="$(wc -l < "$PROBE_LOG")"
failed="$(awk '$3 != "200"' "$PROBE_LOG" | wc -l)"
log "Sonda: $total peticiones a /search durante la rotación, $failed fallidas"
awk '$3 != "200"' "$PROBE_LOG" | tee -a "$LOG_FILE"

log "Listo. Ahora borrar el token VIEJO en BookStack y editar una página para verificar la reindexación."
log "Evidencia: $LOG_FILE"
