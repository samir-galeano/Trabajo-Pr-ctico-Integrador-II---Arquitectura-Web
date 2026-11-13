

\# F5-E2 - Matriz de acoplamiento módulo × tabla



\## 1. Objetivo



Identificar las dependencias entre los seis módulos funcionales de BookStack y las tablas de su base de datos MariaDB.



El objetivo es reconocer las tablas compartidas que generan acoplamiento entre módulos y que deben considerarse durante la extracción progresiva hacia microservicios.



\## 2. Módulos analizados



Se consideran los siguientes módulos funcionales:



1\. Content

2\. Search

3\. Activity

4\. Export

5\. Images/attachments

6\. Users/permissions



\## 3. Matriz de acoplamiento



La marca X indica que existe una dependencia funcional identificada mediante el análisis del esquema de la base de datos y las relaciones del código fuente.



| Tabla | Content | Search | Activity | Export | Media | Users/Permissions |

|---|:---:|:---:|:---:|:---:|:---:|:---:|

| entities | X | X | X | X | | X |

| entity\_container\_data | X | X | | X | | |

| entity\_page\_data | X | X | | X | | |

| page\_revisions | X | | X | X | | |

| bookshelves\_books | X | | | X | | |

| search\_terms | | X | | | | |

| tags | X | X | | X | | |

| activities | | | X | | | X |

| views | | X | X | | | |

| comments | | X | X | | | |

| deletions | | | X | | | |

| favourites | | | X | | | X |

| watches | | | X | | | X |

| references | X | | X | | | |

| slug\_history | X | | X | | | |

| images | X | | | X | X | X |

| attachments | X | | | X | X | |

| imports | | | | X | | |

| users | | X | X | | | X |

| roles | | | | | | X |

| role\_user | | | | | | X |

| role\_permissions | | | | | | X |

| permission\_role | | | | | | X |

| entity\_permissions | | | | | | X |

| joint\_permissions | | | X | | X | X |



\*\*Nota:\*\* Esta matriz representa dependencias funcionales directas e indirectas identificadas durante la inspección. No implica que todos los módulos escriban directamente en todas las tablas marcadas.



\## 4. Tablas compartidas y acoplamiento



\### entities



Es una tabla central del modelo de entidades de BookStack. Se relaciona con libros, capítulos, páginas y estanterías. También participa en las relaciones polimórficas utilizadas por búsqueda, actividad y permisos.



Su dependencia transversal dificulta extraer estos módulos sin definir contratos e interfaces de integración.



\### users



Se relaciona con roles, actividad, propiedad de entidades y otras funcionalidades. Su utilización por diferentes contextos genera dependencia entre usuarios, seguridad y auditoría.



\### tags



Las etiquetas están asociadas a entidades y son utilizadas por la indexación de búsqueda y los procesos de exportación.



\### images y attachments



Los archivos multimedia se relacionan con las páginas y con los procesos de exportación. La extracción de Media debe contemplar estas relaciones y las referencias a los archivos físicos.



\### joint\_permissions



Esta tabla participa en las restricciones de acceso de diferentes tipos de entidades y funcionalidades. Su utilización transversal representa un punto importante de acoplamiento.



\## 5. Consideraciones para la extracción



\- No se deben extraer tablas compartidas sin analizar previamente sus dependencias.

\- Los microservicios no deben consultar directamente la base de datos MariaDB de BookStack.

\- La integración con BookStack debe realizarse mediante REST API y Webhooks.

\- Cada microservicio deberá administrar sus propios datos.

\- Las relaciones entre contextos deberán resolverse mediante contratos e integración por eventos cuando corresponda.

\- Se deberá contemplar la consistencia eventual y la recuperación ante fallos.



\## 6. Limitaciones del análisis



La matriz se elaboró a partir de la inspección del esquema de MariaDB y de las relaciones entre modelos Eloquent de BookStack.



Las marcas representan dependencias funcionales identificadas, no un registro exhaustivo de todas las consultas SQL ejecutadas en tiempo de ejecución. Las dependencias indirectas deberán validarse durante la implementación y las pruebas de extracción.



\## 7. Evidencia



\- F5-E1: Inventario de tablas de la base de datos BookStack.

\- F5-E2: Matriz de acoplamiento módulo × tabla.



