
# Diagramas de Secuencia - BookStack Cloud Native

Los siguientes diagramas representan las tres interacciones obligatorias definidas en la sección 3.4 del TP Integrador II. Cada interacción incluye su flujo normal y sus caminos de error.

---

# 1. Edición e indexación asíncrona

## Descripción

Cuando un usuario edita una página en BookStack, el monolito dispara el Webhook `page_update`. El Event Bridge publica el evento `bookstack.page.updated` en NATS JetStream. Search Service (S1) consume el evento, consulta la API REST de BookStack y actualiza el índice. Audit Service (S2) consume el mismo evento y registra la auditoría.

```mermaid
sequenceDiagram
    autonumber
    actor Usuario
    participant BS as BookStack
    participant EB as Event Bridge
    participant NATS as NATS JetStream
    participant S1 as Search Service
    participant DB1 as Search DB
    participant S2 as Audit Service
    participant DB2 as Audit DB

    Usuario->>BS: Editar página
    BS->>EB: Webhook page_update
    EB->>EB: Validar y transformar evento
    EB->>NATS: Publicar bookstack.page.updated
    NATS-->>EB: Confirmación de publicación

    par Indexación
        NATS-->>S1: Entregar evento
        S1->>BS: GET /api/pages/{id}
        BS-->>S1: Datos actualizados de página
        S1->>DB1: Actualizar índice
        DB1-->>S1: Confirmar actualización
    and Auditoría
        NATS-->>S2: Entregar evento
        S2->>DB2: Registrar actividad y request_id
        DB2-->>S2: Confirmar registro
    end

    Note over S1,S2: Ambos consumidores procesan el evento independientemente.

    alt Webhook inválido
        EB-->>BS: Rechazar evento / registrar error
    else BookStack API no disponible
        S1->>S1: Reintentar con backoff exponencial
        S1->>NATS: Solicitar redelivery
        Note over S1,NATS: Si se agotan los reintentos, enviar a DLQ
    else Error al actualizar índice
        DB1-->>S1: Error de persistencia
        S1->>NATS: Reintentar procesamiento
    else Error al guardar auditoría
        DB2-->>S2: Error de persistencia
        S2->>NATS: Reintentar procesamiento
    end
```

---

# 2. Búsqueda síncrona

## Descripción

El cliente realiza una búsqueda a través del gateway HTTPS. El gateway dirige la solicitud al Search Service (S1), que consulta su almacén de búsqueda y devuelve resultados JSON con enlaces a las páginas originales de BookStack.

```mermaid
sequenceDiagram
    autonumber
    actor Cliente
    participant GW as Nginx API Gateway
    participant S1 as Search Service
    participant DB as Search DB

    Cliente->>GW: GET /v2/search?q=nginx
    Note over GW: HTTPS TLS 1.3
    GW->>GW: Validar solicitud y propagar X-Request-ID
    GW->>S1: GET /v2/search?q=nginx
    S1->>DB: Consultar índice
    DB-->>S1: Resultados encontrados
    S1->>S1: Construir respuesta JSON con enlaces
    S1-->>GW: HTTP 200 + resultados
    GW-->>Cliente: HTTP 200 + resultados JSON

    alt Error de autenticación
        GW-->>Cliente: HTTP 401 Unauthorized
    else Search Service no disponible
        GW-->>Cliente: HTTP 503 Service Unavailable
    else Error en la base de datos
        DB-->>S1: Error de consulta
        S1-->>GW: Error interno
        GW-->>Cliente: HTTP 503 Service Unavailable
    else Sin resultados
        DB-->>S1: Lista vacía
        S1-->>GW: HTTP 200 + results []
        GW-->>Cliente: HTTP 200 + resultados vacíos
    end
```

---

# 3. Exportación asíncrona mediante trabajos

## Descripción

El cliente solicita una exportación mediante `POST /v2/exports`. S3 devuelve `202 Accepted` y un `job_id`. Un worker independiente toma el trabajo, solicita la exportación a BookStack mediante su API REST, guarda el archivo en MinIO y actualiza el estado del trabajo. El cliente consulta el resultado mediante `GET /v2/exports/{job_id}`.

```mermaid
sequenceDiagram
    autonumber
    actor Cliente
    participant GW as Nginx API Gateway
    participant S3 as Export Service API
    participant Redis as Redis Jobs
    participant Worker as Export Worker
    participant BS as BookStack API
    participant MinIO as MinIO Storage
    participant NATS as NATS JetStream

    Cliente->>GW: POST /v2/exports {page_id, format}
    GW->>S3: Solicitar exportación
    S3->>Redis: Crear job queued
    Redis-->>S3: Confirmar job_id
    S3-->>GW: HTTP 202 Accepted + job_id
    GW-->>Cliente: HTTP 202 Accepted + job_id

    Worker->>Redis: Obtener trabajo pendiente
    Redis-->>Worker: Entregar job
    Worker->>Redis: Cambiar estado a running
    Worker->>BS: Solicitar exportación por API REST
    BS-->>Worker: Entregar contenido exportado
    Worker->>MinIO: Guardar archivo
    MinIO-->>Worker: Confirmar objeto almacenado
    Worker->>Redis: Cambiar estado a done
    Redis-->>Worker: Confirmar actualización
    Worker->>NATS: Publicar export.completed
    NATS-->>Worker: Confirmación

    Cliente->>GW: GET /v2/exports/{job_id}
    GW->>S3: Consultar estado del job
    S3->>Redis: Obtener estado
    Redis-->>S3: Estado done + ubicación del archivo
    S3->>MinIO: Generar URL prefirmada
    MinIO-->>S3: URL prefirmada
    S3-->>GW: HTTP 200 + estado + URL
    GW-->>Cliente: HTTP 200 + resultado

    alt Error al consultar BookStack
        BS-->>Worker: Error de API
        Worker->>Redis: Reintentar trabajo
        Note over Worker,Redis: Al agotar reintentos: estado failed
        Worker->>NATS: Publicar fallo / enviar a DLQ según política
    else Error al guardar en MinIO
        MinIO-->>Worker: Error de almacenamiento
        Worker->>Redis: Registrar fallo y reintentar
    else Worker interrumpido
        Note over Worker,Redis: El trabajo pendiente puede ser recuperado por otro worker
        Worker->>Redis: Recuperar job mediante política de reintentos
    end
```

---

# Consideraciones generales

- Las tres interacciones siguen los flujos obligatorios de la sección 3.4.
- La edición e indexación es asíncrona y utiliza Webhooks y NATS JetStream.
- La búsqueda es síncrona y pasa por el API Gateway.
- La exportación utiliza trabajos independientes y responde inicialmente con HTTP 202.
- Los servicios propagan `X-Request-ID` y el campo `request_id` de los eventos.
- Los consumidores implementan idempotencia mediante `event_id`.
- Los mensajes fallidos contemplan reintentos y Dead Letter Queue (DLQ).
- Los microservicios no acceden directamente a MariaDB de BookStack.
