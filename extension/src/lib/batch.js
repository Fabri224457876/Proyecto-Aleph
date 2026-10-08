// Acumulador de captura: deduplica cuentas, publicaciones, interacciones y fragmentos de texto,
// y arma lotes con la forma exacta de CaptureBatch (core/schemas.py).

import { makeAccount, makePost, handleKey, cleanText, shortHash } from './normalize.js';

export const LIMITS = {
  postsPerBatch: 80,
  snippetsPerBatch: 40,
  snippetChars: 4000,
  interactionsPerBatch: 400,
};

const MERGE_TEXT = ['platform_uid', 'display_name', 'bio', 'url', 'avatar_url'];

function accountFingerprint(a) {
  return JSON.stringify([
    a.platform_uid, a.display_name, a.bio, a.url, a.created_at_platform, a.followers, a.following,
    a.avatar_url, a.following_handles.length, a.follower_handles.length,
  ]);
}

export class CaptureAccumulator {
  constructor() {
    this.accounts = new Map(); // key -> { account, dirty, sent }
    this.postIds = new Set(); // "key|post_id" ya vistos
    this.pendingPosts = new Map(); // key -> PostRecord[]
    this.interactionKeys = new Set();
    this.pendingInteractions = [];
    this.snippetKeys = new Set();
    this.pendingSnippets = [];
    this.totals = { accounts: 0, posts: 0, interactions: 0, snippets: 0 };
  }

  static key(platform, handle) {
    return `${platform}:${handleKey(handle)}`;
  }

  /**
   * Suma una cuenta (y opcionalmente una publicación suya). Devuelve qué fue nuevo.
   * Los datos de la cuenta se fusionan: un valor no vacío nunca se pisa con uno vacío.
   * `authoritative`: los datos vienen de la cabecera del perfil y reemplazan a los ya vistos.
   */
  add(accountPartial, postPartial = null, { authoritative = false } = {}) {
    const incoming = makeAccount(accountPartial);
    const result = { key: '', newAccount: false, newPost: false };
    if (!incoming.platform || !incoming.handle) return result;
    const key = CaptureAccumulator.key(incoming.platform, incoming.handle);
    result.key = key;

    let entry = this.accounts.get(key);
    if (!entry) {
      entry = { account: incoming, dirty: true, sent: false };
      this.accounts.set(key, entry);
      this.totals.accounts += 1;
      result.newAccount = true;
    } else {
      const before = accountFingerprint(entry.account);
      const acc = entry.account;
      // Los datos sueltos (un nombre visto en un repost) solo completan huecos; la cabecera del
      // perfil ("authoritative") sí reemplaza. Así una cuenta no "cambia" con cada aparición.
      for (const f of MERGE_TEXT) if (incoming[f] && incoming[f] !== acc[f] && (authoritative || !acc[f])) acc[f] = incoming[f];
      if (incoming.created_at_platform) acc.created_at_platform = incoming.created_at_platform;
      if (incoming.followers !== null) acc.followers = incoming.followers;
      if (incoming.following !== null) acc.following = incoming.following;
      for (const f of ['following_handles', 'follower_handles']) {
        const seen = new Set(acc[f].map((h) => h.toLowerCase()));
        for (const h of incoming[f]) if (!seen.has(h.toLowerCase())) { seen.add(h.toLowerCase()); acc[f].push(h); }
      }
      Object.assign(acc.meta, incoming.meta);
      if (accountFingerprint(acc) !== before) entry.dirty = true;
    }

    if (postPartial) {
      const post = makePost(postPartial);
      const pid = `${key}|${post.platform_post_id}`;
      if (post.platform_post_id && !this.postIds.has(pid)) {
        this.postIds.add(pid);
        if (!this.pendingPosts.has(key)) this.pendingPosts.set(key, []);
        this.pendingPosts.get(key).push(post);
        this.totals.posts += 1;
        result.newPost = true;
      }
    }
    return result;
  }

  /** Interacción vista en pantalla: (origen, destino, tipo). Handles sin @ y en minúscula. */
  addInteraction(src, dst, type) {
    const a = handleKey(src);
    const b = handleKey(dst);
    const t = String(type || '').trim().toLowerCase();
    if (!a || !b || !t || a === b) return false;
    const k = `${a}|${b}|${t}`;
    if (this.interactionKeys.has(k)) return false;
    this.interactionKeys.add(k);
    this.pendingInteractions.push([a, b, t]);
    this.totals.interactions += 1;
    return true;
  }

  addSnippet(text) {
    const s = cleanText(text).slice(0, LIMITS.snippetChars);
    if (s.length < 3) return false;
    const k = shortHash(s) + s.length;
    if (this.snippetKeys.has(k)) return false;
    this.snippetKeys.add(k);
    this.pendingSnippets.push(s);
    this.totals.snippets += 1;
    return true;
  }

  hasPending() {
    if (this.pendingPosts.size || this.pendingInteractions.length || this.pendingSnippets.length) return true;
    for (const e of this.accounts.values()) if (e.dirty) return true;
    return false;
  }

  /**
   * Saca lo pendiente como un CaptureBatch, o null si no hay nada nuevo. Lo que sale no vuelve a salir.
   * Si hay más de LIMITS.postsPerBatch publicaciones pendientes, quedan para la próxima llamada.
   */
  drain({ pageUrl, pageTitle = '', platform, now = new Date(), version = '' }) {
    if (!this.hasPending()) return null;
    const profiles = [];
    let budget = LIMITS.postsPerBatch;

    for (const [key, entry] of this.accounts) {
      const queued = this.pendingPosts.get(key) || [];
      if (!entry.dirty && !queued.length) continue;
      if (budget <= 0 && queued.length) continue;
      const posts = queued.splice(0, Math.max(budget, 0));
      budget -= posts.length;
      if (!queued.length) this.pendingPosts.delete(key);
      profiles.push({ account: structuredClone(entry.account), posts });
      entry.dirty = false;
      entry.sent = true;
    }

    return {
      page_url: String(pageUrl || ''),
      page_title: String(pageTitle || ''),
      platform: String(platform || 'generic'),
      captured_at: (now instanceof Date ? now : new Date(now)).toISOString(),
      profiles,
      interactions: this.pendingInteractions.splice(0, LIMITS.interactionsPerBatch),
      text_snippets: this.pendingSnippets.splice(0, LIMITS.snippetsPerBatch),
      extension_version: String(version || ''),
    };
  }
}
