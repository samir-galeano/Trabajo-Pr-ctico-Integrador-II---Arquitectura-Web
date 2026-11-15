# Despliegue en Docker Swarm (B5)

Decisión y justificación en [ADR-011](../../docs/adr/ADR-011.md). Para desarrollo o un solo nodo sigue funcionando `docker compose up -d` desde la raíz.

## 1. Formar el clúster (una sola vez)

Con dos VM, por ejemplo VM-2 (`cna-node1`, manager) y VM-3 (`cna-node2`, worker), ambas con Docker Engine.

**Puertos de Swarm entre los nodos** (en cada nodo, permitiendo solo la IP del otro):

```bash
sudo ufw allow from <IP-otro-nodo> to any port 2377 proto tcp   # gestión del clúster
sudo ufw allow from <IP-otro-nodo> to any port 7946             # comunicación entre nodos (tcp y udp)
sudo ufw allow from <IP-otro-nodo> to any port 4789 proto udp   # red overlay (VXLAN)
```

**En VM-2:**

```bash
docker swarm init --advertise-addr <IP-VM-2>
# imprime: docker swarm join --token SWMTKN-... <IP-VM-2>:2377
```

**En VM-3:** pegar el `docker swarm join ...` que imprimió el paso anterior.

**De nuevo en VM-2: etiquetar los nodos**

```bash
docker node update --label-add cna.data=true          cna-node1   # bases, NATS, MinIO, Redis
docker node update --label-add cna.observability=true cna-node2   # Prometheus, Loki, Grafana, Jaeger
docker node ls                                                     # F6-E1: los dos nodos Ready
```

Si no se etiqueta ningún nodo, `deploy.sh` pone las dos etiquetas en el manager, y todo lo que tiene restricción corre ahí.

## 2. Desplegar (un comando)

En VM-2, desde la raíz del repo:

```bash
./cna/deploy/swarm/deploy.sh
docker stack services cna        # F6-E16: todos N/N (nats-init y minio-init quedan 0/1 "Complete")
```

El script genera los `.env` que falten, levanta el registry del clúster, construye y sube las imágenes con la etiqueta `2.0.0-<commit>`, crea el secreto del token, despliega y espera a que todo converja. Es idempotente: volver a correrlo actualiza solo lo que cambió (actualización rodante `start-first`, sin corte).

Antes de la primera ejecución, completar `BOOKSTACK_TOKEN_ID` y `BOOKSTACK_TOKEN_SECRET` en `cna/services/search-svc/.env` y `BOOKSTACK_HOST_IP` en `.env`. El token se copia una vez al secreto `bookstack_token_v1`.

## 3. Operación

| Tarea | Comando |
|---|---|
| Estado | `docker stack services cna` · `docker stack ps cna` |
| Dónde corre cada réplica | `docker service ps cna_export-worker` |
| Escalar workers (C2) | `docker service scale cna_export-worker=3` (`deploy.sh` conserva la escala) |
| Logs de un servicio | `docker service logs -f cna_search-svc` (o Grafana → Loki) |
| Rotar el token (F7-E14) | `./cna/security/rotate-bookstack-token.sh` |
| Quitar el stack | `docker stack rm cna` (los volúmenes quedan) |

Los puertos publicados (8000–8004, 9000/9001, 3000, 9090, 3100, 16686) responden en **cualquier** nodo por la routing mesh. El gateway de VM-1 puede seguir apuntando a la IP de VM-2.

## 4. Pasar de Compose a Swarm en VM-2

```bash
docker compose down            # baja los contenedores; los volúmenes quedan
./cna/deploy/swarm/deploy.sh   # reutiliza search-index-data, audit-db-data, nats-data, minio-data, export-redis-data
```

Prometheus, Loki y Grafana empiezan con volúmenes nuevos (`cna_*`): el historial de métricas y logs de Compose no se traslada.

## Experimentos en Swarm

Los scripts de `cna/chaos`, `cna/perf` y `cna/security` detectan Swarm solos. Diferencias:

- **X1:** en Swarm también sirve `docker kill`, porque el orquestador crea una tarea nueva (prueba local: unos 12 s). El script mata el proceso, que vale para los dos modos. Debe correrse en el nodo donde está search-svc: `docker service ps cna_search-svc`.
- **X3:** si el job lo toma un worker de otro nodo, el script indica en qué nodo volver a correrlo.
- **X4:** apagar el nodo **worker** demuestra la redistribución: sus réplicas de export-worker se reprograman en el manager. Apagar el nodo `cna.data` deja sin servicio a los microservicios hasta que vuelve (sus volúmenes son locales), pero el monolito sigue respondiendo.
- **X5:** correrlo en el nodo donde está search-svc (la latencia de `tc` es por nodo).
