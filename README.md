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
