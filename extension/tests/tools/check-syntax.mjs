// Verifica que la extensión se pueda cargar descomprimida: `node --check` sobre cada JS, manifest MV3
// coherente y todos los archivos referenciados presentes.   Uso: npm run check

import { execFileSync } from 'node:child_process';
import { readFileSync, readdirSync, existsSync, statSync } from 'node:fs';
import { join, relative, dirname } from 'node:path';
import { ROOT } from './helpers.mjs';

export function listFiles(dir, ext) {
  const out = [];
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name.startsWith('.')) continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...listFiles(full, ext));
    else if (ext.some((e) => name.endsWith(e))) out.push(full);
  }
  return out;
}

export function checkAll() {
  const problems = [];
  const scripts = [...listFiles(join(ROOT, 'src'), ['.js']), ...listFiles(join(ROOT, 'tests'), ['.js', '.mjs']), ...listFiles(join(ROOT, 'tools'), ['.mjs'])];
  for (const file of scripts) {
    try {
      execFileSync(process.execPath, ['--check', file], { stdio: 'pipe' });
    } catch (err) {
      problems.push(`${relative(ROOT, file)}: ${String(err.stderr || err.message).split('\n').slice(0, 4).join(' ')}`);
    }
  }

  const manifest = JSON.parse(readFileSync(join(ROOT, 'manifest.json'), 'utf8'));
  const need = (cond, message) => { if (!cond) problems.push(`manifest.json: ${message}`); };
  need(manifest.manifest_version === 3, 'manifest_version tiene que ser 3');
  need(/^\d+(\.\d+){0,3}$/.test(manifest.version || ''), 'version inválida');
  need(typeof manifest.name === 'string' && manifest.name.length <= 75, 'name');
  need((manifest.description || '').length <= 132, 'description supera los 132 caracteres');
  const allowed = ['activeTab', 'scripting', 'sidePanel', 'storage'];
  need(JSON.stringify([...(manifest.permissions || [])].sort()) === JSON.stringify(allowed), `permissions tiene que ser exactamente ${allowed.join(', ')}`);
  need(!manifest.host_permissions, 'no tiene que haber host_permissions fijos');
  need(!manifest.content_scripts, 'no tiene que haber content_scripts declarados: se inyecta a pedido');
  const everything = JSON.stringify(manifest);
  need(!everything.includes('<all_urls>'), 'no usar <all_urls>');
  need(Array.isArray(manifest.optional_host_permissions) && manifest.optional_host_permissions.length > 0, 'faltan optional_host_permissions');
  need(manifest.background && manifest.background.type === 'module', 'el service worker tiene que ser módulo');

  const referenced = [
    manifest.background && manifest.background.service_worker,
    manifest.side_panel && manifest.side_panel.default_path,
    manifest.options_ui && manifest.options_ui.page,
    ...Object.values(manifest.icons || {}),
    ...Object.values((manifest.action && manifest.action.default_icon) || {}),
    'src/content/loader.js', 'src/content/main.js', 'src/content/highlight.css',
  ];
  for (const ref of referenced) need(ref && existsSync(join(ROOT, ref)), `falta el archivo ${ref}`);

  // Páginas de la extensión: los <script src> y <link href> locales tienen que existir, sin JS en línea.
  for (const page of listFiles(join(ROOT, 'src'), ['.html'])) {
    const html = readFileSync(page, 'utf8');
    for (const m of html.matchAll(/(?:src|href)="([^"#:]+)"/g)) {
      if (!existsSync(join(dirname(page), m[1]))) problems.push(`${relative(ROOT, page)}: falta ${m[1]}`);
    }
    if (/<script(?![^>]*\bsrc=)[^>]*>/i.test(html) || /\son[a-z]+="/i.test(html)) problems.push(`${relative(ROOT, page)}: JS en línea (lo bloquea la CSP de MV3)`);
  }

  // Todo módulo que el content script importa tiene que estar en web_accessible_resources.
  const war = (manifest.web_accessible_resources || []).flatMap((w) => w.resources);
  const covered = (rel) => war.some((pattern) => new RegExp(`^${pattern.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '[^/]*')}$`).test(rel));
  const seen = new Set();
  const visit = (file) => {
    const rel = relative(ROOT, file).replace(/\\/g, '/');
    if (seen.has(rel)) return;
    seen.add(rel);
    if (!covered(rel)) problems.push(`manifest.json: ${rel} lo importa el content script pero no está en web_accessible_resources`);
    for (const m of readFileSync(file, 'utf8').matchAll(/(?:from|import)\s+'(\.[^']+)'/g)) visit(join(dirname(file), m[1]));
  };
  visit(join(ROOT, 'src/content/main.js'));

  return { problems, scripts: scripts.length, contentModules: seen.size };
}

if (process.argv[1] && process.argv[1].endsWith('check-syntax.mjs')) {
  const { problems, scripts, contentModules } = checkAll();
  if (problems.length) {
    console.error(problems.join('\n'));
    process.exit(1);
  }
  console.log(`OK: ${scripts} archivos JS sin errores de sintaxis, manifest MV3 válido, ${contentModules} módulos del content script accesibles.`);
}
