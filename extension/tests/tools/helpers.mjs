import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const here = dirname(fileURLToPath(import.meta.url));
export const ROOT = join(here, '..', '..');

/** Carga un fixture como documento jsdom con la URL indicada (los extractores miran location). */
export function loadFixture(name, url) {
  const html = readFileSync(join(here, '..', 'fixtures', name), 'utf8');
  const dom = new JSDOM(html, { url });
  return { dom, window: dom.window, document: dom.window.document };
}

export function domFrom(html, url = 'https://sitio-demo.example/pagina') {
  const dom = new JSDOM(html, { url });
  return { dom, window: dom.window, document: dom.window.document };
}

/** Junta todo lo que un extractor saca de un documento: página + cada unidad. */
export function extractAll(extractor, document, url) {
  const out = { records: [], interactions: [], snippets: [], items: [] };
  const page = extractor.extractPage(document, { url });
  out.records.push(...page.records);
  out.interactions.push(...page.interactions);
  for (const el of extractor.findItems(document)) {
    const item = extractor.extractItem(el, { url });
    out.items.push(item);
    if (!item) continue;
    out.records.push(...item.records);
    out.interactions.push(...item.interactions);
    out.snippets.push(...(item.snippets || []));
  }
  return out;
}

export function postsOf(result, handle) {
  return result.records.filter((r) => r.post && r.account.handle.toLowerCase() === handle.toLowerCase()).map((r) => r.post);
}
