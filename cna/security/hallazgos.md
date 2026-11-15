# Registro de hallazgos – Fase 7 (amplía la sección 7.5 del TP I)

Escaneo con Trivy 0.67.2 y SBOM con Syft 1.33.0 (CycloneDX) sobre las imágenes 2.0.0. Para regenerar los informes: `./cna/security/scan-images.sh` (salidas en `reports/` y `sbom/`).

## Resumen del escaneo de las imágenes propias

| Imagen | CRITICAL | HIGH | HIGH/CRITICAL con parche disponible |
|---|---|---|---|
| `search-svc:2.0.0` | 0 | 47 | 0 |
| `event-bridge:2.0.0` | 0 | 47 | 0 |
| `audit-svc:2.0.0` | 0 | 47 | 0 |
| `export-svc:2.0.0` | 0 | 47 | 0 |
| `media-svc:2.0.0` | 0 | 47 | 0 |

Antes de las correcciones (H-02 y H-03), `media-svc` tenía 5 CRITICAL y todas las imágenes tenían HIGH con parche disponible.

## Hallazgos

| ID | Hallazgo | Severidad | Estado | Tratamiento |
|---|---|---|---|---|
| H-01 | El token del webhook de BookStack (va en la URL) aparecía en los logs de event-bridge, en la etiqueta `path` de Prometheus, en el log de acceso de uvicorn y en los atributos `http.target`/`http.url` de las trazas. Con Loki, Prometheus y Jaeger quedaba visible para cualquiera con acceso a Grafana. | Alta | Corregido | Métricas y logs usan la plantilla de la ruta (`/webhooks/bookstack/{token}`); el log de acceso de uvicorn se desactivó (`--no-access-log`); un hook de OpenTelemetry reemplaza el token por `[REDACTED]`. Cubierto por `event-bridge/tests/test_main.py::test_webhook_token_not_in_metrics_or_trace`. |
| H-02 | `media-svc`: Pillow 11.2.1 con 5 CVE CRITICAL y 36 HIGH; python-multipart 0.0.20 con 3 HIGH. | Crítica | Corregido | Pillow 13.0.0 y python-multipart 0.0.30. Pruebas de media-svc OK. |
| H-03 | Todas las imágenes: Starlette 0.46.2 (dependencia de FastAPI 0.115) con 3 HIGH, y `zlib1g` del sistema base con 2 HIGH. | Alta | Corregido | FastAPI 0.163.1 + Starlette 1.3.1; `apt-get upgrade` en la etapa final de cada Dockerfile. Pruebas de los 5 servicios OK. |
| H-04 | 47 HIGH en paquetes de Debian 13 sin parche publicado (`util-linux`, `mount`, `login`, `libblkid1`, `libsqlite3-0`, `ncurses`, `libsystemd0`, `perl-base` *fix_deferred*, etc.). | Alta | Riesgo aceptado | No hay versión corregida. Son utilidades del sistema que los servicios no ejecutan; los contenedores corren como UID 10001, con `read_only: true` y sin puertos más allá del suyo. Se vuelve a escanear en cada entrega; si aparece el parche, `apt-get upgrade` lo aplica al reconstruir. |
| H-05 | Rotar el token de la API de BookStack obligaba a recrear search-svc y export-svc (corte de servicio). | Media | Corregido | El token se lee de `cna/deploy/secrets/token` y se recarga en caliente al cambiar (`app/credentials.py`). Procedimiento: `cna/security/rotate-bookstack-token.sh` (F7-E14). |
| H-06 | Alloy monta `/var/run/docker.sock` (de solo lectura) para leer los logs de los contenedores. El acceso al socket equivale a control del daemon de Docker. | Media | Riesgo aceptado | Montaje `:ro`, Alloy sin puertos publicados y en la red interna. Alternativa descartada: el driver de logging de Loki, que exige instalar un plugin en cada nodo y no queda en el repositorio. |
| H-07 | Los puertos de los servicios (8000–8004), MinIO (9000/9001) y observabilidad (3000, 9090, 3100, 16686) se publican en VM-2 (y, con Swarm, en todos los nodos por la routing mesh). **Las reglas de UFW no los filtran:** Docker inserta sus reglas de iptables (cadenas `DOCKER` / `DOCKER-INGRESS`) antes que las de UFW, así que un `ufw allow from 192.168.100.23` no impide que otro host llegue a esos puertos. | Alta | Corregir en las VM | Filtrar en la cadena `DOCKER-USER`, que Docker evalúa antes que las suyas: solo 192.168.100.23 (gateway) a los puertos de servicio, y la red del laboratorio a Grafana, Prometheus y Jaeger. Comandos y verificación al final de este documento. El exterior sigue entrando únicamente por 443 de VM-1. |
| H-09 | La API pública (`/v2/search`, `/v2/audit`, `/v2/exports`, `/v2/media`) no tenía autenticación, aunque los contratos declaraban `BearerAuth`: cualquiera que llegara al gateway podía consultar la auditoría o generar exportaciones. | Alta | Corregido | Middleware Bearer en los cuatro servicios (`app/auth.py`): con `API_AUTH_TOKEN` definido, responde 401 sin un token válido (comparación en tiempo constante). Admite varios tokens para rotarlos sin corte. `/healthz`, `/readyz` y `/metrics` quedan abiertos para el orquestador y Prometheus. Cubierto por `tests/test_auth.py` en cada servicio. |
| H-08 | Las imágenes se etiquetaban solo con versión semántica (`search-svc:2.0.0`): dos construcciones distintas podían tener la misma etiqueta (B3 pide versión más hash del commit). | Baja | Corregido (Swarm) | `deploy.sh` etiqueta `<versión>-<commit>` (por ejemplo `2.0.0-a1b2c3d`; `-dirty` si hay cambios sin commit) y despliega por esa etiqueta desde el registry del clúster. Compose, que queda para desarrollo, conserva `x.y.z`. |

## SBOM

Un archivo CycloneDX por imagen en `sbom/` (`<imagen>.cdx.json`), generado con Syft. Se versiona junto con el código de la entrega.

## Corrección de H-07: filtrar los puertos publicados por Docker

En VM-2 (y en cada nodo del Swarm), cambiando `<RED-LAB>` por la red desde la que se abre Grafana (por ejemplo `192.168.100.0/24`):

```bash
# Puertos de servicio, MinIO y Loki: solo el gateway (VM-1)
sudo iptables -I DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL \
  -m multiport --ctorigdstport 8000:8004,9000,9001,3100 ! -s 192.168.100.23 -j DROP
# Grafana, Prometheus y Jaeger: solo la red del laboratorio
sudo iptables -I DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL \
  -m multiport --ctorigdstport 3000,9090,16686 ! -s <RED-LAB> -j DROP

sudo apt install iptables-persistent && sudo netfilter-persistent save   # persistir
```

Si la versión de iptables no acepta `-m multiport --ctorigdstport`, usar una regla por puerto o rango (`-m conntrack --ctorigdstport 8000:8004`).

Verificación: desde el anfitrión, `curl -m 3 http://<IP-VM-2>:8000/healthz` debe fallar; desde VM-1 debe responder. Evidencia: `sudo iptables -L DOCKER-USER -n -v` junto con `sudo ufw status verbose`.
