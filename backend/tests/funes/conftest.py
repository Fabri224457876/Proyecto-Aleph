"""Servidor simulado compatible con OpenAI para los tests de FUNES. Sin red y sin LLM real."""

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from aleph.funes.client import FunesClient


def chat_response(content: str | None, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={
        "id": "x", "object": "chat.completion",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}]})


def fake_embedding(text: str, dim: int = 64) -> list[float]:
    """Bolsa de palabras con hash: textos que comparten palabras quedan cerca."""
    vector = [0.0] * dim
    for word in re.findall(r"\w+", text.lower()):
        vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % dim] += 1.0
    return vector


class FakeServer:
    """Responde /chat/completions con una cola de respuestas y /embeddings con vectores falsos.

    Cada respuesta de la cola puede ser un str (contenido del mensaje), un dict/list (se
    serializa a JSON), un httpx.Response, una excepción (se lanza) o un callable(body) que
    devuelve cualquiera de las anteriores.
    """

    def __init__(self, replies: list[Any] | None = None, default: Any = None):
        self.replies = list(replies or [])
        self.default = default
        self.chat_requests: list[dict[str, Any]] = []
        self.embed_requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self.embed_handler: Callable[[dict[str, Any]], httpx.Response] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.headers.append(request.headers)
        if request.url.path.endswith("/embeddings"):
            self.embed_requests.append(body)
            if self.embed_handler:
                return self.embed_handler(body)
            data = [{"index": i, "embedding": fake_embedding(t)} for i, t in enumerate(body["input"])]
            return httpx.Response(200, json={"data": list(reversed(data))})  # orden revuelto
        assert request.url.path.endswith("/chat/completions"), request.url.path
        self.chat_requests.append(body)
        reply = self.replies.pop(0) if self.replies else self.default
        if callable(reply):
            reply = reply(body)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        if isinstance(reply, (dict, list)):
            reply = json.dumps(reply, ensure_ascii=False)
        if reply is None:
            raise AssertionError("El test hizo más llamadas al LLM de las previstas")
        return chat_response(reply)

    # Ayudas para inspeccionar lo que se le mandó al modelo
    def system(self, n: int = -1) -> str:
        return next(m["content"] for m in self.chat_requests[n]["messages"] if m["role"] == "system")

    def user(self, n: int = -1) -> str:
        return [m["content"] for m in self.chat_requests[n]["messages"] if m["role"] == "user"][-1]


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
def chat_reply():
    """chat_reply(content, status=200) -> httpx.Response con forma de chat completion."""
    return chat_response


@pytest.fixture
def make_client():
    """make_client(replies, **kwargs) -> (FunesClient, FakeServer)."""
    opened: list[httpx.AsyncClient] = []

    def factory(replies: list[Any] | None = None, *, default: Any = None,
                **kwargs: Any) -> tuple[FunesClient, FakeServer]:
        server = FakeServer(replies, default)
        http = httpx.AsyncClient(transport=httpx.MockTransport(server))
        opened.append(http)
        params = dict(llm_base_url="http://127.0.0.1:4000/v1", llm_model="modelo-local",
                      llm_api_key="clave-de-prueba", embed_base_url="http://127.0.0.1:8081/v1",
                      embed_model="bge-m3", http=http, sleep=_no_sleep)
        params.update(kwargs)
        return FunesClient(**params), server

    yield factory
    # MockTransport no abre conexiones: no hace falta cerrar los clientes.
