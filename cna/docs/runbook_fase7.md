# Runbook Fase 7: experimentos, mediciones y evidencias F7-E1 a F7-E14

Cada script deja un log en `cna/docs/evidencias/fase7/` con marcas de tiempo y, en la primera línea, `usuario@host:directorio$ comando` (el requisito de mostrar en qué nodo se ejecutó). Además de ese log, sacar la captura de pantalla indicada en cada caso.

Los scripts funcionan igual con Docker Compose y con el stack de Swarm (`cna/deploy/swarm/`): detectan el modo solos. En Swarm, X1, X3 y X5 deben correrse en el nodo donde está la tarea (`docker service ps cna_<servicio>`); si no, el script indica el nodo.

## Antes de empezar

- Stack desplegado en VM-2 con todos los servicios `healthy` (`docker compose ps`).
- VM-2 resuelve los nombres del gateway, en `/etc/hosts`: `192.168.100.23 bookstack.local api.bookstack.local`.
- Grafana abierto en el tablero **BookStack CNA - RED, colas y logs** (`http://<VM-2>:3000`) y Prometheus en **Alerts** (`http://<VM-2>:9090/alerts`).
- Todos los comandos se ejecutan desde la raíz del repositorio (`~/tp2`).

## C1: experimentos de caos (F7-E1 a F7-E5)

Para cada experimento, el informe debe incluir hipótesis, procedimiento, resultado esperado, resultado obtenido y tiempo de recuperación medido. Los cuatro primeros datos vienen en la cabecera de cada script y en su log.

### X1 (F7-E1): caída de search-svc y MTTR

```bash
./cna/chaos/x1-kill-search.sh
```

- **Qué hace:** mata el proceso de search-svc con `kill -9` y sondea cada 0,5 s el monolito y `/v2/search` por el gateway.
- **Esperado:** el monolito responde 200 siempre; `/v2/search` responde 503 JSON del gateway; Docker reinicia el contenedor solo; el MTTR queda medido.
- **Captura:** el log del script, más el panel "Servicios (up)" de Grafana con search-svc en CAÍDO y luego en OK.
- **Ojo:** con Docker Compose no usar `docker kill`: cuenta como parada manual y el contenedor **no** se reinicia (verificado). Con `kill -9` sobre el proceso, el MTTR en una prueba local fue de unos 7 s. En Swarm, `docker kill` también sirve: el orquestador crea una tarea nueva (unos 12–13 s en una prueba local, incluido el healthcheck).

### X2 (F7-E2): audit-svc detenido, cero pérdida de eventos

```bash
EDITOR_TOKEN=<id>:<secreto> BOOK_ID=<id> ./cna/chaos/x2-stop-audit.sh
```

- **Qué hace:** crea 20 páginas de prueba, detiene audit-svc, las edita por la API, espera 2 minutos, lo vuelve a iniciar y compara los eventos pendientes con los registros nuevos. Al final borra las páginas de prueba.
- **Requisitos:** `EDITOR_TOKEN` es un token de un usuario con permiso de edición, **no** el token de servicio (que es de solo lectura). Sin `EDITOR_TOKEN`, el script pide editar 20 páginas a mano.
- **Esperado:** "Cero pérdida de eventos: OK", con 20 publicados y 20 registrados.
- **Captura:** el log, más el panel "JetStream: eventos pendientes" subiendo a 20 y volviendo a 0. La alerta `ConsumerBacklog` debería dispararse.

### X3 (F7-E3): matar un worker a mitad de un job

```bash
PAGE_ID=<id-página-existente> ./cna/chaos/x3-kill-worker.sh
```

- **Qué hace:** levanta 2 workers con una demora artificial de 30 s por job, crea un job, mata al worker que lo tiene y sigue el estado del job.
- **Esperado:** `running attempts=1` → (vence el lease, unos 15–20 s) → `running attempts=2` en el otro worker → `done`. El job nunca queda en `running` indefinidamente. Al terminar, el script vuelve a dejar 1 worker sin demora.
- **Captura:** el log, más la traza `export job` en Jaeger (servicio `export-worker`), que muestra los dos intentos.

### X4 (F7-E4): apagar VM-2

En **VM-1** (o en el anfitrión):

```bash
./cna/chaos/x4-vm2-down.sh            # opcional: VM2_SSH=usuario@ip-vm2
```

Cuando el script lo indique, apagar VM-2 desde VMware (Power Off) y volver a encenderla.

- **Esperado:** el monolito no falla nunca (CA-5); `/v2/search` responde 503 mientras VM-2 está apagada; al encender VM-2, todo vuelve solo y queda estable 30 s.
- **Requisito:** Docker habilitado al inicio en VM-2 (`sudo systemctl is-enabled docker` debe responder `enabled`).
- **Captura:** el log, más `docker compose ps` en VM-2 después del arranque.

### X5 (F7-E5): latencia de 2 s hacia BookStack

```bash
./cna/chaos/x5-bookstack-latency.sh
```

- **Qué hace:** agrega 2 s de latencia con `tc netem` **solo** al tráfico de VM-2 hacia VM-1:443. Durante 90 s llama a `POST /sync` y sondea `/search`; después quita la latencia y espera a que el circuito se cierre.
- **Esperado:** las primeras llamadas a `/sync` fallan por timeout después de los reintentos con backoff; luego el circuito pasa a ABIERTO y `/sync` responde 503 al instante (`Retry-After`); `/search` sigue respondiendo en milisegundos; al quitar la latencia, el circuito pasa a semiabierto y se cierra.
- **Captura:** el log, el panel "search-svc → API de BookStack" y la alerta `BookStackCircuitOpen` disparada (también vale como **F7-E12**).

## C2: escalabilidad y rendimiento (F7-E6 a F7-E8)

### F7-E6: k6, monolito vs. search-svc (c=10 y c=50)

Desde el anfitrión, mientras en VM-1 y VM-2 corre `./cna/perf/capture-stats.sh 360`:

```bash
VM1_IP=192.168.100.23 ./cna/perf/run-k6.sh
# si la búsqueda del monolito exige sesión:
BOOKSTACK_EMAIL=... BOOKSTACK_PASSWORD=... VM1_IP=192.168.100.23 ./cna/perf/run-k6.sh
```

Genera `comparativa.md` con la tabla contra la línea base de la Fase 4 (~50 req/s, 0 errores).

Para interpretar el resultado:
- Los saltos de red adicionales (gateway → VM-2) suben la latencia del servicio frente a la línea base, y eso también es un hallazgo válido.
- Con c=50, el `limit_req` del gateway devuelve 429 (columna aparte). Es la protección funcionando, no un fallo del servicio.

### F7-E7: escalar export-worker de 1 a 3

En VM-2:

```bash
./cna/perf/export-burst.sh 1
./cna/perf/export-burst.sh 3
```

Cada corrida agrega una fila a `F7-E7-export-tabla.md` (jobs/min). Si BookStack exporta tan rápido que no se nota la diferencia, repetir con `DELAY=2` en las dos corridas, para que domine el trabajo de los workers.

### F7-E8: consumo por nodo

`./cna/perf/capture-stats.sh <segundos>` en VM-1 y en VM-2 durante las pruebas anteriores. Deja un CSV de `docker stats` y un log con carga y memoria. Sacar además una captura de `htop` en el pico de la prueba.

## C3: observabilidad (F7-E9 a F7-E12)

| Evidencia | Dónde |
|---|---|
| **F7-E9** tablero RED | Grafana → "BookStack CNA - RED, colas y logs", con tráfico (por ejemplo, durante F7-E6) |
| **F7-E10** logs por `request_id` | Editar una página; tomar el `request_id` del log de event-bridge; en el tablero, campo `request_id`, o en Explore: `{service=~".+"} \| request_id="<id>"`. Deben aparecer gateway, event-bridge, search-svc y audit-svc |
| **F7-E11** traza | Jaeger → servicio `event-bridge`, tag `request_id=<id>`: webhook → publish → search-svc (GET a BookStack + INSERT) y audit-svc |
| **F7-E12** alerta disparada | Prometheus → Alerts durante X5 (`BookStackCircuitOpen`), X2 (`ConsumerBacklog`) o X1 (`ServiceDown`) |

## C4: seguridad (F7-E13, F7-E14)

### F7-E13: Trivy + SBOM

En VM-2, con las imágenes construidas:

```bash
./cna/security/scan-images.sh
```

Genera `cna/security/reports/` (Trivy por imagen y `resumen.md`) y `cna/security/sbom/` (CycloneDX). Las imágenes propias deberían dar 0 CRITICAL; el tratamiento de lo restante está en `cna/security/hallazgos.md`.

### F7-E14: rotación del token sin cortar el servicio

1. En BookStack, en el usuario de servicio → API Tokens, crear un token **nuevo** sin borrar el anterior.
2. En VM-2: `./cna/security/rotate-bookstack-token.sh` (pide el ID y el secreto nuevos).
3. El script valida el token contra BookStack, lo escribe en `cna/deploy/secrets/token`, comprueba que search-svc y export-svc lo recargaron **sin reiniciarse** y muestra cuántas peticiones fallaron durante la rotación (esperado: 0).
4. Borrar el token **viejo** en BookStack, editar una página y verificar que aparece en `/v2/search`.

## C5: matriz de pruebas P49 a P62

Continúa la Tabla 5 del TP I. Completar la columna "Resultado obtenido" con los valores de cada log.

| Prueba | Evidencia | Ítem | Descripción | Procedimiento | Resultado esperado | Resultado obtenido |
|---|---|---|---|---|---|---|
| P49 | F7-E1 | C1 | X1: caída de S1 y MTTR | `x1-kill-search.sh` | Monolito 200; `/v2/search` 503 JSON; reinicio automático; MTTR medido | |
| P50 | F7-E2 | C1 | X2: cero pérdida de eventos en S2 | `x2-stop-audit.sh` | 20 eventos publicados = 20 registrados | |
| P51 | F7-E3 | C1 | X3: reintento de job tras caída de worker | `x3-kill-worker.sh` | Job `done` con `attempts=2`; nunca queda en `running` | |
| P52 | F7-E4 | C1 | X4: apagado de VM-2 y convergencia | `x4-vm2-down.sh` | 0 fallos del monolito; convergencia sin intervención | |
| P53 | F7-E5 | C1 | X5: timeout, backoff e interruptor de circuito | `x5-bookstack-latency.sh` | Circuito abierto → 503 inmediato; `/search` rápido; se cierra al quitar la latencia | |
| P54 | F7-E6 | C2 | k6: monolito vs. S1 | `run-k6.sh` | Tabla con c=10 y c=50 contra la línea base | |
| P55 | F7-E7 | C2 | Escalado de export-worker 1 → 3 | `export-burst.sh 1` y `3` | Más jobs/min con 3 workers | |
| P56 | F7-E8 | C2 | Consumo de recursos por nodo | `capture-stats.sh` + htop | Consumo por contenedor bajo los límites declarados | |
| P57 | F7-E9 | C3 | Tablero RED en Grafana | Captura del tablero | Tasa, errores y p50/p95/p99 por servicio, y colas | |
| P58 | F7-E10 | C3 | Logs correlacionados por `request_id` | Consulta en Loki | Gateway, event-bridge, S1 y S2 con el mismo `request_id` | |
| P59 | F7-E11 | C3 | Traza distribuida en Jaeger | Búsqueda por `request_id` | Tramos de event-bridge, S1 y S2 con su duración | |
| P60 | F7-E12 | C3 | Alerta disparada | Prometheus → Alerts durante X5 o X2 | Alerta en estado FIRING | |
| P61 | F7-E13 | C4 | Informe Trivy y SBOM | `scan-images.sh` | 0 CRITICAL o justificadas; un SBOM por imagen | |
| P62 | F7-E14 | C4 | Rotación del token de API | `rotate-bookstack-token.sh` | Sin reinicios y 0 peticiones fallidas | |

## Demostración en vivo (15 min, C5)

1. `git clone` + `./cna/deploy/setup-env.sh` + `docker compose up -d --build` → `docker compose ps`, todos `healthy`.
2. Editar una página en BookStack → buscarla en `https://api.bookstack.local/v2/search?q=...` (menos de 5 s).
3. Mostrar la traza de esa edición en Jaeger y sus logs por `request_id` en Grafana.
4. `./cna/chaos/x1-kill-search.sh` con el tablero de Grafana a la vista: search-svc cae y se recupera solo.
