# Pruebas en vivo

Corridas de las herramientas, los conectores y la IA contra servicios reales, por la API real
(sin dependencias simuladas), en un caso nuevo. Objetivos públicos e inofensivos: dominios y
rangos de ejemplo, vectores de prueba, cuentas oficiales o de prueba de cada plataforma. Nunca
personas. IA: Ollama con qwen3:8b y bge-m3 en el nodo con GPU.

## 20/11/2026

TLÖN como trabajo en segundo plano, contra la API real (uvicorn, hilo real, SQLite en archivo y la consola
simple en el navegador) sobre el caso de demostración recién sembrado. Esta corrida fue **sin IA**: sin
servidor de embeddings ni de lenguaje, así que TLÖN describió las entidades con n-gramas y FUNES no revisó
las hipótesis (la revisión con el modelo ya está probada en la corrida síncrona del 02/11).

| Qué se probó | Resultado |
|---|---|
| `POST /tlon/jobs` | 202 en 0,02 s con el trabajo en cola; la corrida en sí tardó unos 10 s |
| Un segundo pedido mientras corre | 409 «TLÖN ya está corriendo en este caso» |
| Seguimiento por `GET /jobs/{id}` | `running` → `done` en 10,3 s; 81 entidades, 45 vínculos, 8 hipótesis propuestas |
| `GET /tlon/latest` | Coincide con el resultado guardado en el trabajo |
| Consola simple | Avisa que corre en segundo plano, se actualiza sola al terminar y **retoma una corrida en curso** al recargar la página; sin errores en la consola del navegador ni en el servidor |
| Auditoría | Un evento `tlon.run` por trabajo y la cadena íntegra (50 eventos) con el hilo de segundo plano escribiendo junto a los pedidos |

Pendiente: repetir con el modelo de lenguaje y los embeddings locales, que en esta corrida no estuvieron disponibles.

## 02/11/2026

IA local: Ollama 0.24 con qwen3:8b (sin razonamiento) y bge-m3 en el nodo con GPU, sobre el caso de
demostración recién sembrado.

| Función | Resultado | Tiempo |
|---|---|---|
| Contradicciones | **OK en 3 de 3 corridas**: con el prompt corregido el 28/10, extrae bien las dos afirmaciones (Rosario 14:00, Ushuaia 14:20) y detecta la imposibilidad espacio-temporal | 7,6–8,1 s |
| Preguntar al caso | OK. Antes respondía «no se mencionan servidores» a «¿en qué servidores se alojan los dominios?»: el contexto traía las entidades sin sus vínculos. Ahora cada entidad lleva sus relaciones y la respuesta lista dominio → IP con citas válidas | 11,5 s |
| Informe | **Corregido.** Ollama recorta cada pedido a 4096 tokens por el principio, y el inventario completo del caso (157 datos) se comía las instrucciones: el modelo inventaba otro formato o dejaba todo sin citas. Ahora el modelo recibe un inventario compacto (lo estructural primero) y un esquema de salida con `ids` obligatorio: 11 y 13 afirmaciones en dos corridas, todas con citas válidas | 12,3–12,8 s |
| TLÖN | OK con embeddings bge-m3: el extremo correcto queda entre las 5 primeras opciones en el 86 % de los vínculos ocultos (azar: 39 %); la hipótesis más fuerte es el vínculo omitido a propósito en la siembra. FUNES revisó las propuestas | 11,2 s |
| Búsqueda por significado | OK con el tope de 1000 caracteres por texto | — |

Observación del nodo: después de un reinicio, el contenedor `aleph-ollama` quedó corriendo pero sin
el puerto publicado (está atado a la IP de Tailscale, que todavía no existía cuando arrancó Docker).
Se resuelve con `docker restart aleph-ollama`.

## 28/10/2026

### Herramientas OSINT

| Herramienta | Objetivo | Resultado | Tiempo |
|---|---|---|---|
| wayback | example.com | OK | 3,3 s |
| dorks | example.com | OK | 0,0 s |
| typosquat | example.com | OK | 5,2 s |
| favicon | github.com | OK | 0,4 s |
| asn | 8.8.8.8 | OK | 1,9 s |
| wallet | vector BIP-173 | OK | 1,5 s |
| vulns | CVE-2021-44228 | OK | 1,6 s |
| ransomware | AR | **Falla**: la API pública de ransomware.live responde 404 en todas sus rutas, incluidas las de su documentación vigente. La herramienta informa el error con claridad | 1,2 s |
| email_intel | test@example.com | OK | 0,1 s |
| phone_intel | +54 11 4000-0000 | OK (sin red) | 0,0 s |
| image_meta | JPEG sintético con EXIF | OK | 0,3 s |
| doc_meta | PDF sintético con metadatos | OK | 0,0 s |
| evidence | HTML sintético | OK | 0,0 s |

### Conectores

| Conector | Objetivo | Resultado | Tiempo |
|---|---|---|---|
| github | octocat (cuenta de prueba de GitHub) | OK | 0,5 s |
| bluesky | bsky.app (cuenta oficial) | OK | 2,3 s |
| mastodon | Mastodon@mastodon.social (cuenta oficial) | OK | 1,9 s |
| reddit | AutoModerator (bot) | **Falla**: Reddit responde 403 al acceso anónimo. Aleph no intenta esquivarlo; hace falta la API oficial con credenciales | 1,2 s |
| telegram_channel | telegram (canal oficial) | OK | 1,6 s |
| domain_intel | example.com | OK | 2,5 s |
| username_search | octocat | OK | 3,8 s |
| x_api | — | Sin clave `ALEPH_X_BEARER_TOKEN`: responde 503 con el motivo, como corresponde | 0,0 s |

### IA local

| Función | Resultado | Tiempo |
|---|---|---|
| FUNES NER | OK: URL, correo, persona y lugares del texto de prueba | 5,9 s |
| Contradicciones | **No detectó** la contradicción planteada (Rosario 14:00 → Ushuaia 14:20): qwen3:8b sin razonamiento extrajo mal las afirmaciones. Con razonamiento las extrae bien pero tarda unos 3 minutos. Se mejoró el prompt (una afirmación por lugar y momento, con ejemplo) y se rescatan citas con palabras antepuestas; falta volver a medir | 3,7 s |
| Búsqueda por significado | **Falló** en esta corrida: un texto largo tiró abajo el proceso de embeddings de Ollama. Se limitó cada texto a 1000 caracteres | 5,4 s |
| Preguntar al caso | OK: listó cuentas y dominios recolectados, con citas; una cita apuntaba a la entidad equivocada | 8,5 s |
| Informe | OK | 16,9 s |
| TLÖN | OK: 178 entidades, 192 vínculos; propuso vínculos entre dominios typosquat e IPs y FUNES los marcó como descartables | 29 s |

### Incidente en el nodo

Durante las pruebas, la máquina virtual de Docker Desktop del nodo 1 llegó a su tope de 6 GB de
memoria y el motor de Docker dejó de responder (error 500). Los servicios publicados siguieron
contestando, pero no se pueden detener ni consultar contenedores hasta reiniciar Docker Desktop.
Para evitarlo: subir la memoria de WSL (`.wslconfig`) o limitar los modelos cargados a la vez.
