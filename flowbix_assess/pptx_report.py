"""Generates the final PPTX presentation from the same Finding list used by
the HTML report.

Visual design follows the Flowbix reference deck
(`Assessment_Zabbix_Usiminas.pptx`) exactly: same slide size, palette,
fonts and component patterns (cover, stat cards, tables, priority badges,
recommendation callouts, closing slide) — extracted from that file's raw
XML (colors) and shape layout (positions). Content is generated from
whatever sections/findings the rule engine actually produced, since our
data doesn't match that deck's hand-authored Grafana-specific narrative.

Findings are rendered as paginated tables (a handful of rows per slide),
not one slide per finding — a real run can produce hundreds of findings,
and the reference deck itself never puts more than ~8 items on one slide.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Pt

from .models import SEVERITY_ORDER, Severity

# ---- design tokens, extracted from the Flowbix reference deck's XML ----
RED = RGBColor(0xCC, 0x00, 0x00)
DARK = RGBColor(0x1A, 0x1A, 0x1A)
MUTED = RGBColor(0x55, 0x55, 0x55)
BORDER = RGBColor(0xDD, 0xDD, 0xDD)
CARD_BG = RGBColor(0xF5, 0xF5, 0xF5)
STRIPE_BG = RGBColor(0xF9, 0xF9, 0xF9)
OPEN_BG = RGBColor(0xFD, 0xE8, 0xE8)  # cover / contact / index background
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
GREEN = RGBColor(0x2D, 0x7A, 0x4F)  # recommendation callouts
GREEN_BG = RGBColor(0xF0, 0xFF, 0xF4)
AMBER = RGBColor(0xE6, 0xA8, 0x17)  # P2 badge, matches the reference exactly
CLOSE_BG = RGBColor(0x11, 0x11, 0x11)
LIGHT_GRAY_TEXT = RGBColor(0xCF, 0xCF, 0xCF)

HEADING_FONT = "Calibri Light"
BODY_FONT = "Calibri"

# The reference deck only needed P1 (critical) / P2 (warning) priority
# badges. Our model has two extra severities it never covered — P3/P4 and
# their colors are this project's own extension, not from the reference.
PRIORITY = {
    Severity.CRITICAL: ("P1", RED),
    Severity.WARNING: ("P2", AMBER),
    Severity.INFO: ("P3", MUTED),
    Severity.MANUAL_REVIEW: ("P4", GREEN),
}

SLIDE_W = Emu(9144000)
SLIDE_H = Emu(5143500)
LEFT = Emu(457200)
CONTENT_W = Emu(8229600)
BLANK_LAYOUT = 6

LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo.jpg"
LOGO_ASPECT = 1697 / 447

ROWS_PER_PAGE = 6
ROW_H = Emu(420624)
TABLE_TOP = Emu(1097280)
HEADER_H = Emu(320040)
DESCRIPTION_MAX_CHARS = 170


def _slide(prs, bg):
    slide = prs.slides.add_slide(prs.slide_layouts[BLANK_LAYOUT])
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, SLIDE_W, SLIDE_H)
    shape.fill.solid()
    shape.fill.fore_color.rgb = bg
    shape.line.fill.background()
    shape.shadow.inherit = False
    return slide


def _textbox(slide, x, y, w, h):
    tf = slide.shapes.add_textbox(x, y, w, h).text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    return tf


def _set_text(paragraph, text, size, color, bold=False, font=BODY_FONT, align=None):
    paragraph.text = text
    paragraph.font.size = Pt(size)
    paragraph.font.color.rgb = color
    paragraph.font.bold = bold
    paragraph.font.name = font
    if align:
        paragraph.alignment = align
    return paragraph


def _rect(slide, x, y, w, h, fill=None, line_color=None, line_width=None, rounded=False):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(shape_type, x, y, w, h)
    if fill is not None:
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill
    else:
        shape.fill.background()
    if line_color is not None:
        shape.line.color.rgb = line_color
        shape.line.width = line_width or Pt(1)
    else:
        shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _add_logo(slide, x, y, height):
    width = Emu(int(height * LOGO_ASPECT))
    slide.shapes.add_picture(str(LOGO_PATH), x, y, width=width, height=height)
    return width


def _eyebrow_and_title(slide, eyebrow, title):
    tf = _textbox(slide, LEFT, Emu(164592), CONTENT_W, Emu(201168))
    _set_text(tf.paragraphs[0], eyebrow, 10, RED, bold=True)
    tf2 = _textbox(slide, LEFT, Emu(384048), CONTENT_W, Emu(640080))
    _set_text(tf2.paragraphs[0], title, 26, DARK, bold=True, font=HEADING_FONT)


def _label_value(slide, x, y, label, value, w=Emu(1828800)):
    tf = _textbox(slide, x, y, w, Emu(201168))
    _set_text(tf.paragraphs[0], label, 8, MUTED)
    tf2 = _textbox(slide, x, Emu(y + 201168), w, Emu(256032))
    _set_text(tf2.paragraphs[0], value, 12, DARK, bold=True)


def _footer(slide, text="Confidencial · Relatório Técnico"):
    tf = _textbox(slide, Emu(3200400), Emu(4846320), Emu(2743200), Emu(182880))
    _set_text(tf.paragraphs[0], text, 8, MUTED, align=PP_ALIGN.CENTER)


def _callout_box(slide, label, text, y, height=Emu(822960)):
    """The reference deck's "Recomendação"/"Conclusão" pattern: a pale
    green rounded box with a bold green label and dark body text."""
    _rect(slide, LEFT, y, CONTENT_W, height, fill=GREEN_BG, line_color=GREEN, line_width=Pt(1.5), rounded=True)
    label_w = Emu(1371600)
    tf = _textbox(slide, Emu(int(LEFT) + 137160), y, label_w, height)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    _set_text(tf.paragraphs[0], label, 10, GREEN, bold=True)
    tf2 = _textbox(slide, Emu(int(LEFT) + 137160 + int(label_w)), y, Emu(6309360), height)
    tf2.vertical_anchor = MSO_ANCHOR.MIDDLE
    _set_text(tf2.paragraphs[0], text, 11, DARK)


# ---------------------------------------------------------------- opening

def _cover_slide(prs, config, total_findings):
    slide = _slide(prs, OPEN_BG)

    tf = _textbox(slide, LEFT, Emu(731520), CONTENT_W, Emu(274320))
    _set_text(tf.paragraphs[0], "FLOWBIX · ASSESSMENT TÉCNICO", 10, RED, bold=True)

    tf2 = _textbox(slide, LEFT, Emu(1097280), CONTENT_W, Emu(1097280))
    _set_text(tf2.paragraphs[0], "Assessment de Infraestrutura e Zabbix", 40, DARK, bold=True, font=HEADING_FONT)

    tf3 = _textbox(slide, LEFT, Emu(2194560), CONTENT_W, Emu(457200))
    _set_text(tf3.paragraphs[0], config.client_name, 20, RED, bold=True)

    hosting = (config.client.get("hosting_type") or "").strip()
    hosting_label = {
        "cloud": "ambiente em nuvem",
        "on-premise": "ambiente on-premise",
        "hybrid": "ambiente híbrido",
    }.get(hosting, "ambiente do cliente")
    description = (
        f"Análise técnica de infraestrutura, banco de dados e configuração Zabbix "
        f"({hosting_label}), com achados classificados por prioridade e recomendações práticas."
    )
    tf4 = _textbox(slide, LEFT, Emu(2788920), Emu(6858000), Emu(822960))
    _set_text(tf4.paragraphs[0], description, 12, MUTED)

    _label_value(slide, LEFT, Emu(3840480), "VERSÃO-ALVO ZABBIX", config.target_zabbix_version)
    _label_value(slide, Emu(2560320), Emu(3840480), "TOTAL DE ACHADOS", str(total_findings))

    logo_w = _add_logo(slide, Emu(5943600), Emu(4515000), Emu(300000))
    tf9 = _textbox(slide, Emu(5943600) + logo_w + Emu(137160), Emu(4480560), Emu(1463040), Emu(365760))
    tf9.vertical_anchor = MSO_ANCHOR.MIDDLE
    _set_text(tf9.paragraphs[0], config.client_name.upper(), 14, DARK, bold=True)
    return slide


def _contact_summary_slide(prs, findings):
    slide = _slide(prs, OPEN_BG)

    tf = _textbox(slide, LEFT, Emu(365760), Emu(1828800), Emu(320040))
    _set_text(tf.paragraphs[0], "FLOWBIX", 16, RED, bold=True, font=HEADING_FONT)

    tf2 = _textbox(slide, LEFT, Emu(822960), Emu(3200400), Emu(274320))
    _set_text(tf2.paragraphs[0], "Flowbix — Soluções, Inovações e Automações", 11, DARK)

    tf3 = _textbox(slide, LEFT, Emu(1188720), Emu(3200400), Emu(548640))
    _set_text(tf3.paragraphs[0], "Telefone  +55 16 3256-1412", 11, MUTED)
    _set_text(tf3.add_paragraph(), "Website  https://flowbix.com", 11, MUTED)

    _rect(slide, Emu(3931920), Emu(274320), Emu(36576), Emu(4572000), fill=BORDER)

    tf4 = _textbox(slide, Emu(4206240), Emu(365760), Emu(4572000), Emu(228600))
    _set_text(tf4.paragraphs[0], "RESUMO", 11, RED, bold=True)

    counts = Counter(f.severity for f in findings)
    summary = (
        f"Assessment técnico do ambiente Zabbix e infraestrutura, com {len(findings)} achados "
        f"identificados — {counts.get(Severity.CRITICAL, 0)} críticos, "
        f"{counts.get(Severity.WARNING, 0)} de atenção, {counts.get(Severity.INFO, 0)} informativos "
        f"e {counts.get(Severity.MANUAL_REVIEW, 0)} marcados para revisão manual. A coleta foi "
        "conduzida em blocos read-only — API do Zabbix, consultas de leitura no MySQL e um script "
        "local sem dependências no host — sem aplicar nenhuma mudança no ambiente do cliente."
    )
    tf5 = _textbox(slide, Emu(4206240), Emu(685800), Emu(4754880), Emu(3200400))
    _set_text(tf5.paragraphs[0], summary, 12, DARK)
    return slide


def _index_slide(prs, sections):
    slide = _slide(prs, OPEN_BG)

    tf = _textbox(slide, LEFT, Emu(228600), CONTENT_W, Emu(228600))
    _set_text(tf.paragraphs[0], "ÍNDICE", 10, RED, bold=True)
    tf2 = _textbox(slide, LEFT, Emu(457200), CONTENT_W, Emu(502920))
    _set_text(tf2.paragraphs[0], "Tópicos do relatório", 22, DARK, bold=True, font=HEADING_FONT)

    items = ["Resumo executivo"] + list(sections)
    col_x = [LEFT, Emu(4754880)]
    col_w = [Emu(3474720), Emu(3657600)]
    row_h = Emu(457200)
    start_y = Emu(1234440)
    half = (len(items) + 1) // 2

    for col_idx, col_items in enumerate([items[:half], items[half:]]):
        x = col_x[col_idx]
        for i, title in enumerate(col_items):
            y = start_y + i * row_h
            num_tf = _textbox(slide, x, y, Emu(411480), Emu(320040))
            _set_text(num_tf.paragraphs[0], f"{(col_idx * half) + i + 1:02d}", 16, RED, bold=True)
            title_tf = _textbox(slide, x + Emu(457200), Emu(y + 18288), col_w[col_idx], Emu(320040))
            _set_text(title_tf.paragraphs[0], title, 14, DARK)

    _footer(slide)
    return slide


# ---------------------------------------------------------------- content

def _summary_slide(prs, findings):
    counts = Counter(f.severity for f in findings)
    slide = _slide(prs, WHITE)
    _eyebrow_and_title(slide, "RESUMO EXECUTIVO", "Panorama geral do assessment")

    cards = [
        ("Total de achados", len(findings), DARK),
        ("Críticos", counts.get(Severity.CRITICAL, 0), RED),
        ("Atenção", counts.get(Severity.WARNING, 0), AMBER),
        ("Informativos", counts.get(Severity.INFO, 0), MUTED),
        ("Revisão Manual", counts.get(Severity.MANUAL_REVIEW, 0), GREEN),
    ]
    gap = Emu(63500)
    card_w = Emu((int(CONTENT_W) - 4 * int(gap)) // 5)
    card_y = Emu(1097280)
    card_h = Emu(1005840)
    x = LEFT
    for label, value, color in cards:
        _rect(slide, x, card_y, card_w, card_h, fill=CARD_BG)
        num_tf = _textbox(slide, x, Emu(int(card_y) + 137160), card_w, Emu(457200))
        _set_text(num_tf.paragraphs[0], str(value), 28, color, bold=True, align=PP_ALIGN.CENTER)
        label_tf = _textbox(slide, x, Emu(int(card_y) + 137160 + 457200), card_w, Emu(228600))
        _set_text(label_tf.paragraphs[0], label, 11, DARK, bold=True, align=PP_ALIGN.CENTER)
        x = Emu(int(x) + int(card_w) + int(gap))

    ordered = sorted(findings, key=lambda f: SEVERITY_ORDER[f.severity])
    conclusion = ordered[0].description if ordered else "Nenhum achado identificado nesta execução."
    _callout_box(slide, "CONCLUSÃO", conclusion, y=Emu(3246120))
    _footer(slide)
    return slide


def _truncate(text, max_chars=DESCRIPTION_MAX_CHARS):
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _table_header(slide, y, headers, col_x, col_w):
    _rect(slide, LEFT, y, CONTENT_W, HEADER_H, fill=CARD_BG)
    for text, x, w in zip(headers, col_x, col_w):
        tf = _textbox(slide, Emu(int(x) + 45720), y, Emu(int(w) - 45720), HEADER_H)
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        _set_text(tf.paragraphs[0], text, 11, DARK, bold=True)


def _table_row(slide, y, height, finding, col_x, col_w, striped):
    if striped:
        _rect(slide, LEFT, y, CONTENT_W, height, fill=STRIPE_BG)

    title_tf = _textbox(slide, Emu(int(col_x[0]) + 45720), y, Emu(int(col_w[0]) - 91440), height)
    title_tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    _set_text(title_tf.paragraphs[0], finding.title, 10.5, DARK, bold=True)

    desc_tf = _textbox(slide, Emu(int(col_x[1]) + 45720), y, Emu(int(col_w[1]) - 91440), height)
    desc_tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    _set_text(desc_tf.paragraphs[0], _truncate(finding.description), 9.5, MUTED)

    label, color = PRIORITY[finding.severity]
    badge_w, badge_h = Emu(438912), Emu(228600)
    bx = Emu(int(col_x[2]) + (int(col_w[2]) - int(badge_w)) // 2)
    by = Emu(int(y) + (int(height) - int(badge_h)) // 2)
    _rect(slide, bx, by, badge_w, badge_h, fill=color, rounded=True)
    badge_tf = _textbox(slide, bx, by, badge_w, badge_h)
    badge_tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    _set_text(badge_tf.paragraphs[0], label, 8, WHITE, bold=True, align=PP_ALIGN.CENTER)


def _section_slides(prs, section, section_findings):
    ordered = sorted(section_findings, key=lambda f: SEVERITY_ORDER[f.severity])
    col_w = [Emu(4114800), Emu(3200400), Emu(914400)]
    col_x = [LEFT, Emu(int(LEFT) + int(col_w[0])), Emu(int(LEFT) + int(col_w[0]) + int(col_w[1]))]
    headers = ["Achado", "Descrição", "Prio."]

    pages = [ordered[i : i + ROWS_PER_PAGE] for i in range(0, len(ordered), ROWS_PER_PAGE)] or [[]]
    for page_idx, page_findings in enumerate(pages):
        slide = _slide(prs, WHITE)
        title = section if len(pages) == 1 else f"{section} ({page_idx + 1}/{len(pages)})"
        _eyebrow_and_title(slide, section.upper(), title)
        _table_header(slide, TABLE_TOP, headers, col_x, col_w)

        y = Emu(int(TABLE_TOP) + int(HEADER_H))
        for i, finding in enumerate(page_findings):
            _table_row(slide, y, ROW_H, finding, col_x, col_w, striped=(i % 2 == 1))
            y = Emu(int(y) + int(ROW_H))

        if page_idx == len(pages) - 1 and ordered:
            _callout_box(slide, "RECOMENDAÇÃO", ordered[0].recommendation, y=Emu(4160520))
        _footer(slide)


def _closing_slide(prs):
    slide = _slide(prs, CLOSE_BG)
    logo_h = Emu(600000)
    logo_w = Emu(int(logo_h * LOGO_ASPECT))
    logo_x = Emu((int(SLIDE_W) - int(logo_w)) // 2)
    _add_logo(slide, logo_x, Emu(1600200), logo_h)

    tf = _textbox(slide, LEFT, Emu(2743200), CONTENT_W, Emu(457200))
    _set_text(tf.paragraphs[0], "Fale conosco", 24, WHITE, bold=True, align=PP_ALIGN.CENTER)
    tf2 = _textbox(slide, LEFT, Emu(3291840), CONTENT_W, Emu(320040))
    _set_text(tf2.paragraphs[0], "+55 16 3256-1412  ·  flowbix.com", 13, LIGHT_GRAY_TEXT, align=PP_ALIGN.CENTER)
    return slide


def render(config, findings, output_path: str) -> str:
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    by_section = defaultdict(list)
    for finding in findings:
        by_section[finding.section].append(finding)

    _cover_slide(prs, config, len(findings))
    _contact_summary_slide(prs, findings)
    _index_slide(prs, list(by_section.keys()))
    _summary_slide(prs, findings)

    for section, section_findings in by_section.items():
        _section_slides(prs, section, section_findings)

    _closing_slide(prs)

    prs.save(output_path)
    return output_path
