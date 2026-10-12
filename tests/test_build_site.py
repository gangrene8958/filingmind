"""Tests del generador del dashboard."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_site  # noqa: E402

RECORD_10K = {
    "id": "a", "form_type": "10-K", "title": "10-K", "filed": "2025-10-31",
    "url": "https://www.sec.gov/aapl.htm",
    "summary": "1. Evolució del negoci\n- Vendes de $391,035 milions\n- Marge **46.2%**\n\n"
               "2. Riscos principals\n- Risc <script>alert(1)</script>",
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
    build_site.build(out)
    return out


def test_one_page_per_portfolio_company(tmp_path, monkeypatch):
    out = build_with(tmp_path, monkeypatch, ["AAPL", "msft", "BRK.B"], {})
    assert sorted(p.name for p in out.iterdir()) == ["aapl.html", "brk.b.html", "index.html", "msft.html"]
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
    assert '<a href="aapl.html" class="active">AAPL</a>' in page
    assert "<h3>1. Evolució del negoci</h3>" in page
    assert "<li>Vendes de $391,035 milions</li>" in page
    assert "<strong>46.2%</strong>" in page
    assert "<script>alert" not in page  # El text del resum s'escapa.
    assert "Xifres verificades" in page
    assert "⚠️ Xifres no verificades" in page and "93,736" in page
    assert "https://www.sec.gov/aapl.htm" in page


def test_company_without_data_is_pending(tmp_path, monkeypatch):
    out = build_with(tmp_path, monkeypatch, ["NVDA"], {})
    assert "Pendent" in (out / "index.html").read_text()
    assert "Encara no hi ha cap resum" in (out / "nvda.html").read_text()
