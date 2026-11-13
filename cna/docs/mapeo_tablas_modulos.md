

\# F5-E1 – Mapeo de tablas por módulo de BookStack



\## 1. Objetivo



Identificar las tablas de la base de datos MariaDB utilizadas por los seis módulos funcionales de BookStack, analizar sus relaciones y reconocer las dependencias compartidas que pueden dificultar la extracción de funcionalidades hacia microservicios independientes.



\## 2. Inventario de la base de datos



La base de datos `bookstack` contiene 41 tablas.



El inventario se obtuvo mediante la consulta:



```sql

SELECT TABLE\_NAME, TABLE\_ROWS

FROM information\_schema.TABLES

WHERE TABLE\_SCHEMA = 'bookstack'

ORDER BY TABLE\_NAME;

```



La estructura de las tablas y sus columnas se obtuvo mediante `information\_schema.COLUMNS`.



Se identificaron también cuatro relaciones declaradas explícitamente mediante claves foráneas:



| Tabla | Columna | Tabla relacionada | Columna relacionada |

|---|---|---|---|

| permission\_role | permission\_id | role\_permissions | id |

| permission\_role | role\_id | roles | id |

| role\_user | role\_id | roles | id |

| role\_user | user\_id | users | id |



BookStack también utiliza relaciones gestionadas por Eloquent, incluidas relaciones polimórficas, que no necesariamente aparecen como claves foráneas en MariaDB.



\## 3. Mapeo funcional por módulo



\### 3.1 Content (Gestión de contenido)



Responsabilidad: administrar la estructura y el contenido de libros, estanterías, capítulos y páginas.



Tablas principales:



\- `entities`: información general de las entidades de contenido.

\- `entity\_container\_data`: datos específicos de contenedores.

\- `entity\_page\_data`: contenido y datos específicos de páginas.

\- `bookshelves\_books`: relación entre estanterías y libros.

\- `page\_revisions`: historial de revisiones de páginas.

\- `tags`: etiquetas asociadas a entidades.

\- `comments`: comentarios asociados a entidades.

\- `references`: referencias entre entidades.

\- `slug\_history`: historial de identificadores URL.

\- `sort\_rules`: reglas de ordenamiento.

\- `deletions`: registro de eliminaciones.



\### 3.2 Search (Búsqueda)



Responsabilidad: indexar y recuperar contenido para permitir búsquedas dentro de BookStack.



Tablas principales:



\- `search\_terms`: términos indexados y referencias a entidades.

\- `entities`: identificación de las entidades indexadas.

\- `entity\_container\_data`: información descriptiva de contenedores.

\- `entity\_page\_data`: contenido textual de páginas.

\- `tags`: etiquetas utilizadas en la búsqueda.



El módulo de búsqueda depende de información administrada por el módulo de contenido.



\### 3.3 Activity (Actividad)



Responsabilidad: registrar y consultar las actividades realizadas por los usuarios sobre los recursos de BookStack.



Tablas principales:



\- `activities`: registro de actividades.

\- `users`: identificación del usuario que realiza la actividad.

\- `entities`: entidades sobre las cuales pueden registrarse actividades.



La tabla `activities` utiliza los campos `loggable\_id` y `loggable\_type` para representar una relación polimórfica con diferentes tipos de recursos.



\### 3.4 Export (Exportación)



Responsabilidad: generar exportaciones de libros, capítulos, páginas y recursos asociados.



Tablas principales:



\- `entities`: identificación de los recursos.

\- `entity\_container\_data`: información de contenedores.

\- `entity\_page\_data`: contenido de páginas.

\- `page\_revisions`: historial de páginas.

\- `tags`: etiquetas asociadas.

\- `images`: imágenes utilizadas.

\- `attachments`: archivos adjuntos.



El proceso de exportación consulta información perteneciente a otros módulos, especialmente contenido y archivos multimedia.



\### 3.5 Images/Attachments (Imágenes y archivos adjuntos)



Responsabilidad: administrar imágenes y archivos asociados al contenido.



Tablas principales:



\- `images`: información y ubicación de imágenes.

\- `attachments`: información y ubicación de archivos adjuntos.

\- `entities`: identificación de recursos de contenido relacionados.

\- `joint\_permissions`: permisos compartidos asociados a recursos.



Las tablas `images` y `attachments` utilizan el campo `uploaded\_to` para identificar el recurso al que se asocian.



\### 3.6 Users/Permissions (Usuarios y permisos)



Responsabilidad: administrar usuarios, roles, autenticación y autorización de acceso a los recursos.



Tablas principales:



\- `users`: información de usuarios.

\- `roles`: definición de roles.

\- `role\_user`: asociación entre usuarios y roles.

\- `role\_permissions`: catálogo de permisos.

\- `permission\_role`: asociación entre roles y permisos.

\- `entity\_permissions`: permisos específicos sobre entidades.

\- `joint\_permissions`: permisos compartidos.

\- `api\_tokens`: tokens de acceso a la API.

\- `sessions`: sesiones de usuarios.

\- `mfa\_values`: configuración de autenticación multifactor.

\- `social\_accounts`: cuentas de autenticación social.

\- `user\_invites`: invitaciones de usuarios.

\- `email\_confirmations`: confirmaciones de correo.

\- `password\_resets`: solicitudes de recuperación de contraseña.



\## 4. Tablas compartidas y dependencias



Las siguientes tablas son relevantes para más de un módulo:



| Tabla | Módulos relacionados | Motivo |

|---|---|---|

| `entities` | Content, Search, Activity, Export, Images/Attachments, Users/Permissions | Identificación y asociación de recursos. |

| `users` | Activity, Content, Images/Attachments, Users/Permissions | Identificación de usuarios y responsables de operaciones. |

| `tags` | Content, Search, Export | Etiquetas asociadas al contenido. |

| `images` | Content, Export, Images/Attachments | Imágenes vinculadas a recursos y exportaciones. |

| `attachments` | Content, Export, Images/Attachments | Archivos asociados a páginas y exportaciones. |

| `joint\_permissions` | Images/Attachments, Users/Permissions | Permisos compartidos sobre recursos. |

| `entity\_permissions` | Content, Users/Permissions | Control de acceso a entidades. |



La tabla `entities` representa una dependencia especialmente importante porque centraliza la identificación de diferentes tipos de contenido.



\## 5. Relaciones polimórficas



BookStack utiliza relaciones polimórficas mediante pares de columnas que identifican el tipo y el identificador del recurso relacionado.



Algunos ejemplos identificados en el esquema:



| Tabla | Columnas | Función |

|---|---|---|

| `activities` | `loggable\_id`, `loggable\_type` | Relaciona actividades con distintos recursos. |

| `comments` | `commentable\_id`, `commentable\_type` | Relaciona comentarios con distintos recursos. |

| `tags` | `entity\_id`, `entity\_type` | Asocia etiquetas con entidades. |

| `views` | `viewable\_id`, `viewable\_type` | Registra visualizaciones de recursos. |

| `favourites` | `favouritable\_id`, `favouritable\_type` | Asocia favoritos con distintos recursos. |

| `watches` | `watchable\_id`, `watchable\_type` | Asocia seguimientos con recursos. |

| `references` | `from\_id`, `from\_type`, `to\_id`, `to\_type` | Representa referencias entre recursos. |



Estas relaciones incrementan el acoplamiento entre funcionalidades porque diferentes módulos pueden depender de una misma estructura de entidades.



\## 6. Observaciones del análisis



\- La base de datos contiene 41 tablas.

\- Se identificaron cuatro relaciones declaradas mediante claves foráneas.

\- Existen relaciones adicionales administradas por la capa de aplicación.

\- La tabla `entities` constituye un punto central de integración entre funcionalidades.

\- Las tablas compartidas representan dependencias que deben considerarse antes de extraer microservicios.

\- Las consultas realizadas muestran que las tablas `entities` y `tags` no contienen registros actualmente. Esto no impide analizar su estructura y relaciones.



\## 7. Conclusión



El análisis evidencia que BookStack presenta dependencias entre sus seis módulos funcionales mediante tablas compartidas y relaciones polimórficas.



La extracción directa de funcionalidades hacia microservicios independientes podría generar problemas de integridad y consistencia si varios servicios continúan accediendo y modificando las mismas tablas.



Por este motivo, la arquitectura propuesta contempla una integración mediante API REST y eventos, evitando que los microservicios accedan directamente a la base de datos MariaDB de BookStack.



