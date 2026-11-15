#!/usr/bin/env bash
# C4 (F7-E13): escanea con Trivy y genera el SBOM (Syft, CycloneDX) de cada
# imagen del despliegue. Se ejecuta en VM-2, desde la raíz del repo, con las
# imágenes ya construidas (docker compose build):
#
#   ./cna/security/scan-images.sh              # todas las imágenes del compose
#   ./cna/security/scan-images.sh search-svc:2.0.0 ...
#
# Salidas (versionadas):
#   cna/security/reports/trivy/<imagen>.txt   tabla HIGH/CRITICAL
#   cna/security/reports/trivy/<imagen>.json  informe completo
#   cna/security/sbom/<imagen>.cdx.json       SBOM CycloneDX
#   cna/security/reports/resumen.md           conteo por severidad
#
# Cada CRITICAL que quede debe justificarse en cna/security/hallazgos.md.

source "$(dirname "$0")/../chaos/lib.sh"
require docker

TRIVY_IMAGE="aquasec/trivy:0.67.2"
SYFT_IMAGE="anchore/syft:v1.33.0"
SEC_DIR="$REPO_ROOT/cna/security"
mkdir -p "$SEC_DIR/reports/trivy" "$SEC_DIR/sbom"

if [ "$#" -gt 0 ]; then
  images=("$@")
else
  mapfile -t images < <(docker compose -f "$REPO_ROOT/compose.yaml" config --images | sort -u)
fi

start_evidence F7-E13-trivy "${images[@]}"

docker volume create trivy-cache >/dev/null
trivy() {
  MSYS_NO_PATHCONV=1 docker run --rm \
    -v /var/run/docker.sock:/var/run/docker.sock:ro \
    -v trivy-cache:/root/.cache/ \
    -v "$SEC_DIR/reports/trivy:/out" \
    "$TRIVY_IMAGE" "$@"
}

SUMMARY="$SEC_DIR/reports/resumen.md"
{
  echo "# Escaneo de imágenes (Trivy $TRIVY_IMAGE)"
  echo
  echo "Fecha: $(date '+%Y-%m-%d %H:%M')  ·  Nodo: $(hostname)"
  echo
  echo "| Imagen | CRITICAL | HIGH | MEDIUM | LOW | Paquetes (SBOM) |"
  echo "|---|---|---|---|---|---|"
} > "$SUMMARY"

for image in "${images[@]}"; do
  safe="$(echo "$image" | tr '/:' '__')"
  log ">>> $image"

  trivy image --quiet --scanners vuln --format json --output "/out/$safe.json" "$image"
  trivy image --quiet --scanners vuln --severity HIGH,CRITICAL --format table --output "/out/$safe.txt" "$image"

  MSYS_NO_PATHCONV=1 docker run --rm -v /var/run/docker.sock:/var/run/docker.sock:ro \
    "$SYFT_IMAGE" -q "docker:$image" -o cyclonedx-json > "$SEC_DIR/sbom/$safe.cdx.json"

  counts="$(grep -o '"Severity": *"[A-Z]*"' "$SEC_DIR/reports/trivy/$safe.json" | grep -o '[A-Z]*"$' | tr -d '"' | sort | uniq -c)"
  count() { echo "$counts" | awk -v s="$1" '$2 == s {print $1}' | grep . || echo 0; }
  packages="$(grep -o '"bom-ref"' "$SEC_DIR/sbom/$safe.cdx.json" | wc -l)"

  row="| \`$image\` | $(count CRITICAL) | $(count HIGH) | $(count MEDIUM) | $(count LOW) | $packages |"
  echo "$row" >> "$SUMMARY"
  log "$row"
done

log "Resumen: ${SUMMARY#"$REPO_ROOT"/}"
tee -a "$LOG_FILE" < "$SUMMARY"
log "Evidencia: $LOG_FILE"
