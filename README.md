# Aleph

**Plataforma soberana de OSINT y ciberinteligencia (CTI).**
Investigación en fuentes abiertas con grafo de entidades, IA 100 % local, atribución de cuentas por estilometría y trazabilidad completa de cada dato y cada acción.

> Estado: **en desarrollo activo** (octubre 2026). Los motores y la consola web están implementados y probados; el despliegue y la validación contra plataformas reales están en curso. La sección [Estado y verificación](#estado-y-verificación) dice exactamente qué está probado y qué no.

Producto: **P.R.O.A.** (Plataforma de Reconocimiento y Operaciones Analíticas).

![Grafo de un caso de demostración con datos sintéticos](frontend/screenshots/grafo.png)

*Caso de demostración con datos 100 % sintéticos.*

---

## Por qué existe

Los equipos de análisis dependen de herramientas propietarias extranjeras o de servicios en la nube para tratar información sensible. Aleph explora la alternativa: una plataforma que corre entera en infraestructura propia, habla los estándares del rubro y deja registro verificable de todo lo que hace un operador.

Tres principios de diseño:

1. **Soberanía.** Ningún dato sale de la red propia. El cliente de IA rechaza cualquier dirección que no sea interna.
2. **Trazabilidad.** Todo dato tiene procedencia (fuente, fecha, hash del crudo, fiabilidad según Admiralty Code). Toda acción queda en una auditoría encadenada por hash.
3. **Hipótesis, no veredictos.** Lo que proponen los motores queda pendiente hasta que un analista lo confirma o lo descarta, y esa decisión también se audita.

## Módulos

| Módulo | Qué hace |
|---|---|
| **Núcleo** | Casos con base legal obligatoria y marca TLP, grafo de entidades y relaciones, expediente por incisos, roles (admin, analista, auditor), auditoría inmutable |
| **MENARD** | Detección de posibles multicuentas: estilometría, patrones horarios, conducta, red y perfil, fusionados en un puntaje calibrado con evidencia legible |
| **FUNES** | IA local: extracción de entidades (reglas para documentos argentinos + LLM), motor de contradicciones espacio-temporales, borrador de informe, mapeo a ATT&CK |
| **CTI** | Extracción y enriquecimiento de IOCs, exportación e importación STIX 2.1, eventos MISP, capas para ATT&CK Navigator, TLP 2.0 |
| **Conectores** | Bluesky, Mastodon, Reddit, GitHub, Telegram (canales públicos), X (API oficial); importación de archivos oficiales de X, Instagram y Discord |
| **Caja OSINT** | Metadatos de imágenes y documentos, Wayback Machine, dorks, typosquatting, favicon hash, ASN, wallets, CVE/KEV, ransomware, preservación de evidencia |
| **Aleph Lens** | Extensión de navegador: mientras el analista navega, resalta datos relevantes en la página y permite arrastrarlos al expediente del caso |
| **TLÖN** | Hipótesis de vínculos faltantes: un modelo predictivo estilo JEPA aprende la estructura de cada caso y propone lo que el patrón sugiere; FUNES revisa cada propuesta y explica cómo verificarla |
| **Búsqueda** | Una barra para todos tus casos, por texto (sin importar tildes) y por significado (embeddings locales); preguntas en lenguaje natural respondidas con citas; plan de búsqueda en fuentes públicas a partir de un dato |

Los nombres vienen de Borges: *El Aleph* (el punto que contiene todos los puntos), *Funes el memorioso* (el que no olvida ningún detalle) *Pierre Menard, autor del Quijote* (mismo texto, ¿mismo autor?) y *Tlön, Uqbar, Orbis Tertius* (un mundo entero inferido a partir de fragmentos).

## MENARD: atribución de cuentas

Compara cuentas de a pares, también entre plataformas distintas, usando señales independientes:

- **Estilometría**: n-gramas de caracteres, palabras función, puntuación, mayúsculas, alargamientos, emojis, errores ortográficos recurrentes, marcas rioplatenses.
- **Temporal**: actividad por hora y día, ventana de sueño, huso inferido, alternancia.
- **Conducta**: cliente, hashtags, dominios compartidos, destinatarios de respuestas.
- **Red**: solapamiento de seguidos y seguidores ponderado por rareza.
- **Perfil**: similitud de nombre de usuario y bio, fecha de creación, hash perceptual del avatar.

Cada señal informa su puntaje, su explicación y ejemplos concretos. Si no hay datos suficientes se declara no disponible en lugar de inventar un valor.

### Evaluación

Sobre un dataset **sintético** propio (4 mundos de prueba, 990 cuentas, 122 064 pares, 0,48 % positivos; ninguna persona de prueba aparece en entrenamiento):

| Métrica | Valor |
|---|---|
| AUC-ROC | 0,987 |
| EER | 0,058 |
| Precisión a umbral 0,5 | 0,873 |
| Recall a umbral 0,5 | 0,597 |
| AUC entre plataformas distintas | 0,984 |
| AUC cuando una cuenta disimula su estilo | 0,957 (recall 0,115) |

![Revisión de una hipótesis de MENARD](frontend/screenshots/menard.png)

**Estos números son un techo optimista.** El texto sintético sale de plantillas y es más regular que el de personas reales, y entrenamiento y prueba comparten el proceso generador. Todavía no hay evaluación con datos reales; el cargador para datasets públicos de verificación de autoría (formato PAN) está preparado para eso. El detalle completo, incluidos los casos donde el motor falla, está en [`backend/aleph/menard/EVALUATION.md`](backend/aleph/menard/EVALUATION.md).

## TLÖN y FUNES: dos modelos a la par

TLÖN no lee: aprende la forma del caso. Cada entidad se describe con embeddings locales (bge-m3) y n-gramas de su texto; un codificador de contexto resume a la entidad con sus vecinos y un predictor estima la *representación* de la entidad del otro extremo de un vínculo, como en I-JEPA (objetivo de un codificador EMA, sin reconstruir el texto). En grafos chicos eso solo no distingue entidades del mismo tipo, así que suma un término contrastivo y la regularización de VICReg. Aprende también la cardinalidad de cada relación (un correo pertenece a un solo dominio) y no vuelve a proponer lo que un analista ya rechazó.

Antes de proponer, mide su calidad ocultándose vínculos reales al entrenar. En el caso de demostración (81 entidades, 45 vínculos) pone el extremo correcto entre sus 5 primeras opciones entre el 71 % y el 79 % de las veces según la corrida, contra 38 % al azar, y la hipótesis más fuerte es justamente el vínculo que se omitió a propósito en la siembra. Entrena en CPU en segundos, NumPy puro, con gradientes verificados contra diferencias finitas.

FUNES, el modelo de lenguaje (qwen3:8b en las pruebas), revisa cada hipótesis: la marca como plausible, dudosa o descartable, explica por qué y propone qué verificar en fuentes abiertas. Las descartadas no entran al grafo; las demás quedan como **propuestas** en Revisar. La misma IA responde preguntas sobre el caso usando solo los datos recuperados y citándolos; las citas a datos que no recibió se marcan.

## Límites que el proyecto respeta

- Solo datos públicos, APIs oficiales o exportaciones que aporta el operador.
- Sin evasión de controles de plataformas, sin resolución de CAPTCHAs, sin uso de credenciales de terceros.
- Aleph Lens es pasiva: lee lo que el analista ya tiene en pantalla; no navega, no hace scroll ni clics por su cuenta, y excluye mensajes privados.
- Todo caso exige propósito y base legal (Ley 25.326 de Protección de Datos Personales).
- Las pruebas y demostraciones usan datos sintéticos, no personas reales.

## Arquitectura

```
backend/aleph/
  core/        modelo de datos, esquemas compartidos, auditoría encadenada
  api/         FastAPI: auth, casos, grafo, expediente, MENARD, auditoría
  menard/      detección de multicuentas + generador sintético + evaluador
  funes/       IA local
  cti/         IOCs, STIX 2.1, MISP, ATT&CK, TLP
  tlon/        hipótesis de vínculos (modelo predictivo estilo JEPA)
  connectors/  recolección
  osint/       caja de herramientas
extension/     Aleph Lens (Manifest V3, sin framework)
frontend/      consola web completa (React + TypeScript + Cytoscape.js)
frontend-simple/ consola simple: Reunir, Revisar, Entender y contar; búsqueda y preguntas
docs/          plan maestro y contrato OpenAPI
```

Stack: Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL (SQLite en desarrollo), scikit-learn, NumPy, React, TypeScript, Cytoscape.js. La IA usa cualquier servidor compatible con la API de OpenAI dentro de la red propia (Ollama, llama.cpp, LiteLLM); en las pruebas, Ollama con qwen3:8b y bge-m3 en un nodo con GPU.

Los motores no tocan la base de datos: reciben y devuelven esquemas tipados, y por eso se prueban sin red, sin GPU y sin servicios externos.

## Estado y verificación

| Componente | Tests | Verificado contra el mundo real |
|---|---|---|
| Núcleo y API | pasan | Sí, sobre SQLite. PostgreSQL pendiente |
| Auditoría encadenada | pasan, incluida escritura concurrente | Sí |
| MENARD | pasan | Solo dataset sintético |
| FUNES | pasan | Reglas: sí. LLM: probado con qwen3:8b local sobre el caso de demostración (informe, preguntas, revisión de hipótesis) |
| TLÖN | pasan, incluido chequeo de gradientes | Sobre el caso de demostración sintético con embeddings reales; sin datos reales todavía |
| Búsqueda | pasan | Texto y significado (bge-m3) sobre el caso de demostración; la búsqueda externa reusa conectores y herramientas |
| CTI | pasan | STIX validado con la librería `stix2`. Proveedores externos: solo simulados |
| Conectores | pasan | GitHub parcial. El resto contra ejemplos escritos a mano |
| Caja OSINT | pasan | Algoritmos verificados con vectores conocidos. APIs: solo simuladas |
| Aleph Lens | pasan | Extractores contra HTML de ejemplo, no contra las páginas reales |
| Consolas web | build sin errores | Recorridas en navegador contra la API real; sin tests unitarios |

Total: 895 tests de backend (incluido un recorrido de punta a punta sobre el caso de demostración) y 106 de la extensión.

## Cómo correrlo

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e "backend[dev]"
.venv/Scripts/python -m pytest backend/tests -q
```

```bash
cd backend && ../.venv/Scripts/python -m aleph.menard.eval --write-md
```

### Demo con datos sintéticos

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1
```

Crea una base local, siembra un caso ficticio de phishing (33 cuentas, 1 487 publicaciones, 23 hipótesis de MENARD, una contradicción espacio-temporal, técnicas ATT&CK, ocho kits espejo de la campaña con un vínculo omitido para TLÖN) y levanta la API en `http://127.0.0.1:8100/docs`. Las credenciales de demo se generan e imprimen al final. Después, `npm install && npm run dev` en `frontend/`.

La configuración va por variables `ALEPH_*`; ver [`.env.example`](.env.example).

## Hoja de ruta

- Evaluación de MENARD con datasets públicos de verificación de autoría.
- Embeddings de estilo en GPU como señal adicional para MENARD.
- TLÖN sobre casos reales y con más tipos de vínculo; correrlo como trabajo en segundo plano.
- Validación de conectores y de Aleph Lens contra las plataformas reales.
- Despliegue con Docker Compose.

## Autoría

Aleph fue desarrollado por Fabrizio Pianarosa: diseño, arquitectura, implementación y pruebas. Se presentó en el marco de Prácticas Profesionalizantes (6.º año de Informática, Escuela Técnica N°21), donde estaba planificado como trabajo de equipo; en la práctica fue realizado por una sola persona.

Desarrollado con asistencia de herramientas de IA bajo dirección y revisión humana: la arquitectura, los contratos entre módulos, los límites del producto y la verificación de cada entrega son decisiones del autor.

Datos de MITRE ATT&CK® sujetos a sus términos de uso.
