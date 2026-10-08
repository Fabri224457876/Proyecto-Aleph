# Aleph Lens

Extensión de navegador (Chrome y Edge, Manifest V3) para Aleph. Es una captura asistida: vos navegás con tu propia sesión, y la extensión lee lo que la página ya tiene cargado, lo manda al caso activo y resalta lo que el caso ya sabe. No navega, no hace clics y no automatiza nada.

> **Estado: prototipo para revisión.** Se probó cargándola en Microsoft Edge (Chromium) y contra la API real de Aleph. Los extractores de cada plataforma se probaron solo con HTML de ejemplo escrito a mano, **no contra las páginas reales**: hay que validarlos en vivo (ver "Selectores a validar en vivo").

## Qué hace y qué no hace

Hace:
- Mientras la captura está encendida, lee las publicaciones, perfiles e interacciones que ya están renderizados y los manda al caso en lotes.
- Resalta en la página fragmentos útiles: correos, teléfonos, dominios, URLs, IPs, hashes, billeteras, CVE, fechas, lugares y cuentas. Además suma lo que detectan las reglas del servidor (IOCs y reglas de FUNES, sin IA). Son reglas, no un modelo de lenguaje.
- Muestra si una cuenta ya está en el caso y, si hay una hipótesis de MENARD (mismo operador), la muestra como hipótesis para revisar.
- Te deja mandar un fragmento al expediente: arrastrándolo, con "Enviar a…" o desde una selección manual.

No hace:
- No navega, no hace clics ni scroll automático, y no cambia el HTML de la página. Solo agrega su propio elemento (cerrado) mientras la captura está encendida.
- No usa APIs privadas de las plataformas, no lee ni envía tus cookies y no inicia sesión en ningún servicio.
- No lee mensajes directos, correo, mensajería, pantallas de ingreso ni campos de contraseña.
- No manda nada a otro lado que no sea el servidor de Aleph que vos configurás.
- No resuelve CAPTCHAs, no evade bloqueos y no rota proxies.
- No decide nada por vos: un resaltado es una sugerencia local hasta que lo envíes, y una hipótesis de MENARD es para revisión humana, no un veredicto.

## Requisitos

- Microsoft Edge o Google Chrome 116 o superior.
- Un servidor de Aleph accesible desde tu equipo y un usuario.

## Cargarla en el navegador (modo desarrollador)

No está publicada en ninguna tienda: se carga descomprimida. Elegí la carpeta `extension/` (la que tiene `manifest.json`), no la raíz del repositorio.

**Chrome**
1. Abrí `chrome://extensions`.
2. Activá el modo de desarrollador (arriba a la derecha).
3. Tocá **Cargar descomprimida** y elegí la carpeta.
4. Si cambiás el código, tocá el botón de recargar de la tarjeta de la extensión.

**Edge**
1. Abrí `edge://extensions`.
2. Activá el modo de desarrollador (el texto cambia según el idioma).
3. Tocá **Cargar desempaquetada** y elegí la carpeta.

Para tener el ícono a mano, fijálo desde el menú de extensiones de la barra.

## Conectarla al servidor

1. Abrí **Opciones** (desde el panel lateral, o con clic derecho en el ícono → Opciones).
2. En "URL del servidor" poné la dirección de Aleph, por ejemplo `http://192.0.2.10:8100`, sin `/api` al final. Tocá **Guardar**: el navegador te pide permiso para hablar con ese servidor; aceptalo.
3. Ingresá tu usuario y contraseña de Aleph y tocá **Iniciar sesión**. Con **Recordar la sesión** el token queda guardado en el navegador, sin cifrar; sin esa casilla se borra al cerrar el navegador.
4. **Probar conexión** tiene que mostrar tu usuario y la cantidad de casos visibles.

Si el servidor no es local, usá HTTPS: el token viaja en cada pedido.

## Uso

1. Abrí la pestaña que querés documentar y tocá el ícono de Aleph Lens: se abre el panel lateral.
2. Elegí el caso. Los cerrados o archivados aparecen deshabilitados: el servidor no acepta capturas en ellos.
3. Encendé la captura con el interruptor. El navegador pide permiso para leer ese sitio: aceptalo solo si lo vas a documentar.
4. Mientras está encendida, arriba al centro de la página aparece "Aleph Lens · capturando" y un borde de color. Al apagarla, todo eso desaparece.
5. Navegá normalmente. El panel muestra cuántas cuentas y publicaciones se fueron capturando en la sesión, cuántos lotes se enviaron y si hay algo en cola.

Cómo leer la página:
- **Amarillo:** fragmento detectado. Es una sugerencia local: no entra al expediente hasta que lo mandes.
- **Verde:** ya está en el expediente.
- **Azul:** cuenta que el caso ya conoce.
- **Insignia "EN EL CASO" o "MENARD 0.87" (rojo):** cuenta conocida o hipótesis de mismo operador, para revisar.

Cómo mandar un fragmento al expediente:
- **Arrastrarlo:** pasá el mouse por el fragmento para que aparezca su manija y arrastrala hasta un inciso del panel. Si la soltás sobre una entidad del grafo, queda adjunta a ella.
- **Enviar a…:** hacé clic en la manija y elegí el inciso.
- **Selección manual:** seleccioná un texto y tocá **+ Datachunk**.
- **Atajos** (opcionales, se cambian en `chrome://extensions/shortcuts` o `edge://extensions/shortcuts`): Alt+Shift+S manda el fragmento enfocado al último inciso usado; Alt+Shift+D convierte la selección en fragmento.

## Qué se guarda y dónde

- **En el servidor:** cada lote de captura (cuentas, publicaciones, interacciones y texto de la página) queda como fuente, con URL, hora y hash, y en la auditoría como `collection.ingest`. Los fragmentos resaltados no se guardan: solo lo que enviás al expediente.
- Para detectar fragmentos con las reglas del servidor, el texto de la página se manda una vez más al servidor (`captures/chunks`). Ese pedido no guarda nada: queda en la auditoría como `captures.chunks`, con cantidades y sin el texto.
- **En el navegador:** el estado de cada pestaña (hasta que se cierra el navegador), el registro de lo ya enviado por caso y, si lo marcaste, el token.
- Antes de encender la captura, tené claro el propósito y la base legal del caso. Encendela solo en lo que vas a documentar y apagala después.

## Dónde no lee

Estas rutas se excluyen por URL: la captura queda en pausa y no se lee nada.
- Mensajes directos y chats de X, Instagram, Bluesky, Reddit, Facebook, LinkedIn, TikTok y Discord.
- Correo y mensajería: Gmail, Outlook, Yahoo, Proton, WhatsApp Web, Telegram Web, Messenger, Slack, Teams y Signal.
- Pantallas de ingreso, de cuenta y de ajustes, y cualquier formulario o diálogo con campo de contraseña.

La lista no reemplaza tu criterio: no la encendás en banca, historias clínicas ni cuentas personales ajenas al caso.

## Selectores a validar en vivo

Las plataformas cambian su HTML seguido. Antes de confiar en una captura, verificá cada punto con la sesión iniciada. En DevTools (F12, pestaña Consola), `document.querySelectorAll('<selector>').length` tiene que dar más de 0 en la página indicada.

**X (x.com)**: `src/extractors/x.js`, objeto `SELECTORS`.
- Inicio (`/home`): los tweets (`article[data-testid="tweet"]`) suman en "publicaciones" al bajar, y las menciones, URLs y fechas se resaltan. Verificar `[data-testid="tweetText"]` y `[data-testid="User-Name"]`.
- Perfil (`/<usuario>`): aparece "Cuenta nueva en pantalla" en Hallazgos. Verificar `[data-testid="UserName"]`, `[data-testid="UserDescription"]` y `[data-testid="UserJoinDate"]`.
- Hilo (`/<usuario>/status/<id>`): las respuestas aparecen como "responde a". Verificar `[data-testid="socialContext"]` y `a[href*="/status/"]`.
- Seguidores (`/<usuario>/followers`): verificar `[data-testid="UserCell"]`.
- Control: en `/messages` el panel no tiene que dejar encender la captura ("Mensajes privados…").

**Instagram**: `src/extractors/instagram.js`. Es el extractor menos confiable.
- Publicación (`/p/<código>/`): se leen el pie de foto (`h1`) y los comentarios (cada uno con su `time[datetime]`). Verificar que existan `h1` y `time[datetime]`.
- Perfil (`/<usuario>/`): los datos salen de `og:title`, `og:description` y la cabecera. Verificar que `meta[property="og:description"]` diga "X followers, Y following…" y que `header section` exista. Los seguidores y seguidos guardados en la cuenta tienen que coincidir con los del perfil.
- Feed y explorar: no se captura nada, a propósito.

**Bluesky (bsky.app)**: `src/extractors/bluesky.js`.
- Feed (`/`): cada post tiene `[data-testid^="feedItem-by-"]` y su texto `[data-testid="postText"]`. El handle sale del propio `data-testid`.
- Perfil (`/profile/<handle>`): `[data-testid="profileHeaderDisplayName"]`, `[data-testid="profileHeaderDescription"]` y `[data-testid="profileHeaderFollowersButton"]`.
- Hilo (`/profile/<handle>/post/<rkey>`): `[data-testid^="postThreadItem-by-"]`.

**Reddit**: `src/extractors/reddit.js`. Cubre la interfaz nueva y la vieja.
- Reddit nuevo (`www.reddit.com/r/<sub>/comments/…`): `shreddit-post` y `shreddit-comment`, con los datos en atributos (`author`, `post-title`, `created-timestamp`, `thingid`, `depth`). Verificar que `[slot="text-body"]` y `[slot="comment"]` tengan texto.
- Reddit viejo (`old.reddit.com/r/<sub>/comments/…`): `.thing[data-fullname][data-author]`, `.usertext-body .md` y `time[datetime]` dentro de `.entry`.
- Perfil (`/user/<nombre>`): `shreddit-profile-header` o `[data-testid="profile-main"]`, y `[data-testid="karma-number"]`.

**Cómo corregir un selector que cambió**: abrí el archivo indicado, buscá el objeto `SELECTORS` y reemplazá la entrada que falló. Si el cambio es de estructura (cómo se detecta una publicación o un comentario), la lógica está en el mismo archivo (`pageKind`, `findItems`, `extractItem`). Después actualizá el fixture de `tests/fixtures/` que corresponda y corré `npm test`. Los fixtures tienen que ser HTML sintético: no pegues páginas reales con personas reales.

## Desarrollo y pruebas

- `npm install` (una vez), `npm test` y `npm run check`.
- `node tests/tools/smoke-browser.mjs` carga la extensión en Edge o Chrome sin interfaz y recorre una captura contra el servidor simulado. Usa una copia con permiso fijo para `127.0.0.1`, porque la extensión real no lo tiene. Si el navegador no está en la ruta por defecto, pasala como argumento.
- `npm run mock-server` levanta el servidor simulado en `http://127.0.0.1:8199`, para probar a mano.

Estructura: `src/background/` (service worker: token, cola de envío, estado por pestaña), `src/content/` (lectura pasiva y resaltado), `src/extractors/` (un módulo por plataforma), `src/lib/` (lógica pura: detector, cliente de la API, cola, grafo), `src/sidepanel/` (panel lateral) y `src/options/` (opciones).

## Lo verificado y lo que no

**Verificado:**
- `npm test` y `npm run check` pasan.
- En Microsoft Edge (Chromium, versión 154), la extensión carga sin errores, el service worker arranca, el content script se inyecta, resalta y envía lotes y hallazgos a la API real de Aleph, con CORS desde el origen de la extensión. La prueba usó una copia del manifest que solo da permiso al sitio de prueba.
- Con una página de prueba con CSP estricta (`script-src 'self'`), el content script carga igual.
- Las rutas que usa el cliente existen en los routers del backend, y los campos de lotes, hallazgos, incisos y de `captures/chunks` coinciden con el backend. Hay pruebas que lo controlan.
- `captures/chunks` contra la API real: la extensión manda el texto y recibe datachunks de las reglas del servidor. Si el endpoint no responde (404, 405 o 501), la extensión sigue sola con sus reglas.

**No verificado:**
- Google Chrome: no estaba instalado en el equipo de prueba. Es el mismo motor, pero no se probó directamente.
- X, Instagram, Bluesky y Reddit reales: los extractores se probaron solo con HTML escrito a mano.
- Los pasos que requieren un clic humano: el ícono, el permiso del sitio y el del servidor.
- Los atajos de teclado.
- Rendimiento en páginas muy largas, como un timeline de miles de publicaciones.
- Calidad de las detecciones de las reglas del servidor sobre páginas reales: se probó con textos de ejemplo.
- Firefox: no es compatible.

**Limitaciones conocidas:**
- Chrome exige declarar los módulos del content script como recursos accesibles desde la web (`web_accessible_resources`). Sin eso la captura no arranca. La consecuencia es que una página puede detectar que la extensión está instalada.
- Para armar la lista de cuentas de la página, la extensión consulta al servidor de Aleph por los handles que están en el DOM, no solo por los visibles. El pedido va únicamente a tu servidor.
