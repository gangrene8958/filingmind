"""Tests del bucle principal: enviament, desat per al dashboard i resums pendents."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402


def filing(ticker, form, fid):
    return {"id": fid, "ticker": ticker, "form_type": form, "title": form,
            "updated": "2026-01-01", "index_url": "https://sec/x"}


def run(tmp_path, monkeypatch, processed, data, feeds):
    """Executa main() amb fitxers temporals i xarxa simulada."""
    for name, value in {"SEC_USER_AGENT": "t", "TELEGRAM_BOT_TOKEN": "t",
                        "TELEGRAM_CHAT_ID": "1", "ANTHROPIC_API_KEY": "k"}.items():
        monkeypatch.setenv(name, value)
    paths = {"PORTFOLIO_FILE": tmp_path / "portfolio.json",
             "STATE_FILE": tmp_path / "processed.json",
             "DATA_FILE": tmp_path / "data" / "filings.json"}
    for attr, path in paths.items():
        monkeypatch.setattr(main, attr, path)
    paths["PORTFOLIO_FILE"].write_text(json.dumps(sorted({t for t, _ in feeds})))
    paths["STATE_FILE"].write_text(json.dumps({"processed": processed}))
    paths["DATA_FILE"].parent.mkdir()
    paths["DATA_FILE"].write_text(json.dumps(data))

    def fake_fetch(session, ticker, form, max_entries):
        return [filing(ticker, form, feeds[(ticker, form)])] if (ticker, form) in feeds else []

    def fake_process(f, *args, notify=True):
        return {"id": f["id"], "summary": "ok", "notified": notify}

    with mock.patch.object(main, "fetch_filings", side_effect=fake_fetch), \
         mock.patch.object(main, "process_filing", side_effect=fake_process) as process:
        assert main.main() == 0

    state = json.loads(paths["STATE_FILE"].read_text())
    stored = json.loads(paths["DATA_FILE"].read_text())
    return process, state, stored


def test_new_filing_is_sent_and_stored(tmp_path, monkeypatch):
    process, state, stored = run(tmp_path, monkeypatch, [], {}, {("AAPL", "10-K"): "k1"})
    assert process.call_args.kwargs["notify"] is True
    assert state["processed"] == ["k1"]
    assert stored["AAPL"]["10-K"]["id"] == "k1"


def test_sent_filing_without_summary_is_backfilled_silently(tmp_path, monkeypatch):
    process, state, stored = run(tmp_path, monkeypatch, ["k1"], {}, {("AAPL", "10-K"): "k1"})
    assert process.call_args.kwargs["notify"] is False  # No es torna a enviar a Telegram.
    assert state["processed"] == ["k1"]
    assert stored["AAPL"]["10-K"]["notified"] is False


def test_sent_and_stored_filing_is_skipped(tmp_path, monkeypatch):
    data = {"AAPL": {"10-K": {"id": "k1"}}}
    process, _, _ = run(tmp_path, monkeypatch, ["k1"], data, {("AAPL", "10-K"): "k1"})
    assert process.call_count == 0


def test_newer_filing_replaces_stored_one(tmp_path, monkeypatch):
    data = {"AAPL": {"10-Q": {"id": "q1"}}}
    _, state, stored = run(tmp_path, monkeypatch, ["q1"], data, {("AAPL", "10-Q"): "q2"})
    assert stored["AAPL"]["10-Q"]["id"] == "q2"
    assert state["processed"] == ["q1", "q2"]
