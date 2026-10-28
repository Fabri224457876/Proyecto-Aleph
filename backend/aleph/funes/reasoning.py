"""FUNES a la par de TLÖN: revisar hipótesis, leer el caso y responder preguntas citando.

TLÖN propone vínculos por estructura; acá el modelo de lenguaje los revisa con sentido común,
los explica y sugiere cómo verificarlos. Para las preguntas, responde solo con los fragmentos
recuperados del caso y se descartan las citas a ids que no estaban en el contexto.
"""

import re
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from . import prompts

Verdict = Literal["plausible", "dudosa", "descartable"]
_CITE = re.compile(r"\[([A-Z]{1,2}\d{1,7})\]")


class ChatClient(Protocol):
    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str: ...
    async def chat_json(self, messages: list[dict[str, str]], schema: type[BaseModel], **kwargs: Any) -> Any: ...


class Review(BaseModel):
    id: str
    veredicto: Verdict
    razon: str = ""
    verificar: str = ""


class Statement(BaseModel):
    texto: str
    ids: list[str] = Field(default_factory=list)


class TlonReviewOut(BaseModel):
    revisiones: list[Review] = Field(default_factory=list)
    lectura: list[Statement] = Field(default_factory=list)


class InventoryItem(BaseModel):
    id: str  # E12, R4, T1, ...
    text: str


async def review_hypotheses(client: ChatClient, title: str, inventory: list[InventoryItem],
                            hypothesis_ids: set[str]) -> tuple[TlonReviewOut, list[str]]:
    """Revisión del LLM. Devuelve la revisión depurada y avisos (ids inventados, revisiones faltantes)."""
    block, nonce = prompts.wrap_untrusted("\n".join(f"[{i.id}] {i.text}" for i in inventory), "INVENTARIO")
    messages = [
        {"role": "system", "content": prompts.render(prompts.TLON_REVIEW_SYSTEM,
                                                     security=prompts.security_block("INVENTARIO", nonce))},
        {"role": "user", "content": prompts.render(prompts.TLON_REVIEW_USER, title=title, inventory=block,
                                                   reminder=prompts.UNTRUSTED_REMINDER)},
    ]
    out: TlonReviewOut = await client.chat_json(messages, TlonReviewOut, temperature=0.1)
    known = {i.id for i in inventory}
    warnings: list[str] = []
    reviews = [r for r in out.revisiones if r.id in hypothesis_ids]
    if len(reviews) < len(out.revisiones):
        warnings.append("El modelo de lenguaje revisó hipótesis que no existen; se descartaron.")
    missing = hypothesis_ids - {r.id for r in reviews}
    if missing:
        warnings.append(f"El modelo de lenguaje no revisó {len(missing)} hipótesis.")
    lectura = []
    for st in out.lectura:
        bad = [i for i in st.ids if i not in known]
        if bad:
            warnings.append(f"Se quitaron ids inexistentes de la lectura: {', '.join(bad[:5])}.")
        lectura.append(Statement(texto=st.texto, ids=[i for i in st.ids if i in known]))
    return TlonReviewOut(revisiones=reviews, lectura=lectura), warnings


class Fragment(BaseModel):
    id: str  # E12, C3, P45, S7, H2
    kind: str
    title: str
    text: str


class AnswerOut(BaseModel):
    answer: str
    cited: list[str] = Field(default_factory=list, description="Ids citados que existen en el contexto")
    invalid: list[str] = Field(default_factory=list, description="Ids citados que no estaban: se marcan")


async def answer_question(client: ChatClient, question: str, fragments: list[Fragment]) -> AnswerOut:
    context = "\n\n".join(f"[{f.id}] ({f.kind}) {f.title}\n{f.text[:1200]}" for f in fragments)
    block, nonce = prompts.wrap_untrusted(context or "(no se recuperó ningún fragmento)", "FRAGMENTOS")
    messages = [
        {"role": "system", "content": prompts.render(prompts.ASK_SYSTEM,
                                                     security=prompts.security_block("FRAGMENTOS", nonce))},
        {"role": "user", "content": prompts.render(prompts.ASK_USER, question=question.strip(), context=block)},
    ]
    text = (await client.chat(messages, temperature=0.1)).strip()
    known = {f.id for f in fragments}
    cited, invalid = [], []
    for ref in _CITE.findall(text):
        target = cited if ref in known else invalid
        if ref not in target:
            target.append(ref)
    for ref in invalid:  # que se vea en el texto que esa cita no tiene respaldo
        text = text.replace(f"[{ref}]", f"[{ref}?]")
    return AnswerOut(answer=text, cited=cited, invalid=invalid)
