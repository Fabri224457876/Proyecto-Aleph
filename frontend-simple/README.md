# Aleph · consola simple

Segunda interfaz de Aleph, pensada para quien nunca usó la herramienta. Organiza el trabajo en tres momentos: **Reunir**, **Revisar** y **Entender y contar**. La consola completa sigue en `frontend/`; esta vive aparte y no importa nada de ella.

Cobertura de la API: ver [COBERTURA.md](COBERTURA.md).

## Levantarla

Requisitos: Node 20 o superior, y una API de Aleph corriendo.

```powershell
cd frontend-simple
npm install
npm run dev
```

Queda en <http://127.0.0.1:5180>. Las llamadas a `/api` y a `/health` se reenvían a la API. Por defecto apunta a `http://127.0.0.1:8200`; para otra instancia:

```powershell
$env:ALEPH_API_TARGET = "http://127.0.0.1:8300"
npm run dev
```

Para una compilación de producción: `npm run build` (typecheck + Vite). `npm run preview` sirve la compilación en <http://127.0.0.1:5181>.

## API de prueba con el caso de demostración

Para probar sin tocar la base de nadie, usar una base temporal fuera del repo. Las contraseñas se pasan por argumento y no se escriben en ningún archivo:

```powershell
$env:ALEPH_DATABASE_URL = "sqlite:///C:/ruta/temporal/simple.db"
$env:ALEPH_DATA_DIR = "C:/ruta/temporal/files"
.venv\Scripts\python -m aleph.demo.seed --password "<clave-analista>" --admin-password "<clave-admin>"
.venv\Scripts\python -m uvicorn aleph.main:app --port 8200
```

El caso de demostración es ficticio. Usuarios: `analista-demo` y `auditor-demo` (la clave de `--password`) y el administrador (la de `--admin-password`).

## Estructura

- `src/api/`: cliente de la API y tipos (`client.ts`, `types.ts` y `endpoints.ts` vienen de la consola completa; `contract.ts` agrega lo que la consola simple usa).
- `src/lib/reunirActions.ts`: qué acciones ofrece cada tipo de dato, de dónde sale y qué queda en el caso.
- `src/pages/`: una pantalla por ruta. `revisar/` tiene la vista de decisión, `entender/` las vistas del caso.
- `src/components/`: piezas compartidas, el contenedor del caso y el grafo (Cytoscape, cargado en diferido).

## Teclado

En la vista de decisión de Revisar: `C` confirma, `D` descarta, `→` y `←` cambian de ítem, `Esc` vuelve a la lista. Los atajos no se disparan mientras se escribe en una nota.
