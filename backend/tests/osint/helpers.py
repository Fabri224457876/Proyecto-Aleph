"""Constructores de archivos y respuestas de prueba. Todo es sintético: sin red y sin datos de personas reales."""

from __future__ import annotations

import io
import zipfile
import zlib
from collections.abc import Callable
from typing import Any

import httpx
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

CORE_NS = (
    'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
)
APP_NS = 'xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"'


def make_jpeg(*, with_exif: bool = True, with_gps: bool = True) -> bytes:
    """JPEG de 64x48. Con GPS: 34°36'12.34\" S, 58°22'56.78\" W, 14:30:00 UTC del 2024-05-01."""
    image = Image.new("RGB", (64, 48), (200, 30, 30))
    buffer = io.BytesIO()
    if not with_exif:
        image.save(buffer, "JPEG")
        return buffer.getvalue()
    exif = Image.Exif()
    exif[0x010F] = "Canon"
    exif[0x0110] = "EOS R5"
    exif[0x0131] = "Adobe Photoshop 25.0"
    exif[0x0132] = "2024:05:01 12:00:00"
    exif.get_ifd(0x8769)[0x9003] = "2024:05:01 11:58:30"
    if with_gps:
        gps = exif.get_ifd(0x8825)
        gps[1] = "S"
        gps[2] = (IFDRational(34, 1), IFDRational(36, 1), IFDRational(1234, 100))
        gps[3] = "W"
        gps[4] = (IFDRational(58, 1), IFDRational(22, 1), IFDRational(5678, 100))
        gps[29] = "2024:05:01"
        gps[7] = (IFDRational(14, 1), IFDRational(30, 1), IFDRational(0, 1))
    image.save(buffer, "JPEG", exif=exif.tobytes())
    return buffer.getvalue()


def make_zip(entries: dict[str, bytes], *, compress: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compress) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def make_docx(core_xml: bytes | None = None, app_xml: bytes | None = None,
              extra: dict[str, bytes] | None = None) -> bytes:
    entries: dict[str, bytes] = {
        "[Content_Types].xml": b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "_rels/.rels": b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
        "word/document.xml": b'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
    }
    if core_xml is not None:
        entries["docProps/core.xml"] = core_xml
    if app_xml is not None:
        entries["docProps/app.xml"] = app_xml
    entries.update(extra or {})
    return make_zip(entries)


def core_xml(*, title: str = "", creator: str = "", last_modified_by: str = "", created: str = "",
             modified: str = "") -> bytes:
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', f"<cp:coreProperties {CORE_NS}>"]
    if title:
        parts.append(f"<dc:title>{title}</dc:title>")
    if creator:
        parts.append(f"<dc:creator>{creator}</dc:creator>")
    if last_modified_by:
        parts.append(f"<cp:lastModifiedBy>{last_modified_by}</cp:lastModifiedBy>")
    if created:
        parts.append(f'<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>')
    if modified:
        parts.append(f'<dcterms:modified xsi:type="dcterms:W3CDTF">{modified}</dcterms:modified>')
    parts.append("</cp:coreProperties>")
    return "".join(parts).encode("utf-8")


def app_xml(*, application: str = "", app_version: str = "", company: str = "") -> bytes:
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', f"<Properties {APP_NS}>"]
    if application:
        parts.append(f"<Application>{application}</Application>")
    if app_version:
        parts.append(f"<AppVersion>{app_version}</AppVersion>")
    if company:
        parts.append(f"<Company>{company}</Company>")
    parts.append("</Properties>")
    return "".join(parts).encode("utf-8")


def stream_object(attrs: bytes, data: bytes) -> bytes:
    return b"<< " + attrs + b" /Length " + str(len(data)).encode() + b" >>\nstream\n" + data + b"\nendstream"


def make_pdf(objects: list[bytes], trailer: bytes) -> bytes:
    """Objetos numerados desde 1 y un trailer clásico. El analizador no depende de la tabla xref."""
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    for number, body in enumerate(objects, start=1):
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    out += b"trailer\n" + trailer + b"\n%%EOF\n"
    return bytes(out)


def make_pdf_xref_stream(objects: list[bytes], xref_dict: bytes) -> bytes:
    """PDF 1.5 sin palabra clave trailer: el diccionario del flujo XRef hace de trailer."""
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    for number, body in enumerate(objects, start=1):
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    out += b"%%EOF\n"
    return bytes(out)


XMP_SAMPLE = (
    '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:pdf="http://ns.adobe.com/pdf/1.3/">'
    '<dc:creator><rdf:Seq><rdf:li>Ana Pérez (XMP)</rdf:li></rdf:Seq></dc:creator>'
    '<dc:title><rdf:Alt><rdf:li xml:lang="x-default">Título XMP</rdf:li></rdf:Alt></dc:title>'
    '<xmp:CreatorTool>Adobe InDesign 18.0</xmp:CreatorTool>'
    '<xmp:CreateDate>2024-05-01T12:00:00-03:00</xmp:CreateDate>'
    '<pdf:Producer>Acrobat Distiller</pdf:Producer>'
    '</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
).encode()


Handler = Callable[[httpx.Request], httpx.Response]


def json_response(payload: Any, status: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers)


def text_response(text: str, status: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, text=text, headers=headers)


def mock_client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def deflate(data: bytes) -> bytes:
    return zlib.compress(data)
