"""Tests del generador del dashboard."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_site  # noqa: E402

RECORD_10K = {
    "id": "a", "form_type": "10-K", "title": "10-K", "filed": "2025-10-31",
    "url": "https://www.sec.gov/aapl.htm",
    "summary": "RESUM EXECUTIU – Apple Inc. (AAPL), 10-K any fiscal 2025\n\n"
               "1. EVOLUCIÓ DEL NEGOCI\n- Vendes netes: $391,035 milions, un 6% més que l'any anterior.\n"
               "- Marge **46.2%**\n\n"
               "2. Riscos principals\n- Risc <script>alert(1)</script>\n\n"
               "4. ALERTES FINANCERES (RED FLAGS)\n- Deute de $91.3 billion a vigilar.",
    "unverified": [],
}
RECORD_10Q = {
    "id": "b", "form_type": "10-Q", "title": "10-Q", "filed": "2026-08-01",
    "url": "https://www.sec.gov/aapl-q.htm", "summary": "Resum trimestral.",
    "unverified": [{"token": "93,736", "context": "benefici de $93,736 milions"}],
}


def build_with(tmp_path, monkeypatch, portfolio, data):
    portfolio_file = tmp_path / "portfolio.json"
    data_file = tmp_path / "filings.json"
    portfolio_file.write_text(json.dumps(portfolio))
    data_file.write_text(json.dumps(data))
    monkeypatch.setattr(build_site, "PORTFOLIO_FILE", portfolio_file)
    monkeypatch.setattr(build_site, "DATA_FILE", data_file)
    out = tmp_path / "site"
    build_site.build(out, now=datetime(2026, 10, 12, 22, 30, tzinfo=timezone.utc))
    return out


def test_one_page_per_portfolio_company(tmp_path, monkeypatch):
    out = build_with(tmp_path, monkeypatch, ["AAPL", "msft", "BRK.B"], {})
    assert sorted(p.name for p in out.iterdir()) == ["aapl.html", "assets", "brk.b.html", "index.html", "msft.html"]
    assert (out / "assets" / "style.css").exists() and (out / "assets" / "app.js").exists()
    index = (out / "index.html").read_text()
    for ticker in ("AAPL", "MSFT", "BRK.B"):
        assert f">{ticker}</a>" in index  # Pestanya


def test_removed_company_page_disappears(tmp_path, monkeypatch):
    out = build_with(tmp_path, monkeypatch, ["AAPL", "MSFT"], {})
    assert (out / "msft.html").exists()
    out = build_with(tmp_path, monkeypatch, ["AAPL"], {})
    assert not (out / "msft.html").exists()
    assert "MSFT" not in (out / "aapl.html").read_text()


def test_company_page_shows_both_filings(tmp_path, monkeypatch):
    out = build_with(tmp_path, monkeypatch, ["AAPL"], {"AAPL": {"10-K": RECORD_10K, "10-Q": RECORD_10Q}})
    page = (out / "aapl.html").read_text()
    assert '<a href="aapl.html" aria-current="page">AAPL</a>' in page
    assert '<p class="hero__name">Apple Inc.</p>' in page
    assert 'id="f-10-K"' in page and 'id="f-10-Q" data-default' in page  # El més recent, per defecte.
    assert "Evolució del negoci</h3>" in page  # Títol en majúscules passat a format frase.
    assert "<strong>46.2%</strong>" in page or '<strong><span class="fig">46.2%</span></strong>' in page
    assert "<script>alert" not in page  # El text del resum s'escapa.
    assert "chapter--flags" in page
    assert "xifres contrastades amb el document" in page
    assert "Nota d'auditoria" in page and '<mark class="fig fig--flag"' not in page.split("f-10-Q")[0]
    assert "https://www.sec.gov/aapl.htm" in page


def test_kpis_are_copied_from_the_summary():
    _, chapters = build_site.parse_summary(RECORD_10K["summary"])
    kpi = build_site.extract_kpis(chapters)[0]
    assert (kpi.label, kpi.value, kpi.delta, kpi.direction) == ("Vendes netes", "$391,035 M", "+6%", "up")


def test_unverified_figures_are_marked():
    rendered = build_site.inline("Benefici de $93,736 milions i marge del 40%", ["93,736"])
    assert '<mark class="fig fig--flag"' in rendered and "$93,736 milions</mark>" in rendered
    assert '<span class="fig">40%</span>' in rendered


def test_index_lists_portfolio(tmp_path, monkeypatch):
    out = build_with(tmp_path, monkeypatch, ["AAPL", "NVDA"], {"AAPL": {"10-K": RECORD_10K, "10-Q": RECORD_10Q}})
    index = (out / "index.html").read_text()
    assert 'data-href="aapl.html"' in index and "Apple Inc." in index
    assert "fa 11 mesos" in index and "1 per revisar" in index
    assert '<svg class="cal"' in index
    out = build_with(tmp_path, monkeypatch, ["AAPL"], {"AAPL": {"10-K": RECORD_10K}})
    index = (out / "index.html").read_text()
    assert "Punts a vigilar" in index and "Deute de" in index  # Alertes de l'informe més recent.


def test_company_without_data_is_pending(tmp_path, monkeypatch):
    out = build_with(tmp_path, monkeypatch, ["NVDA"], {})
    assert "Pendent" in (out / "index.html").read_text()
    assert "Encara no hi ha cap resum" in (out / "nvda.html").read_text()


def test_nested_bullets_become_sublists():
    rendered = build_site.render_lines(["- Segments:", "  - Compute", "  - Graphics", "- Total"])
    assert rendered.count("<ul>") == 2
    assert rendered.index("Compute") > rendered.index("<ul>", rendered.index("Segments"))
