// Cola de envío con reintentos y espera exponencial. No depende de chrome.*: el envío, el reloj,
// el temporizador y la persistencia se inyectan, así se puede probar en Node.

export const QUEUE_DEFAULTS = {
  maxAttempts: 6,
  baseDelayMs: 2000,
  maxDelayMs: 120000,
  maxItems: 200,
};

export function backoffDelay(attempts, { baseDelayMs, maxDelayMs } = QUEUE_DEFAULTS) {
  return Math.min(maxDelayMs, baseDelayMs * 2 ** Math.max(0, attempts - 1));
}

export class SendQueue {
  /**
   * @param send     async (item) => void. Tira un error con .retriable / .auth para clasificar la falla.
   * @param persist  async (items) => void. Guarda la cola (por ejemplo en chrome.storage.session).
   * @param onEvent  (evento) => void. { type: "sent"|"retry"|"dropped"|"auth", item, error }
   */
  constructor({ send, persist = async () => {}, onEvent = () => {}, now = () => Date.now(),
    setTimer = (fn, ms) => setTimeout(fn, ms), clearTimer = (t) => clearTimeout(t), options = {} }) {
    this.send = send;
    this.persist = persist;
    this.onEvent = onEvent;
    this.now = now;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.options = { ...QUEUE_DEFAULTS, ...options };
    this.items = [];
    this.paused = false; // true tras un 401: espera a que el usuario vuelva a iniciar sesión
    this.running = null;
    this.timer = null;
    this.seq = 0;
  }

  restore(items) {
    this.items = Array.isArray(items) ? items.filter((i) => i && i.payload) : [];
    this.seq = this.items.reduce((max, i) => Math.max(max, Number(i.id) || 0), 0);
  }

  get size() {
    return this.items.length;
  }

  pendingFor(tabId) {
    return this.items.filter((i) => i.tabId === tabId).length;
  }

  async enqueue(entry) {
    const item = { id: ++this.seq, attempts: 0, nextAt: 0, createdAt: this.now(), ...entry };
    this.items.push(item);
    // Si la cola se llena (servidor caído mucho tiempo), se descarta lo más viejo y se avisa.
    while (this.items.length > this.options.maxItems) {
      const dropped = this.items.shift();
      this.onEvent({ type: 'dropped', item: dropped, error: new Error('Cola llena: se descartó el lote más viejo.') });
    }
    await this.persist(this.items);
    return item;
  }

  resume() {
    this.paused = false;
    for (const item of this.items) item.nextAt = 0;
    return this.process();
  }

  /** Procesa todo lo que ya está en hora. Las llamadas concurrentes comparten la misma pasada. */
  process() {
    if (!this.running) {
      this.running = this.#drain().finally(() => {
        this.running = null;
        this.#schedule();
      });
    }
    return this.running;
  }

  async #drain() {
    for (;;) {
      if (this.paused) return;
      const now = this.now();
      const item = this.items.find((i) => i.nextAt <= now);
      if (!item) return;
      item.attempts += 1;
      try {
        await this.send(item);
        this.#remove(item);
        this.onEvent({ type: 'sent', item });
      } catch (error) {
        if (error && error.auth) {
          item.attempts -= 1;
          this.paused = true;
          this.onEvent({ type: 'auth', item, error });
        } else if (error && error.retriable && item.attempts < this.options.maxAttempts) {
          item.nextAt = this.now() + backoffDelay(item.attempts, this.options);
          this.onEvent({ type: 'retry', item, error });
        } else {
          this.#remove(item);
          this.onEvent({ type: 'dropped', item, error });
        }
      }
      await this.persist(this.items);
    }
  }

  #remove(item) {
    const i = this.items.indexOf(item);
    if (i >= 0) this.items.splice(i, 1);
  }

  #schedule() {
    if (this.timer) this.clearTimer(this.timer);
    this.timer = null;
    if (this.paused || !this.items.length) return;
    const next = Math.min(...this.items.map((i) => i.nextAt));
    const wait = Math.max(0, next - this.now());
    this.timer = this.setTimer(() => {
      this.timer = null;
      this.process();
    }, wait);
  }
}
