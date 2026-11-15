# Trabajo-Practico-Integrador-II - Arquitectura-Web

Trabajo Práctico Integrador II de la materia Electiva III — Arquitectura Web (FP-UNA, 2.º Semestre 2026).

Descomposición, contenerización, despliegue distribuido, resiliencia y observabilidad de BookStack

---

## Equipo

* Jazmin Figueredo
* Fernando Servian
* Samir Galeano

Docente: Rodrigo Benitez

---

## Despliegue (un comando)

Requisitos: Docker Engine con el plugin Compose y `openssl`.

```bash
git clone https://github.com/samir-galeano/Trabajo-Pr-ctico-Integrador-II---Arquitectura-Web.git tp2
cd tp2
./cna/deploy/setup-env.sh      # genera los .env a partir de los .env.example
docker compose up -d --build   # construye y levanta todo
docker compose ps              # todos los servicios deben quedar healthy
```

`setup-env.sh` genera contraseñas al azar y no sobrescribe los `.env` existentes. Después hay que completar a mano:

| Archivo | Variable | Valor |
|---|---|---|
| `.env` | `BOOKSTACK_HOST_IP` | IP de la VM de BookStack (VM-1) |
| `cna/services/search-svc/.env` y `cna/services/export-svc/.env` | `BOOKSTACK_TOKEN_ID`, `BOOKSTACK_TOKEN_SECRET` | Token del usuario de servicio de BookStack |

Y en BookStack (Settings → Webhooks) configurar el webhook con los eventos `page_create`, `page_update` y `page_delete` apuntando a:

```
http://<host-de-event-bridge>:8001/webhooks/bookstack/<WEBHOOK_TOKEN de cna/services/event-bridge/.env>
```

Los servicios `nats-init` y `minio-init` se ejecutan una sola vez en cada `up`: crean los streams de NATS (`BOOKSTACK`, `BOOKSTACK_DLQ`, `MEDIA`) y los buckets de MinIO (`bookstack-exports`, `bookstack-media`) si no existen, y terminan.

### Migración desde la versión anterior del compose

La red `cna-net` ya no es `external`: ahora la crea Compose. Si la habían creado a mano con `docker network create`, hay que borrarla una vez (los volúmenes se reutilizan, no se pierden datos):

```bash
docker compose down
docker network rm cna-net      # desconectar antes cualquier otro contenedor que la use
docker compose up -d
```

## Exportaciones (export-svc + export-worker)

`export-svc` solo encola: guarda el job en Redis (`export-redis`) y responde `202`. Los `export-worker` (proceso separado, misma imagen) toman los jobs, piden la exportación a la API de BookStack, suben el archivo a MinIO y marcan el job como `done`.

```bash
# Pedir una exportación (page_id o book_id; format: pdf | markdown)
curl -sk -H "Authorization: Bearer $TOKEN" -X POST https://api.bookstack.local/v2/exports \
  -H 'Content-Type: application/json' -d '{"page_id": 1, "format": "pdf"}'

# Consultar el estado (queued -> running -> done | failed) y la URL prefirmada
curl -sk -H "Authorization: Bearer $TOKEN" https://api.bookstack.local/v2/exports/<job_id>

# Escalar los workers (C2)
docker compose up -d --scale export-worker=3
```

Si un worker muere a mitad de un job (X3), su *lease* en Redis vence a los `EXPORT_LEASE_TTL` segundos y otro worker lo devuelve a la cola. Después de `EXPORT_MAX_ATTEMPTS` intentos, el job pasa a `failed`. Para los experimentos se puede alargar cada job con `EXPORT_SIMULATED_DELAY`.

Pruebas:

```bash
cd cna/services/export-svc
pip install -r requirements-dev.txt
python -m pytest -q
```

## Auditoría (audit-svc + audit-db)

`audit-svc` consume los eventos `bookstack.page.*` (consumidor durable `AUDIT_CONSUMER` de JetStream) y los guarda en su propio PostgreSQL (`audit-db`). `event_id` es la clave primaria, así que un evento repetido no genera un segundo registro (F6-E5).

```bash
curl -sk -H "Authorization: Bearer $TOKEN" 'https://api.bookstack.local/v2/audit?entity=page&id=25&start=2026-11-01T00:00:00Z'
curl -sk -H "Authorization: Bearer $TOKEN" https://api.bookstack.local/v2/audit/<event_id>
```

Al actualizar desde la versión anterior, `./cna/deploy/setup-env.sh` crea `cna/deploy/audit-db.env` y agrega las variables `DATABASE_*` al `.env` existente de audit-svc. Los registros del antiguo `audit.jsonl` (volumen `audit-data`) no se migran.

## Resiliencia de search-svc hacia BookStack (X5)

Todas las llamadas de `search-svc` a la API de BookStack pasan por `app/bookstack.py`:

- **Timeout** de conexión y lectura (`BOOKSTACK_CONNECT_TIMEOUT`, `BOOKSTACK_READ_TIMEOUT`).
- **Reintentos con backoff exponencial y jitter** ante timeouts, errores de red, 429 y 5xx (`BOOKSTACK_MAX_RETRIES`, `BOOKSTACK_BACKOFF_BASE`). Los 4xx no se reintentan.
- **Interruptor de circuito**: tras `BOOKSTACK_CB_FAILURES` fallos seguidos se abre durante `BOOKSTACK_CB_RESET` segundos y rechaza al instante. Mientras está abierto, el consumidor deja de pedir eventos (quedan en el stream, no van a la DLQ), `POST /sync` responde `503` con `Retry-After` y `/v2/search` sigue respondiendo desde el índice local.
- **Bulkhead**: como máximo `BOOKSTACK_MAX_CONCURRENCY` llamadas simultáneas.

Métricas: `bookstack_circuit_state` (0 cerrado, 1 semiabierto, 2 abierto), `bookstack_client_requests_total{outcome}` y `bookstack_client_retries_total`. El estado también aparece en `/readyz`.

Experimento (en VM-2, con la IP de VM-1):

```bash
sudo tc qdisc add dev <iface> root netem delay 2000ms   # latencia de 2 s
# ... editar páginas y observar bookstack_circuit_state en /metrics o Grafana ...
sudo tc qdisc del dev <iface> root                       # quitar la latencia
```

## Observabilidad

`docker compose up -d` también levanta Prometheus (`:9090`), Grafana (`:3000`), Loki, Alloy y Jaeger (`:16686`). El usuario de Grafana es `admin` y la contraseña está en `cna/observability/grafana.env`. Detalle de métricas, alertas, logs y trazas en [`cna/observability/README.md`](cna/observability/README.md).

## Fase 7: experimentos, rendimiento y seguridad

- Runbook con los pasos y la matriz P49–P62: [`cna/docs/runbook_fase7.md`](cna/docs/runbook_fase7.md)
- Caos: `cna/chaos/x1…x5-*.sh` · Rendimiento: `cna/perf/` (k6, ráfaga de exportaciones, consumo) · Seguridad: `cna/security/` (Trivy + SBOM, rotación del token, hallazgos)

## Despliegue en Docker Swarm (2 nodos)

Recomendado para la entrega (B5, ADR-011). Con el clúster formado y los nodos etiquetados:

```bash
./cna/deploy/swarm/deploy.sh      # construye, sube al registry del clúster, crea el secreto y despliega
docker stack services cna
```

Detalle (formar el clúster, etiquetas, puertos, operación y migración desde Compose): [`cna/deploy/swarm/README.md`](cna/deploy/swarm/README.md).

## Autenticación de la API (BearerAuth)

Todas las rutas `/v2/*` de search, audit, export y media exigen `Authorization: Bearer <token>` (401 si falta o no coincide). El token lo genera `setup-env.sh` en `API_AUTH_TOKEN` (es el mismo en los cuatro servicios); `/healthz`, `/readyz` y `/metrics` quedan abiertos para el orquestador y Prometheus.

```bash
TOKEN=$(grep ^API_AUTH_TOKEN= cna/services/search-svc/.env | cut -d= -f2)
curl -sk -H "Authorization: Bearer $TOKEN" 'https://api.bookstack.local/v2/search?q=nginx'
```

`API_AUTH_TOKEN` admite varios tokens separados por comas, así se puede rotar sin corte: agregar el nuevo, actualizar los clientes y quitar el viejo. Si se deja vacío, la autenticación queda desactivada.

## Eventos de exportación

export-svc publica en `bookstack.export.events` (stream `EXPORT`) `export.requested` al encolar, y export-worker publica `export.completed` o `export.failed`, con el sobre común (ver `cna/contracts/asyncapi/events.yaml`).
