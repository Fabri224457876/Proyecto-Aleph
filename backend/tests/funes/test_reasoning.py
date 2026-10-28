"""FUNES a la par de TLÖN: revisión de hipótesis y respuestas con citas, con un cliente simulado."""

import asyncio

from aleph.funes.reasoning import (
    Fragment,
    InventoryItem,
    TlonReviewOut,
    answer_question,
    review_hypotheses,
)


class FakeClient:
    def __init__(self, *, json_out=None, text_out=""):
        self.json_out, self.text_out, self.messages = json_out, text_out, []

    async def chat_json(self, messages, schema, **kwargs):
        self.messages.append(messages)
        return schema.model_validate(self.json_out)

    async def chat(self, messages, **kwargs):
        self.messages.append(messages)
        return self.text_out


INVENTORY = [InventoryItem(id="E1", text="account: @alfa"), InventoryItem(id="E2", text="domain: alfa.test"),
             InventoryItem(id="T1", text="HIPÓTESIS DE TLÖN: E1 -[shares_domain]-> E2.")]


def test_revision_descarta_ids_inventados_y_avisa():
    client = FakeClient(json_out={
        "revisiones": [{"id": "T1", "veredicto": "plausible", "razon": "Mismo nombre.", "verificar": "WHOIS"},
                       {"id": "T9", "veredicto": "dudosa", "razon": "x", "verificar": "y"}],
        "lectura": [{"texto": "Es posible que la cuenta use el dominio.", "ids": ["E1", "E2", "E77"]}],
    })
    out, warnings = asyncio.run(review_hypotheses(client, "Caso", INVENTORY, {"T1"}))
    assert isinstance(out, TlonReviewOut)
    assert [r.id for r in out.revisiones] == ["T1"]
    assert out.lectura[0].ids == ["E1", "E2"]
    assert any("no existen" in w for w in warnings) and any("E77" in w for w in warnings)
    # El inventario va delimitado como dato no confiable
    assert "<<<INVENTARIO" in client.messages[0][1]["content"]


def test_revision_avisa_hipotesis_sin_revisar():
    client = FakeClient(json_out={"revisiones": [], "lectura": []})
    _, warnings = asyncio.run(review_hypotheses(client, "Caso", INVENTORY, {"T1"}))
    assert any("no revisó 1" in w for w in warnings)


def test_respuesta_marca_citas_sin_respaldo():
    fragments = [Fragment(id="E1", kind="entity", title="@alfa", text="account: @alfa"),
                 Fragment(id="P4", kind="post", title="@alfa", text="Tu cupón vence hoy")]
    client = FakeClient(text_out="La cuenta @alfa publicó el cupón [P4] [E1]. También usa otro dominio [E9].")
    out = asyncio.run(answer_question(client, "¿Qué publicó @alfa?", fragments))
    assert out.cited == ["P4", "E1"] and out.invalid == ["E9"]
    assert "[E9?]" in out.answer and "[P4]" in out.answer
    assert "<<<FRAGMENTOS" in client.messages[0][1]["content"]
