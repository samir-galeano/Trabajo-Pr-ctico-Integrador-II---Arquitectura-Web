#!/usr/bin/env bash
# Genera los archivos .env a partir de los .env.example del repositorio.
#
# - Si un .env ya existe, NO lo sobrescribe.
# - Las contraseñas se generan al azar y se reutilizan entre archivos
#   (la de Postgres en search-svc, la de MinIO en export-svc y media-svc).
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
MINIO_PASSWORD="$(read_var cna/deploy/minio/.env MINIO_ROOT_PASSWORD)"
WEBHOOK_TOKEN="$(read_var cna/services/event-bridge/.env WEBHOOK_TOKEN)"

PG_PASSWORD="${PG_PASSWORD:-$(random)}"
MINIO_PASSWORD="${MINIO_PASSWORD:-$(random)}"
WEBHOOK_TOKEN="${WEBHOOK_TOKEN:-$(random)}"

EXAMPLES=(
  .env.example
  cna/deploy/postgres.env.example
  cna/deploy/minio/.env.example
  cna/services/search-svc/.env.example
  cna/services/event-bridge/.env.example
  cna/services/audit-svc/.env.example
  cna/services/export-svc/.env.example
  cna/services/media-svc/.env.example
)

for example in "${EXAMPLES[@]}"; do
  target="${example%.example}"
  if [ -f "$target" ]; then
    echo "  existe   $target (no se modifica)"
    continue
  fi
  sed -e "s|CAMBIAR_PG_PASSWORD|${PG_PASSWORD}|g" \
      -e "s|CAMBIAR_MINIO_PASSWORD|${MINIO_PASSWORD}|g" \
      -e "s|CAMBIAR_WEBHOOK_TOKEN|${WEBHOOK_TOKEN}|g" \
      "$example" > "$target"
  chmod 600 "$target"
  echo "  creado   $target"
done

echo
echo "Listo. Pendiente a mano:"
echo "  - BOOKSTACK_TOKEN_ID / BOOKSTACK_TOKEN_SECRET en search-svc/.env y export-svc/.env"
echo "  - BOOKSTACK_HOST_IP en .env (IP de la VM de BookStack)"
echo "  - URL del webhook en BookStack: http://<event-bridge>:8001/webhooks/bookstack/<WEBHOOK_TOKEN de event-bridge/.env>"
