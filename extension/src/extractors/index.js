// Registro de extractores. Todos exponen la misma interfaz:
//   platform, SELECTORS, matches(url), pageKind(url),
//   findItems(root) -> Element[]            unidades capturables (publicaciones, comentarios, bloques)
//   extractItem(el, ctx) -> { records: [{account, post|null}], interactions, snippets?, primary } | null
//   extractPage(doc, ctx) -> { records, interactions, snippets }      datos de página (cabecera de perfil)
//   findHandleTargets(root) -> [{ element, handle }]                  dónde resaltar cuentas

import * as x from './x.js';
import * as instagram from './instagram.js';
import * as bluesky from './bluesky.js';
import * as reddit from './reddit.js';
import * as generic from './generic.js';

export const EXTRACTORS = { x, instagram, bluesky, reddit, generic };

export function extractorFor(url) {
  for (const ex of [x, instagram, bluesky, reddit]) if (ex.matches(url)) return ex;
  return generic;
}
