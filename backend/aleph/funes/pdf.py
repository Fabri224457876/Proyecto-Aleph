"""Informe de inteligencia en PDF a partir del borrador de FUNES.

No toca la base: recibe el Markdown del borrador (el subconjunto que produce `report.py`:
títulos, viñetas, citas, negrita e itálica) y los metadatos del caso, y devuelve los bytes del PDF.

Cada página lleva la marca TLP arriba y abajo (colores de TLP 2.0), el número de página y la
huella SHA-256 del texto del informe, para que una copia impresa se pueda cotejar con la auditoría.
"""

import hashlib
import io
import re
from datetime import datetime

from pydantic import BaseModel, Field
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# TLP 2.0 (FIRST): texto en el color de la marca sobre fondo negro.
TLP_COLORS = {
    "clear": "#FFFFFF",
    "green": "#33FF00",
    "amber": "#FFC000",
    "amber+strict": "#FFC000",
    "red": "#FF2B2B",
}
TLP_MEANING = {
    "clear": "Sin restricciones de difusión.",
    "green": "Difusión limitada a la comunidad del destinatario; no publicar.",
    "amber": "Difusión limitada a la organización del destinatario y a quienes necesiten saberlo.",
    "amber+strict": "Difusión limitada a la organización del destinatario; no a sus clientes.",
    "red": "Solo para los destinatarios presentes; no redistribuir.",
}
RELIABILITY = {"A": "completamente fiable", "B": "usualmente fiable", "C": "bastante fiable",
               "D": "no usualmente fiable", "E": "no fiable", "F": "no se puede juzgar"}
CREDIBILITY = {"1": "confirmado", "2": "probablemente cierto", "3": "posiblemente cierto",
               "4": "dudoso", "5": "improbable", "6": "no se puede juzgar"}

INK = colors.HexColor("#16202b")
MUTED = colors.HexColor("#5a6877")
LINE = colors.HexColor("#cfd6dd")
PAPER = colors.HexColor("#f3f5f7")
WARN = colors.HexColor("#9a6200")

# Las fuentes base del PDF solo cubren Latin-1 y algunos signos: el resto se traduce.
_GLYPHS = str.maketrans({"→": "->", "←": "<-", "≈": "~", "≥": ">=", "≤": "<=", "✓": "OK",
                         "×": "x", "−": "-", "‑": "-", " ": " "})


class SourceRow(BaseModel):
    id: int
    kind: str = ""
    reference: str = ""
    reliability: str = "F"
    credibility: str = "6"
    sha256: str = ""
    retrieved_at: datetime | None = None


class ReportMeta(BaseModel):
    case_id: int
    case_name: str
    title: str = ""
    tlp: str = "amber"
    purpose: str = ""
    legal_basis: str = ""
    author: str = ""
    generated_at: datetime
    generated_by: str = "plantilla"  # llm | plantilla
    sources: list[SourceRow] = Field(default_factory=list)


def markdown_digest(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=10, leading=14, textColor=INK,
                          alignment=TA_LEFT)
    return {
        "base": base,
        "small": ParagraphStyle("small", parent=base, fontSize=8, leading=11, textColor=MUTED),
        "title": ParagraphStyle("title", parent=base, fontName="Helvetica-Bold", fontSize=18,
                                leading=23, spaceAfter=4),
        "kicker": ParagraphStyle("kicker", parent=base, fontName="Helvetica-Bold", fontSize=8,
                                 leading=11, textColor=MUTED, spaceAfter=2),
        "h1": ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=14, leading=18,
                             spaceBefore=10, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=12, leading=16,
                             spaceBefore=12, spaceAfter=5),
        "quote": ParagraphStyle("quote", parent=base, fontSize=9, leading=13, textColor=MUTED,
                                leftIndent=8, borderPadding=(4, 6, 4, 6), backColor=PAPER,
                                spaceBefore=4, spaceAfter=8),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8, leading=10.5),
        "cellb": ParagraphStyle("cellb", parent=base, fontName="Helvetica-Bold", fontSize=8,
                                leading=10.5),
    }


def _escape(text: str) -> str:
    return text.translate(_GLYPHS).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])_(.+?)_(?![\w*])")
_MARK = re.compile(r"\[(ID INEXISTENTE: [^\]]+|SIN RESPALDO EN LOS DATOS|REVISAR: [^\]]+)\]")
_REF = re.compile(r"\[([A-Z]\d+)\]")


def _inline(text: str) -> str:
    """Markdown en línea → mini HTML de ReportLab. Las marcas de validación van en color de aviso."""
    out = _escape(text)
    out = _BOLD.sub(r"<b>\1</b>", out)
    out = _ITALIC.sub(r"<i>\1</i>", out)
    out = _MARK.sub(r'<font color="#9a6200"><b>[\1]</b></font>', out)
    out = _REF.sub(r'<font color="#5a6877" size="8">[\1]</font>', out)
    return out


def _markdown_flowables(markdown: str, st: dict[str, ParagraphStyle]) -> list:
    flow: list = []
    bullets: list[str] = []

    def flush() -> None:
        if bullets:
            flow.append(ListFlowable(
                [ListItem(Paragraph(_inline(b), st["base"]), leftIndent=12, value="•")
                 for b in bullets],
                bulletType="bullet", start="•", leftIndent=12, bulletFontSize=9, spaceAfter=4))
            bullets.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("- "):
            bullets.append(line[2:])
            continue
        flush()
        if not line.strip():
            continue
        if line.startswith("# ") or line.startswith("**Propósito del caso:**"):
            continue  # el título y el propósito van en la portada
        if line.startswith("## "):
            flow.append(Paragraph(_inline(line[3:]), st["h1"]))
        elif line.startswith("### "):
            flow.append(Paragraph(_inline(line[4:]), st["h2"]))
        elif line.startswith("> "):
            flow.append(Paragraph(_inline(line[2:]), st["quote"]))
        else:
            flow.append(Paragraph(_inline(line), st["base"]))
    flush()
    return flow


def _cover(meta: ReportMeta, digest: str, st: dict[str, ParagraphStyle]) -> list:
    tlp = meta.tlp.lower()
    when = meta.generated_at.strftime("%d/%m/%Y %H:%M UTC")
    origin = ("Redactado por la IA local (FUNES) y validado contra los datos del caso"
              if meta.generated_by == "llm" else "Armado con plantilla determinística, sin modelo de lenguaje")
    rows = [
        ("Caso", f"#{meta.case_id} · {meta.case_name}"),
        ("Clasificación", f"TLP:{tlp.upper()} — {TLP_MEANING.get(tlp, '')}"),
        ("Propósito", meta.purpose or "No declarado."),
        ("Base legal", meta.legal_basis or "No declarada."),
        ("Elaborado por", meta.author or "—"),
        ("Fecha", when),
        ("Origen del texto", origin),
        ("Huella SHA-256", digest),
    ]
    table = Table(
        [[Paragraph(k, st["cellb"]), Paragraph(_escape(v), st["cell"])] for k, v in rows],
        colWidths=[32 * mm, None])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("BACKGROUND", (0, 0), (0, -1), PAPER),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [
        Paragraph("ALEPH · P.R.O.A. — INFORME DE INTELIGENCIA", st["kicker"]),
        Paragraph(_escape(meta.title or meta.case_name), st["title"]),
        Spacer(1, 4),
        table,
        Spacer(1, 8),
    ]


def _sources_table(meta: ReportMeta, st: dict[str, ParagraphStyle]) -> list:
    if not meta.sources:
        return []
    head = [Paragraph(h, st["cellb"]) for h in ("#", "Fuente", "Valoración (Admiralty)", "Obtenida", "SHA-256 del crudo")]
    body = []
    for s in meta.sources:
        rating = (f"<b>{s.reliability}{s.credibility}</b> — fuente {RELIABILITY.get(s.reliability, '?')}, "
                  f"dato {CREDIBILITY.get(s.credibility, '?')}")
        body.append([
            Paragraph(str(s.id), st["cell"]),
            Paragraph(_escape((s.reference or s.kind)[:160]), st["cell"]),
            Paragraph(rating, st["cell"]),
            Paragraph(s.retrieved_at.strftime("%d/%m/%Y") if s.retrieved_at else "—", st["cell"]),
            Paragraph(f'<font face="Courier" size="6.5">{s.sha256[:16] or "—"}</font>', st["cell"]),
        ])
    table = Table([head, *body], colWidths=[9 * mm, None, 50 * mm, 21 * mm, 27 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), PAPER),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return [
        Paragraph("Anexo. Fuentes valoradas", st["h1"]),
        Paragraph("Fiabilidad de la fuente (A–F) y credibilidad del dato (1–6) según el Admiralty Code. "
                  "El hash permite verificar que el crudo guardado no cambió.", st["small"]),
        Spacer(1, 4),
        table,
    ]


def render_pdf(markdown: str, meta: ReportMeta) -> bytes:
    st = _styles()
    digest = markdown_digest(markdown)
    tlp = meta.tlp.lower()
    tlp_color = colors.HexColor(TLP_COLORS.get(tlp, "#FFC000"))
    label = f"TLP:{tlp.upper()}"

    def page(canvas, doc) -> None:
        width, height = A4
        canvas.saveState()
        for y in (height - 11 * mm, 6 * mm):
            canvas.setFillColor(colors.black)
            canvas.rect(width - 46 * mm, y, 34 * mm, 6 * mm, stroke=0, fill=1)
            canvas.setFillColor(tlp_color)
            canvas.setFont("Helvetica-Bold", 9)
            canvas.drawCentredString(width - 29 * mm, y + 1.8 * mm, label)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(14 * mm, height - 9 * mm, f"Aleph · Caso #{meta.case_id}")
        canvas.drawString(14 * mm, 8 * mm,
                          f"Hipótesis para revisión humana · SHA-256 {digest[:16]}… · página {doc.page}")
        canvas.setStrokeColor(LINE)
        canvas.line(14 * mm, height - 13 * mm, width - 14 * mm, height - 13 * mm)
        canvas.line(14 * mm, 13.5 * mm, width - 14 * mm, 13.5 * mm)
        canvas.restoreState()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=18 * mm,
        bottomMargin=18 * mm, title=meta.title or meta.case_name, author=meta.author,
        subject=f"Informe de inteligencia · {label}", creator="Aleph · P.R.O.A.")
    story = _cover(meta, digest, st)
    story += _markdown_flowables(markdown, st)
    story += _sources_table(meta, st)
    story.append(Spacer(1, 10))
    story.append(KeepTogether([Paragraph(
        "Este documento es un borrador de análisis. Las hipótesis indican un nivel de confianza y "
        "remiten a los datos del caso; ninguna constituye identificación ni imputación de personas. "
        "Su generación quedó registrada en la auditoría encadenada de Aleph.", st["small"])]))
    doc.build(story, onFirstPage=page, onLaterPages=page)
    return buffer.getvalue()
