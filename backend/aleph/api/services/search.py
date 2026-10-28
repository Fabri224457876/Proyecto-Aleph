"""Búsqueda dentro de Aleph: por texto (sin distinguir tildes ni mayúsculas) y por significado.

La búsqueda por texto arma un prefiltro en SQL y puntúa en Python con el texto plegado. La
semántica, si hay endpoint de embeddings, reordena los candidatos con bge-m3 (o el modelo que
esté configurado); si no responde, queda la búsqueda por texto y un aviso. Todo se limita a los
casos que el usuario ve (`scope_to_user`).
"""

import asyncio
import hashlib
import re
from collections import OrderedDict
from dataclasses import dataclass, field

import httpx
import numpy as np
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from aleph.core.models import Account, AccountLink, Case, Entity, Finding, Post, Source, User
from aleph.funes.client import FunesClient, LLMError, NonLocalEndpointError
from aleph.tlon.features import fold

from ..deps import scope_to_user

KINDS = ("case", "entity", "account", "post", "source", "finding", "link")
PREFIX = {"case": "K", "entity": "E", "account": "A", "post": "P", "source": "S", "finding": "F", "link": "H"}
PER_KIND = 200  # candidatos por tipo desde SQL
# Lo que aparece solo por significado tiene que parecerse bastante (coseno de bge-m3).
SEMANTIC_ONLY_MIN = 0.5
EMBED_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
_VOWELS = re.compile(r"[aeiouñn]")


@dataclass
class Hit:
    kind: str
    id: int
    case_id: int
    title: str
    snippet: str
    score: float = 0.0
    lexical: float = 0.0
    semantic: float | None = None
    status: str = ""
    text: str = ""  # texto completo para embeddings y para el contexto de una pregunta
    extra: dict = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{PREFIX[self.kind]}{self.id}"


# Palabras vacías (ya plegadas): en una pregunta no ayudan a encontrar y traen ruido.
STOPWORDS = frozenset(["a", "al", "algo", "algun", "alguna", "algunas", "alguno", "algunos", "ante", "antes", "aun", "bajo", "cada", "como", "con", "contra", "cual", "cuales", "cuando", "cuanto", "de", "del", "desde", "donde", "dos", "e", "el", "ella", "ellas", "ellos", "en", "entre", "era", "es", "esa", "esas", "ese", "eso", "esos", "esta", "estan", "estas", "este", "esto", "estos", "fue", "fueron", "ha", "hay", "hasta", "la", "las", "le", "les", "lo", "los", "mas", "me", "mi", "mucho", "muy", "nada", "ni", "no", "nos", "o", "otra", "otras", "otro", "otros", "para", "pero", "poco", "por", "porque", "que", "quien", "quienes", "se", "segun", "ser", "si", "sin", "sobre", "son", "su", "sus", "tambien", "tan", "tanto", "te", "tiene", "tienen", "todo", "todos", "tu", "un", "una", "uno", "unos", "usa", "usan", "y", "ya", "hace", "hacen", "cuantos", "cuantas", "puede", "pueden"])


# Cómo se nombra cada tipo de entidad en una pregunta (plegado y en singular).
TYPE_ES = {
    "person": "persona", "account": "cuenta", "email": "correo", "phone": "telefono", "domain": "dominio",
    "ip": "ip", "url": "url enlace", "organization": "organizacion empresa", "location": "lugar ubicacion",
    "event": "evento", "hash": "hash archivo", "wallet": "billetera wallet cripto", "document": "documento",
    "vehicle": "vehiculo", "malware": "malware troyano", "vulnerability": "vulnerabilidad cve",
    "bank_account": "cuenta bancaria cbu", "alias": "alias",
}


def singular(token: str) -> str:
    """Plural español simple: billeteras → billetera, direcciones → direccion."""
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
        if token.endswith("e") and len(token) > 5 and token[-2] not in "aeiou":
            token = token[:-1]
    return token


def tokens(query: str) -> list[str]:
    raw = [t.strip(".-") for t in re.split(r"[^\w@.#-]+", fold(query))]
    return [singular(t) if "." not in t and "@" not in t else t
            for t in raw if len(t) >= 2 and t not in STOPWORDS][:12]


def types_named(toks: list[str]) -> list[str]:
    return [etype for etype, words in TYPE_ES.items() if any(t in words.split() for t in toks)]


def _like(token: str) -> str:
    # Las vocales (y la ñ) pasan a comodín de un carácter: "auditoria" encuentra "auditoría".
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{_VOWELS.sub('_', escaped)}%"


def _prefilter(columns, toks: list[str]):
    return or_(*[func.lower(col).like(_like(t), escape="\\") for t in toks for col in columns])


def _snippet(text: str, toks: list[str], width: int = 90) -> str:
    folded = fold(text)
    pos = min((folded.find(t) for t in toks if folded.find(t) >= 0), default=-1)
    if pos < 0:
        return text[: 2 * width].strip()
    start = max(0, pos - width)
    return ("…" if start else "") + text[start:pos + width].strip() + ("…" if pos + width < len(text) else "")


def _score(fields: list[tuple[str, float]], toks: list[str], phrase: str) -> float:
    """Suma ponderada por campo; premia que estén todos los términos y la frase entera."""
    if not toks:
        return 0.0
    total, matched = 0.0, set()
    for text, weight in fields:
        folded = fold(text or "")
        if not folded:
            continue
        for t in toks:
            if t in folded:
                matched.add(t)
                total += weight
                if re.search(rf"(^|[\s@#._-]){re.escape(t)}", folded):
                    total += 0.5 * weight
        if len(toks) > 1 and phrase in folded:
            total += 2 * weight
    return total * (len(matched) / len(toks)) ** 2


def lexical_search(session: Session, user: User, query: str, *, case_id: int | None = None,
                   kinds: tuple[str, ...] = KINDS, include_rejected: bool = False) -> list[Hit]:
    toks = tokens(query)
    if not toks:
        return []
    phrase = " ".join(toks)
    hits: list[Hit] = []

    def scoped(stmt, column):
        stmt = scope_to_user(stmt, user, column)
        return stmt.where(column == case_id) if case_id is not None else stmt

    if "case" in kinds:
        stmt = scoped(select(Case).where(_prefilter([Case.name, Case.description], toks)), Case.id)
        for c in session.execute(stmt.limit(PER_KIND)).scalars():
            s = _score([(c.name, 3), (c.description, 1)], toks, phrase)
            if s:
                hits.append(Hit("case", c.id, c.id, c.name, _snippet(c.description or c.name, toks), lexical=s,
                                status=c.status, text=f"{c.name}. {c.description}"))
    if "entity" in kinds:
        named = types_named(toks)
        cond = _prefilter([Entity.label], toks)
        stmt = select(Entity).where(or_(cond, Entity.type.in_(named)) if named else cond)
        if not include_rejected:
            stmt = stmt.where(Entity.status != "rejected")
        for e in session.execute(scoped(stmt, Entity.case_id).limit(PER_KIND)).scalars():
            props = " ".join(str(v) for v in (e.props or {}).values()
                         if isinstance(v, (str, int, float)) and not isinstance(v, bool))
            type_es = TYPE_ES.get(e.type, e.type)
            s = _score([(e.label, 3), (type_es, 1.5), (props, 1)], toks, phrase)
            if s:
                hits.append(Hit("entity", e.id, e.case_id, e.label, f"{e.type} · {_snippet(props, toks) if props else e.label}",
                                lexical=s, status=e.status, text=f"{type_es.split()[0]}: {e.label}. {props}"[:2000],
                                extra={"type": e.type}))
    if "account" in kinds:
        stmt = select(Account).where(_prefilter([Account.handle, Account.display_name, Account.bio], toks))
        for a in session.execute(scoped(stmt, Account.case_id).limit(PER_KIND)).scalars():
            s = _score([(a.handle, 3), (a.display_name, 2), (a.bio, 1)], toks, phrase)
            if s:
                hits.append(Hit("account", a.id, a.case_id, f"@{a.handle} ({a.platform})",
                                _snippet(a.bio or a.display_name or a.handle, toks), lexical=s,
                                text=f"Cuenta @{a.handle} en {a.platform}. {a.display_name}. {a.bio}"[:2000],
                                extra={"platform": a.platform, "entity_id": a.entity_id}))
    if "post" in kinds:
        stmt = (select(Post, Account).join(Account, Post.account_id == Account.id)
                .where(_prefilter([Post.text], toks)).order_by(Post.created_at.desc()))
        for p, a in session.execute(scoped(stmt, Account.case_id).limit(PER_KIND)).all():
            s = _score([(p.text, 1)], toks, phrase)
            if s:
                when = p.created_at.strftime("%d/%m/%Y %H:%M") if p.created_at else "sin fecha"
                hits.append(Hit("post", p.id, a.case_id, f"@{a.handle} · {when}", _snippet(p.text, toks),
                                lexical=s, text=f"Publicación de @{a.handle} ({a.platform}, {when}): {p.text}"[:2000],
                                extra={"account_id": a.id}))
    if "source" in kinds:
        stmt = select(Source).where(_prefilter([Source.reference, Source.connector], toks))
        for src in session.execute(scoped(stmt, Source.case_id).limit(PER_KIND)).scalars():
            s = _score([(src.reference, 2), (src.connector, 1)], toks, phrase)
            if s:
                hits.append(Hit("source", src.id, src.case_id, src.reference[:200] or f"fuente {src.id}",
                                f"{src.kind} · {src.connector} · fiabilidad {src.reliability}{src.credibility}",
                                lexical=s, text=f"Fuente ({src.kind}, {src.connector}): {src.reference}"[:2000]))
    if "finding" in kinds:
        stmt = select(Finding).where(_prefilter([Finding.value, Finding.note], toks))
        for f in session.execute(scoped(stmt, Finding.case_id).limit(PER_KIND)).scalars():
            s = _score([(f.value, 2), (f.note, 1)], toks, phrase)
            if s:
                hits.append(Hit("finding", f.id, f.case_id, f"{f.kind}: {f.value[:120]}", _snippet(f.note or f.value, toks),
                                lexical=s, text=f"Hallazgo del expediente ({f.kind}): {f.value}. {f.note}"[:2000]))
    if "link" in kinds:
        a_acc = select(Account.id).where(_prefilter([Account.handle], toks))
        stmt = select(AccountLink).where(or_(AccountLink.account_a_id.in_(a_acc), AccountLink.account_b_id.in_(a_acc)))
        links = session.execute(scoped(stmt, AccountLink.case_id).limit(PER_KIND)).scalars().all()
        handles = dict(session.execute(select(Account.id, Account.handle).where(
            Account.id.in_({x for link in links for x in (link.account_a_id, link.account_b_id)}))).all()) if links else {}
        for link in links:
            a, b = handles.get(link.account_a_id, "?"), handles.get(link.account_b_id, "?")
            summary = str((link.signals or {}).get("summary", ""))
            s = _score([(f"{a} {b}", 2), (summary, 1)], toks, phrase)
            if s:
                hits.append(Hit("link", link.id, link.case_id, f"MENARD: @{a} ⇄ @{b} ({link.score:.2f})",
                                _snippet(summary, toks) if summary else f"estado: {link.review_status}", lexical=s,
                                status=link.review_status,
                                text=f"Hipótesis MENARD de mismo operador entre @{a} y @{b}, puntaje {link.score:.2f}, "
                                     f"estado {link.review_status}. {summary}"[:2000]))
    return hits


def case_pool(session: Session, case_id: int, *, limit: int = 200) -> list[Hit]:
    """Candidatos extra para la búsqueda semántica en un caso: entidades, cuentas y publicaciones recientes."""
    pool: list[Hit] = []
    for e in session.execute(select(Entity).where(Entity.case_id == case_id, Entity.status != "rejected")
                             .order_by(Entity.id.desc()).limit(limit)).scalars():
        props = " ".join(str(v) for v in (e.props or {}).values()
                         if isinstance(v, (str, int, float)) and not isinstance(v, bool))
        pool.append(Hit("entity", e.id, case_id, e.label, f"{e.type} · {props[:160]}" if props else e.type,
                        status=e.status, text=f"{TYPE_ES.get(e.type, e.type).split()[0]}: {e.label}. {props}"[:2000],
                        extra={"type": e.type}))
    for a in session.execute(select(Account).where(Account.case_id == case_id).limit(limit)).scalars():
        pool.append(Hit("account", a.id, case_id, f"@{a.handle} ({a.platform})", (a.bio or a.display_name)[:180],
                        text=f"Cuenta @{a.handle} en {a.platform}. {a.display_name}. {a.bio}"[:2000],
                        extra={"platform": a.platform, "entity_id": a.entity_id}))
    rows = session.execute(select(Post, Account).join(Account, Post.account_id == Account.id)
                           .where(Account.case_id == case_id).order_by(Post.created_at.desc()).limit(limit)).all()
    for p, a in rows:
        when = p.created_at.strftime("%d/%m/%Y %H:%M") if p.created_at else "sin fecha"
        pool.append(Hit("post", p.id, case_id, f"@{a.handle} · {when}", p.text[:180],
                        text=f"Publicación de @{a.handle} ({a.platform}, {when}): {p.text}"[:2000],
                        extra={"account_id": a.id}))
    return pool


# ---------------------------------------------------------------- semántica

_cache: OrderedDict[str, np.ndarray] = OrderedDict()
_CACHE_MAX = 20_000


def _key(model: str, text: str) -> str:
    return hashlib.sha1(f"{model}\x00{text}".encode()).hexdigest()


async def _embed(client: FunesClient, texts: list[str]) -> np.ndarray:
    out: list[np.ndarray | None] = [None] * len(texts)
    missing = []
    for i, t in enumerate(texts):
        k = _key(client.embed_model, t)
        if k in _cache:
            _cache.move_to_end(k)
            out[i] = _cache[k]
        else:
            missing.append(i)
    for start in range(0, len(missing), 64):
        batch = missing[start:start + 64]
        vectors = await client.embed([texts[i] for i in batch])
        for i, v in zip(batch, vectors):
            arr = np.asarray(v, dtype=float)
            arr /= (np.linalg.norm(arr) or 1.0)
            out[i] = arr
            _cache[_key(client.embed_model, texts[i])] = arr
            if len(_cache) > _CACHE_MAX:
                _cache.popitem(last=False)
    return np.vstack(out) if out else np.zeros((0, 1))


def embed_client(settings, http: httpx.AsyncClient | None = None) -> FunesClient | None:
    if not (settings.embed_base_url or "").strip() or not (settings.embed_model or "").strip():
        return None
    try:
        return FunesClient(llm_base_url=settings.llm_base_url or settings.embed_base_url, llm_model=settings.llm_model,
                           embed_base_url=settings.embed_base_url, embed_model=settings.embed_model,
                           embed_api_key=settings.embed_api_key or "", http=http, timeout=EMBED_TIMEOUT.read,
                           max_retries=1, backoff=0.5)
    except NonLocalEndpointError:
        return None


async def _rerank(client: FunesClient, query: str, hits: list[Hit]) -> None:
    vectors = await _embed(client, [query, *[h.text or h.title for h in hits]])
    sims = vectors[1:] @ vectors[0]
    top_lex = max((h.lexical for h in hits), default=0.0) or 1.0
    for h, s in zip(hits, sims):
        h.semantic = round(float(s), 4)
        h.score = 0.55 * (h.lexical / top_lex) + 0.45 * max(0.0, float(s))


def semantic_rerank(settings, http, query: str, hits: list[Hit]) -> tuple[str, str]:
    """Reordena en el lugar. Devuelve (estado, aviso): usado | no_configurado | no_responde."""
    client = embed_client(settings, http)
    if client is None:
        return "no_configurado", "La búsqueda por significado no está configurada (ALEPH_EMBED_BASE_URL)."
    if not hits:
        return "usado", ""

    async def run():
        try:
            await _rerank(client, query, hits)
        finally:
            await client.aclose()

    try:
        asyncio.run(run())
    except LLMError as exc:
        return "no_responde", f"El servidor de embeddings no respondió ({str(exc)[:160]}); se ordenó solo por texto."
    return "usado", ""


def search(session: Session, user: User, query: str, *, settings, http=None, case_id: int | None = None,
           kinds: tuple[str, ...] = KINDS, semantic: bool = False, limit: int = 30,
           boost: dict[str, float] | None = None) -> tuple[list[Hit], str, list[str]]:
    hits = lexical_search(session, user, query, case_id=case_id, kinds=kinds)
    warnings: list[str] = []
    status = "omitido"
    if semantic:
        if case_id is not None:
            seen = {(h.kind, h.id) for h in hits}
            hits += [h for h in case_pool(session, case_id) if h.kind in kinds and (h.kind, h.id) not in seen]
        status, warning = semantic_rerank(settings, http, query, hits)
        if warning:
            warnings.append(warning)
    for h in hits:
        if h.semantic is None:
            h.score = h.lexical
        h.score *= (boost or {}).get(h.kind, 1.0)
    hits.sort(key=lambda h: (-h.score, h.kind, -h.id))
    if semantic and status == "usado":
        hits = [h for h in hits if h.lexical > 0 or (h.semantic or 0) >= SEMANTIC_ONLY_MIN]
    return hits[:limit], status, warnings
