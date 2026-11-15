#!/usr/bin/env bash
# Despliegue completo en Docker Swarm con un comando (B5, F6-E16).
# Se ejecuta en el nodo manager (VM-2), desde la raíz del repositorio:
#
#   ./cna/deploy/swarm/deploy.sh
#
# Pasos (todos idempotentes):
#   1. Genera los .env que falten (setup-env.sh).
#   2. Etiqueta los nodos que no tengan rol (por defecto, el manager).
#   3. Levanta un registry del clúster en 127.0.0.1:5000 (lo alcanzan todos
#      los nodos por la routing mesh, sin configurar registries inseguros).
#   4. Construye las imágenes propias con la etiqueta <versión>-<commit>
#      (p. ej. search-svc:2.0.0-a1b2c3d) y las sube al registry.
#   5. Crea el secreto del token de BookStack si no existe.
#   6. docker stack deploy y espera a que todo converja.
#
# Variables: STACK (cna), EXPORT_WORKERS (se conserva la escala actual).

set -euo pipefail
cd "$(dirname "$0")/../../.."

STACK="${STACK:-cna}"
REGISTRY="127.0.0.1:5000"
VERSION="2.0.0"
SERVICES=(search-svc event-bridge audit-svc export-svc media-svc)
STACK_FILE="cna/deploy/swarm/stack.yaml"

step() { printf '\n==> %s\n' "$*"; }

# ---------------------------------------------------------------- 0. Swarm
[ "$(docker info --format '{{.Swarm.LocalNodeState}}')" = "active" ] \
  || { echo "Este nodo no está en un Swarm. Ver cna/deploy/swarm/README.md"; exit 1; }
[ "$(docker info --format '{{.Swarm.ControlAvailable}}')" = "true" ] \
  || { echo "Ejecutar en un nodo manager"; exit 1; }

# ---------------------------------------------------------------- 1. .env
step "Archivos .env"
./cna/deploy/setup-env.sh

# ---------------------------------------------------------------- 2. nodos
step "Etiquetas de nodos"
SELF="$(docker info --format '{{.Swarm.NodeID}}')"
for label in cna.data cna.observability; do
  if [ -z "$(docker node ls -q --filter "node.label=$label=true")" ]; then
    docker node update --label-add "$label=true" "$SELF" >/dev/null
    echo "  $label=true → $(docker node inspect -f '{{.Description.Hostname}}' "$SELF") (ningún nodo lo tenía)"
  fi
done
docker node ls --format '{{.Hostname}}' | while read -r node; do
  echo "  $node: $(docker node inspect -f '{{range $k, $v := .Spec.Labels}}{{$k}}={{$v}} {{end}}' "$node")"
done

# ---------------------------------------------------------------- 3. registry
step "Registry del clúster ($REGISTRY)"
if ! docker service inspect registry >/dev/null 2>&1; then
  docker service create --name registry --detach=false \
    --publish published=5000,target=5000 \
    --constraint node.role==manager \
    --mount type=volume,source=registry-data,target=/var/lib/registry \
    registry:2.8.3 >/dev/null
fi
until curl -sf "http://$REGISTRY/v2/" >/dev/null; do sleep 1; done
echo "  registry listo"

# ---------------------------------------------------------------- 4. imágenes
GIT_SHA="$(git rev-parse --short HEAD)"
git diff --quiet HEAD -- cna/services || GIT_SHA="$GIT_SHA-dirty"
export GIT_SHA REGISTRY
step "Imágenes ${VERSION}-${GIT_SHA}"
for svc in "${SERVICES[@]}"; do
  image="$REGISTRY/$svc:$VERSION-$GIT_SHA"
  docker build -q -t "$image" -t "$svc:$VERSION" "cna/services/$svc" >/dev/null
  docker push -q "$image" >/dev/null
  echo "  $image"
done

# ---------------------------------------------------------------- 5. secreto
step "Secreto del token de BookStack"
BOOKSTACK_TOKEN_SECRET="$(docker secret ls --format '{{.Name}}' | grep -E '^bookstack_token_v[0-9]+$' | sort -V | tail -1 || true)"
if [ -z "$BOOKSTACK_TOKEN_SECRET" ]; then
  env_file=cna/services/search-svc/.env
  id="$(grep -E '^BOOKSTACK_TOKEN_ID=' "$env_file" | cut -d= -f2-)"
  secret="$(grep -E '^BOOKSTACK_TOKEN_SECRET=' "$env_file" | cut -d= -f2-)"
  case "$id$secret" in *CAMBIAR*|"") echo "  Completar BOOKSTACK_TOKEN_ID/SECRET en $env_file"; exit 1 ;; esac
  BOOKSTACK_TOKEN_SECRET=bookstack_token_v1
  printf '%s:%s' "$id" "$secret" | docker secret create "$BOOKSTACK_TOKEN_SECRET" - >/dev/null
fi
export BOOKSTACK_TOKEN_SECRET
echo "  usando $BOOKSTACK_TOKEN_SECRET"

# ---------------------------------------------------------------- 6. deploy
# Configs inmutables: la versión depende del contenido
CONFIG_VERSION="$(cat cna/deploy/init/*.py cna/observability/prometheus/prometheus.yml \
  cna/observability/prometheus/rules/*.yml cna/observability/loki/loki.yml \
  cna/observability/alloy/config.alloy cna/observability/grafana/provisioning/*/*.yml \
  cna/observability/grafana/dashboards/*.json | sha256sum | cut -c1-10)"
BOOKSTACK_HOST_IP="$(grep -E '^BOOKSTACK_HOST_IP=' .env | cut -d= -f2)"
# Conserva la escala actual de export-worker al volver a desplegar
if [ -z "${EXPORT_WORKERS:-}" ]; then
  EXPORT_WORKERS="$(docker service inspect -f '{{.Spec.Mode.Replicated.Replicas}}' "${STACK}_export-worker" 2>/dev/null | tr -dc '0-9' || true)"
  EXPORT_WORKERS="${EXPORT_WORKERS:-1}"
fi
export CONFIG_VERSION BOOKSTACK_HOST_IP EXPORT_WORKERS

step "docker stack deploy $STACK (export-worker: $EXPORT_WORKERS réplicas)"
docker stack deploy --detach=true --prune -c "$STACK_FILE" "$STACK"

# Espera propia: --detach=false nunca termina porque nats-init y
# minio-init son tareas de una sola vez (quedan en "Complete", 0/1).
step "Esperando que los servicios converjan"
deadline=$(( $(date +%s) + ${WAIT_SECONDS:-300} ))
while :; do
  pending="$(docker stack services "$STACK" --format '{{.Name}} {{.Replicas}}' \
    | grep -vE '_(nats|minio)-init ' \
    | awk '{ split($2, r, "/"); if (r[1] != r[2] + 0 || r[2] + 0 == 0) print $1 " " $2 }')"
  [ -n "$pending" ] || break
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "  Sin converger después de ${WAIT_SECONDS:-300}s:"; echo "$pending" | sed 's/^/    /'
    echo "  Ver: docker service ps <servicio> --no-trunc"
    exit 1
  fi
  sleep 5
done
echo "  todos los servicios en ejecución"

# Configs de despliegues anteriores que ya no usa nadie
docker config ls --format '{{.Name}}' | grep "^cna_" | grep -v "_${CONFIG_VERSION}$" \
  | xargs -r docker config rm >/dev/null 2>&1 || true

step "Estado"
docker stack services "$STACK" --format 'table {{.Name}}\t{{.Mode}}\t{{.Replicas}}\t{{.Image}}'
