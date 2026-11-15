# Observabilidad (C3)

Se levanta junto con el resto con `docker compose up -d` (el `compose.yaml` raíz incluye `cna/observability/compose.yaml`).

| Componente | Imagen | Puerto | Para qué |
|---|---|---|---|
| Prometheus | `prom/prometheus:v3.5.0` | 9090 | Métricas, reglas RED y alertas |
| nats-exporter | `natsio/prometheus-nats-exporter:0.17.3` | interno | Profundidad de streams y consumidores de JetStream |
| Loki | `grafana/loki:3.5.5` | 3100 | Logs (también recibe el log del gateway desde VM-1) |
| Alloy | `grafana/alloy:v1.11.0` | interno | Envía el stdout de los contenedores a Loki |
| Jaeger | `jaegertracing/jaeger:2.10.0` | 16686 | Trazas (recibe OTLP en 4318 por la red interna) |
| Grafana | `grafana/grafana:12.2.0` | 3000 | Tablero `BookStack CNA - RED, colas y logs` |

La contraseña de Grafana (`admin`) está en `cna/observability/grafana.env`, que genera `./cna/deploy/setup-env.sh`.

UFW en VM-2: 3000, 9090 y 16686 solo desde la red del laboratorio o el anfitrión, y 3100 solo desde 192.168.100.23 (VM-1).

## Métricas

Cada servicio expone `/metrics` y Prometheus los encuentra por nombre DNS en `cna-net`. Los `export-worker` se descubren por DNS (`dns_sd_configs`), así que al escalar a 3 réplicas aparecen solos.

Como los servicios nombran distinto sus métricas HTTP, `prometheus/rules/recording.yml` las normaliza en estas series (sin `/metrics`, `/healthz` ni `/readyz`):

| Serie | Qué es |
|---|---|
| `job:http_requests:rate1m` | Peticiones por segundo por servicio |
| `job:http_errors:ratio1m` | Proporción de 5xx |
| `job:http_request_duration_seconds:p50`, `:p95`, `:p99` | Percentiles de latencia |

Profundidad de colas: `jetstream_consumer_num_pending` (por consumidor), `jetstream_stream_total_messages{stream_name="BOOKSTACK_DLQ"}` y `export_queue_depth{queue="queued|processing"}`.

## Alertas (`prometheus/rules/alerts.yml`)

| Alerta | Condición | Experimento |
|---|---|---|
| `ServiceDown` | `up == 0` durante 15 s | X1, X4 |
| `HighErrorRate` | 5xx > 5 % durante 1 min | X1 (a través del gateway) |
| `DeadLetterMessages` | llegaron mensajes a la DLQ en los últimos 5 min | F6-E13 |
| `ConsumerBacklog` | más de 10 eventos pendientes durante 1 min | X2 |
| `BookStackCircuitOpen` | circuito de search-svc abierto | X5 |
| `ExportJobsStuck` | jobs en `processing` sin que ningún worker termine en 5 min | X3 |

Se ven en Prometheus → Alerts y en el panel "Alertas disparadas" del tablero.

## Logs

Alloy lee el stdout de cada contenedor, toma `level` como etiqueta y `request_id` como metadato estructurado. En Grafana → Explore → Loki:

```logql
{service=~".+"} | request_id="<request_id>"
```

El tablero tiene un panel de logs con un campo `request_id`. Para que también aparezca el gateway:

1. En VM-1, copiar `nginx/log_format.conf` a `/etc/nginx/conf.d/` y en el server block de `api.bookstack.local` usar `access_log /var/log/nginx/api.bookstack.local.access.log json_gateway;` y `proxy_set_header X-Request-ID $request_id;`.
2. Instalar Alloy en VM-1 y correr `alloy/vm1-gateway.alloy` con `LOKI_URL=http://<IP-VM-2>:3100/loki/api/v1/push`.

## Trazas

Los servicios arrancan con `opentelemetry-instrument`, que instrumenta FastAPI, httpx, psycopg2, Redis y boto3 sin cambiar el código. El contexto viaja:

- por NATS en las cabeceras del mensaje (`traceparent`): event-bridge → search-svc y audit-svc;
- por Redis en el hash del job: export-svc → export-worker.

La traza de una edición en Jaeger (servicio `event-bridge`, tag `request_id=<id>`) muestra: `POST /webhooks/bookstack/{token}` → `publish bookstack.content.events` → `process` en search-svc (con los `GET` a la API de BookStack y el `INSERT` en PostgreSQL) y `process` en audit-svc (con su `INSERT`).

El token del webhook se redacta: no aparece en las trazas (`http.target = /webhooks/bookstack/[REDACTED]`), ni en los logs, ni en las etiquetas de Prometheus.

## Tablero

`grafana/dashboards/cna-red.json` se genera con `grafana/generate_dashboard.py`. Para cambiarlo, editar el script y volver a generarlo, porque Grafana lo carga como solo lectura.
