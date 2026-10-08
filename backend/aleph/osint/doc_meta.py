"""Metadatos de documentos: PDF (diccionario Info y XMP) y Office Open XML (docx, xlsx, pptx).

Sin dependencias nuevas: el lector de PDF es propio y se apoya en zlib y re; el de OOXML, en zipfile
y xml.etree. Son archivos no confiables, así que el parseo es defensivo:

- límites de tamaño (archivo, flujo descomprimido, parte XML) y de número de entradas ZIP;
- rechazo de zip bombs por relación de compresión y por tamaño declarado (se lee con tope igualmente);
- rechazo de XML con DOCTYPE o declaraciones ENTITY antes de parsear (evita la expansión de entidades);
- en PDF, un flujo que supera el tope de descompresión se omite con advertencia, no se decodifica.

Referencias:
- PDF: ISO 32000-1 (PDF 1.7), diccionario de información (§14.3.3) y metadatos XMP (§14.3.2).
- OOXML: ECMA-376 Parte 2 (propiedades del núcleo, docProps/core.xml) y propiedades extendidas
  (docProps/app.xml).

Advertencia: los metadatos los escribe el software y el propio autor; pueden estar vacíos,
desactualizados o falsificados. Las atribuciones que se derivan son hipótesis con confianza 0.6.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from xml.etree import ElementTree as ET

from pydantic import BaseModel, Field

from aleph.core.schemas import EntityRecord, RelationRecord

from .errors import InvalidInputError, UnsafeFileError

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_STREAM_BYTES = 5 * 1024 * 1024  # tamaño máximo tras descomprimir un flujo PDF
MAX_XML_BYTES = 2 * 1024 * 1024  # tamaño máximo de una parte XML (XMP o docProps)
MAX_ZIP_ENTRIES = 1000
MAX_ZIP_DECLARED_BYTES = 200 * 1024 * 1024
MAX_ZIP_RATIO = 100
MAX_ZIP_RATIO_MIN_SIZE = 512 * 1024
MAX_PDF_OBJECTS = 200_000
MAX_PDF_DEPTH = 64
MAX_PDF_ITEMS = 10_000
MAX_PDF_STRING = 1024 * 1024
ATTRIBUTION_CONFIDENCE = 0.6

_PDF_WS = b"\x00\t\n\x0c\r "
_PDF_DELIM = b"()<>[]{}/%"
_OBJ_RE = re.compile(rb"(?<![0-9])([0-9]{1,10})[\x00\t\n\x0c\r ]+([0-9]{1,5})[\x00\t\n\x0c\r ]+obj(?![A-Za-z])")
_TRAILER_RE = re.compile(rb"trailer(?![A-Za-z])")
_INT_RE = re.compile(rb"[+-]?[0-9]+")
_REAL_RE = re.compile(rb"[+-]?([0-9]+\.[0-9]*|\.[0-9]+)")
_HEX_CLEAN_RE = re.compile(rb"[\x00\t\n\x0c\r ]")
_HEX_RE = re.compile(rb"[0-9A-Fa-f]*")
_NAME_ESC_RE = re.compile(rb"#([0-9A-Fa-f]{2})")
_PDF_DATE_RE = re.compile(
    r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?([Zz+-])?(\d{2})?'?(\d{2})?'?"
)
_DTD_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY|ELEMENT|ATTLIST)", re.IGNORECASE)
_INFO_KEYS = ("Producer", "Creator", "Author", "Title", "CreationDate", "ModDate", "Subject", "Keywords")
_ESCAPES = {
    ord("n"): 0x0A, ord("r"): 0x0D, ord("t"): 0x09, ord("b"): 0x08, ord("f"): 0x0C,
    ord("("): 0x28, ord(")"): 0x29, ord("\\"): 0x5C,
}

NS_DC = "http://purl.org/dc/elements/1.1/"
NS_DCTERMS = "http://purl.org/dc/terms/"
NS_CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
NS_EP = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
NS_XMP = "http://ns.adobe.com/xap/1.0/"
NS_PDF = "http://ns.adobe.com/pdf/1.3/"
NS_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"


class DocMetaResult(BaseModel):
    filename: str = ""
    format: str  # pdf | docx | xlsx | pptx | ooxml
    sha256: str
    size_bytes: int
    title: str = ""
    subject: str = ""
    keywords: str = ""
    author: str = ""
    last_modified_by: str = ""
    software: str = ""  # Creator (PDF) o Application (Office)
    producer: str = ""  # Producer (PDF)
    company: str = ""  # Company (app.xml)
    created: datetime | None = None
    modified: datetime | None = None
    encrypted: bool = False
    entities: list[EntityRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- PDF


class _PdfSyntaxError(Exception):
    pass


class _PdfUnsupported(Exception):
    pass


class _PdfName(str):
    __slots__ = ()


@dataclass(frozen=True)
class _PdfRef:
    num: int
    gen: int


@dataclass
class _PdfStream:
    attrs: dict
    raw: bytes


class _PdfParser:
    """Analizador de objetos PDF (valores, no contenido de páginas)."""

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def _skip(self) -> None:
        data, n, p = self.data, len(self.data), self.pos
        while p < n:
            ch = data[p]
            if ch in _PDF_WS:
                p += 1
            elif ch == 0x25:  # comentario hasta fin de línea
                while p < n and data[p] not in (0x0A, 0x0D):
                    p += 1
            else:
                break
        self.pos = p

    def _token(self) -> bytes:
        data, n, p = self.data, len(self.data), self.pos
        start = p
        while p < n and data[p] not in _PDF_WS and data[p] not in _PDF_DELIM:
            p += 1
        self.pos = p
        return data[start:p]

    def parse(self, depth: int = 0) -> Any:
        if depth > MAX_PDF_DEPTH:
            raise _PdfSyntaxError("anidamiento excesivo")
        self._skip()
        data, p = self.data, self.pos
        if p >= len(data):
            raise _PdfSyntaxError("fin de datos")
        ch = data[p]
        if ch == 0x3C:  # '<'
            if data[p:p + 2] == b"<<":
                return self._dict(depth)
            return self._hex_string()
        if ch == 0x5B:  # '['
            return self._array(depth)
        if ch == 0x28:  # '('
            return self._literal_string()
        if ch == 0x2F:  # '/'
            return self._name()
        token = self._token()
        if not token:
            raise _PdfSyntaxError("token vacío")
        if _INT_RE.fullmatch(token):
            save = self.pos
            self._skip()
            gen = self._token()
            if gen and _INT_RE.fullmatch(gen):
                self._skip()
                if self._token() == b"R":
                    return _PdfRef(int(token), int(gen))
            self.pos = save
            return int(token)
        if _REAL_RE.fullmatch(token):
            return float(token)
        if token == b"true":
            return True
        if token == b"false":
            return False
        if token == b"null":
            return None
        raise _PdfSyntaxError("token inesperado")

    def _dict(self, depth: int) -> dict:
        self.pos += 2
        result: dict[str, Any] = {}
        while True:
            self._skip()
            if self.data[self.pos:self.pos + 2] == b">>":
                self.pos += 2
                return result
            if self.pos >= len(self.data) or len(result) > MAX_PDF_ITEMS:
                raise _PdfSyntaxError("diccionario no cerrado o demasiado grande")
            key = self.parse(depth + 1)
            if not isinstance(key, _PdfName):
                raise _PdfSyntaxError("clave de diccionario no es un nombre")
            result[str(key)] = self.parse(depth + 1)

    def _array(self, depth: int) -> list:
        self.pos += 1
        items: list[Any] = []
        while True:
            self._skip()
            if self.pos >= len(self.data):
                raise _PdfSyntaxError("arreglo no cerrado")
            if self.data[self.pos] == 0x5D:  # ']'
                self.pos += 1
                return items
            if len(items) > MAX_PDF_ITEMS:
                raise _PdfSyntaxError("arreglo demasiado grande")
            items.append(self.parse(depth + 1))

    def _name(self) -> _PdfName:
        self.pos += 1
        token = self._token()
        decoded = _NAME_ESC_RE.sub(lambda m: bytes([int(m.group(1), 16)]), token)
        return _PdfName(decoded.decode("latin-1"))

    def _literal_string(self) -> bytes:
        data, n = self.data, len(self.data)
        p = self.pos + 1
        depth = 1
        out = bytearray()
        while p < n:
            ch = data[p]
            if ch == 0x5C:  # barra invertida
                p += 1
                if p >= n:
                    break
                nxt = data[p]
                if nxt in _ESCAPES:
                    out.append(_ESCAPES[nxt])
                    p += 1
                elif 0x30 <= nxt <= 0x37:  # octal de hasta tres dígitos
                    value, count = 0, 0
                    while p < n and count < 3 and 0x30 <= data[p] <= 0x37:
                        value = value * 8 + (data[p] - 0x30)
                        p += 1
                        count += 1
                    out.append(value & 0xFF)
                elif nxt == 0x0D:  # continuación de línea
                    p += 2 if data[p + 1:p + 2] == b"\n" else 1
                elif nxt == 0x0A:
                    p += 1
                else:
                    out.append(nxt)
                    p += 1
            elif ch == 0x28:
                depth += 1
                out.append(ch)
                p += 1
            elif ch == 0x29:
                depth -= 1
                p += 1
                if depth == 0:
                    self.pos = p
                    return bytes(out)
                out.append(ch)
            else:
                out.append(ch)
                p += 1
            if len(out) > MAX_PDF_STRING:
                raise _PdfSyntaxError("cadena demasiado larga")
        raise _PdfSyntaxError("cadena literal sin cerrar")

    def _hex_string(self) -> bytes:
        end = self.data.find(b">", self.pos + 1)
        if end < 0 or end - self.pos > 2 * MAX_PDF_STRING:
            raise _PdfSyntaxError("cadena hexadecimal inválida")
        raw = _HEX_CLEAN_RE.sub(b"", self.data[self.pos + 1:end])
        if len(raw) % 2:
            raw += b"0"
        if not _HEX_RE.fullmatch(raw):
            raise _PdfSyntaxError("caracteres no hexadecimales")
        self.pos = end + 1
        return bytes.fromhex(raw.decode("ascii"))


def _maybe_stream(data: bytes, parser: _PdfParser, attrs: dict) -> _PdfStream | None:
    """Si tras el diccionario viene «stream», lee el contenido hasta «endstream»."""
    parser._skip()
    p = parser.pos
    if not data.startswith(b"stream", p):
        return None
    p += len(b"stream")
    if data.startswith(b"\r\n", p):
        p += 2
    elif data[p:p + 1] in (b"\n", b"\r"):
        p += 1
    start = p
    end = -1
    length = attrs.get("Length")
    if isinstance(length, int) and not isinstance(length, bool) and length >= 0:
        candidate = start + length
        if data[candidate:candidate + 32].lstrip(_PDF_WS).startswith(b"endstream"):
            end = candidate
    if end < 0:
        end = data.find(b"endstream", start)
        if end < 0:
            return None
        raw = data[start:end]
        if raw.endswith(b"\r\n"):
            raw = raw[:-2]
        elif raw.endswith((b"\n", b"\r")):
            raw = raw[:-1]
    else:
        raw = data[start:end]
        end = data.find(b"endstream", end)
    parser.pos = end + len(b"endstream")
    return _PdfStream(attrs=attrs, raw=raw)


def _scan_objects(data: bytes) -> tuple[dict[int, Any], list[dict]]:
    """Recorre los objetos indirectos en orden de archivo; la última definición gana."""
    objects: dict[int, Any] = {}
    xref_dicts: list[dict] = []
    pos = 0
    scanned = 0
    while scanned < MAX_PDF_OBJECTS:
        match = _OBJ_RE.search(data, pos)
        if match is None:
            break
        scanned += 1
        number = int(match.group(1))
        parser = _PdfParser(data, match.end())
        try:
            value = parser.parse()
        except _PdfSyntaxError:
            pos = match.end()
            continue
        pos = parser.pos
        if isinstance(value, dict):
            try:
                stream = _maybe_stream(data, parser, value)
            except _PdfSyntaxError:
                stream = None
            if stream is not None:
                value = stream
                pos = parser.pos
        objects[number] = value
        if isinstance(value, _PdfStream) and value.attrs.get("Type") == "XRef":
            xref_dicts.append(value.attrs)
    return objects, xref_dicts


def _decode_stream(stream: _PdfStream, max_bytes: int = MAX_STREAM_BYTES) -> bytes:
    """Decodifica un flujo sin filtro o con FlateDecode, con tope de tamaño (anti zip bomb)."""
    filters = stream.attrs.get("Filter")
    if filters is None:
        names: list[Any] = []
    elif isinstance(filters, list):
        names = filters
    else:
        names = [filters]
    if not names:
        if len(stream.raw) > max_bytes:
            raise UnsafeFileError("flujo sin comprimir demasiado grande")
        return stream.raw
    if len(names) == 1 and names[0] in ("FlateDecode", "Fl"):
        try:
            output = zlib.decompressobj().decompress(stream.raw, max_bytes + 1)
        except zlib.error as exc:
            raise _PdfUnsupported("flujo Flate corrupto") from exc
        if len(output) > max_bytes:
            raise UnsafeFileError("flujo descomprimido por encima del límite (posible zip bomb)")
        return output
    raise _PdfUnsupported("filtro no soportado")


class _PdfDoc:
    def __init__(self, objects: dict[int, Any], objstm: dict[int, Any]):
        self.objects = objects
        self.objstm = objstm

    def get(self, value: Any, depth: int = 0) -> Any:
        if isinstance(value, _PdfRef):
            if depth > 8:
                return None
            target = self.objects.get(value.num)
            if target is None:
                target = self.objstm.get(value.num)
            return self.get(target, depth + 1) if isinstance(target, _PdfRef) else target
        return value


def _objstm_objects(objects: dict[int, Any], warnings: list[str]) -> dict[int, Any]:
    """Objetos empaquetados en flujos de objetos (PDF 1.5+), donde suele estar el diccionario Info."""
    found: dict[int, Any] = {}
    for value in objects.values():
        if not (isinstance(value, _PdfStream) and value.attrs.get("Type") == "ObjStm"):
            continue
        try:
            body = _decode_stream(value)
            count = min(int(value.attrs.get("N", 0)), MAX_PDF_ITEMS)
            first = int(value.attrs.get("First", 0))
            header = _PdfParser(body, 0)
            pairs = [(header.parse(), header.parse()) for _ in range(count)]
        except (_PdfUnsupported, UnsafeFileError, _PdfSyntaxError, ValueError, TypeError):
            warnings.append("un flujo de objetos no pudo decodificarse; parte de los metadatos puede faltar")
            continue
        for number, offset in pairs:
            if not (isinstance(number, int) and isinstance(offset, int)):
                continue
            try:
                found.setdefault(number, _PdfParser(body, first + offset).parse())
            except _PdfSyntaxError:
                continue
    return found


def _pick_trailer(data: bytes, xref_dicts: list[dict]) -> dict | None:
    candidates: list[dict] = []
    for match in _TRAILER_RE.finditer(data):
        try:
            value = _PdfParser(data, match.end()).parse()
        except _PdfSyntaxError:
            continue
        if isinstance(value, dict) and ("Info" in value or "Root" in value):
            candidates.append(value)
    if candidates:
        return candidates[-1]
    for attrs in reversed(xref_dicts):
        if "Info" in attrs or "Root" in attrs:
            return attrs
    return None


def _fallback_dict(doc: _PdfDoc, predicate, min_score: int) -> dict | None:
    """Busca un diccionario por contenido cuando el trailer no lo referencia (archivos dañados)."""
    best: tuple[int, int] = (0, -1)
    chosen: dict | None = None
    for source in (doc.objects, doc.objstm):
        for number, value in source.items():
            if isinstance(value, dict) and predicate(value):
                score = sum(1 for key in _INFO_KEYS if key in value)
                if score >= min_score and (score, number) > best:
                    best = (score, number)
                    chosen = value
    return chosen


def _pdf_text(value: Any) -> str:
    """Decodifica una cadena de texto PDF (UTF-16 con BOM, UTF-8 o PDFDocEncoding aproximado)."""
    if isinstance(value, _PdfName):
        return str(value)
    if isinstance(value, bytes):
        if value.startswith(b"\xfe\xff"):
            text = value[2:].decode("utf-16-be", errors="replace")
        elif value.startswith(b"\xff\xfe"):
            text = value[2:].decode("utf-16-le", errors="replace")
        elif value.startswith(b"\xef\xbb\xbf"):
            text = value[3:].decode("utf-8", errors="replace")
        else:
            try:
                text = value.decode("utf-8")
            except UnicodeDecodeError:
                text = value.decode("cp1252", errors="replace")
        return text.replace("\x00", "").strip()
    if isinstance(value, str):
        return value.strip()
    return ""


def _pdf_date(text: str) -> datetime | None:
    match = _PDF_DATE_RE.fullmatch(text.strip())
    if not match:
        return None
    parts = [int(g) if g else None for g in match.groups()[:6]]
    year, month, day, hour, minute, second = (
        parts[0], parts[1] or 1, parts[2] or 1, parts[3] or 0, parts[4] or 0, parts[5] or 0,
    )
    zone: timezone | None = None
    sign_char = match.group(7)
    if sign_char in ("Z", "z"):
        zone = UTC
    elif sign_char in ("+", "-"):
        delta = timedelta(hours=int(match.group(8) or 0), minutes=int(match.group(9) or 0))
        zone = timezone(delta if sign_char == "+" else -delta)
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=zone)
    except ValueError:
        return None


def _reject_dtd(xml: bytes) -> None:
    if _DTD_RE.search(xml):
        raise UnsafeFileError("XML con declaraciones DTD o ENTITY rechazado (posible expansión de entidades)")


def _xmp_values(xml: bytes) -> dict[str, str]:
    """Pares «{espacio}nombre» → valor de un paquete XMP (elementos, contenedores y atributos)."""
    _reject_dtd(xml)
    root = ET.fromstring(xml)  # sin DTD, el parser no expande entidades
    values: dict[str, str] = {}
    rdf_containers = {f"{{{NS_RDF}}}{name}" for name in ("Seq", "Bag", "Alt")}
    rdf_li = f"{{{NS_RDF}}}li"
    for elem in root.iter():
        for attr, value in elem.attrib.items():
            if attr.startswith("{") and value.strip():
                values.setdefault(attr, value.strip())
        children = list(elem)
        if len(children) == 1 and children[0].tag in rdf_containers:
            items = [(li.text or "").strip() for li in children[0] if li.tag == rdf_li]
            joined = "; ".join(item for item in items if item)
            if joined:
                values[elem.tag] = joined
        elif not children and (elem.text or "").strip():
            values.setdefault(elem.tag, elem.text.strip())
    return values


def _xmp_date(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _analyze_pdf(data: bytes, filename: str, sha: str, warnings: list[str]) -> DocMetaResult:
    objects, xref_dicts = _scan_objects(data)
    objstm = _objstm_objects(objects, warnings)
    doc = _PdfDoc(objects, objstm)
    trailer = _pick_trailer(data, xref_dicts)
    encrypted = bool(trailer and "Encrypt" in trailer)
    if encrypted:
        warnings.append("PDF cifrado: las cadenas de metadatos pueden no ser legibles")
    info = doc.get(trailer.get("Info")) if trailer else None
    root = doc.get(trailer.get("Root")) if trailer else None
    if not isinstance(info, dict):
        info = _fallback_dict(doc, lambda d: True, min_score=2) or {}
        if info:
            warnings.append("el trailer no referencia el diccionario Info; se usó el diccionario de metadatos hallado")
    if not isinstance(root, dict):
        root = _fallback_dict(doc, lambda d: d.get("Type") == "Catalog", min_score=0) or {}

    xmp: dict[str, str] = {}
    metadata_stream = doc.get(root.get("Metadata"))
    if isinstance(metadata_stream, _PdfStream):
        try:
            xml = _decode_stream(metadata_stream, max_bytes=MAX_XML_BYTES)
            xmp = _xmp_values(xml)
        except (_PdfUnsupported, UnsafeFileError, ET.ParseError, _PdfSyntaxError) as exc:
            warnings.append(f"XMP omitido: {exc}")

    def info_text(key: str) -> str:
        return _pdf_text(doc.get(info.get(key)))

    title = info_text("Title") or xmp.get(f"{{{NS_DC}}}title", "")
    author = info_text("Author") or xmp.get(f"{{{NS_DC}}}creator", "")
    subject = info_text("Subject") or xmp.get(f"{{{NS_DC}}}description", "")
    keywords = info_text("Keywords") or xmp.get(f"{{{NS_PDF}}}Keywords", "")
    creator = info_text("Creator") or xmp.get(f"{{{NS_XMP}}}CreatorTool", "")
    producer = info_text("Producer") or xmp.get(f"{{{NS_PDF}}}Producer", "")
    created = _pdf_date(info_text("CreationDate")) or _xmp_date(xmp.get(f"{{{NS_XMP}}}CreateDate", ""))
    modified = _pdf_date(info_text("ModDate")) or _xmp_date(xmp.get(f"{{{NS_XMP}}}ModifyDate", ""))
    xmp_author = xmp.get(f"{{{NS_DC}}}creator", "")
    info_author = info_text("Author")
    if info_author and xmp_author and info_author != xmp_author:
        warnings.append("el autor difiere entre el diccionario Info y el XMP; se muestra el de Info")

    raw_info = {key: _pdf_text(doc.get(value)) for key, value in info.items() if key in _INFO_KEYS}
    raw = {"info": raw_info, "xmp": {k.split("}")[-1]: v for k, v in xmp.items()}}
    return _build_result(
        filename=filename, fmt="pdf", sha=sha, size=len(data), title=title, subject=subject,
        keywords=keywords, author=author, last_modified_by="", software=creator, producer=producer,
        company="", created=created, modified=modified, encrypted=encrypted, warnings=warnings, raw=raw,
    )


# --------------------------------------------------------------------------- OOXML


def _read_member(zf: zipfile.ZipFile, infos: dict[str, zipfile.ZipInfo], name: str,
                 warnings: list[str]) -> bytes | None:
    info = infos.get(name)
    if info is None:
        return None
    if info.flag_bits & 0x1:
        warnings.append(f"{name} está cifrado dentro del paquete y no se leyó")
        return None
    if info.file_size > MAX_XML_BYTES:
        raise UnsafeFileError(f"{name}: tamaño descomprimido excesivo")
    if info.compress_size == 0 and info.file_size > 0:
        raise UnsafeFileError(f"{name}: entrada con tamaño comprimido cero (sospechosa)")
    if info.file_size > MAX_ZIP_RATIO_MIN_SIZE and info.file_size > MAX_ZIP_RATIO * info.compress_size:
        raise UnsafeFileError(f"{name}: relación de compresión sospechosa (posible zip bomb)")
    try:
        with zf.open(info) as handle:
            payload = handle.read(MAX_XML_BYTES + 1)  # tope real, aunque el tamaño declarado mienta
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, ValueError) as exc:
        warnings.append(f"{name} no pudo leerse: {type(exc).__name__}")
        return None
    if len(payload) > MAX_XML_BYTES:
        raise UnsafeFileError(f"{name}: tamaño descomprimido excesivo")
    return payload


def _parse_props(xml: bytes, warnings: list[str], name: str) -> dict[str, str]:
    _reject_dtd(xml)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        warnings.append(f"{name} no es XML válido")
        return {}
    return {child.tag: (child.text or "").strip() for child in root}


def _parse_date(text: str) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _analyze_ooxml(data: bytes, filename: str, sha: str, warnings: list[str]) -> DocMetaResult:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise InvalidInputError("paquete ZIP inválido") from exc
    entries = archive.infolist()
    if len(entries) > MAX_ZIP_ENTRIES:
        raise UnsafeFileError(f"paquete con demasiadas entradas ({len(entries)}); posible zip bomb")
    if sum(entry.file_size for entry in entries) > MAX_ZIP_DECLARED_BYTES:
        raise UnsafeFileError("tamaño descomprimido declarado excesivo; posible zip bomb")
    infos = {entry.filename: entry for entry in entries}
    if "[Content_Types].xml" not in infos:
        raise InvalidInputError("el ZIP no es un paquete Office Open XML")
    if any(name.startswith("word/") for name in infos):
        fmt = "docx"
    elif any(name.startswith("xl/") for name in infos):
        fmt = "xlsx"
    elif any(name.startswith("ppt/") for name in infos):
        fmt = "pptx"
    else:
        fmt = "ooxml"

    core_bytes = _read_member(archive, infos, "docProps/core.xml", warnings)
    app_bytes = _read_member(archive, infos, "docProps/app.xml", warnings)
    core = _parse_props(core_bytes, warnings, "docProps/core.xml") if core_bytes else {}
    app = _parse_props(app_bytes, warnings, "docProps/app.xml") if app_bytes else {}

    application = app.get(f"{{{NS_EP}}}Application", "")
    app_version = app.get(f"{{{NS_EP}}}AppVersion", "")
    software = f"{application} {app_version}".strip() if application else ""
    raw = {
        "core": {k.split("}")[-1]: v for k, v in core.items()},
        "app": {k.split("}")[-1]: v for k, v in app.items()},
    }
    return _build_result(
        filename=filename, fmt=fmt, sha=sha, size=len(data),
        title=core.get(f"{{{NS_DC}}}title", ""), subject=core.get(f"{{{NS_DC}}}subject", ""),
        keywords=core.get(f"{{{NS_CP}}}keywords", ""), author=core.get(f"{{{NS_DC}}}creator", ""),
        last_modified_by=core.get(f"{{{NS_CP}}}lastModifiedBy", ""), software=software, producer="",
        company=app.get(f"{{{NS_EP}}}Company", ""),
        created=_parse_date(core.get(f"{{{NS_DCTERMS}}}created", "")),
        modified=_parse_date(core.get(f"{{{NS_DCTERMS}}}modified", "")),
        encrypted=False, warnings=warnings, raw=raw,
    )


# --------------------------------------------------------------------------- resultado


def _build_result(*, filename: str, fmt: str, sha: str, size: int, title: str, subject: str,
                  keywords: str, author: str, last_modified_by: str, software: str, producer: str,
                  company: str, created: datetime | None, modified: datetime | None, encrypted: bool,
                  warnings: list[str], raw: dict[str, Any]) -> DocMetaResult:
    doc_label = title or filename or f"documento {sha[:12]}"
    entities = [EntityRecord(
        type="document", label=doc_label, ref="doc", confidence=1.0,
        props={
            "sha256": sha, "format": fmt, "software": software, "producer": producer,
            "created": created.isoformat() if created else "", "modified": modified.isoformat() if modified else "",
        },
    )]
    relations: list[RelationRecord] = []
    people: dict[str, str] = {}

    def ensure_person(name: str, ref_hint: str) -> str:
        """Crea la entidad persona una sola vez por nombre y devuelve su referencia."""
        if name not in people:
            people[name] = ref_hint
            entities.append(EntityRecord(
                type="person", label=name, ref=ref_hint, confidence=ATTRIBUTION_CONFIDENCE,
                props={"source": "metadatos del documento"},
            ))
        return people[name]

    if author:
        ref = ensure_person(author, "author")
        relations.append(RelationRecord(src_ref=ref, dst_ref="doc", type="author_of",
                                        confidence=ATTRIBUTION_CONFIDENCE))
    if last_modified_by:
        ref = ensure_person(last_modified_by, "editor")
        relations.append(RelationRecord(src_ref=ref, dst_ref="doc", type="last_editor_of",
                                        confidence=ATTRIBUTION_CONFIDENCE))
    if company:
        entities.append(EntityRecord(
            type="organization", label=company, ref="company", confidence=ATTRIBUTION_CONFIDENCE,
            props={"source": "docProps/app.xml"},
        ))
        relations.append(RelationRecord(src_ref="company", dst_ref="doc", type="associated_with",
                                        confidence=ATTRIBUTION_CONFIDENCE))

    return DocMetaResult(
        filename=filename, format=fmt, sha256=sha, size_bytes=size, title=title, subject=subject,
        keywords=keywords, author=author, last_modified_by=last_modified_by, software=software,
        producer=producer, company=company, created=created, modified=modified, encrypted=encrypted,
        entities=entities, relations=relations, warnings=warnings, raw=raw,
    )


def analyze_document(data: bytes, *, filename: str = "") -> DocMetaResult:
    """Extrae metadatos de un PDF o de un paquete Office Open XML. Función sync: cálculo local."""
    if not data:
        raise InvalidInputError("no hay datos de documento")
    if len(data) > MAX_FILE_BYTES:
        raise InvalidInputError(f"documento demasiado grande (máximo {MAX_FILE_BYTES} bytes)")
    payload = bytes(data)
    sha = hashlib.sha256(payload).hexdigest()
    warnings: list[str] = []
    if b"%PDF-" in payload[:1024]:
        return _analyze_pdf(payload, filename, sha, warnings)
    if payload[:4] == b"PK\x03\x04":
        return _analyze_ooxml(payload, filename, sha, warnings)
    raise InvalidInputError("formato no reconocido: se esperaba PDF o Office Open XML (docx, xlsx, pptx)")
