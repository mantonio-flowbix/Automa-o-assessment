"""Regression tests for flowbix_assess.pptx_report.

Guards the text-overflow bug found when real-world long titles/
descriptions were used: table cells have a fixed pixel height in PPTX (no
reflow like HTML), so unbounded text silently bled into the next row.
"""
from __future__ import annotations

from datetime import datetime

from pptx import Presentation

from flowbix_assess import pptx_report as pr
from flowbix_assess.models import Finding, Severity

LONG_TITLE = "Itens de baixa prioridade com intervalo curto e sem trigger em 999 template(s) muito compridos de verdade"
LONG_DESCRIPTION = (
    "142 item(ns) com coleta abaixo de 60s e sem nenhuma trigger associada — indica menor "
    "criticidade. Templates afetados: 3001_Template_PDV_ER (12), 3300_Grupo_PDV_LABORATORIOS (8), "
    "7300_Template_Cisco_Meraki_Device_MX_ER_GBTech (4), e mais 20 template(s) que ainda nem "
    "foram listados aqui para deixar esse texto ainda mais comprido."
)
LONG_RECOMMENDATION = (
    "Usar abordagem de item master com itens dependentes para reduzir a quantidade de conexões "
    "diretas ao banco, considerando o volume de proxies e scripts conectando simultaneamente ao "
    "servidor de banco de dados principal em todos os ambientes avaliados neste assessment."
)


def _text_of(shape) -> str:
    return shape.text_frame.text if shape.has_text_frame else ""


def test_truncate_adds_ellipsis_only_when_needed():
    assert pr._truncate("short text", 50) == "short text"
    truncated = pr._truncate("a" * 300, 50)
    assert len(truncated) == 50
    assert truncated.endswith("…")


def test_long_title_and_description_never_exceed_the_calibrated_budget():
    finding = Finding(
        section="Análise de Templates",
        title=LONG_TITLE,
        severity=Severity.WARNING,
        description=LONG_DESCRIPTION,
        recommendation=LONG_RECOMMENDATION,
        deadline_days=30,
    )

    prs = Presentation()
    prs.slide_width = pr.SLIDE_W
    prs.slide_height = pr.SLIDE_H
    pr._section_slides(prs, "Análise de Templates", [finding], datetime.now())

    slide = list(prs.slides)[0]
    texts = [_text_of(s) for s in slide.shapes]

    title_text = next(t for t in texts if t.startswith("Itens de baixa prioridade"))
    desc_text = next(t for t in texts if t.startswith("142 item(ns)"))
    rec_text = next(t for t in texts if "Usar abordagem" in t)

    assert len(title_text) <= pr.TITLE_MAX_CHARS
    assert len(desc_text) <= pr.DESCRIPTION_MAX_CHARS
    assert len(rec_text) <= pr.CALLOUT_MAX_CHARS
    # the raw strings are longer than the budgets above — truncation must
    # have actually kicked in, not just happened to fit.
    assert len(LONG_TITLE) > pr.TITLE_MAX_CHARS
    assert len(LONG_DESCRIPTION) > pr.DESCRIPTION_MAX_CHARS
    assert len(LONG_RECOMMENDATION) > pr.CALLOUT_MAX_CHARS


def test_table_rows_fit_within_the_space_reserved_before_the_callout_box():
    """5 rows at ROW_H starting at TABLE_TOP+HEADER_H must land exactly at
    (not past) the callout box's y — this is what keeps rows from ever
    visually colliding with the recommendation box below them."""
    rows_bottom = int(pr.TABLE_TOP) + int(pr.HEADER_H) + pr.ROWS_PER_PAGE * int(pr.ROW_H)
    callout_y = 4160520
    assert rows_bottom <= callout_y


def _fake_screenshot(tmp_path, key="proxies", title="Proxies: status, versão e PSK", section="Arquitetura do Ambiente"):
    from PIL import Image

    image_path = tmp_path / f"{key}.png"
    Image.new("RGB", (1600, 900), (240, 240, 240)).save(image_path)
    return {
        "key": key, "section": section, "title": title,
        "screen": "zabbix.php?action=proxy.list", "path": str(image_path),
        "captured_at": "2026-10-02T11:20:00", "status": "ok",
    }


def _demo_config():
    from flowbix_assess.config import Config

    return Config({"client": {"name": "Cliente Demo", "target_zabbix_version": "7.0"}})


def _render(tmp_path, screenshots):
    finding = Finding(section="Banco de Dados", title="T", severity=Severity.INFO, description="d", recommendation="r")
    out = tmp_path / "deck.pptx"
    pr.render(_demo_config(), [finding], str(out), screenshots=screenshots)
    return list(Presentation(str(out)).slides)


def _slide_texts(slide):
    return [s.text_frame.text for s in slide.shapes if s.has_text_frame]


def test_evidence_slides_are_added_before_closing_and_listed_in_the_index(tmp_path):
    without = _render(tmp_path, None)
    shots = [_fake_screenshot(tmp_path), _fake_screenshot(tmp_path, "queue", "Fila de itens (queue)", "Processamento das Máquinas")]
    with_shots = _render(tmp_path, shots)

    assert len(with_shots) == len(without) + 2
    assert not any("Evidências do ambiente" in t for t in _slide_texts(without[2]))
    assert any("Evidências do ambiente" in t for t in _slide_texts(with_shots[2]))

    first_evidence = with_shots[-3]
    texts = _slide_texts(first_evidence)
    assert "EVIDÊNCIA · ARQUITETURA DO AMBIENTE" in texts
    assert "Proxies: status, versão e PSK" in texts
    assert any(t.startswith("Capturado em 02/10/2026 11:20 · zabbix.php?action=proxy.list") for t in texts)
    # last slide is still the closing one
    assert any("Fale conosco" in t for t in _slide_texts(with_shots[-1]))


def test_evidence_picture_keeps_aspect_ratio_and_stays_inside_the_content_area(tmp_path):
    slides = _render(tmp_path, [_fake_screenshot(tmp_path)])
    evidence = slides[-2]
    picture = next(s for s in evidence.shapes if s.shape_type == 13)  # MSO_SHAPE_TYPE.PICTURE

    assert abs(picture.width / picture.height - 16 / 9) < 0.01
    assert picture.width <= int(pr.CONTENT_W)
    assert picture.top + picture.height < 4846320  # above the footer
    assert abs((picture.left + picture.width / 2) - int(pr.SLIDE_W) / 2) < 2  # centered
