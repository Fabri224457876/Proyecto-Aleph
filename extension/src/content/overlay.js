// Interfaz de Aleph Lens sobre la página: indicador de captura, insignias de cuentas, manija de
// datachunks, menú "Enviar a…", botón de selección y avisos. Todo vive en UN elemento con Shadow DOM
// cerrado y posición fija: los estilos no se mezclan con los de la página y la página no se modifica.

import { LENS_HOST_TAG } from '../lib/dom.js';
import { CHUNK_KIND_LABELS, CHUNK_MIME, SUGGESTED_SECTION } from '../lib/chunks.js';
import { clip } from '../lib/normalize.js';

const CSS = `
:host { all: initial; }
* { box-sizing: border-box; }
.layer { position: fixed; inset: 0; pointer-events: none; z-index: 2147483646;
  font: 12px/1.35 ui-monospace, "Cascadia Mono", "JetBrains Mono", Consolas, Menlo, monospace; color: #d7e3ea; }
.frame { position: fixed; inset: 0; border: 2px solid #35e0c2; opacity: .75; pointer-events: none; }
.frame.paused { border-color: #8a97a3; border-style: dashed; }
.indicator { position: fixed; top: 0; left: 50%; transform: translateX(-50%); display: flex; gap: 8px;
  align-items: center; padding: 4px 12px 5px; background: #0a1014; color: #35e0c2; border: 1px solid #35e0c2;
  border-top: 0; border-radius: 0 0 8px 8px; letter-spacing: .08em; text-transform: uppercase; font-size: 11px;
  white-space: nowrap; max-width: 90vw; overflow: hidden; text-overflow: ellipsis; }
.indicator.paused { color: #b6c0c8; border-color: #8a97a3; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; flex: none; }
.indicator:not(.paused) .dot { animation: pulse 1.6s ease-in-out infinite; }
.indicator .case { color: #d7e3ea; text-transform: none; letter-spacing: 0; }
@keyframes pulse { 50% { opacity: .25; } }

.mark { position: fixed; border: 1px solid #35e0c2; border-radius: 4px; pointer-events: none;
  box-shadow: 0 0 0 1px rgba(53,224,194,.18); }
.mark.menard { border-color: #ff5c7a; box-shadow: 0 0 0 1px rgba(255,92,122,.22); }
.badge { position: fixed; pointer-events: auto; cursor: help; padding: 0 5px; height: 15px; line-height: 15px;
  font-size: 10px; letter-spacing: .06em; background: #35e0c2; color: #06110f; border-radius: 3px;
  white-space: nowrap; animation: appear .25s ease-out; }
.badge.menard { background: #ff5c7a; color: #1a0508; }
@keyframes appear { from { opacity: 0; transform: translateY(3px); } }

.tooltip { position: fixed; max-width: 340px; padding: 8px 10px; background: #0a1014; border: 1px solid #2a3a44;
  border-left: 3px solid #35e0c2; border-radius: 4px; box-shadow: 0 6px 24px rgba(0,0,0,.5); pointer-events: none;
  white-space: pre-wrap; overflow-wrap: anywhere; }
.tooltip.menard { border-left-color: #ff5c7a; }
.tooltip.chunk { border-left-color: #ffd84d; }
.tooltip b { color: #fff; font-weight: 600; }
.tooltip .dim { color: #8a97a3; }

.grip { position: fixed; pointer-events: auto; display: flex; align-items: center; gap: 5px; height: 20px;
  padding: 0 7px 0 5px; background: #ffd84d; color: #14110a; border: 1px solid #14110a; border-radius: 4px 4px 4px 0;
  font-size: 11px; cursor: grab; user-select: none; box-shadow: 0 3px 10px rgba(0,0,0,.35); }
.grip:active { cursor: grabbing; }
.grip.sent { background: #1f9e86; color: #fff; }
.grip.known { background: #2f5fc4; color: #fff; }
.grip:focus-visible { outline: 2px solid #fff; outline-offset: 1px; }
.grip .dots { letter-spacing: -1px; opacity: .7; }
.outline { position: fixed; border: 1px solid #14110a; outline: 1px solid #ffd84d; border-radius: 2px; pointer-events: none; }

.menu { position: fixed; pointer-events: auto; min-width: 210px; max-width: 300px; max-height: 60vh; overflow: auto;
  background: #0a1014; border: 1px solid #35e0c2; border-radius: 6px; box-shadow: 0 10px 30px rgba(0,0,0,.6); padding: 4px; }
.menu .title { padding: 5px 8px 6px; color: #8a97a3; font-size: 10px; letter-spacing: .08em; text-transform: uppercase; }
.menu .value { padding: 0 8px 6px; color: #ffd84d; overflow-wrap: anywhere; }
.menu button { display: flex; justify-content: space-between; gap: 10px; width: 100%; padding: 5px 8px; background: none;
  border: 0; border-radius: 3px; color: #d7e3ea; font: inherit; text-align: left; cursor: pointer; }
.menu button:hover, .menu button:focus-visible { background: #14323a; outline: none; }
.menu button .hint { color: #35e0c2; font-size: 10px; }
.menu hr { border: 0; border-top: 1px solid #1c2a32; margin: 4px 0; }

.selbtn { position: fixed; pointer-events: auto; height: 22px; padding: 0 9px; background: #ffd84d; color: #14110a;
  border: 1px solid #14110a; border-radius: 4px; font: inherit; font-size: 11px; cursor: pointer;
  box-shadow: 0 3px 10px rgba(0,0,0,.35); }
.toast { position: fixed; left: 50%; bottom: 22px; transform: translateX(-50%); padding: 7px 14px; background: #0a1014;
  border: 1px solid #35e0c2; border-radius: 4px; color: #d7e3ea; pointer-events: none; animation: appear .2s ease-out; }
.toast.error { border-color: #ff5c7a; color: #ffc2cd; }
.ghost { position: fixed; background: #ffd84d; border-radius: 2px; pointer-events: none; opacity: .9;
  transition: transform .5s cubic-bezier(.5,0,.8,.4), opacity .5s ease-in; }
@media (prefers-reduced-motion: reduce) { .ghost, .badge, .toast { animation: none; transition: none; }
  .indicator .dot { animation: none; } }
`;

function el(tag, className, textValue) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (textValue !== undefined) node.textContent = textValue;
  return node;
}

function place(node, left, top) {
  node.style.left = `${Math.round(left)}px`;
  node.style.top = `${Math.round(top)}px`;
}

export class Overlay {
  /**
   * handlers: onSend(chunk, section|null), onDragStart(chunk), onDragEnd(), onManual(), getSections() -> Promise<[]>
   */
  constructor(handlers) {
    this.handlers = handlers;
    this.host = null;
    this.layer = null;
    this.marks = [];
    this.gripChunk = null;
    this.gripTimer = null;
    this.toastTimer = null;
  }

  mount() {
    if (this.host) return;
    this.host = document.createElement(LENS_HOST_TAG);
    const shadow = this.host.attachShadow({ mode: 'closed' });
    try {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(CSS);
      shadow.adoptedStyleSheets = [sheet];
    } catch {
      shadow.appendChild(el('style', '', CSS));
    }
    this.layer = el('div', 'layer');
    this.frame = el('div', 'frame');
    this.indicator = el('div', 'indicator');
    this.marksBox = el('div');
    this.tooltip = el('div', 'tooltip');
    this.tooltip.hidden = true;
    this.layer.append(this.frame, this.indicator, this.marksBox, this.tooltip);
    shadow.appendChild(this.layer);
    document.documentElement.appendChild(this.host);
  }

  unmount() {
    clearTimeout(this.gripTimer);
    clearTimeout(this.toastTimer);
    if (this.host) this.host.remove();
    this.host = null;
    this.layer = null;
    this.marks = [];
    this.grip = null;
    this.menu = null;
    this.selBtn = null;
  }

  contains(node) {
    return Boolean(this.host && node && (node === this.host || this.host.contains(node)));
  }

  // --- Indicador de captura: siempre visible mientras la captura está encendida ---
  setIndicator({ caseName, paused = false, reason = '' }) {
    if (!this.layer) return;
    this.indicator.textContent = '';
    this.indicator.classList.toggle('paused', paused);
    this.frame.classList.toggle('paused', paused);
    this.indicator.append(
      el('span', 'dot'),
      el('span', '', paused ? 'Aleph Lens · en pausa' : 'Aleph Lens · capturando'),
      el('span', 'case', paused ? clip(reason, 70) : `Caso: ${clip(caseName || '—', 40)}`),
    );
  }

  // --- Cuentas conocidas / con hipótesis MENARD ---
  /** marks: [{ rect, handle, level: "known"|"menard", score, lines }] */
  setAccountMarks(marks) {
    if (!this.layer) return;
    while (this.marks.length > marks.length) {
      const old = this.marks.pop();
      old.box.remove();
      old.badge.remove();
    }
    marks.forEach((m, i) => {
      let slot = this.marks[i];
      if (!slot) {
        slot = { box: el('div', 'mark'), badge: el('div', 'badge'), data: null };
        slot.badge.addEventListener('mouseenter', () => this.showTooltip(slot.data.lines, slot.badge.getBoundingClientRect(), slot.data.level));
        slot.badge.addEventListener('mouseleave', () => this.hideTooltip());
        this.marksBox.append(slot.box, slot.badge);
        this.marks.push(slot);
      }
      slot.data = m;
      const menard = m.level === 'menard';
      slot.box.classList.toggle('menard', menard);
      slot.badge.classList.toggle('menard', menard);
      const label = menard ? `MENARD ${m.score.toFixed(2)}` : 'EN EL CASO';
      if (slot.badge.textContent !== label) slot.badge.textContent = label;
      const r = m.rect;
      place(slot.box, r.left - 3, r.top - 2);
      slot.box.style.width = `${Math.round(r.width + 6)}px`;
      slot.box.style.height = `${Math.round(r.height + 4)}px`;
      place(slot.badge, r.left - 3, Math.max(0, r.top - 17));
    });
  }

  showTooltip(lines, rect, variant = '') {
    if (!this.layer || !lines || !lines.length) return;
    this.tooltip.textContent = '';
    this.tooltip.className = `tooltip ${variant}`;
    lines.forEach((line, i) => {
      const row = el('div', line.dim ? 'dim' : '');
      if (line.bold) row.append(el('b', '', line.text));
      else row.textContent = line.text;
      if (i === 0 && !line.bold) row.style.color = '#fff';
      this.tooltip.append(row);
    });
    this.tooltip.hidden = false;
    const box = this.tooltip.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;
    let top = rect.bottom + 6;
    if (top + box.height > vh - 4) top = Math.max(4, rect.top - box.height - 6);
    place(this.tooltip, Math.min(Math.max(4, rect.left), vw - box.width - 4), top);
  }

  hideTooltip() {
    if (this.tooltip) this.tooltip.hidden = true;
  }

  // --- Manija del datachunk: aparece al pasar por encima; se arrastra o se le hace clic ---
  showGrip(chunk, rects) {
    if (!this.layer || !rects.length) return;
    clearTimeout(this.gripTimer);
    if (this.gripChunk === chunk && this.grip && this.grip.isConnected) return;
    this.hideGrip(true);
    this.gripChunk = chunk;
    this.gripRects = rects;
    this.outlines = rects.map((r) => {
      const o = el('div', 'outline');
      place(o, r.left - 1, r.top - 1);
      o.style.width = `${Math.round(r.width + 2)}px`;
      o.style.height = `${Math.round(r.height + 2)}px`;
      this.layer.append(o);
      return o;
    });
    const grip = el('div', `grip ${chunk.status === 'new' ? '' : chunk.status}`);
    grip.tabIndex = 0;
    grip.draggable = true;
    grip.setAttribute('role', 'button');
    grip.append(el('span', 'dots', '⋮⋮'), el('span', '', CHUNK_KIND_LABELS[chunk.kind] || chunk.kind));
    const first = rects[0];
    place(grip, first.left - 1, Math.max(0, first.top - 21));
    grip.addEventListener('mouseenter', () => {
      clearTimeout(this.gripTimer);
      this.showTooltip(this.#chunkLines(chunk), grip.getBoundingClientRect(), 'chunk');
    });
    grip.addEventListener('mouseleave', () => { this.hideTooltip(); this.scheduleHideGrip(); });
    grip.addEventListener('click', (ev) => { ev.stopPropagation(); this.openMenu(chunk, grip.getBoundingClientRect()); });
    grip.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); this.openMenu(chunk, grip.getBoundingClientRect()); }
      if (ev.key === 'Escape') this.hideGrip(true);
    });
    grip.addEventListener('dragstart', (ev) => {
      const data = this.handlers.onDragStart(chunk);
      if (!data) { ev.preventDefault(); return; }
      ev.dataTransfer.effectAllowed = 'copy';
      ev.dataTransfer.setData(CHUNK_MIME, JSON.stringify(data));
      ev.dataTransfer.setData('text/plain', data.value);
      this.hideTooltip();
      this.closeMenu();
    });
    grip.addEventListener('dragend', () => { this.handlers.onDragEnd(); this.scheduleHideGrip(); });
    this.grip = grip;
    this.layer.append(grip);
  }

  scheduleHideGrip(ms = 450) {
    clearTimeout(this.gripTimer);
    this.gripTimer = setTimeout(() => { if (!this.menu) this.hideGrip(true); }, ms);
  }

  hideGrip(now = false) {
    if (!now) return this.scheduleHideGrip();
    clearTimeout(this.gripTimer);
    if (this.grip) this.grip.remove();
    for (const o of this.outlines || []) o.remove();
    this.grip = null;
    this.outlines = [];
    this.gripChunk = null;
    return undefined;
  }

  #chunkLines(chunk) {
    const lines = [
      { text: `${CHUNK_KIND_LABELS[chunk.kind] || chunk.kind}: ${clip(chunk.value, 120)}`, bold: true },
    ];
    if (chunk.status === 'sent') lines.push({ text: `En el expediente · inciso: ${chunk.section || 'Sin clasificar'}` });
    else if (chunk.status === 'known') lines.push({ text: 'El caso ya lo conoce.' });
    else lines.push({ text: 'Sugerencia local: todavía no está en el expediente.' });
    if (chunk.detected_by === 'funes') lines.push({ text: 'Detectado por las reglas de FUNES del servidor (sin IA): revisalo antes de usarlo.', dim: true });
    lines.push({ text: 'Arrastralo al panel o hacé clic para elegir inciso.', dim: true });
    return lines;
  }

  // --- Menú "Enviar a…": respaldo del arrastre, funciona siempre ---
  async openMenu(chunk, anchor) {
    if (!this.layer) return;
    this.closeMenu();
    clearTimeout(this.gripTimer);
    const menu = el('div', 'menu');
    menu.setAttribute('role', 'menu');
    menu.append(el('div', 'title', 'Enviar al expediente'), el('div', 'value', clip(chunk.value, 90)));
    this.menu = menu;
    this.layer.append(menu);
    place(menu, anchor.left, anchor.bottom + 4);
    let sections = [];
    try {
      sections = await this.handlers.getSections();
    } catch {
      sections = [];
    }
    if (this.menu !== menu) return;
    const suggested = SUGGESTED_SECTION[chunk.kind] || '';
    for (const section of sections) {
      const btn = el('button');
      btn.setAttribute('role', 'menuitem');
      btn.append(el('span', '', section.name));
      if (section.last) btn.append(el('span', 'hint', 'último'));
      else if (section.name === suggested) btn.append(el('span', 'hint', 'sugerido'));
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        this.closeMenu();
        this.handlers.onSend(chunk, section);
      });
      menu.append(btn);
    }
    const box = menu.getBoundingClientRect();
    const vh = document.documentElement.clientHeight;
    const vw = document.documentElement.clientWidth;
    place(menu, Math.min(anchor.left, vw - box.width - 6), anchor.bottom + 4 + box.height > vh ? Math.max(4, anchor.top - box.height - 4) : anchor.bottom + 4);
    const firstBtn = menu.querySelector('button');
    if (firstBtn) firstBtn.focus({ preventScroll: true });
    menu.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') { ev.stopPropagation(); this.closeMenu(); }
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        const items = [...menu.querySelectorAll('button')];
        const active = menu.getRootNode().activeElement;
        const i = items.indexOf(active);
        const next = items[(i + (ev.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length];
        if (next) next.focus({ preventScroll: true });
      }
    });
  }

  closeMenu() {
    if (this.menu) this.menu.remove();
    this.menu = null;
  }

  // --- Selección manual ---
  showSelectionButton(rect) {
    if (!this.layer) return;
    if (!this.selBtn) {
      this.selBtn = el('button', 'selbtn', '+ Datachunk');
      this.selBtn.title = 'Convertir la selección en datachunk (Alt+Shift+D)';
      // mousedown: evita que el clic borre la selección antes de leerla.
      this.selBtn.addEventListener('mousedown', (ev) => ev.preventDefault());
      this.selBtn.addEventListener('click', (ev) => { ev.stopPropagation(); this.handlers.onManual(); });
      this.layer.append(this.selBtn);
    }
    this.selBtn.hidden = false;
    const vw = document.documentElement.clientWidth;
    place(this.selBtn, Math.min(rect.right + 6, vw - 110), Math.max(2, rect.top - 26));
  }

  hideSelectionButton() {
    if (this.selBtn) this.selBtn.hidden = true;
  }

  toast(message, kind = '') {
    if (!this.layer) return;
    clearTimeout(this.toastTimer);
    if (this.toastEl) this.toastEl.remove();
    this.toastEl = el('div', `toast ${kind}`, message);
    this.layer.append(this.toastEl);
    this.toastTimer = setTimeout(() => { if (this.toastEl) this.toastEl.remove(); }, kind === 'error' ? 5000 : 2200);
  }

  /** Animación breve: el fragmento "vuela" hacia el panel lateral (a la derecha). */
  fly(rects) {
    if (!this.layer) return;
    for (const r of rects.slice(0, 3)) {
      const ghost = el('div', 'ghost');
      place(ghost, r.left, r.top);
      ghost.style.width = `${Math.round(r.width)}px`;
      ghost.style.height = `${Math.round(r.height)}px`;
      this.layer.append(ghost);
      const dx = document.documentElement.clientWidth - r.left;
      requestAnimationFrame(() => {
        ghost.style.transform = `translate(${Math.round(dx)}px, -40px) scale(.3)`;
        ghost.style.opacity = '0';
      });
      setTimeout(() => ghost.remove(), 650);
    }
  }
}
