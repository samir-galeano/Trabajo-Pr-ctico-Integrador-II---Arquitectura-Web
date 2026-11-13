

# F5-E2 - Matriz de acoplamiento módulo × tabla



## 1. Objetivo



Identificar las dependencias entre los seis módulos funcionales de BookStack y las tablas de su base de datos MariaDB.



El objetivo es reconocer las tablas compartidas que generan acoplamiento entre módulos y que deben considerarse durante la extracción progresiva hacia microservicios.



## 2. Módulos analizados



Se consideran los siguientes seis módulos funcionales:



1. Content

2. Search

3. Activity

4. Export

5. Images/Attachments

6. Users/Permissions



## 3. Criterio de clasificación



La marca X indica que una tabla participa en las operaciones o relaciones funcionales identificadas para el módulo.



La ausencia de una marca indica que no se identificó una dependencia funcional suficientemente justificada en el análisis realizado.



Se distinguen las tablas funcionales de las tablas de infraestructura, configuración y soporte interno de BookStack.



## 4. Matriz de acoplamiento



| Tabla | Content | Search | Activity | Export | Media | Users/Permissions |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| activities | | | X | | | |
| api_tokens | | | | | | X |
| attachments | X | | | X | X | |
| bookshelves_books | X | | | X | | |
| cache | | | | | | |
| comments | X | | | | | |
| deletions | X | | | | | |
| email_confirmations | | | | | | X |
| entities | X | X | X | X | X | X |
| entity_container_data | X | X | | X | | |
| entity_page_data | X | X | | X | | |
| entity_permissions | X | | | | | X |
| failed_jobs | | | | | | |
| favourites | X | | | | | X |
| images | X | | | X | X | |
| imports | X | | | | | |
| jobs | | | | | | |
| joint_permissions | X | | | | X | X |
| mention_history | X | | | | | X |
| mfa_values | | | | | | X |
| migrations | | | | | | |
| page_revisions | X | | | X | | |
| password_resets | | | | | | X |
| permission_role | | | | | | X |
| references | X | | | | | |
| roles | | | | | | X |
| role_permissions | | | | | | X |
| role_user | | | | | | X |
| search_terms | | X | | | | |
| sessions | | | | | | X |
| settings | | | | | | |
| slug_history | X | | | | | |
| social_accounts | | | | | | X |
| sort_rules | X | | | | | |
| tags | X | X | | X | | |
| users | X | | X | | X | X |
| user_invites | | | | | | X |
| views | X | | | | | X |
| watches | X | | | | | X |
| webhooks | | | | | | |
| webhook_tracked_events | | | | | | |



**Aclaración:** las marcas representan dependencias funcionales identificadas a partir del esquema y las relaciones del código fuente. No significan necesariamente que cada módulo ejecute operaciones de escritura sobre todas las tablas marcadas.



Las tablas sin marcas corresponden a infraestructura, configuración o funcionalidades auxiliares que no se asignaron directamente a los seis módulos analizados.



## 5. Tablas compartidas y acoplamiento



### 5.1 entities



Es una tabla central del modelo de entidades de BookStack.



Se relaciona con diferentes tipos de contenido, como estanterías, libros, capítulos y páginas. También participa en relaciones polimórficas utilizadas por actividades, etiquetas, comentarios, permisos y otras funcionalidades.



Su dependencia transversal dificulta la extracción independiente de los módulos.



### 5.2 users



Almacena la información de los usuarios y participa en diferentes funcionalidades:



- Identificación del usuario que realiza una actividad.

- Propiedad y administración del contenido.

- Asociación con roles y permisos.

- Creación y administración de recursos.

- Seguimiento de recursos y preferencias.



La tabla representa una dependencia compartida entre distintos contextos.



### 5.3 tags



Almacena etiquetas asociadas a entidades mediante una relación polimórfica.



Es utilizada por las funcionalidades de contenido, búsqueda y exportación.



### 5.4 images y attachments



Almacenan información sobre imágenes y archivos adjuntos asociados al contenido.



Participan en la administración de recursos multimedia y en los procesos de exportación.



La extracción del módulo Media debe contemplar las referencias a los archivos físicos y las relaciones con las entidades de BookStack.



### 5.5 entity_permissions y joint_permissions



Estas tablas representan mecanismos de autorización sobre recursos.



Su utilización debe considerarse al separar las funcionalidades de contenido y multimedia del módulo de usuarios y permisos.



## 6. Relaciones polimórficas



BookStack utiliza relaciones polimórficas mediante columnas que identifican el tipo y el identificador del recurso relacionado.



Algunos ejemplos identificados en el esquema:



| Tabla | Columnas | Función |
|---|---|---|
| activities | loggable_id, loggable_type | Relaciona actividades con distintos recursos. |
| comments | commentable_id, commentable_type | Relaciona comentarios con distintos recursos. |
| tags | entity_id, entity_type | Asocia etiquetas con entidades. |
| views | viewable_id, viewable_type | Registra visualizaciones de recursos. |
| favourites | favouritable_id, favouritable_type | Asocia favoritos con recursos. |
| watches | watchable_id, watchable_type | Asocia seguimientos con recursos. |
| references | from_id, from_type, to_id, to_type | Representa referencias entre recursos. |
| joint_permissions | entity_id, entity_type | Asocia permisos compartidos con entidades. |



Estas relaciones incrementan el acoplamiento porque diferentes funcionalidades pueden depender de una misma estructura de entidades.



## 7. Consideraciones para la extracción



- No se deben extraer tablas compartidas sin analizar previamente sus dependencias.

- Los microservicios no deben consultar directamente la base de datos MariaDB de BookStack.

- La integración con BookStack debe realizarse mediante REST API y Webhooks.

- Cada microservicio deberá administrar sus propios datos.

- Las relaciones entre contextos deberán resolverse mediante contratos e integración por eventos cuando corresponda.

- Se deberá contemplar la consistencia eventual, la idempotencia y la recuperación ante fallos.

- Las tablas de infraestructura y configuración deben analizarse por separado antes de asignarlas a un microservicio.



## 8. Limitaciones del análisis



La matriz se elaboró a partir de la inspección del esquema de MariaDB y de las relaciones entre modelos Eloquent de BookStack.



Las marcas representan dependencias funcionales identificadas, no un registro exhaustivo de todas las consultas SQL ejecutadas en tiempo de ejecución.



Las dependencias indirectas deberán validarse durante la implementación y las pruebas de extracción.



## 9. Evidencias



- F5-E1: Inventario y mapeo de tablas de la base de datos BookStack por módulo.

- F5-E2: Matriz de acoplamiento módulo × tabla.



