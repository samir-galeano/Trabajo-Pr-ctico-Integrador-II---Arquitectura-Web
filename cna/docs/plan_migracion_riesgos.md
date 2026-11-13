# F5 - A5: Estrategia de migración y matriz de riesgos

## 1. Objetivo

Definir una estrategia de extracción progresiva de funcionalidades de BookStack hacia microservicios independientes, reduciendo el riesgo de interrupciones y manteniendo la posibilidad de volver a la implementación anterior.

La migración seguirá el patrón Strangler Fig. BookStack continuará funcionando como sistema principal mientras las nuevas funcionalidades se incorporan progresivamente.

## 2. Microservicios propuestos

Se consideran cuatro microservicios:

| Microservicio | Responsabilidad |
|---|---|
| Search Service | Indexación y búsqueda de contenido. |
| Audit Service | Registro y consulta de actividades. |
| Export Service | Generación de exportaciones. |
| Media Service | Administración de imágenes y archivos adjuntos. |

Los módulos Content y Users/Permissions permanecerán inicialmente dentro del monolito BookStack.

## 3. Orden de extracción

La extracción se realizará de manera progresiva:

| Orden | Microservicio | Justificación |
|---|---|---|
| 1 | Search | Permite separar la indexación y búsqueda del contenido mediante eventos y una base de datos propia. |
| 2 | Audit | Permite registrar actividades de forma independiente y consumir eventos del sistema. |
| 3 | Export | Permite ejecutar procesos de exportación de manera asíncrona. |
| 4 | Media | Permite independizar progresivamente la gestión de archivos e imágenes, considerando sus dependencias con el contenido. |

Este orden es una propuesta de implementación y deberá validarse durante las pruebas de integración.

## 4. Estrategia de migración

Se aplicará el patrón Strangler Fig mediante las siguientes etapas:

### 4.1. Preparación

- Mantener BookStack funcionando sin modificaciones en su código fuente.
- Preparar los contratos REST API y los canales de eventos.
- Configurar el API Gateway y la capa de integración.
- Preparar una base de datos independiente para cada microservicio.
- Configurar NATS JetStream para la comunicación asíncrona.

### 4.2. Extracción progresiva

- Implementar el primer microservicio.
- Integrarlo con BookStack mediante REST API y Webhooks.
- Validar sus operaciones y el intercambio de eventos.
- Habilitar progresivamente el enrutamiento hacia el nuevo servicio.
- Repetir el procedimiento para los demás microservicios.

### 4.3. Validación

- Verificar las respuestas de las API.
- Comprobar la recepción y procesamiento de eventos.
- Validar la persistencia de datos en las bases independientes.
- Comprobar los mecanismos de reintentos e idempotencia.
- Comparar los resultados con el comportamiento esperado de BookStack.

## 5. Estrategia de rollback

El rollback permitirá regresar al comportamiento anterior cuando un microservicio presente fallos que afecten su funcionalidad.

Se establece como objetivo que la restauración del enrutamiento anterior para las rutas /v2/* pueda realizarse en menos de un minuto.

Procedimiento:

1. Detectar el fallo mediante los mecanismos de monitoreo.
2. Deshabilitar el enrutamiento hacia el microservicio afectado.
3. Restaurar la ruta anterior configurada en el API Gateway.
4. Redirigir las solicitudes a la funcionalidad correspondiente de BookStack, cuando exista una alternativa compatible.
5. Verificar que el servicio anterior responda correctamente.
6. Registrar el incidente y analizar su causa.

El objetivo de menos de un minuto deberá comprobarse mediante una prueba de rollback. No se considera un tiempo ya validado.

El rollback del enrutamiento no implica revertir automáticamente los datos que ya fueron procesados por un microservicio. Para esos casos se deberá evaluar la consistencia y la posibilidad de reprocesamiento.

## 6. Consistencia e idempotencia

La comunicación asíncrona puede provocar que los cambios no estén disponibles simultáneamente en todos los servicios.

Se adoptarán las siguientes medidas:

- Utilizar un identificador event_id único por evento.
- Validar los eventos recibidos para evitar procesamientos duplicados.
- Mantener el registro de eventos procesados.
- Implementar reintentos ante fallos temporales.
- Utilizar mecanismos de Dead Letter Queue (DLQ) para eventos que no puedan procesarse después de los reintentos.
- Registrar errores para permitir su análisis y recuperación.

La consistencia eventual será aceptada para las operaciones que no requieran una actualización inmediata entre todos los servicios.

## 7. Matriz de riesgos

| Riesgo | Impacto | Probabilidad | Mitigación |
|---|---|---|---|
| Consistencia eventual | Datos temporalmente desactualizados entre servicios. | Media | Eventos, reintentos, idempotencia y monitoreo. |
| Consumo elevado de RAM | Degradación del rendimiento o falta de recursos. | Media | Monitorear memoria, limitar recursos y ajustar servicios. |
| Latencia entre máquinas virtuales | Aumento del tiempo de respuesta. | Media | Medir latencia, optimizar solicitudes y reducir comunicaciones innecesarias. |
| Exposición de secretos compartidos | Acceso no autorizado a servicios o recursos. | Media | Utilizar variables de entorno, controlar permisos y evitar almacenar secretos en Git. |
| API Gateway como punto único de falla | Indisponibilidad de los servicios expuestos. | Media | Monitoreo, reinicio automático y estrategia de recuperación. |
| Pérdida o duplicación de eventos | Información incompleta o procesamiento repetido. | Media | NATS JetStream, persistencia, identificadores únicos y DLQ. |
| Fallo durante una extracción | Interrupción de una funcionalidad. | Media | Despliegue gradual, validaciones y rollback. |
| Dependencia de BookStack | Dificultad para independizar funcionalidades. | Alta | Integración mediante REST API, Webhooks y Anti-Corruption Layer. |

Las probabilidades e impactos indicados son estimaciones iniciales de diseño y deberán revisarse durante la implementación.

## 8. Consideraciones de seguridad y operación

- Mantener la base de datos MariaDB de BookStack aislada de los microservicios.
- No permitir que los microservicios consulten directamente las tablas de BookStack.
- Proteger las comunicaciones mediante autenticación y autorización.
- Evitar la exposición pública de los puertos internos.
- Centralizar los registros y monitorear los servicios.
- Gestionar los secretos fuera del repositorio.
- Documentar los procedimientos de recuperación y rollback.

## 9. Criterios de aceptación

La estrategia se considerará preparada para su implementación cuando:

- Los cuatro microservicios tengan responsabilidades definidas.
- El orden de extracción esté documentado.
- La integración con BookStack esté definida mediante REST API y Webhooks.
- Cada microservicio tenga prevista su propia base de datos.
- El procedimiento de rollback esté documentado.
- Los riesgos principales tengan medidas de mitigación.
- Se contemple consistencia eventual, idempotencia y DLQ.
- El objetivo de rollback inferior a un minuto pueda verificarse mediante pruebas.

## 10. Conclusión

La estrategia propuesta permite evolucionar BookStack hacia una arquitectura distribuida de manera gradual, manteniendo inicialmente el monolito en funcionamiento.

La extracción progresiva, el uso de contratos de integración, la comunicación mediante eventos y la definición de mecanismos de recuperación permiten reducir los riesgos asociados a la migración.