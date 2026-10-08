from datetime import UTC, datetime, timedelta, timezone

import pytest

from aleph.osint import doc_meta
from aleph.osint.doc_meta import analyze_document
from aleph.osint.errors import InvalidInputError, UnsafeFileError

from .helpers import (
    XMP_SAMPLE,
    app_xml,
    core_xml,
    deflate,
    make_docx,
    make_pdf,
    make_pdf_xref_stream,
    make_zip,
    stream_object,
)

MINUS_3 = timezone(timedelta(hours=-3))
PDF_INFO = (
    b"<< /Title (Informe anual) /Author (Ana P\xc3\xa9rez) /Creator (Writer) "
    b"/Producer (LibreOffice 7.5) /CreationDate (D:20240501120000-03'00') "
    b"/ModDate (D:20240502093000Z) >>"
)


def _pdf_with_info_and_xmp(info: bytes = PDF_INFO, xmp: bytes = XMP_SAMPLE, *, compressed_xmp: bool = False) -> bytes:
    if compressed_xmp:
        xmp_obj = stream_object(b"/Type /Metadata /Subtype /XML /Filter /FlateDecode", deflate(xmp))
    else:
        xmp_obj = stream_object(b"/Type /Metadata /Subtype /XML", xmp)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R /Metadata 4 0 R >>",
        b"<< /Type /Pages /Kids [] /Count 0 >>",
        info,
        xmp_obj,
    ]
    return make_pdf(objects, b"<< /Size 5 /Root 1 0 R /Info 3 0 R >>")


def test_pdf_info_dictionary_and_dates():
    result = analyze_document(_pdf_with_info_and_xmp(), filename="informe.pdf")
    assert result.format == "pdf"
    assert result.title == "Informe anual"
    assert result.author == "Ana Pérez"
    assert result.software == "Writer"
    assert result.producer == "LibreOffice 7.5"
    assert result.created == datetime(2024, 5, 1, 12, 0, 0, tzinfo=MINUS_3)
    assert result.modified == datetime(2024, 5, 2, 9, 30, 0, tzinfo=UTC)
    assert result.encrypted is False


def test_pdf_xmp_is_used_as_fallback_and_conflicts_are_reported():
    result = analyze_document(_pdf_with_info_and_xmp(info=b"<< /Producer (X) >>"))
    assert result.author == "Ana Pérez (XMP)"  # Info sin Author: se usa XMP
    assert result.title == "Título XMP"
    assert result.created == datetime(2024, 5, 1, 12, 0, 0, tzinfo=MINUS_3)
    result = analyze_document(_pdf_with_info_and_xmp())
    assert result.author == "Ana Pérez"  # Info tiene prioridad
    assert any("difiere" in w for w in result.warnings)


def test_pdf_compressed_xmp_is_decoded():
    result = analyze_document(_pdf_with_info_and_xmp(compressed_xmp=True))
    assert result.author == "Ana Pérez"
    assert result.raw["xmp"]["CreatorTool"] == "Adobe InDesign 18.0"


def test_pdf_info_inside_object_stream_is_found():
    # El diccionario Info queda dentro de un flujo de objetos (PDF 1.5), como en muchos archivos modernos
    info_body = b"<< /Title (Oculto en ObjStm) /Author (Carla Ruiz) /Producer (Generador) >>"
    header = b"6 0 "
    stream_data = header + info_body
    objstm = stream_object(
        f"/Type /ObjStm /N 1 /First {len(header)} /Filter /FlateDecode".encode(), deflate(stream_data),
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [] /Count 0 >>",
        objstm,
        b"<< /Type /XRef /Size 7 /Root 1 0 R /Info 6 0 R /W [1 2 1] >>\nstream\n\nendstream",
    ]
    data = make_pdf_xref_stream(objects, b"")
    result = analyze_document(data)
    assert result.title == "Oculto en ObjStm"
    assert result.author == "Carla Ruiz"
    assert result.software == ""
    assert result.producer == "Generador"


def test_pdf_utf16_and_hex_strings_are_decoded():
    utf16_title = b"\xfe\xff" + "Ñandú 2024".encode("utf-16-be")
    hex_author = b"<" + b"Mar\xc3\xada".hex().encode() + b">"  # cadena hexadecimal con UTF-8
    info = b"<< /Title (" + utf16_title + b") /Author " + hex_author + b" >>"
    result = analyze_document(make_pdf([b"<< /Type /Catalog >>", info], b"<< /Root 1 0 R /Info 2 0 R >>"))
    assert result.title == "Ñandú 2024"
    assert result.author == "María"


def test_pdf_literal_string_escapes_are_decoded():
    info = rb"<< /Title (Parent\(es\)is \101\102 \\ fin) >>"
    result = analyze_document(make_pdf([b"<< /Type /Catalog >>", info], b"<< /Root 1 0 R /Info 2 0 R >>"))
    assert result.title == r"Parent(es)is AB \ fin"


def test_pdf_encrypted_flag_and_warning():
    data = make_pdf([b"<< /Type /Catalog >>", PDF_INFO],
                    b"<< /Root 1 0 R /Info 2 0 R /Encrypt 9 0 R >>")
    result = analyze_document(data)
    assert result.encrypted is True
    assert any("cifrado" in w for w in result.warnings)


def test_pdf_without_info_reference_uses_content_fallback():
    data = make_pdf([b"<< /Type /Catalog >>", PDF_INFO], b"<< /Root 1 0 R >>")
    result = analyze_document(data)
    assert result.title == "Informe anual"
    assert any("trailer no referencia" in w for w in result.warnings)


def test_pdf_oversized_xmp_stream_is_skipped_with_warning(monkeypatch):
    monkeypatch.setattr(doc_meta, "MAX_XML_BYTES", 1024)
    bomb = deflate(b"<" + b" " * (2 * 1024 * 1024) + b"/>")
    xmp_obj = stream_object(b"/Type /Metadata /Subtype /XML /Filter /FlateDecode", bomb)
    data = make_pdf([b"<< /Type /Catalog /Metadata 3 0 R >>", PDF_INFO, xmp_obj],
                    b"<< /Root 1 0 R /Info 2 0 R >>")
    result = analyze_document(data)
    assert result.title == "Informe anual"  # el resto de los metadatos sigue disponible
    assert any(w.startswith("XMP omitido") for w in result.warnings)


def test_pdf_unsupported_filter_on_xmp_is_skipped():
    xmp_obj = stream_object(b"/Type /Metadata /Filter /LZWDecode", b"\x00\x01")
    data = make_pdf([b"<< /Type /Catalog /Metadata 3 0 R >>", PDF_INFO, xmp_obj], b"<< /Root 1 0 R /Info 2 0 R >>")
    result = analyze_document(data)
    assert result.author == "Ana Pérez"
    assert any("XMP omitido" in w for w in result.warnings)


def test_docx_core_and_app_properties_and_graph():
    data = make_docx(
        core_xml=core_xml(title="Plan de trabajo", creator="Juan Gómez", last_modified_by="María Soto",
                          created="2024-03-01T10:00:00Z", modified="2024-03-05T18:30:00Z"),
        app_xml=app_xml(application="Microsoft Office Word", app_version="16.0000", company="Ejemplo S.A."),
    )
    result = analyze_document(data, filename="plan.docx")
    assert result.format == "docx"
    assert result.title == "Plan de trabajo"
    assert result.author == "Juan Gómez"
    assert result.last_modified_by == "María Soto"
    assert result.software == "Microsoft Office Word 16.0000"
    assert result.company == "Ejemplo S.A."
    assert result.created == datetime(2024, 3, 1, 10, 0, tzinfo=UTC)
    assert result.modified == datetime(2024, 3, 5, 18, 30, tzinfo=UTC)

    people = {e.label: e for e in result.entities if e.type == "person"}
    assert set(people) == {"Juan Gómez", "María Soto"}
    assert [e.label for e in result.entities if e.type == "organization"] == ["Ejemplo S.A."]
    relation_types = sorted(r.type for r in result.relations)
    assert relation_types == ["associated_with", "author_of", "last_editor_of"]
    doc = next(e for e in result.entities if e.type == "document")
    author_ref = next(e.ref for e in result.entities if e.type == "person" and e.label == "Juan Gómez")
    assert any(r.src_ref == author_ref and r.dst_ref == doc.ref and r.type == "author_of" for r in result.relations)
    assert all(e.confidence <= 0.6 for e in result.entities if e.type in ("person", "organization"))


def test_docx_same_author_and_editor_share_one_person_entity():
    data = make_docx(core_xml=core_xml(creator="Ana", last_modified_by="Ana"), app_xml=None)
    result = analyze_document(data)
    assert [e.label for e in result.entities if e.type == "person"] == ["Ana"]
    person_ref = next(e.ref for e in result.entities if e.type == "person")
    assert {r.src_ref for r in result.relations if r.type != "associated_with"} == {person_ref}


def test_ooxml_without_core_parts_still_parses():
    result = analyze_document(make_docx())
    assert result.format == "docx"
    assert result.author == ""
    assert result.entities[0].type == "document"


def test_xlsx_and_pptx_are_recognized():
    xlsx = make_zip({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<workbook/>"})
    pptx = make_zip({"[Content_Types].xml": b"<Types/>", "ppt/presentation.xml": b"<p/>"})
    assert analyze_document(xlsx).format == "xlsx"
    assert analyze_document(pptx).format == "pptx"


def test_xml_with_doctype_or_entities_is_rejected():
    # Expansión de entidades (variante «billion laughs» en miniatura)
    evil = (b'<?xml version="1.0"?><!DOCTYPE cp:coreProperties [<!ENTITY a "aaaaaaaaaa">'
            b'<!ENTITY b "&a;&a;&a;&a;">]>'
            b'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            b'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>&b;</dc:creator></cp:coreProperties>')
    with pytest.raises(UnsafeFileError):
        analyze_document(make_docx(core_xml=evil))


def test_zip_bomb_by_declared_size_is_rejected():
    huge = b'<?xml version="1.0"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties">' \
        + b" " * (3 * 1024 * 1024) + b"</cp:coreProperties>"
    with pytest.raises(UnsafeFileError):
        analyze_document(make_docx(core_xml=huge))


def test_zip_bomb_by_compression_ratio_is_rejected():
    # 1 MiB de espacios dentro de un XML: bajo el tope de tamaño, pero con una relación de compresión enorme
    padded = b'<?xml version="1.0"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties">' \
        + b" " * (1024 * 1024) + b"</cp:coreProperties>"
    with pytest.raises(UnsafeFileError):
        analyze_document(make_docx(core_xml=padded))


def test_too_many_zip_entries_is_rejected(monkeypatch):
    monkeypatch.setattr(doc_meta, "MAX_ZIP_ENTRIES", 20)
    entries = {"[Content_Types].xml": b"<Types/>"}
    entries.update({f"word/part{i}.xml": b"<x/>" for i in range(25)})
    with pytest.raises(UnsafeFileError):
        analyze_document(make_zip(entries))


def test_unknown_format_and_bad_zip_are_rejected():
    with pytest.raises(InvalidInputError):
        analyze_document(b"texto plano sin formato")
    with pytest.raises(InvalidInputError):
        analyze_document(b"PK\x03\x04 truncado")
    with pytest.raises(InvalidInputError):
        analyze_document(make_zip({"otro.txt": b"x"}))  # ZIP que no es OOXML


def test_empty_input_rejected():
    with pytest.raises(InvalidInputError):
        analyze_document(b"")


def test_size_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(doc_meta, "MAX_FILE_BYTES", 16)
    with pytest.raises(InvalidInputError):
        analyze_document(_pdf_with_info_and_xmp())
