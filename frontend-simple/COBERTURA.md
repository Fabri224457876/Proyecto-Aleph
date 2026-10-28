# Cobertura de rutas de la API en la consola simple

Fuente: `docs/openapi.json` (el contrato). Operaciones en el contrato: **86**. Cubiertas desde algún lugar de la consola simple: **86**. Sin interfaz: **0**.

Todas las rutas se usan desde la navegación normal o desde "Más opciones", que es donde van las funciones avanzadas. Lo que no tiene un botón real en la consola no figura como cubierto.

| Método | Ruta | Qué hace (contrato) | Dónde está en la consola simple |
|---|---|---|---|
| GET | `/health` | Health | Pie de página y pantalla de ingreso: estado de la API |
| POST | `/api/auth/login` | Iniciar sesión | Ingreso |
| GET | `/api/auth/me` | Usuario actual | Al abrir la consola: se restaura la sesión (lib/auth.tsx) |
| POST | `/api/auth/password` | Cambiar la contraseña propia | Mi contraseña |
| GET | `/api/users` | Listar usuarios | Usuarios (solo administrador) |
| POST | `/api/users` | Crear usuario | Usuarios: crear usuario |
| PATCH | `/api/users/{user_id}` | Cambiar rol, estado o contraseña de un usuario | Usuarios: rol, activo y contraseña |
| POST | `/api/cases` | Abrir un caso | Abrir un caso nuevo (asistente de tres pasos) |
| GET | `/api/cases` | Listar casos | Inicio: Tus casos (y filtro de Actividad) |
| GET | `/api/cases/{case_id}` | Ver un caso (queda auditado) | Cabecera de cada caso (CaseLayout) y redirección de /casos/:id |
| PATCH | `/api/cases/{case_id}` | Editar un caso abierto | Más opciones: Datos del caso |
| DELETE | `/api/cases/{case_id}` | Borrar un caso vacío (admin) | Más opciones: Datos del caso, borrar caso vacío (administrador) |
| POST | `/api/cases/{case_id}/close` | Cerrar un caso (queda de solo lectura) | Más opciones: Datos del caso, cerrar caso |
| POST | `/api/cases/{case_id}/archive` | Archivar un caso | Más opciones: Datos del caso, archivar caso |
| POST | `/api/cases/{case_id}/reopen` | Reabrir un caso cerrado o archivado (admin) | Más opciones: Datos del caso, reabrir (administrador) |
| GET | `/api/cases/{case_id}/timeline` | Línea de tiempo del caso | Entender y contar: Línea de tiempo |
| GET | `/api/cases/{case_id}/export` | Exportar el caso en JSON (queda auditado) | Entender y contar: Informe y exportar, copia completa (JSON) |
| GET | `/api/cases/{case_id}/members` | Miembros del caso (quién tiene acceso) | Equipo del caso |
| POST | `/api/cases/{case_id}/members` | Agregar un analista al caso, por id o por nombre (admin o miembro) | Equipo del caso: agregar a un analista |
| DELETE | `/api/cases/{case_id}/members/{user_id}` | Quitar un miembro del caso (admin o miembro) | Equipo del caso: quitar del caso |
| POST | `/api/cases/{case_id}/entities` | Crear entidad | Más opciones: Entidades, nueva entidad a mano |
| GET | `/api/cases/{case_id}/entities` | Listar entidades | Revisar (propuestas); Más opciones: Entidades; Personas y cuentas |
| POST | `/api/cases/{case_id}/entities/merge` | Fusionar dos entidades duplicadas | Detalle de entidad (Más opciones: Entidades; Conexiones): fusionar |
| GET | `/api/cases/{case_id}/entities/{entity_id}` | Ver entidad | Detalle de entidad (Personas, Entidades, Conexiones, Revisar) |
| PATCH | `/api/cases/{case_id}/entities/{entity_id}` | Editar entidad | Detalle de entidad: editar |
| DELETE | `/api/cases/{case_id}/entities/{entity_id}` | Borrar entidad y sus relaciones | Detalle de entidad: borrar entidad |
| POST | `/api/cases/{case_id}/entities/{entity_id}/review` | Aceptar o rechazar una entidad propuesta | Revisar: confirmar o descartar entidad; detalle de entidad |
| GET | `/api/cases/{case_id}/entities/{entity_id}/neighbors` | Vecindario de un nodo | Detalle de entidad: conexiones directas |
| POST | `/api/cases/{case_id}/relations` | Crear relación | Más opciones: Relaciones, crear; detalle de entidad |
| GET | `/api/cases/{case_id}/relations` | Listar relaciones | Revisar (propuestas); Más opciones: Relaciones |
| GET | `/api/cases/{case_id}/relations/{relation_id}` | Ver relación | Más opciones: Relaciones, Ver |
| PATCH | `/api/cases/{case_id}/relations/{relation_id}` | Editar relación | Más opciones: Relaciones, cambiar el tipo |
| DELETE | `/api/cases/{case_id}/relations/{relation_id}` | Borrar relación | Más opciones: Relaciones, Borrar |
| POST | `/api/cases/{case_id}/relations/{relation_id}/review` | Aceptar o rechazar una relación propuesta | Revisar: confirmar o descartar relación; Más opciones: Relaciones |
| GET | `/api/cases/{case_id}/graph` | Grafo del caso, listo para el visor (queda auditado) | Entender y contar: Conexiones (mapa) |
| GET | `/api/cases/{case_id}/graph/path` | Camino más corto entre dos nodos | Entender y contar: Conexiones, camino más corto |
| GET | `/api/cases/{case_id}/search` | Buscar texto en entidades, cuentas y publicaciones | Más opciones: Buscar en el caso |
| POST | `/api/cases/{case_id}/sources` | Registrar una fuente | Más opciones: Fuentes, registrar una fuente a mano |
| GET | `/api/cases/{case_id}/sources` | Listar fuentes | Más opciones: Fuentes (y Revisar: fuentes con texto para contradicciones) |
| POST | `/api/cases/{case_id}/sources/upload` | Subir un archivo como fuente (guarda el crudo y su sha256) | Reunir: al soltar un archivo, guardarlo como fuente |
| GET | `/api/cases/{case_id}/sources/{source_id}` | Ver fuente | Más opciones: Fuentes, detalle |
| GET | `/api/cases/{case_id}/sources/{source_id}/raw` | Descargar el crudo de una fuente (verifica el hash; queda auditado) | Más opciones: Fuentes, descargar original |
| GET | `/api/cases/{case_id}/accounts` | Listar cuentas del caso | Entender y contar: Personas y cuentas |
| GET | `/api/cases/{case_id}/accounts/{account_id}` | Ver cuenta | Personas y cuentas: detalle de la cuenta |
| GET | `/api/cases/{case_id}/accounts/{account_id}/posts` | Publicaciones de una cuenta (queda auditado) | Personas y cuentas: ver publicaciones (queda auditado) |
| POST | `/api/cases/{case_id}/collections` | Ingresar una recolección ya normalizada (CollectionResult) | Más opciones: Importar JSON (recolección normalizada) |
| POST | `/api/cases/{case_id}/captures` | Captura en vivo desde la extensión | Más opciones: Importar JSON (captura de la extensión) |
| POST | `/api/cases/{case_id}/captures/lookup` | Qué sabe el caso de los handles visibles en pantalla | Más opciones: Verificar cuentas; Reunir (al escribir un usuario) |
| GET | `/api/cases/{case_id}/sections` | Incisos del expediente, con contador de hallazgos | Entender y contar: Expediente |
| POST | `/api/cases/{case_id}/sections` | Crear inciso | Expediente: gestionar incisos, crear inciso |
| PATCH | `/api/cases/{case_id}/sections/{section_id}` | Renombrar o reordenar un inciso | Expediente: gestionar incisos, guardar nombre |
| DELETE | `/api/cases/{case_id}/sections/{section_id}` | Borrar inciso (sus hallazgos pasan a sin clasificar) | Expediente: gestionar incisos, borrar |
| POST | `/api/cases/{case_id}/findings` | Incorporar un datachunk al expediente | Reunir: agregar datos al expediente; Revisar: incorporar contradicción |
| GET | `/api/cases/{case_id}/findings` | Listar hallazgos | Entender y contar: Expediente |
| GET | `/api/cases/{case_id}/findings/{finding_id}` | Ver hallazgo | Expediente: ver origen |
| PATCH | `/api/cases/{case_id}/findings/{finding_id}` | Mover de inciso o editar la nota | Expediente: mover a otro inciso, editar nota |
| DELETE | `/api/cases/{case_id}/findings/{finding_id}` | Quitar un hallazgo del expediente (su fuente y su entidad se conservan) | Expediente: quitar del expediente |
| POST | `/api/cases/{case_id}/menard/run` | Correr MENARD sobre las cuentas del caso | Revisar: buscar cuentas que podrían ser del mismo operador |
| GET | `/api/cases/{case_id}/menard/links` | Hipótesis de mismo operador, por puntaje | Revisar: hipótesis pendientes |
| GET | `/api/cases/{case_id}/menard/links/{link_id}` | Ver un vínculo con su evidencia | Revisar: decisión de hipótesis (evidencia actualizada) |
| POST | `/api/cases/{case_id}/menard/links/{link_id}/review` | Confirmar o rechazar una hipótesis de MENARD | Revisar: confirmar o descartar hipótesis |
| GET | `/api/jobs` | Listar trabajos (solo de los casos que ves) | Más opciones: Trabajos |
| GET | `/api/jobs/{job_id}` | Consultar un trabajo (solo de los casos que ves) | Más opciones: Trabajos, Ver |
| GET | `/api/audit/events` | Eventos de auditoría (auditor o admin) | Registro de actividad |
| GET | `/api/audit/verify` | Verificar la integridad de la cadena de auditoría | Registro de actividad: integridad de la cadena |
| GET | `/api/connectors` | Conectores instalados | Reunir (acciones de conectores); Más opciones: Herramientas y conectores |
| POST | `/api/cases/{case_id}/collect` | Ejecutar un conector y guardar lo recolectado (queda como trabajo y en la auditoría) | Reunir: conectores en vivo y CSV/JSONL importado |
| GET | `/api/tools` | Herramientas OSINT disponibles (solo lectura) | Reunir (acciones de herramientas); Más opciones: Herramientas y conectores |
| POST | `/api/cases/{case_id}/tools/{name}/run` | Ejecutar una herramienta OSINT y guardar sus resultados como propuestas | Reunir: herramientas OSINT según el dato |
| GET | `/api/cases/{case_id}/export/stix` | Exportar el caso como Bundle STIX 2.1 (queda auditado) | Entender y contar: Informe y exportar, paquete STIX 2.1 |
| GET | `/api/cases/{case_id}/export/misp` | Exportar el caso como evento MISP (queda auditado) | Entender y contar: Informe y exportar, evento MISP |
| POST | `/api/cases/{case_id}/import/stix` | Importar un Bundle STIX 2.1 como entidades y relaciones propuestas | Reunir: archivo JSON STIX (importar como inteligencia STIX 2.1) |
| POST | `/api/cases/{case_id}/ioc/extract` | Extraer IOCs de un texto y guardarlos como propuestas | Reunir: extraer indicadores y técnicas ATT&CK de un texto |
| POST | `/api/cases/{case_id}/entities/{entity_id}/enrich` | Consultar proveedores CTI sobre un indicador; lo nuevo queda como propuesta | Detalle de entidad: consultar proveedores CTI |
| POST | `/api/cases/{case_id}/captures/chunks` | Datachunks de un texto: IOCs y reglas argentinas de FUNES (sin LLM, sin guardar nada) | Reunir: buscar datos dentro del texto (vista previa) |
| GET | `/api/cases/{case_id}/attack/layer` | Capa para ATT&CK Navigator con las técnicas vinculadas al caso | Entender y contar: Informe y exportar, capa para ATT&CK Navigator |
| POST | `/api/cases/{case_id}/attack/techniques` | Vincular una técnica ATT&CK al caso (decisión del analista) | Entender y contar: Informe y exportar, técnicas ATT&CK (vincular) |
| GET | `/api/attack/techniques` | Buscar técnicas ATT&CK por ID o texto | Entender y contar: Informe y exportar, técnicas ATT&CK (buscar) |
| POST | `/api/cases/{case_id}/funes/ner` | Extraer entidades y relaciones de un texto o una fuente (propuestas) | Reunir: extraer personas, organizaciones y relaciones con la IA local |
| POST | `/api/cases/{case_id}/funes/contradictions` | Detectar contradicciones espacio-temporales y de valores entre fuentes | Revisar: buscar contradicciones entre fuentes |
| POST | `/api/cases/{case_id}/funes/report` | Borrador de informe de inteligencia en Markdown (queda auditado) | Entender y contar: Informe y exportar, redactar borrador |
| GET | `/api/search` | Buscar en los casos que ves (texto y significado) | Barra de búsqueda de arriba y página Buscar |
| POST | `/api/cases/{case_id}/ask` | Preguntarle al caso: FUNES responde con los datos del caso y los cita | Entender y contar: Preguntarle al caso |
| GET | `/api/cases/{case_id}/lookup` | Búsqueda OSINT externa: qué fuentes públicas aplican a un dato | Buscar (dentro de un caso): Buscar afuera, en fuentes públicas |
| POST | `/api/cases/{case_id}/tlon/run` | Entrenar el modelo del caso y proponer vínculos faltantes | Entender y contar: Vínculos posibles |
| GET | `/api/cases/{case_id}/tlon/latest` | Resultado de la última corrida de TLÖN | Entender y contar: Vínculos posibles (al abrir) |

## Limitaciones conocidas de la API para esta interfaz

- **Contradicciones sin registro de decisión.** `POST /funes/contradictions` no guarda nada. La consola permite incorporar una contradicción al expediente (crea un hallazgo, que sí se guarda), pero el descarte queda solo en la sesión: no hay ruta para registrarlo.
- **Búsqueda de contradicciones dependiente del modelo.** Con fuentes de texto la ruta exige el modelo de IA; si no responde, la consola muestra el motivo (503).
- **Relectura de hipótesis.** `GET /menard/links/{id}` existe y la consola lo usa para refrescar la evidencia al decidir, pero la bandeja toma las hipótesis de la lista, que ya trae la evidencia completa.
- **Trabajos y auditoría por usuario.** La auditoría muestra el número de usuario para quien no es administrador, porque `GET /api/users` es solo de administrador.

