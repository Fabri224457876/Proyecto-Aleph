"""Informe en PDF: documento válido, marca TLP, fuentes valoradas y caracteres fuera de Latin-1."""

from datetime import UTC, datetime

from aleph.funes.pdf import ReportMeta, SourceRow, markdown_digest, render_pdf

MD = """# Borrador de informe de inteligencia — Caso de prueba

> Borrador generado por FUNES.

## 1. Resumen ejecutivo

- **Confianza media.** El dominio pagos-seguros.example → 203.0.113.12 ≈ kit espejo [E1][H1]
- Afirmación sin datos [SIN RESPALDO EN LOS DATOS]

## 2. Hallazgos

_Sin hallazgos._
"""


def _meta(**kw) -> ReportMeta:
    base = dict(case_id=7, case_name="Caso <de> prueba & cía", tlp="red", purpose="Demostración",
                legal_basis="Datos sintéticos (Ley 25.326)", author="analista-demo",
                generated_at=datetime(2026, 11, 2, 12, 0, tzinfo=UTC), generated_by="llm",
                sources=[SourceRow(id=1, kind="upload", reference="nota.txt", reliability="B",
                                   credibility="2", sha256="ab" * 32,
                                   retrieved_at=datetime(2026, 3, 15, tzinfo=UTC))])
    base.update(kw)
    return ReportMeta(**base)


def test_pdf_valido_con_metadatos():
    pdf = render_pdf(MD, _meta())
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")
    assert len(pdf) > 2000
    assert b"TLP:RED" in pdf  # en los metadatos del documento


def test_pdf_sin_fuentes_y_todas_las_marcas_tlp():
    for tlp in ("clear", "green", "amber", "amber+strict", "red", "desconocido"):
        assert render_pdf(MD, _meta(tlp=tlp, sources=[])).startswith(b"%PDF-")


def test_pdf_markdown_vacio_igual_tiene_portada():
    assert render_pdf("", _meta()).startswith(b"%PDF-")


def test_huella_del_texto_es_estable():
    assert markdown_digest(MD) == markdown_digest(MD) and len(markdown_digest(MD)) == 64
