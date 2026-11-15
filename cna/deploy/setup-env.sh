#!/usr/bin/env bash
# Genera los archivos .env a partir de los .env.example del repositorio.
#
# - Si un .env ya existe, NO lo sobrescribe.
# - Las contraseñas se generan al azar y se reutilizan entre archivos
#   (las de Postgres en search-svc y audit-svc, la de MinIO en export-svc y media-svc).
# - El token de la API de BookStack se debe completar a mano después.
#
# Uso (desde la raíz del repositorio):  ./cna/deploy/setup-env.sh
set -euo pipefail

cd "$(dirname "$0")/../.."

# Lee el valor de una variable de un .env existente (vacío si no existe)
read_var() {
  local file="$1" var="$2"
  [ -f "$file" ] && grep -E "^${var}=" "$file" | head -1 | cut -d= -f2- || true
}

random() { openssl rand -hex 16; }

PG_PASSWORD="$(read_var cna/deploy/postgres.env POSTGRES_PASSWORD)"
AUDIT_PG_PASSWORD="$(read_var cna/deploy/audit-db.env POSTGRES_PASSWORD)"
MINIO_PASSWORD="$(read_var cna/deploy/minio/.env MINIO_ROOT_PASSWORD)"
WEBHOOK_TOKEN="$(read_var cna/services/event-bridge/.env WEBHOOK_TOKEN)"
GRAFANA_PASSWORD="$(read_var cna/observability/grafana.env GF_SECURITY_ADMIN_PASSWORD)"
API_TOKEN="$(read_var cna/services/search-svc/.env API_AUTH_TOKEN)"

PG_PASSWORD="${PG_PASSWORD:-$(random)}"
API_TOKEN="${API_TOKEN:-$(random)$(random)}"
AUDIT_PG_PASSWORD="${AUDIT_PG_PASSWORD:-$(random)}"
MINIO_PASSWORD="${MINIO_PASSWORD:-$(random)}"
WEBHOOK_TOKEN="${WEBHOOK_TOKEN:-$(random)}"
GRAFANA_PASSWORD="${GRAFANA_PASSWORD:-$(random)}"

render() {
  sed -e "s|CAMBIAR_AUDIT_PG_PASSWORD|${AUDIT_PG_PASSWORD}|g" \
      -e "s|CAMBIAR_PG_PASSWORD|${PG_PASSWORD}|g" \
      -e "s|CAMBIAR_MINIO_PASSWORD|${MINIO_PASSWORD}|g" \
      -e "s|CAMBIAR_WEBHOOK_TOKEN|${WEBHOOK_TOKEN}|g" \
      -e "s|CAMBIAR_GRAFANA_PASSWORD|${GRAFANA_PASSWORD}|g" \
      -e "s|CAMBIAR_API_TOKEN|${API_TOKEN}|g" \
      "$1"
}

EXAMPLES=(
  .env.example
  cna/deploy/postgres.env.example
  cna/deploy/audit-db.env.example
  cna/deploy/minio/.env.example
  cna/services/search-svc/.env.example
  cna/services/event-bridge/.env.example
  cna/services/audit-svc/.env.example
  cna/services/export-svc/.env.example
  cna/services/media-svc/.env.example
  cna/observability/grafana.env.example
)

for example in "${EXAMPLES[@]}"; do
  target="${example%.example}"
  if [ -f "$target" ]; then
    # Solo agrega las variables nuevas del .env.example; no toca las existentes.
    added=0
    [ -n "$(tail -c1 "$target")" ] && echo >> "$target"
    while IFS= read -r line; do
      case "$line" in ''|\#*) continue ;; esac
      if ! grep -qE "^${line%%=*}=" "$target"; then
        echo "$line" >> "$target"
        added=$((added + 1))
      fi
    done < <(render "$example")
    echo "  existe   $target (+${added} variables nuevas)"
    continue
  fi
  render "$example" > "$target"
  chmod 600 "$target"
  echo "  creado   $target"
done

echo
echo "Listo. Pendiente a mano:"
echo "  - BOOKSTACK_TOKEN_ID / BOOKSTACK_TOKEN_SECRET en search-svc/.env y export-svc/.env"
echo "  - BOOKSTACK_HOST_IP en .env (IP de la VM de BookStack)"
echo "  - URL del webhook en BookStack: http://<event-bridge>:8001/webhooks/bookstack/<WEBHOOK_TOKEN de event-bridge/.env>"
echo "  - Token de la API (/v2/*): API_AUTH_TOKEN en search-svc/.env; usar  -H \"Authorization: Bearer <token>\""
