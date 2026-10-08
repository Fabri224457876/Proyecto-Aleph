import httpx
import pytest
from pydantic import BaseModel, Field

from aleph.funes.client import (
    FunesClient,
    LLMError,
    LLMHTTPError,
    NonLocalEndpointError,
    ensure_local_url,
    split_text,
)
from aleph.funes.structured import (
    StructuredOutputError,
    coerce_confidence,
    extract_json_candidates,
    lenient_list,
    parse_structured,
    strip_reasoning,
)


class Item(BaseModel):
    nombre: str
    n: int = 0


ItemList = lenient_list(Item)


class Answer(BaseModel):
    items: ItemList = Field(default_factory=list)  # type: ignore[valid-type]
    nota: str = ""


# --- Razonamiento y extracción de JSON -----------------------------------------------------


def test_strip_reasoning_variants():
    assert strip_reasoning("<think>\npienso {\"a\": 1}\n</think>\n{\"b\": 2}") == '{"b": 2}'
    assert strip_reasoning("razono sin apertura</think>respuesta") == "respuesta"
    assert strip_reasoning("<think>me cortaron a mitad de camino") == ""
    assert strip_reasoning("<THINKING>x</THINKING> hola") == "hola"
    assert strip_reasoning("sin etiquetas") == "sin etiquetas"


def test_extract_json_with_text_around():
    raw = 'Claro, acá va el resultado:\n{"items": [{"nombre": "a {con llaves}"}]}\nEspero que sirva.'
    assert extract_json_candidates(raw) == [{"items": [{"nombre": "a {con llaves}"}]}]


def test_extract_json_from_code_fence_and_trailing_commas():
    raw = '```json\n{"items": [{"nombre": "a", "n": 1,},],}\n```'
    assert extract_json_candidates(raw)[0] == {"items": [{"nombre": "a", "n": 1}]}


def test_extract_json_python_style_quotes():
    assert extract_json_candidates("{'items': [{'nombre': 'a', 'n': None}]}")[0] == {
        "items": [{"nombre": "a", "n": None}]}


def test_extract_json_truncated_keeps_complete_elements():
    raw = '{"items": [{"nombre": "a", "n": 1}, {"nombre": "b", "n": 2}, {"nombre": "c", "n'
    assert extract_json_candidates(raw) == [
        {"items": [{"nombre": "a", "n": 1}, {"nombre": "b", "n": 2}]}]


def test_extract_json_garbage_returns_nothing():
    assert extract_json_candidates("no hay nada {roto: ] acá") == []
    assert extract_json_candidates("") == []


def test_parse_structured_prefers_object_over_footnote_list():
    raw = 'Según la nota [1], el resultado es {"items": [{"nombre": "a"}]}'
    assert parse_structured(raw, Answer, wrap_list_as="items").items[0].nombre == "a"


def test_parse_structured_wraps_bare_list_and_unwraps_envelope():
    assert parse_structured('[{"nombre": "a"}]', Answer, wrap_list_as="items").items[0].nombre == "a"
    wrapped = '{"respuesta": {"items": [{"nombre": "z"}]}}'
    assert parse_structured(wrapped, Answer).items[0].nombre == "z"
    with pytest.raises(StructuredOutputError):
        parse_structured('[{"nombre": "a"}]', Answer)


def test_parse_structured_drops_invalid_items_but_keeps_good_ones():
    raw = '{"items": [{"nombre": "a"}, {"sin_nombre": 1}, "basura", {"nombre": "b", "n": "x"}]}'
    assert [i.nombre for i in parse_structured(raw, Answer).items] == ["a"]


def test_parse_structured_errors_are_explained():
    with pytest.raises(StructuredOutputError, match="No se encontró"):
        parse_structured("perdón, no puedo", Answer)
    with pytest.raises(StructuredOutputError, match="claves esperadas"):
        parse_structured('{"otra_cosa": 1}', Answer)
    with pytest.raises(StructuredOutputError, match="vacía"):
        parse_structured("<think>solo pensé</think>", Answer)
    with pytest.raises(StructuredOutputError, match="nota"):
        parse_structured('{"items": [], "nota": {"no": "es texto"}}', Answer)


@pytest.mark.parametrize(("value", "expected"), [
    (0.8, 0.8), ("0,8", 0.8), ("80%", 0.8), (80, 0.8), ("alta", 0.85), ("Media", 0.6),
    (None, 0.6), ("qué sé yo", 0.6), (-3, 0.0), (1e9, 1.0), (True, 0.6), (float("nan"), 0.6),
])
def test_coerce_confidence(value, expected):
    assert coerce_confidence(value) == pytest.approx(expected)


# --- Recorte --------------------------------------------------------------------------------


def test_split_text_short_and_empty():
    assert split_text("") == []
    assert [c.text for c in split_text("hola", max_chars=100)] == ["hola"]


def test_split_text_chunks_are_literal_slices_with_overlap():
    text = " ".join(f"Oración número {i}." for i in range(400))
    chunks = split_text(text, max_chars=500, overlap=80)
    assert len(chunks) > 5
    for chunk in chunks:
        assert len(chunk.text) <= 500
        assert text[chunk.start:chunk.end] == chunk.text
    for previous, current in zip(chunks, chunks[1:]):
        assert previous.start < current.start <= previous.end  # sin huecos
        assert previous.end - current.start >= 40  # se solapan
    assert chunks[-1].end == len(text)
    # Corta en límites de oración, no a mitad de palabra
    assert all(c.text.rstrip().endswith(".") for c in chunks)


def test_split_text_without_any_separator_still_terminates():
    chunks = split_text("x" * 2500, max_chars=1000, overlap=100)
    assert chunks[-1].end == 2500 and all(len(c.text) <= 1000 for c in chunks)


# --- Soberanía: solo red propia --------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "http://100.64.0.10:4000/v1", "http://127.0.0.1:8080/v1", "http://localhost:1234/v1",
    "http://192.168.1.10/v1", "http://10.0.0.5/v1", "http://litellm:4000/v1",
    "http://nodo1.tailnet.ts.net/v1", "http://[::1]:8000/v1",
])
def test_local_urls_accepted(url):
    assert ensure_local_url(url) == url


@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1", "http://8.8.8.8/v1", "https://ejemplo.com.ar/v1", "no-es-url",
    "http://100.128.0.1/v1",
])
def test_external_urls_rejected(url):
    with pytest.raises(NonLocalEndpointError):
        ensure_local_url(url)


def test_client_refuses_external_endpoint():
    with pytest.raises(NonLocalEndpointError):
        FunesClient(llm_base_url="https://api.openai.com/v1", llm_model="m")
    with pytest.raises(NonLocalEndpointError):
        FunesClient(llm_base_url="http://127.0.0.1/v1", llm_model="m",
                    embed_base_url="https://api.example.com/v1")


def test_from_settings_uses_aleph_config():
    class Settings:
        llm_base_url = "http://100.64.0.10:4000/v1/"
        llm_api_key = "k"
        llm_model = "modelo-local"
        embed_base_url = "http://100.64.0.11:8081/v1"
        embed_api_key = ""
        embed_model = "bge-m3"

    client = FunesClient.from_settings(Settings(), http=httpx.AsyncClient())
    assert client.llm_base_url == "http://100.64.0.10:4000/v1"
    assert client.llm_model == "modelo-local" and client.embed_model == "bge-m3"


# --- Chat -------------------------------------------------------------------------------------


async def test_chat_sends_model_auth_and_strips_think(make_client):
    client, server = make_client(["<think>a ver…</think>\nHola."])
    answer = await client.chat([{"role": "user", "content": "hola"}], max_tokens=50)
    assert answer == "Hola."
    body = server.chat_requests[0]
    assert body["model"] == "modelo-local" and body["max_tokens"] == 50 and body["stream"] is False
    assert "response_format" not in body
    assert server.headers[0]["authorization"] == "Bearer clave-de-prueba"


async def test_chat_without_key_sends_no_auth_header(make_client):
    client, server = make_client(["ok"], llm_api_key="")
    await client.chat([{"role": "user", "content": "x"}])
    assert "authorization" not in server.headers[0]


async def test_chat_retries_transient_errors_then_succeeds(make_client):
    client, server = make_client([
        httpx.Response(503, text="cargando modelo"),
        httpx.ReadTimeout("lento"),
        "al fin",
    ])
    assert await client.chat([{"role": "user", "content": "x"}]) == "al fin"
    assert len(server.chat_requests) == 3


async def test_chat_retries_are_bounded(make_client):
    client, server = make_client(default=httpx.Response(500, text="boom"), max_retries=2)
    with pytest.raises(LLMHTTPError) as info:
        await client.chat([{"role": "user", "content": "x"}])
    assert info.value.status == 500
    assert len(server.chat_requests) == 3


async def test_chat_connection_error_becomes_llm_error(make_client):
    client, server = make_client(default=httpx.ConnectError("sin ruta"), max_retries=1)
    with pytest.raises(LLMError, match="No se pudo conectar"):
        await client.chat([{"role": "user", "content": "x"}])
    assert len(server.chat_requests) == 2


async def test_chat_does_not_retry_client_errors(make_client):
    client, server = make_client(default=httpx.Response(401, text="clave inválida"))
    with pytest.raises(LLMHTTPError):
        await client.chat([{"role": "user", "content": "x"}])
    assert len(server.chat_requests) == 1


async def test_chat_malformed_envelope(make_client):
    client, _ = make_client(default=httpx.Response(200, json={"choices": []}), max_retries=0)
    with pytest.raises(LLMError, match="choices"):
        await client.chat([{"role": "user", "content": "x"}])
    client, _ = make_client(default=httpx.Response(200, text="<html>proxy</html>"), max_retries=0)
    with pytest.raises(LLMError, match="no es JSON"):
        await client.chat([{"role": "user", "content": "x"}])


async def test_chat_null_and_multipart_content(make_client, chat_reply):
    client, _ = make_client([chat_reply(None)])
    assert await client.chat([{"role": "user", "content": "x"}]) == ""
    multipart = httpx.Response(200, json={"choices": [{"message": {"content": [
        {"type": "text", "text": "par"}, {"type": "text", "text": "tes"}]}}]})
    client, _ = make_client([multipart])
    assert await client.chat([{"role": "user", "content": "x"}]) == "partes"


# --- Salida estructurada ------------------------------------------------------------------------


async def test_chat_json_requests_json_schema(make_client):
    client, server = make_client(['{"items": [{"nombre": "a", "n": 3}]}'])
    answer = await client.chat_json([{"role": "user", "content": "x"}], Answer)
    assert answer.items[0].n == 3
    response_format = server.chat_requests[0]["response_format"]
    assert response_format["type"] == "json_schema"
    assert "items" in response_format["json_schema"]["schema"]["properties"]


async def test_chat_json_falls_back_when_server_rejects_response_format(make_client):
    def picky(body):
        if "response_format" in body:
            return httpx.Response(400, text="response_format no soportado")
        return 'Listo: {"items": [{"nombre": "a"}]}'

    client, server = make_client(default=picky)
    answer = await client.chat_json([{"role": "user", "content": "x"}], Answer)
    assert answer.items[0].nombre == "a"
    formats = [r.get("response_format", {}).get("type") for r in server.chat_requests]
    assert formats == ["json_schema", "json_object", None]
    # La degradación se recuerda: la próxima llamada ya no lo intenta.
    await client.chat_json([{"role": "user", "content": "x"}], Answer)
    assert len(server.chat_requests) == 4 and "response_format" not in server.chat_requests[3]


async def test_chat_json_accepts_json_object_mode(make_client):
    def only_json_object(body):
        if body.get("response_format", {}).get("type") == "json_schema":
            return httpx.Response(422, text="json_schema no soportado")
        return '{"items": []}'

    client, server = make_client(default=only_json_object)
    await client.chat_json([{"role": "user", "content": "x"}], Answer)
    await client.chat_json([{"role": "user", "content": "x"}], Answer)
    assert [r["response_format"]["type"] for r in server.chat_requests] == [
        "json_schema", "json_object", "json_object"]


async def test_failed_degradation_is_not_remembered(make_client):
    """Un 400 por otro motivo (p. ej. contexto excedido) no apaga la salida estructurada."""
    client, server = make_client(default=httpx.Response(400, text="context length exceeded"))
    with pytest.raises(LLMHTTPError):
        await client.chat_json([{"role": "user", "content": "x"}], Answer)
    server.default = '{"items": []}'
    server.chat_requests.clear()
    await client.chat_json([{"role": "user", "content": "x"}], Answer)
    assert server.chat_requests[0]["response_format"]["type"] == "json_schema"


async def test_system_role_merged_when_template_rejects_it(make_client):
    def no_system(body):
        if any(m["role"] == "system" for m in body["messages"]):
            return httpx.Response(400, text="System role not supported")
        return "ok"

    client, server = make_client(default=no_system)
    messages = [{"role": "system", "content": "REGLAS"}, {"role": "user", "content": "pregunta"}]
    assert await client.chat(messages) == "ok"
    last = server.chat_requests[-1]["messages"]
    assert [m["role"] for m in last] == ["user"]
    assert last[0]["content"].startswith("REGLAS") and last[0]["content"].endswith("pregunta")


async def test_chat_json_retries_once_with_validation_feedback(make_client):
    client, server = make_client(['{"items": [{"nombre": "a", "n": 1}', '{"items": [{"nombre": "b"}]}'])
    # El primer intento está roto sin remedio (ni un elemento completo); el segundo sirve.
    server.replies[0] = "No puedo responder en JSON, perdón."
    answer = await client.chat_json([{"role": "user", "content": "extraé"}], Answer)
    assert answer.items[0].nombre == "b"
    retry = server.chat_requests[1]["messages"]
    assert [m["role"] for m in retry] == ["user", "assistant", "user"]
    assert retry[1]["content"] == "No puedo responder en JSON, perdón."
    assert "No se encontró ningún objeto JSON" in retry[2]["content"]


async def test_chat_json_gives_up_after_one_retry(make_client):
    client, server = make_client(default='{"nada": "que ver"}')
    with pytest.raises(StructuredOutputError):
        await client.chat_json([{"role": "user", "content": "x"}], Answer)
    assert len(server.chat_requests) == 2


async def test_chat_json_think_block_and_prose(make_client):
    reply = ('<think>El usuario pide {"items": []} pero debo completar…</think>\n'
             'Acá está:\n```json\n{"items": [{"nombre": "final"}]}\n```\nSaludos.')
    client, _ = make_client([reply])
    answer = await client.chat_json([{"role": "user", "content": "x"}], Answer)
    assert [i.nombre for i in answer.items] == ["final"]


# --- Embeddings -----------------------------------------------------------------------------------


async def test_embed_respects_index_order(make_client):
    client, server = make_client()
    vectors = await client.embed(["uno", "dos", "tres"])
    assert len(vectors) == 3 and len(vectors[0]) == 64
    assert vectors[0] != vectors[1]
    assert server.embed_requests[0] == {"model": "bge-m3", "input": ["uno", "dos", "tres"]}
    again = await client.embed(["uno"])
    assert again[0] == vectors[0]  # el servidor devuelve desordenado; el cliente reordena


async def test_embed_errors(make_client):
    client, server = make_client()
    assert await client.embed([]) == []
    server.embed_handler = lambda body: httpx.Response(200, json={"data": []})
    with pytest.raises(LLMError, match="llegaron 0"):
        await client.embed(["a"])
    server.embed_handler = lambda body: httpx.Response(200, json={"sin": "data"})
    with pytest.raises(LLMError):
        await client.embed(["a"])
    no_embed, _ = make_client(embed_base_url="")
    with pytest.raises(LLMError, match="embeddings"):
        await no_embed.embed(["a"])
