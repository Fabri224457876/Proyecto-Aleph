# Aleph · frontend

Consola de analista de Aleph (P.R.O.A.). React + TypeScript + Vite, grafo con Cytoscape.js y CSS propio.
Habla con la API real de `backend/` (ver `docs/openapi.json`); no tiene datos propios.

## Levantarlo

Requisitos: Node 24 y la API de Aleph corriendo en `127.0.0.1:8100`.

```powershell
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173 (el proxy envía /api a 127.0.0.1:8100)
```

Para apuntar a otra API: `ALEPH_API_TARGET=http://host:puerto npm run dev`.

Build de producción: `npm run build` (typecheck + Vite). Para revisarlo: `npm run preview` (puerto 8101).

## Primer administrador y datos

La API no trae usuario por defecto. Desde la raíz del repo, con la base que use la API:

```powershell
$env:ALEPH_ADMIN_PASSWORD = "<contraseña>"
.venv\Scripts\python -m aleph.api.bootstrap --username <usuario>
```

Con un administrador creado, la pantalla de ingreso pide usuario y contraseña. Los roles son
administrador, analista y auditor; la interfaz oculta o deshabilita lo que cada uno no puede hacer.

## Pantallas

- **Casos**: lista con TLP, estado y fecha. Abrir un caso exige propósito y base legal.
- **Caso**: cabecera con contadores y acciones (editar, cerrar, archivar, reabrir, borrar), y pestañas:
  - **Grafo**: filtros por tipo, estado y confianza; búsqueda; inspector con procedencia, vecinos, hallazgos
    y acciones (confirmar, rechazar, editar, fusionar, relacionar, borrar); camino más corto entre dos nodos.
  - **Expediente**: incisos como columnas, hallazgos con cita, contexto, URL y fecha; arrastrar entre incisos
    (o usar el selector); crear, renombrar y borrar incisos.
  - **MENARD**: correr el análisis; hipótesis por puntaje; comparación lado a lado; desglose por familia y
    evidencia de cada señal; confirmar o rechazar con nota obligatoria. Son hipótesis, no identificaciones.
  - **Cuentas**, **Línea de tiempo** y **Fuentes** (Admiralty Code, alta manual, subida de archivos con hash y
    descarga del crudo verificada).
- **Auditoría** (auditor y administrador): verificación de la cadena de hashes y tabla de eventos con filtros.

## Estructura

```
src/api/         cliente HTTP, tipos y endpoints (uno por ruta de openapi.json)
src/lib/         router, autenticación, formatos, etiquetas en español, carga de datos
src/components/  piezas de interfaz (botones, insignias TLP, modales, estados de carga/vacío/error)
src/pages/       ingreso, casos, caso y auditoría; las pestañas del caso están en src/pages/case/
src/styles.css   tokens de la consola (la misma base de color que la extensión, con acento ámbar)
screenshots/     capturas de las pantallas principales
```

El visor del grafo se carga de forma diferida: Cytoscape no pesa en la primera carga.
