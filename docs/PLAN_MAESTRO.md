# Proyecto Aleph — Plan Maestro

> Plataforma soberana de OSINT y Cyber Threat Intelligence (CTI).
> Producto: **P.R.O.A.** (Plataforma de Reconocimiento y Operaciones Analíticas).
> Motores: **FUNES** (IA local) y **MENARD** (detección de multicuentas).


---

## 2. Reposicionamiento

El proyecto deja de prometer acceso a bases estatales y pasa a ser algo **demostrable hoy con fuentes abiertas**, que un organismo podría conectar después a sus propias bases:

**Aleph = plataforma de investigación OSINT/CTI con grafo de entidades, IA local y atribución de cuentas, con trazabilidad completa de cada dato y cada acción.**

Lo que lo hace valer en un CV de CTI no es la cantidad de funciones, es:

1. **Hablar el idioma del rubro**: STIX 2.1, MITRE ATT&CK, TLP 2.0, IOCs, Admiralty Code (fiabilidad de fuente / credibilidad del dato).
2. **Rigor medible**: el motor de multicuentas se evalúa con datasets públicos y se reportan AUC, EER, precisión a umbral fijo. Un número honesto vale más que diez pantallas.
3. **Soberanía real**: toda la IA corre en el cluster propio (nodos 1 y 2), nada sale a la nube.
4. **Responsabilidad**: auditoría con cadena de hashes, base legal obligatoria por caso, salida como hipótesis con evidencia y no como veredicto.

## 3. Módulos

### 3.1 Núcleo P.R.O.A.
- Casos de investigación con base legal/propósito obligatorio, marca TLP y estado.
- Grafo de entidades (persona, cuenta, email, teléfono, dominio, IP, URL, organización, lugar, evento, hash, wallet, documento) y relaciones, cada una con **procedencia** (fuente, fecha, hash del crudo) y confianza.
- Usuarios con roles (admin, analista, auditor) y auditoría inmutable por cadena de hashes, verificable.
- Línea de tiempo y mapa por caso.

### 3.2 Motor MENARD — detección de multicuentas
Nombre por "Pierre Menard, autor del Quijote" (Borges): mismo texto, ¿mismo autor?

Señales independientes, cada una con su propio puntaje y explicación:

| Familia | Señales |
|---|---|
| Estilometría | n-gramas de caracteres, palabras función, puntuación, mayúsculas, alargamientos ("holaaa"), emojis, errores ortográficos recurrentes, voseo y marcas rioplatenses, longitud de frase |
| Temporal | histograma por hora y día, ventana de sueño, huso horario inferido, co-actividad y alternancia (dos cuentas que nunca publican a la vez) |
| Conductual | cliente/dispositivo, hashtags, dominios que comparte, destinatarios de respuestas, ratio propio/respuesta/repost |
| Red | solapamiento de seguidos/seguidores, menciones mutuas, interacción coordinada |
| Perfil | similitud de nombre de usuario, bio, fecha de creación cercana, hash perceptual del avatar |
| Neuronal (GPU) | embeddings de estilo multilingües en la RTX 5060 Ti |

Fusión calibrada → puntaje 0–1 + desglose por señal + ejemplos concretos de evidencia. Agrupamiento en clusters de cuentas. Detección de campañas coordinadas.
Evaluación reproducible: dataset sintético propio + datasets públicos de verificación de autoría (PAN).

**Regla de diseño**: MENARD nunca dice "son la misma persona". Dice "hipótesis de mismo operador, confianza X, por estas razones", y un analista la confirma o descarta. Queda auditado.

### 3.3 Motor FUNES — IA local
- NER en español sobre documentos y publicaciones → propone entidades al grafo (el analista acepta).
- Motor de contradicciones temporales/espaciales entre fuentes.
- Resumen de caso y borrador de informe de inteligencia.
- Mapeo asistido de comportamiento a técnicas ATT&CK.
- Usa el LiteLLM del nodo 1 y los embeddings del nodo 2 (ya en producción por Onix Nube). Sin nube.

### 3.4 Conectores (recolección)
Arquitectura de plugins con interfaz única. Dos clases:

- **En vivo, por API abierta u oficial**: Bluesky, Mastodon, Reddit, GitHub, Telegram (canales públicos), X/Twitter (API oficial, requiere clave paga), Discord (bot en servidores donde el operador tiene autorización).
- **Por importación**: archivo oficial de X, exportación de Instagram, exportación de Discord (DiscordChatExporter), CSV/JSON genérico.

Más: enumeración de nombre de usuario en cientos de sitios, WHOIS/RDAP, DNS, certificate transparency.

> Instagram y X no permiten scraping en sus términos. Aleph no evade bloqueos, CAPTCHAs ni logins: entra por API oficial o por exportaciones. Esto no es un límite técnico, es lo que hace al proyecto presentable ante un organismo.

### 3.5 Módulo CTI
- Enriquecimiento de IOCs: abuse.ch (URLhaus, ThreatFox, MalwareBazaar), Shodan InternetDB, crt.sh, RDAP, AlienVault OTX, VirusTotal y HIBP (con clave).
- Exportación e importación STIX 2.1; exportación compatible con MISP y OpenCTI.
- Matriz ATT&CK por caso.
- Informe de inteligencia en PDF con TLP, fuentes valoradas y nivel de confianza.

### 3.6 Reconocimiento facial (fase final, apagado por defecto)
InsightFace en GPU contra una galería **cargada dentro del caso**. Dato sensible bajo Ley 25.326: la demo usa solo rostros sintéticos o datasets académicos. No se hace búsqueda de rostros en internet.

## 4. Arquitectura

```
Notebook (nodo 0)   desarrollo, pruebas, orquestación
        │ ssh / tailscale
Nodo 1  RTX 5060 Ti 16 GB · 32 GB DDR5 · Docker
        ├─ aleph-api        FastAPI
        ├─ aleph-worker     cola de trabajos (recolección, MENARD, FUNES)
        ├─ aleph-gpu        embeddings de estilo + rostros (CUDA)
        ├─ aleph-web        frontend
        ├─ aleph-postgres   datos · aleph-redis  cola
        └─ LiteLLM :4000    (de Onix, se reutiliza) → LLM de FUNES
Nodo 2  Arc B580 12 GB · 16 GB DDR4 · sin Docker
        ├─ llama-server :8081  embeddings (bge-m3)
        ├─ llama-server :8080  LLM liviano (NER masivo)
        └─ Qdrant :6333        búsqueda vectorial
```

Stack: Python 3.12 · FastAPI · SQLAlchemy 2 · PostgreSQL (SQLite en desarrollo y tests) · Redis · scikit-learn/NumPy · React + TypeScript + Vite · Cytoscape.js (grafo) · MapLibre (mapa) · Docker Compose.

Aleph usa su propio proyecto de Compose y sus propios puertos (API 8100, web 8101). **No toca los contenedores de Onix.**

Atención: el nodo 1 tiene 47 GB libres en C y 0 en D. Las imágenes con CUDA pesan; hay que vigilar el disco.

## 6. Hitos

| Hito | Contenido | Criterio de "listo" |
|---|---|---|
| H0 | Base y contratos | Tests del núcleo pasan; app levanta |
| H1 | Motores por separado | MENARD con AUC reportado sobre sintético; API con auth+grafo+auditoría; 4+ conectores; STIX válido; FUNES con NER |
| H2 | Producto usable | Frontend con grafo; flujo completo: crear caso → recolectar → MENARD → revisar → exportar STIX |
| H3 | Desplegado y demostrable | Corre en nodo 1; demo guiada con datos sintéticos; informe PDF; evaluación sobre dataset público |
| H4 | Extras | Rostros, más conectores, campañas coordinadas |

## 8. Límites que el proyecto respeta

- Solo datos públicos, APIs oficiales o exportaciones aportadas por el operador.
- Sin evasión de controles de plataformas, sin CAPTCHAs, sin credenciales de terceros.
- Todo caso exige propósito y base legal; toda acción queda auditada.
- Las salidas de MENARD, FUNES y rostros son hipótesis para revisión humana.
- Las demos usan datos sintéticos o datasets académicos, no personas reales.
