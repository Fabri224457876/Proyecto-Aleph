"""Cliente async mínimo para los endpoints compatibles con OpenAI del cluster.

FUNES es IA local: este cliente solo habla con hosts de la red propia (ver `ensure_local_url`).
Está pensado para modelos locales medianos: timeout generoso, reintentos acotados, y salida
estructurada que degrada de `response_format` con JSON schema a "JSON en el texto".
"""

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

from aleph.funes import prompts
from aleph.funes.structured import StructuredOutputError, parse_structured, strip_reasoning

T = TypeVar("T", bound=BaseModel)

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
# Respuestas con las que un servidor rechaza un parámetro que no soporta.
UNSUPPORTED_STATUS = frozenset({400, 404, 415, 422, 501})
_CGNAT = ipaddress.ip_network("100.64.0.0/10")  # Tailscale
_LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home.arpa", ".ts.net", ".test")


class LLMError(RuntimeError):
    """Falla al hablar con el endpoint (red, HTTP o respuesta con forma inesperada)."""


class LLMHTTPError(LLMError):
    def __init__(self, status: int, body: str):
        super().__init__(f"El endpoint respondió HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


class NonLocalEndpointError(LLMError):
    """La URL configurada apunta fuera de la red propia."""


def ensure_local_url(url: str) -> str:
    """Valida que `url` apunte a la red propia (loopback, rangos privados, Tailscale o nombre interno).

    No resuelve DNS: un nombre con puntos que no termina en un sufijo interno se rechaza.
    """
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        raise NonLocalEndpointError(f"URL de endpoint inválida: {url!r}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or host.endswith(_LOCAL_SUFFIXES):
            return url  # nombre de servicio de Compose o dominio interno
        raise NonLocalEndpointError(
            f"FUNES solo habla con endpoints de la red propia y {host!r} parece externo. "
            "Usá una IP privada o de Tailscale, o un nombre interno."
        ) from None
    if ip.is_loopback or ip.is_private or ip.is_link_local or (ip.version == 4 and ip in _CGNAT):
        return url
    raise NonLocalEndpointError(
        f"FUNES solo habla con endpoints de la red propia y {host} es una IP pública."
    )


# --- Recorte de entradas largas ------------------------------------------------------------


@dataclass(frozen=True)
class TextChunk:
    text: str
    start: int  # offset del fragmento dentro del texto original

    @property
    def end(self) -> int:
        return self.start + len(self.text)


def split_text(text: str, max_chars: int = 6000, overlap: int = 400) -> list[TextChunk]:
    """Parte `text` en fragmentos de hasta `max_chars` con `overlap` de solapamiento.

    Corta preferentemente en fin de párrafo, de oración o en un espacio. Los fragmentos son
    porciones literales del original, así los offsets se pueden trasladar sumando `start`.
    """
    if max_chars <= 0:
        raise ValueError("max_chars debe ser positivo")
    overlap = max(0, min(overlap, max_chars // 2))
    if len(text) <= max_chars:
        return [TextChunk(text, 0)] if text else []
    chunks: list[TextChunk] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            window_start = start + max_chars // 2
            for separator in ("\n\n", "\n", ". ", " "):
                cut = text.rfind(separator, window_start, end)
                if cut != -1:
                    end = cut + len(separator)
                    break
        chunks.append(TextChunk(text[start:end], start))
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


# --- Cliente -------------------------------------------------------------------------------

Message = dict[str, str]


def _merge_system(messages: list[Message]) -> list[Message]:
    """Para plantillas de chat sin rol system (p. ej. Gemma): lo pasa al primer turno de usuario."""
    system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
    rest = [dict(m) for m in messages if m.get("role") != "system"]
    if not system:
        return rest
    for message in rest:
        if message.get("role") == "user":
            message["content"] = f"{system}\n\n{message['content']}"
            return rest
    return [{"role": "user", "content": system}, *rest]


class FunesClient:
    """Cliente de chat y embeddings. El `httpx.AsyncClient` es inyectable (tests, pool compartido)."""

    def __init__(
        self,
        *,
        llm_base_url: str,
        llm_model: str,
        llm_api_key: str = "",
        embed_base_url: str = "",
        embed_model: str = "",
        embed_api_key: str = "",
        http: httpx.AsyncClient | None = None,
        timeout: float = 300.0,
        max_retries: int = 2,
        backoff: float = 1.0,
        allow_external: bool = False,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ):
        if not allow_external:
            ensure_local_url(llm_base_url)
            if embed_base_url:
                ensure_local_url(embed_base_url)
        self.llm_base_url = llm_base_url.rstrip("/")
        self.llm_model = llm_model
        self._llm_api_key = llm_api_key
        self.embed_base_url = embed_base_url.rstrip("/")
        self.embed_model = embed_model
        self._embed_api_key = embed_api_key
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.backoff = backoff
        self._sleep = sleep
        self._owns_http = http is None
        # trust_env=False: no usar proxies del entorno, el tráfico no sale de la red propia.
        self._http = http or httpx.AsyncClient(trust_env=False, follow_redirects=False)
        # Nivel de salida estructurada que acepta el servidor: 2 json_schema, 1 json_object, 0 nada.
        self._format_level = 2
        self._merge_system = False

    @classmethod
    def from_settings(cls, settings: Any = None, **kwargs: Any) -> "FunesClient":
        if settings is None:
            from aleph.core.config import get_settings

            settings = get_settings()
        return cls(
            llm_base_url=settings.llm_base_url,
            llm_model=settings.llm_model,
            llm_api_key=settings.llm_api_key,
            embed_base_url=settings.embed_base_url,
            embed_model=settings.embed_model,
            embed_api_key=settings.embed_api_key,
            **kwargs,
        )

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> "FunesClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- HTTP con reintentos --

    async def _post(self, url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._http.post(
                    url, json=payload, headers=headers, timeout=self.timeout
                )
            except httpx.TransportError as exc:  # incluye timeouts y errores de conexión
                last = LLMError(f"No se pudo conectar con {url}: {type(exc).__name__}: {exc}")
            else:
                if response.status_code in RETRYABLE_STATUS:
                    last = LLMHTTPError(response.status_code, response.text)
                elif response.status_code >= 400:
                    raise LLMHTTPError(response.status_code, response.text)
                else:
                    try:
                        data = response.json()
                    except ValueError:
                        last = LLMError(f"{url} devolvió algo que no es JSON")
                    else:
                        if isinstance(data, dict):
                            return data
                        last = LLMError(f"{url} devolvió JSON con forma inesperada")
            if attempt < self.max_retries:
                await self._sleep(self.backoff * (2**attempt))
        assert last is not None
        raise last

    # -- Chat --

    @staticmethod
    def _response_format(level: int, schema: dict[str, Any] | None, name: str) -> dict | None:
        if schema is None or level == 0:
            return None
        if level == 1:
            return {"type": "json_object"}
        return {"type": "json_schema", "json_schema": {"name": name, "schema": schema}}

    async def chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
        schema_name: str = "respuesta",
    ) -> str:
        """Devuelve el texto de la respuesta, ya sin bloques de razonamiento.

        Si se pasa `json_schema` se pide salida estructurada; si el servidor la rechaza se baja
        de nivel (json_schema → json_object → sin formato) y se recuerda para las próximas.
        """
        level = self._format_level if json_schema is not None else 0
        merge = self._merge_system
        while True:
            payload: dict[str, Any] = {
                "model": self.llm_model,
                "messages": _merge_system(messages) if merge else messages,
                "temperature": temperature,
                "stream": False,
            }
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            response_format = self._response_format(level, json_schema, schema_name)
            if response_format:
                payload["response_format"] = response_format
            try:
                data = await self._post(
                    f"{self.llm_base_url}/chat/completions", self._llm_api_key, payload
                )
            except LLMHTTPError as exc:
                if exc.status in UNSUPPORTED_STATUS:
                    if level > 0:
                        level -= 1
                        continue
                    if not merge and any(m.get("role") == "system" for m in messages):
                        merge = True
                        continue
                raise
            # Solo se recuerda la degradación si con ella la llamada funcionó.
            if json_schema is not None:
                self._format_level = level
            self._merge_system = merge
            return strip_reasoning(self._content(data))

    @staticmethod
    def _content(data: dict[str, Any]) -> str:
        try:
            content = data["choices"][0]["message"].get("content")
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise LLMError("La respuesta de chat no trae choices[0].message") from exc
        if content is None:
            return ""
        if isinstance(content, list):  # contenido por partes
            return "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        return str(content)

    async def chat_json(
        self,
        messages: list[Message],
        schema: type[T],
        *,
        wrap_list_as: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> T:
        """Chat con salida validada contra `schema`.

        Si la respuesta no valida, se reintenta una vez pasándole al modelo el error.
        Lanza `StructuredOutputError` si tampoco así, o `LLMError` si falla el endpoint.
        """
        json_schema = schema.model_json_schema()
        current = list(messages)
        for attempt in range(2):
            raw = await self.chat(
                current,
                temperature=temperature,
                max_tokens=max_tokens,
                json_schema=json_schema,
                schema_name=schema.__name__.strip("_").lower() or "respuesta",
            )
            try:
                return parse_structured(raw, schema, wrap_list_as=wrap_list_as)
            except StructuredOutputError as exc:
                if attempt == 1:
                    raise
                current = [
                    *messages,
                    {"role": "assistant", "content": raw[:4000] or "(respuesta vacía)"},
                    {"role": "user", "content": prompts.render(prompts.JSON_REPAIR, error=exc)},
                ]
        raise AssertionError("inalcanzable")

    # -- Embeddings --

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embeddings de un lote, en el mismo orden que `texts`."""
        if not texts:
            return []
        if not self.embed_base_url or not self.embed_model:
            raise LLMError("No hay endpoint de embeddings configurado")
        data = await self._post(
            f"{self.embed_base_url}/embeddings",
            self._embed_api_key,
            {"model": self.embed_model, "input": texts},
        )
        try:
            rows = sorted(data["data"], key=lambda row: row.get("index", 0))
            vectors = [[float(x) for x in row["embedding"]] for row in rows]
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise LLMError("La respuesta de embeddings no trae data[].embedding") from exc
        if len(vectors) != len(texts):
            raise LLMError(f"Se pidieron {len(texts)} embeddings y llegaron {len(vectors)}")
        return vectors
