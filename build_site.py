"""Genera el dashboard web estàtic de FilingMind.

Llegeix la cartera (`portfolio.json`) i els últims resums desats
(`data/filings.json`) i escriu a `site/`:

    index.html        Inici: una targeta per empresa de la cartera.
    <ticker>.html     Pàgina de cada empresa amb el resum del 10-K i del 10-Q.

Les pestanyes i les pàgines surten de `portfolio.json`, de manera que afegir o
treure un ticker de la cartera afegeix o treu la seva pàgina a la propera
generació. Les empreses que encara no tenen cap resum surten com a pendents.

Ús:
    python build_site.py [directori_de_sortida]
"""

from __future__ import annotations

import html
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
PORTFOLIO_FILE = BASE_DIR / "portfolio.json"
DATA_FILE = BASE_DIR / "data" / "filings.json"
DEFAULT_OUTPUT = BASE_DIR / "site"
FORM_TYPES = ("10-K", "10-Q")
FORM_LABELS = {"10-K": "Informe anual (10-K)", "10-Q": "Informe trimestral (10-Q)"}

CSS = """
:root {
  --bg: #f6f7f9; --surface: #ffffff; --text: #1b1f24; --muted: #5d6673;
  --border: #dde1e6; --accent: #1f5fbf; --accent-soft: #e8f0fc;
  --ok: #1d7a46; --ok-soft: #e5f4ec; --warn: #9a5b00; --warn-soft: #fdf1dc;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111418; --surface: #1a1e24; --text: #e6e9ed; --muted: #9aa3ae;
    --border: #2c323a; --accent: #7aa7ec; --accent-soft: #1d2a3d;
    --ok: #6fcf97; --ok-soft: #173025; --warn: #f2b45a; --warn-soft: #33270f;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
a { color: var(--accent); }
header { background: var(--surface); border-bottom: 1px solid var(--border); }
.wrap { max-width: 1200px; margin: 0 auto; padding: 0 16px; }
.brand { display: flex; flex-wrap: wrap; align-items: baseline; column-gap: 12px; padding: 18px 0 10px; }
.brand h1 { margin: 0; font-size: 22px; }
.brand span { color: var(--muted); font-size: 14px; }
nav.tabs { display: flex; gap: 4px; overflow-x: auto; }
nav.tabs a {
  padding: 8px 14px; border-radius: 8px 8px 0 0; text-decoration: none;
  color: var(--muted); font-weight: 600; white-space: nowrap;
  border-bottom: 2px solid transparent;
}
nav.tabs a:hover { color: var(--text); }
nav.tabs a.active { color: var(--accent); border-bottom-color: var(--accent); }
main.wrap { padding-top: 24px; padding-bottom: 48px; }
.grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(min(260px, 100%), 1fr)); }
.card {
  background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
  padding: 18px; text-decoration: none; color: inherit; display: block;
}
a.card:hover { border-color: var(--accent); }
.card h2 { margin: 0 0 12px; font-size: 24px; }
.row { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 6px 0;
       border-top: 1px solid var(--border); font-size: 14px; }
.row .label { color: var(--muted); white-space: nowrap; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px;
         font-weight: 600; white-space: nowrap; }
.badge.ok { background: var(--ok-soft); color: var(--ok); }
.badge.warn { background: var(--warn-soft); color: var(--warn); }
.badge.pending { background: var(--accent-soft); color: var(--muted); }
.filings { display: grid; gap: 20px; grid-template-columns: repeat(auto-fit, minmax(min(380px, 100%), 1fr)); }
.filing { background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
          padding: 20px 22px; min-width: 0; }
.filing > h2 { margin: 0 0 4px; font-size: 20px; }
.meta { color: var(--muted); font-size: 14px; margin-bottom: 12px; }
.summary h3 { font-size: 16px; margin: 18px 0 6px; }
.summary ul { margin: 0 0 8px; padding-left: 20px; }
.summary p { margin: 0 0 8px; }
.alert { background: var(--warn-soft); color: var(--text); border-left: 4px solid var(--warn);
         border-radius: 8px; padding: 10px 14px; margin: 12px 0; font-size: 14px; }
.alert strong { color: var(--warn); }
.empty { color: var(--muted); font-style: italic; }
footer.wrap { color: var(--muted); font-size: 13px; padding-bottom: 32px; }
@media (max-width: 480px) {
  .filing { padding: 16px; }
}
"""


def load_json(path: Path, default: Any) -> Any:
    """Carrega un JSON o retorna `default` si no existeix o està buit."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return default
    return json.loads(text) if text else default


def page_name(ticker: str) -> str:
    """Nom del fitxer HTML d'una empresa (només caràcters segurs)."""
    return re.sub(r"[^a-z0-9.-]", "-", ticker.lower()) + ".html"


def inline(text: str) -> str:
    """Escapa el text i converteix **negreta** en <strong>."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(text))


def render_summary(summary: str) -> str:
    """Converteix el resum en text pla a HTML.

    Les línies numerades ("1. Evolució del negoci...") passen a títols, les que
    comencen amb guió a llistes i la resta a paràgrafs.
    """
    parts: list[str] = []
    in_list = False
    for raw in summary.splitlines():
        line = raw.strip()
        bullet = re.match(r"^[-•*]\s+(.*)", line)
        if bullet:
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{inline(bullet.group(1))}</li>")
            continue
        if in_list:
            parts.append("</ul>")
            in_list = False
        if not line:
            continue
        heading = re.match(r"^(?:#+\s*)?(\d+\.\s+.+)$", line)
        if heading and len(line) < 120:
            parts.append(f"<h3>{inline(heading.group(1).rstrip(':'))}</h3>")
        else:
            parts.append(f"<p>{inline(line)}</p>")
    if in_list:
        parts.append("</ul>")
    return "\n".join(parts)


def status_badge(record: dict[str, Any] | None) -> str:
    """Etiqueta d'estat de la verificació de xifres d'un filing."""
    if not record:
        return '<span class="badge pending">Pendent</span>'
    count = len(record.get("unverified") or [])
    if count:
        noun = "xifra" if count == 1 else "xifres"
        return f'<span class="badge warn">{count} {noun} per revisar</span>'
    return '<span class="badge ok">Xifres verificades</span>'


def layout(title: str, tickers: list[str], active: str | None, body: str,
           generated: str) -> str:
    """Plantilla comuna: capçalera, pestanyes, contingut i peu."""
    home_cls = ' class="active"' if active is None else ""
    tabs = [f'<a href="index.html"{home_cls}>Inici</a>']
    for ticker in tickers:
        cls = ' class="active"' if ticker == active else ""
        tabs.append(f'<a href="{page_name(ticker)}"{cls}>{html.escape(ticker)}</a>')
    return f"""<!doctype html>
<html lang="ca">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <div class="wrap">
    <div class="brand"><h1>FilingMind</h1><span>Resums dels últims 10-K i 10-Q de la cartera</span></div>
    <nav class="tabs">{"".join(tabs)}</nav>
  </div>
</header>
<main class="wrap">
{body}
</main>
<footer class="wrap">Actualitzat el {generated} · Resums generats amb Claude a partir dels filings de la SEC.</footer>
</body>
</html>
"""


def render_index(tickers: list[str], data: dict[str, Any], generated: str) -> str:
    """Pàgina d'inici amb una targeta per empresa."""
    if not tickers:
        body = '<p class="empty">La cartera és buida. Afegeix tickers a portfolio.json.</p>'
        return layout("FilingMind", tickers, None, body, generated)

    cards = []
    for ticker in tickers:
        rows = []
        for form in FORM_TYPES:
            record = data.get(ticker, {}).get(form)
            date = html.escape(record["filed"]) if record else "—"
            rows.append(
                f'<div class="row"><span class="label">{form} · {date}</span>'
                f"{status_badge(record)}</div>"
            )
        cards.append(
            f'<a class="card" href="{page_name(ticker)}"><h2>{html.escape(ticker)}</h2>'
            f'{"".join(rows)}</a>'
        )
    body = f'<div class="grid">{"".join(cards)}</div>'
    return layout("FilingMind · Inici", tickers, None, body, generated)


def render_filing(form: str, record: dict[str, Any] | None) -> str:
    """Bloc amb el resum d'un filing (o un avís si encara no n'hi ha)."""
    if not record:
        return (
            f'<section class="filing"><h2>{FORM_LABELS[form]}</h2>'
            '<p class="empty">Encara no hi ha cap resum. Es generarà a la propera execució.</p>'
            "</section>"
        )

    alert = ""
    unverified = record.get("unverified") or []
    if unverified:
        items = "".join(
            f"<li><strong>{html.escape(u['token'])}</strong>: …{html.escape(u['context'])}…</li>"
            for u in unverified
        )
        alert = (
            '<div class="alert"><strong>⚠️ Xifres no verificades.</strong> '
            f"No s'han trobat al document; revisa-les abans de fer-les servir.<ul>{items}</ul></div>"
        )

    url = html.escape(record.get("url", ""), quote=True)
    return (
        f'<section class="filing"><h2>{FORM_LABELS[form]}</h2>'
        f'<div class="meta">Presentat el {html.escape(record.get("filed", "—"))} · '
        f'<a href="{url}" target="_blank" rel="noopener">Document a la SEC</a> · '
        f"{status_badge(record)}</div>"
        f'{alert}<div class="summary">{render_summary(record.get("summary", ""))}</div>'
        "</section>"
    )


def render_company(ticker: str, tickers: list[str], data: dict[str, Any], generated: str) -> str:
    """Pàgina d'una empresa amb el 10-K i el 10-Q."""
    records = data.get(ticker, {})
    sections = "".join(render_filing(form, records.get(form)) for form in FORM_TYPES)
    body = f'<h2 style="margin-top:0">{html.escape(ticker)}</h2><div class="filings">{sections}</div>'
    return layout(f"FilingMind · {ticker}", tickers, ticker, body, generated)


def build(output: Path = DEFAULT_OUTPUT) -> list[Path]:
    """Genera totes les pàgines a `output` i en retorna les rutes."""
    tickers = [t.strip().upper() for t in load_json(PORTFOLIO_FILE, []) if t.strip()]
    data = load_json(DATA_FILE, {})
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Es buida el directori perquè no quedin pàgines d'empreses que ja no hi són.
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    pages = {output / "index.html": render_index(tickers, data, generated)}
    for ticker in tickers:
        pages[output / page_name(ticker)] = render_company(ticker, tickers, data, generated)
    for path, content in pages.items():
        path.write_text(content, encoding="utf-8")
    return list(pages)


if __name__ == "__main__":
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    written = build(out_dir)
    print(f"Dashboard generat: {len(written)} pàgines a {out_dir}")
