

\# F5-E3 - Mapa de contextos DDD de BookStack



\## 1. Objetivo



Identificar los contextos delimitados (Bounded Contexts) de BookStack y sus relaciones, como base para una futura separación progresiva hacia una arquitectura de microservicios.



La estrategia propuesta es Strangler Fig: BookStack continuará funcionando como sistema principal mientras se extraen funcionalidades progresivamente.



\## 2. Contextos delimitados



\### 2.1 Content Context

Responsabilidad:

\- Administrar estanterías, libros, capítulos y páginas.

\- Gestionar revisiones y estructura del contenido.

\- Mantener las relaciones entre las entidades.



Tablas principales:

\- entities

\- entity\_container\_data

\- entity\_page\_data

\- page\_revisions

\- bookshelves\_books



\### 2.2 Search Context

Responsabilidad:

\- Indexar contenido.

\- Procesar términos de búsqueda.

\- Permitir búsquedas por texto y etiquetas.



Tablas principales:

\- search\_terms

\- tags

\- entities



Microservicio propuesto: Search.



\### 2.3 Activity Context

Responsabilidad:

\- Registrar actividad de usuarios.

\- Registrar visualizaciones y comentarios.

\- Gestionar favoritos y seguimiento de entidades.



Tablas principales:

\- activities

\- views

\- comments

\- favourites

\- watches

\- deletions



Microservicio propuesto: Audit.



\### 2.4 Export Context

Responsabilidad:

\- Exportar libros, capítulos y páginas.

\- Incluir etiquetas, imágenes y archivos adjuntos.

\- Generar paquetes de exportación.



Tablas principales:

\- imports

\- entities

\- tags

\- images

\- attachments



Microservicio propuesto: Export.



\### 2.5 Media Context

Responsabilidad:

\- Administrar imágenes.

\- Administrar archivos adjuntos.

\- Gestionar referencias a archivos y su almacenamiento.



Tablas principales:

\- images

\- attachments



Microservicio propuesto: Media.



\### 2.6 Users and Permissions Context

Responsabilidad:

\- Administrar usuarios y roles.

\- Gestionar permisos globales y por entidad.

\- Controlar el acceso a funcionalidades y contenidos.



Tablas principales:

\- users

\- roles

\- role\_user

\- role\_permissions

\- permission\_role

\- entity\_permissions

\- joint\_permissions



\## 3. Relaciones entre contextos



\- Content utiliza información de Users and Permissions para determinar propiedad y acceso al contenido.

\- Search consume información de Content y etiquetas para construir el índice de búsqueda.

\- Activity registra acciones relacionadas con usuarios y entidades de Content.

\- Export consume contenido y referencias a Media para generar paquetes de exportación.

\- Media se relaciona con Content mediante referencias a páginas y entidades.

\- Users and Permissions proporciona información de identidad y autorización a los demás contextos.



\## 4. Estrategia de integración



La integración entre BookStack y los nuevos microservicios se realizará exclusivamente mediante REST API y Webhooks.



No se modificará el código fuente de BookStack.



Se utilizará un API Gateway como punto de entrada para las funcionalidades extraídas y un broker de mensajería para la comunicación asíncrona.



Los microservicios tendrán bases de datos propias y no accederán directamente a la base MariaDB de BookStack.



\## 5. Mapa de contextos



```mermaid

flowchart TB

&#x20;   U\[Usuarios]



&#x20;   subgraph BS\["BookStack - Sistema monolítico"]

&#x20;       C\[Content Context]

&#x20;       S\[Search Context]

&#x20;       A\[Activity Context]

&#x20;       E\[Export Context]

&#x20;       M\[Media Context]

&#x20;       P\[Users and Permissions Context]



&#x20;       P --> C

&#x20;       C --> S

&#x20;       C --> A

&#x20;       C --> E

&#x20;       M --> C

&#x20;       M --> E

&#x20;       P --> A

&#x20;   end



&#x20;   subgraph MS\["Microservicios propuestos"]

&#x20;       GW\[API Gateway]

&#x20;       SS\[Search Service]

&#x20;       AS\[Audit Service]

&#x20;       ES\[Export Service]

&#x20;       MSVC\[Media Service]

&#x20;       B\[Broker NATS JetStream]

&#x20;   end



&#x20;   U --> GW

&#x20;   GW --> SS

&#x20;   GW --> AS

&#x20;   GW --> ES

&#x20;   GW --> MSVC



&#x20;   C -->|REST API / Webhooks| GW

&#x20;   GW --> B

&#x20;   B --> SS

&#x20;   B --> AS

&#x20;   B --> ES

&#x20;   B --> MSVC

```



\## 6. Relaciones DDD y decisiones de integración



| Contexto origen | Contexto destino | Relación |

|---|---|---|

| Content | Search | Customer/Supplier |

| Content | Activity | Customer/Supplier |

| Content | Export | Customer/Supplier |

| Content | Media | Customer/Supplier |

| Users and Permissions | Content | Customer/Supplier |

| Users and Permissions | Activity | Customer/Supplier |



Se propone utilizar una capa anticorrupción (Anti-Corruption Layer) en las integraciones donde sea necesario adaptar el modelo de datos y los contratos de BookStack al modelo de los nuevos servicios.



\## 7. Consideraciones



\- Los contextos representan límites funcionales propuestos; no implican que actualmente existan como servicios independientes.

\- La extracción será incremental.

\- Las tablas compartidas identificadas en F5-E2 no serán compartidas directamente entre microservicios.

\- La sincronización de datos se realizará mediante contratos y eventos.

\- Se deberá contemplar consistencia eventual, idempotencia y mecanismos de recuperación.



\## 8. Evidencia



F5-E3: Mapa de contextos DDD de BookStack.



