"""Genera el dashboard web estàtic de FilingMind (en anglès).

Llegeix la cartera (`portfolio.json`) i els últims resums desats
(`data/filings.json`) i escriu a `site/`:

    index.html        Inici: llibre de la cartera, punts a vigilar i calendari.
    <ticker>.html     Pàgina de cada empresa amb el resum del 10-K i del 10-Q.
    assets/           Full d'estils i script compartits (de `site_assets/`).

Les pestanyes i les pàgines surten de `portfolio.json`, de manera que afegir o
treure un ticker de la cartera afegeix o treu la seva pàgina a la propera
generació. Les empreses que encara no tenen cap resum surten com a pendents.

Tot és determinista: el generador només reorganitza el text dels resums (no
calcula xifres noves) i les pàgines funcionen també sense JavaScript. Entén els
resums en anglès i també els antics en català.

Ús:
    python build_site.py [directori_de_sortida]
"""

from __future__ import annotations

import html
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from verify_numbers import checked_numbers

BASE_DIR = Path(__file__).resolve().parent
PORTFOLIO_FILE = BASE_DIR / "portfolio.json"
DATA_FILE = BASE_DIR / "data" / "filings.json"
ASSETS_DIR = BASE_DIR / "site_assets"
DEFAULT_OUTPUT = BASE_DIR / "site"
FORM_TYPES = ("10-K", "10-Q")
FORM_LABELS = {"10-K": "Annual report", "10-Q": "Quarterly report"}
QUARTER_DAYS = 91  # Interval aproximat entre presentacions per estimar la propera.

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

CHAPTER_KINDS = [  # (paraula clau del títol, en anglès o català; tipus; etiqueta curta)
    ("business", "business", "Business"),
    ("negoci", "business", "Business"),
    ("risk", "risks", "Risks"),
    ("risc", "risks", "Risks"),
    ("strateg", "strategy", "Strategy"),
    ("estrat", "strategy", "Strategy"),
    ("red flag", "flags", "Red flags"),
    ("alert", "flags", "Red flags"),
]

FONTS_URL = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&"
             "family=IBM+Plex+Sans:ital,wght@0,400;0,500;0,600;1,400&"
             "family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;"
             "1,6..72,400&display=swap")

# S'executa abans de pintar la pàgina perquè el tema desat no faci pampallugues.
THEME_SCRIPT = ("(function(d){d.classList.add('js');try{var t=localStorage.getItem('fm-theme');"
                "if(t==='light'||t==='dark')d.setAttribute('data-theme',t)}catch(e){}})"
                "(document.documentElement)")


# --------------------------------------------------------------------------- #
# Utilitats
# --------------------------------------------------------------------------- #
def load_json(path: Path, default: Any) -> Any:
    """Carrega un JSON o retorna `default` si no existeix o està buit."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return default
    return json.loads(text) if text else default


def esc(text: Any) -> str:
    """Escapa text per a HTML (també les cometes, per als atributs)."""
    return html.escape(str(text), quote=True)


def page_name(ticker: str) -> str:
    """Nom del fitxer HTML d'una empresa (només caràcters segurs)."""
    return re.sub(r"[^a-z0-9.-]", "-", ticker.lower()) + ".html"


def vt_name(ticker: str) -> str:
    """Nom de view-transition del ticker (enllaça la fila de l'inici amb el titular)."""
    return "t-" + re.sub(r"[^a-z0-9]", "-", ticker.lower())


def parse_date(value: str | None) -> date | None:
    """Converteix "2025-10-31" (o un ISO amb hora) en una data."""
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def fmt_date(day: date | None) -> str:
    """Data llarga: "October 31, 2025"."""
    if not day:
        return "—"
    return f"{MONTHS[day.month - 1]} {day.day}, {day.year}"


def fmt_date_short(day: date | None) -> str:
    """Data curta: "Oct 31, 2025"."""
    if not day:
        return "—"
    return f"{MONTHS[day.month - 1][:3]} {day.day}, {day.year}"


def fmt_age(day: date | None, today: date) -> str:
    """Antiguitat relativa: "today", "3 days ago", "5 months ago"."""
    if not day:
        return ""
    days = (today - day).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 60:
        return f"{days} days ago"
    months = round(days / 30.4)
    return f"{months} months ago" if months < 24 else f"{round(days / 365)} years ago"


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """"1 company" / "3 companies"."""
    return singular if count == 1 else (plural_form or singular + "s")


def smart_title(text: str) -> str:
    """Passa un nom en majúscules a format títol ("AMAZON.COM, INC." -> "Amazon.com, Inc.")."""
    if text != text.upper():
        return text
    return " ".join(word[:1].upper() + word[1:].lower() for word in text.split())


def sentence_case(text: str) -> str:
    """Títols en majúscules a format frase, conservant les sigles conegudes."""
    if text != text.upper():
        return text
    lowered = text.lower()
    lowered = lowered[:1].upper() + lowered[1:]
    return re.sub(r"\b(md&a|sec|eps|ai|ia)\b", lambda m: m.group().upper(), lowered)


def is_note(line: str) -> bool:
    """Les línies "Note: ..." / "Nota: ..." són aclariments, no contingut."""
    return line.strip().lower().startswith(("note", "nota"))


# --------------------------------------------------------------------------- #
# Lectura del resum
# --------------------------------------------------------------------------- #
@dataclass
class Chapter:
    """Un apartat numerat del resum (negoci, riscos, estratègia, alertes)."""
    number: int
    title: str
    kind: str
    label: str
    lines: list[str] = field(default_factory=list)


@dataclass
class Kpi:
    """Xifra destacada extreta literalment d'una línia del resum."""
    label: str
    value: str
    delta: str | None = None
    direction: str = "flat"  # up, down o flat


HEADING_RE = re.compile(r"^(?:#+\s*)?\**\s*(\d)[.)]\s+(.{3,110}?)\s*\**:?\s*$")
LEDE_PREFIX_RE = re.compile(r"^\**\s*(?:RESUM EXECUTIU|EXECUTIVE SUMMARY)\s*[-–—:]?\s*", re.IGNORECASE)


def parse_summary(summary: str) -> tuple[list[str], list[Chapter]]:
    """Separa el resum en línies d'entrada (abans del primer apartat) i apartats."""
    lede: list[str] = []
    chapters: list[Chapter] = []
    for raw in summary.splitlines():
        heading = HEADING_RE.match(raw.strip())
        if heading and not raw.lstrip().startswith(("-", "•", "*  ")):
            title = sentence_case(heading.group(2).strip("* ").rstrip(":"))
            kind, label = "other", title
            for key, chapter_kind, short in CHAPTER_KINDS:
                if key in title.lower():
                    kind, label = chapter_kind, short
                    break
            chapters.append(Chapter(int(heading.group(1)), title, kind, label))
        elif chapters:
            chapters[-1].lines.append(raw)
        else:
            lede.append(raw)
    return lede, chapters


def company_name(ticker: str, records: list[dict[str, Any]]) -> str:
    """Nom de l'empresa tal com surt a la primera línia del resum ("Apple Inc. (AAPL)")."""
    pattern = re.compile(rf"([A-Za-z][\w.,&'’ -]{{1,60}}?)\s*\({re.escape(ticker)}\)")
    candidates = []
    for record in records:
        for line in (record.get("summary") or "").splitlines()[:2]:
            match = pattern.search(LEDE_PREFIX_RE.sub("", line.strip()))
            if match:
                candidates.append(match.group(1).strip(" ,-–"))
    if not candidates:
        return ""
    mixed = [c for c in candidates if c != c.upper()]  # Preferim "Apple Inc." a "APPLE INC.".
    return smart_title(max(mixed or candidates, key=len))


def lede_parts(ticker: str, lede: list[str]) -> tuple[str, list[str]]:
    """Retorna la frase d'entrada (període del filing) i les notes prèvies."""
    lines = [line.strip() for line in lede if line.strip()]
    notes = [line for line in lines if is_note(line)]
    dek = " · ".join(line for line in lines if not is_note(line))
    dek = LEDE_PREFIX_RE.sub("", dek)
    # Es treu el nom i el ticker, que ja surten al titular de la pàgina.
    dek = re.sub(rf"^.*?\b{re.escape(ticker)}\b\)?\s*[,–—-]?\s*", "", dek, count=1) or dek
    dek = dek.strip(" ·,–—-")
    return dek[:1].upper() + dek[1:], notes


UNIT_RE = r"(?:\s*(?:mil milions|milions|billion|million|bn|[MB])\b)?"
KPI_RE = re.compile(
    r"^(?P<label>[^:$\d][^:$]{2,44}):\s*"
    rf"(?P<value>\$\(?\d[\d,.]*\)?{UNIT_RE})"
    r"(?P<rest>.*)$"
)
NUM = r"(\d+(?:[.,]\d+)?)"
DELTA_PATTERNS = [  # (patró, signe: 1 puja, -1 baixa, 0 segons el signe de la xifra)
    (re.compile(rf"^\s*\(\({NUM}\)%\)"), -1),                                   # ((4)%)
    (re.compile(r"^\s*\(([+-]?\d+(?:[.,]\d+)?)%\)"), 0),                         # (+10%) / (-1%)
    (re.compile(rf"^\s*\({NUM}% (?:de creixement|més|growth|increase|higher)\)"), 1),
    (re.compile(rf"^\s*\({NUM}% (?:menys|decrease|decline|lower)\)"), -1),
    (re.compile(r"^\s*,\s*([+-]\d+(?:[.,]\d+)?)%"), 0),                          # , +18%
    (re.compile(rf"\b(?:up|increased?|grew|growth of|rising)(?: by)? {NUM}%"), 1),
    (re.compile(rf"\b(?:down|decreased?|declined?|fell|falling)(?: by)? {NUM}%"), -1),
    (re.compile(rf"\bun {NUM}% més"), 1),
    (re.compile(rf"creixement (?:és )?del {NUM}%"), 1),
    (re.compile(rf"\bun {NUM}% menys"), -1),
]


def _delta(rest: str) -> tuple[str | None, str]:
    """Busca la variació que el resum dona just després d'una xifra (sense calcular-la)."""
    rest = rest.split(";")[0]
    for pattern, sign in DELTA_PATTERNS:
        match = pattern.search(rest)
        if not match:
            continue
        raw = match.group(1)
        if sign == 0:
            sign = -1 if raw.startswith("-") else 1
            raw = raw.lstrip("+-")
        if float(raw.replace(",", ".")) == 0:
            return f"{raw}%", "flat"
        return (f"+{raw}%", "up") if sign > 0 else (f"−{raw}%", "down")
    return None, "flat"


def _compact_value(value: str) -> str:
    """Escurça la unitat sense tocar la xifra: "$416,161 million" -> "$416,161M"."""
    value = re.sub(r"\s*(?:mil milions|billion|bn)$", "B", value)
    return re.sub(r"\s*(?:milions|million)$", "M", value)


def extract_kpis(chapters: list[Chapter], limit: int = 4) -> list[Kpi]:
    """Primeres xifres "Etiqueta: $valor" de l'apartat de negoci, tal com surten al resum."""
    business = next((c for c in chapters if c.kind == "business"), chapters[0] if chapters else None)
    if not business:
        return []
    kpis: list[Kpi] = []
    seen: set[str] = set()
    skip = ("per ", "nota", "note", "en ", "als ", "a ", "by ", "in ", "for ", "over ")
    for raw in business.lines:
        line = re.sub(r"^[-•*]\s+", "", raw.strip()).replace("**", "")
        for sentence in re.split(r"(?<=[\w)%])\.\s+(?=[A-ZÀ-Ú])", line):
            match = KPI_RE.match(sentence.strip())
            if not match:
                continue
            label = match.group("label").strip()
            if label.lower() in seen or label.lower().startswith(skip):
                continue
            delta, direction = _delta(match.group("rest"))
            kpis.append(Kpi(label[:1].upper() + label[1:], _compact_value(match.group("value")),
                            delta, direction))
            seen.add(label.lower())
            if len(kpis) == limit:
                return kpis
    return kpis


# --------------------------------------------------------------------------- #
# Renderitzat del text
# --------------------------------------------------------------------------- #
FIGURE_RE = (
    r"\$\(?\d[\d,.]*\)?(?:\s+(?:mil milions|milions|billion|million))?"
    r"|\(\(\d[\d,.]*\)%\)"
    r"|[+−-]?\d[\d,.]*\s?%"
)


def inline(text: str, flagged: list[str] | None = None) -> str:
    """Escapa el text, aplica **negreta** i marca les xifres (les no verificades, en vermell)."""
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(text, quote=False))
    tokens = sorted({html.escape(t, quote=False) for t in flagged or [] if t}, key=len, reverse=True)
    alternatives = [rf"(?<![\d,.]){re.escape(t)}(?![\d])" for t in tokens] + [FIGURE_RE]

    def mark(match: re.Match[str]) -> str:
        figure = match.group()
        if any(token in figure for token in tokens):
            return f'<mark class="fig fig--flag" title="Not found in the filing">{figure}</mark>'
        return f'<span class="fig">{figure}</span>'

    return re.sub("|".join(alternatives), mark, out)


def render_lines(lines: list[str], flagged: list[str] | None = None) -> str:
    """Converteix línies de text pla en HTML.

    Les que comencen amb guió passen a llistes (les sagnades, a subllistes), les
    notes ("Note: ...") a paràgrafs destacats i la resta a paràgrafs.
    """
    parts: list[str] = []
    depth = 0  # Nivells de llista oberts.

    def close_lists(level: int) -> None:
        nonlocal depth
        while depth > level:
            parts.append("</li></ul>")
            depth -= 1

    for raw in lines:
        line = raw.strip()
        bullet = re.match(r"^[-•*]\s+(.*)", line)
        if bullet:
            level = 2 if len(raw) - len(raw.lstrip()) >= 2 and depth >= 1 else 1
            if level > depth:
                parts.append("<ul>")
                depth = level
            else:
                close_lists(level)
                parts.append("</li>")
            parts.append(f"<li>{inline(bullet.group(1), flagged)}")
            continue
        close_lists(0)
        if not line:
            continue
        css = ' class="note"' if is_note(line) else ""
        parts.append(f"<p{css}>{inline(line, flagged)}</p>")
    close_lists(0)
    return "\n".join(parts)


def render_summary(summary: str, flagged: list[str] | None = None) -> str:
    """Resum complet en HTML senzill (títols i cos), útil per a proves i vistes curtes."""
    lede, chapters = parse_summary(summary)
    parts = [render_lines(lede, flagged)] if any(l.strip() for l in lede) else []
    for chapter in chapters:
        parts.append(f"<h3>{esc(chapter.title)}</h3>")
        parts.append(render_lines(chapter.lines, flagged))
    return "\n".join(parts)


def verification(record: dict[str, Any]) -> tuple[int, int]:
    """(xifres comprovades, xifres no trobades al document) d'un resum."""
    flagged = len(record.get("unverified") or [])
    total = len(checked_numbers(record.get("summary") or ""))
    return max(total, flagged), flagged


def stamp(record: dict[str, Any] | None) -> str:
    """Segell petit amb l'estat de la verificació, per al llibre de la cartera."""
    if not record:
        return '<span class="stamp stamp--pending">Pending</span>'
    total, flagged = verification(record)
    if flagged:
        return f'<span class="stamp stamp--flag" title="Figures to review">{flagged} to review</span>'
    return f'<span class="stamp stamp--ok" title="Figures checked against the filing">{total}/{total}</span>'


# --------------------------------------------------------------------------- #
# Plantilla
# --------------------------------------------------------------------------- #
THEME_BUTTON = """<button class="theme" type="button" aria-label="Switch to dark mode" hidden>
      <svg viewBox="0 0 24 24" aria-hidden="true"><circle class="theme__disc" cx="12" cy="12" r="5.5"/>
      <path class="theme__rays" d="M12 1.5v3M12 19.5v3M1.5 12h3M19.5 12h3M4.6 4.6l2.1 2.1M17.3 17.3l2.1 2.1M4.6 19.4l2.1-2.1M17.3 6.7l2.1-2.1"/>
      <circle class="theme__bite" cx="16.5" cy="8.5" r="5"/></svg>
      <span class="theme__label">Dark</span>
    </button>"""


def masthead(tickers: list[str], active: str | None, today: date) -> str:
    """Capçalera amb la data, el nom, el canvi de tema i les pestanyes de la cartera."""
    weekday = WEEKDAYS[today.weekday()]
    tabs = []
    for ticker in [None, *tickers]:
        href = "index.html" if ticker is None else page_name(ticker)
        current = ' aria-current="page"' if ticker == active else ""
        text = "Portfolio" if ticker is None else esc(ticker)
        tabs.append(f'<a href="{href}"{current}>{text}</a>')
    return f"""<header class="mast">
  <div class="wrap mast__top">
    <p class="mast__date">{weekday}, {fmt_date(today)}</p>
    <a class="mast__logo" href="index.html">Filing<span>Mind</span></a>
    <div class="mast__side"><p class="mast__tag">The portfolio audit notebook</p>
    {THEME_BUTTON}</div>
  </div>
</header>
<nav class="tabs" aria-label="Portfolio companies"><div class="wrap tabs__row">{"".join(tabs)}</div>
<div class="progress" aria-hidden="true"></div></nav>"""


def layout(title: str, tickers: list[str], active: str | None, body: str, today: date,
           generated: str) -> str:
    """Plantilla comuna: capçalera, pestanyes, contingut i peu."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="Plain-English briefs of the latest 10-K and 10-Q filings for every company in the portfolio.">
<meta name="color-scheme" content="light dark">
<script>{THEME_SCRIPT}</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS_URL}">
<link rel="stylesheet" href="assets/style.css">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' fill='%2316150f'/%3E%3Cpath d='M9 7h14v4H14v4h8v4h-8v6H9z' fill='%23f3efe6'/%3E%3Crect x='19' y='21' width='4' height='4' fill='%23d2401e'/%3E%3C/svg%3E">
<script src="assets/app.js" defer></script>
</head>
<body>
{masthead(tickers, active, today)}
<main class="wrap">
{body}
</main>
<footer class="foot">
  <div class="wrap foot__row">
    <p>Data taken from filings published on SEC EDGAR. Every figure in each brief is automatically checked against the original filing.</p>
    <p class="foot__stamp">Updated {generated}</p>
  </div>
</footer>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# Pàgina d'inici
# --------------------------------------------------------------------------- #
def latest_record(records: dict[str, Any]) -> dict[str, Any] | None:
    """El filing més recent d'una empresa (10-K o 10-Q)."""
    present = [r for r in (records.get(f) for f in FORM_TYPES) if r]
    return max(present, key=lambda r: r.get("filed", ""), default=None)


def next_filing(records: dict[str, Any], today: date) -> date | None:
    """Estimació de la propera presentació: un trimestre després de l'última."""
    last = latest_record(records)
    day = parse_date(last.get("filed")) if last else None
    if not day:
        return None
    day += timedelta(days=QUARTER_DAYS)
    while day < today:
        day += timedelta(days=QUARTER_DAYS)
    return day


def timeline_svg(tickers: list[str], data: dict[str, Any], today: date) -> str:
    """Calendari SVG: presentacions dels últims 12 mesos i la propera estimada."""
    start = date(today.year - 1, today.month, 1)
    end_month = today.month + 4
    end = date(today.year + (end_month - 1) // 12, (end_month - 1) % 12 + 1, 1)
    left, right, top, row_h = 76, 884, 40, 38
    height = top + row_h * max(len(tickers), 1) + 14
    span = (end - start).days

    def x(day: date) -> float:
        return round(left + (right - left) * (day - start).days / span, 1)

    parts = [f'<svg class="cal" viewBox="0 0 900 {height}" role="img" '
             f'aria-labelledby="cal-title"><title id="cal-title">Filing calendar</title>']
    month = start
    while month < end:
        mx = x(month)
        parts.append(f'<line class="cal__grid" x1="{mx}" y1="{top - 10}" x2="{mx}" y2="{height - 8}"/>')
        label = MONTHS[month.month - 1][:3] + (f" ’{month.year % 100:02d}" if month.month == 1 else "")
        parts.append(f'<text class="cal__month" x="{mx + 4}" y="{top - 16}">{label}</text>')
        month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    tx = x(today)
    parts.append(f'<line class="cal__today" x1="{tx}" y1="{top - 30}" x2="{tx}" y2="{height - 8}"/>'
                 f'<text class="cal__today-label" x="{tx + 5}" y="{top - 30}">today</text>')
    marker = 0
    for row, ticker in enumerate(tickers):
        cy = top + row_h * row + row_h / 2
        records = data.get(ticker, {})
        parts.append(f'<a href="{page_name(ticker)}"><text class="cal__ticker" x="0" y="{cy + 5}">'
                     f'{esc(ticker)}</text></a>')
        parts.append(f'<line class="cal__lane" x1="{left}" y1="{cy}" x2="{right}" y2="{cy}"/>')
        for form in FORM_TYPES:
            record = records.get(form)
            day = parse_date(record.get("filed")) if record else None
            if not day or day < start:
                continue
            px = x(day)
            tip = f"<title>{esc(ticker)} · {form} · {fmt_date(day)}</title>"
            marker += 1
            shape = (f'<rect class="cal__k" x="{px - 6}" y="{cy - 6}" width="12" height="12" '
                     f'transform="rotate(45 {px} {cy})"/>' if form == "10-K"
                     else f'<circle class="cal__q" cx="{px}" cy="{cy}" r="6"/>')
            parts.append(f'<g class="cal__mark" style="--i:{marker}">{shape}{tip}</g>')
        upcoming = next_filing(records, today)
        if upcoming and upcoming < end:
            marker += 1
            parts.append(f'<g class="cal__mark" style="--i:{marker}">'
                         f'<circle class="cal__next" cx="{x(upcoming)}" cy="{cy}" r="6"/>'
                         f"<title>{esc(ticker)} · next filing (estimated) · "
                         f"{fmt_date(upcoming)}</title></g>")
    parts.append("</svg>")
    return "".join(parts)


def first_sentences(text: str, limit: int = 240) -> str:
    """Les primeres frases senceres que caben a `limit` caràcters (com a mínim una)."""
    sentences = re.split(r"(?<=[a-z%)\d])[.;]\s+(?=[A-Z$(])", text.strip())
    out = sentences[0]
    for sentence in sentences[1:]:
        if len(out) + len(sentence) + 2 > limit:
            break
        out = f"{out}. {sentence}"
    return out if out.endswith((".", "!", "?")) else out + "."


def watch_points(tickers: list[str], data: dict[str, Any]) -> str:
    """Primer punt de l'apartat d'alertes de l'informe més recent de cada empresa."""
    items = []
    for ticker in tickers:
        record = latest_record(data.get(ticker, {}))
        if not record:
            continue
        _, chapters = parse_summary(record.get("summary") or "")
        flags = next((c for c in chapters if c.kind == "flags"), None)
        bullets = [re.sub(r"^[-•*]\s+", "", l.strip()) for l in (flags.lines if flags else [])
                   if re.match(r"^[-•*]\s+", l)]
        if not bullets:
            continue
        text = bullets[0].rstrip(":")
        if len(text) < 30 and len(bullets) > 1:  # "Margins:" -> s'hi afegeix el primer detall.
            text = f"{text}: {bullets[1][:1].lower()}{bullets[1][1:]}"
        flagged = [u["token"] for u in record.get("unverified") or []]
        label = re.match(r"^([^:$]{3,40}):\s+(.+)$", text)
        if label:  # "Margin pressure: ..." -> el títol en negreta i el text a continuació.
            rest = label.group(2)[:1].upper() + label.group(2)[1:]
            body = f"<strong>{inline(label.group(1))}.</strong> {inline(first_sentences(rest), flagged)}"
        else:
            body = inline(first_sentences(text), flagged)
        anchor = f"f-{record['form_type']}-c{chapters.index(flags) + 1}"
        items.append(
            f'<li class="reveal"><a class="watch__ticker" href="{page_name(ticker)}#{anchor}">'
            f'{esc(ticker)}</a><p>{body}</p>'
            f'<span class="watch__src">{record["form_type"]} · {fmt_date_short(parse_date(record.get("filed")))}'
            f' · <a href="{page_name(ticker)}#{anchor}">All red flags&nbsp;→</a></span></li>'
        )
    if not items:
        return ""
    return ('<section class="block watch"><header class="block__head reveal"><h2>On the watch list</h2>'
            "<p>The first red flag in each company's most recent filing.</p></header>"
            f'<ol class="watch__list">{"".join(items)}</ol></section>')


def render_index(tickers: list[str], data: dict[str, Any], today: date, generated: str) -> str:
    """Pàgina d'inici: estat de la cartera, llibre, punts a vigilar i calendari."""
    if not tickers:
        body = ('<section class="intro"><p class="kicker">Portfolio status</p>'
                '<h1>The portfolio is empty</h1><p class="intro__lede">Add tickers to '
                '<code>portfolio.json</code> and they will show up here after the next run.</p></section>')
        return layout("FilingMind", tickers, None, body, today, generated)

    rows, reports, figures = [], 0, 0
    for ticker in tickers:
        records = data.get(ticker, {})
        name = company_name(ticker, [r for r in records.values() if r])
        cells = []
        for form in FORM_TYPES:
            record = records.get(form)
            if record:
                reports += 1
                figures += verification(record)[0]
                day = parse_date(record.get("filed"))
                cells.append(f'<td data-label="{form}"><span class="when">{fmt_date_short(day)}</span>'
                             f'<span class="age">{fmt_age(day, today)}</span></td>')
            else:
                cells.append(f'<td data-label="{form}"><span class="when muted">—</span></td>')
        latest = latest_record(records)
        kpi_cell = '<span class="muted">—</span>'
        if latest:
            kpis = extract_kpis(parse_summary(latest.get("summary") or "")[1], limit=1)
            if kpis:
                kpi = kpis[0]
                delta = (f'<span class="delta delta--{kpi.direction}">{esc(kpi.delta)}</span>'
                         if kpi.delta else "")
                kpi_cell = (f'<span class="kpi-mini__label">{esc(kpi.label)}</span>'
                            f'<span class="kpi-mini__value">{esc(kpi.value)}{delta}</span>')
        stamps = " ".join(f'<span class="stamp-pair"><small>{f[-1]}</small>{stamp(records.get(f))}</span>'
                          for f in FORM_TYPES)
        href = page_name(ticker)
        name_html = f'<span class="ledger__name">{esc(name)}</span>' if name else ""
        rows.append(
            f'<tr class="reveal" data-href="{href}"><th scope="row">'
            f'<a class="ledger__ticker" href="{href}" style="view-transition-name:{vt_name(ticker)}">'
            f"{esc(ticker)}</a>{name_html}</th>"
            f'{"".join(cells)}<td data-label="Headline figure" class="kpi-mini">{kpi_cell}</td>'
            f'<td data-label="Fact check" class="ledger__stamps">{stamps}</td></tr>'
        )

    companies = len(tickers)
    stats = [
        (companies, plural(companies, "company", "companies")),
        (reports, plural(reports, "filing summarized", "filings summarized")),
        (figures, plural(figures, "figure checked", "figures checked")),
    ]
    stats_html = "".join(f'<li><span class="stat__n" data-count="{n}">{n}</span>'
                         f'<span class="stat__l">{l}</span></li>' for n, l in stats)
    body = f"""<section class="intro">
  <p class="kicker reveal">Portfolio status</p>
  <h1 class="reveal">What the latest filings say about each company</h1>
  <ul class="stats reveal">{stats_html}</ul>
</section>
<section class="block">
  <header class="block__head reveal"><h2>The portfolio ledger</h2>
    <p>Latest annual (10-K) and quarterly (10-Q) filing for each company.</p></header>
  <div class="ledger-wrap"><table class="ledger">
    <thead><tr><th scope="col">Company</th><th scope="col">Latest 10-K</th><th scope="col">Latest 10-Q</th>
    <th scope="col">Headline figure</th><th scope="col">Fact check</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table></div>
</section>
{watch_points(tickers, data)}
<section class="block">
  <header class="block__head reveal"><h2>Filing calendar</h2>
    <p>SEC filing dates over the last twelve months, and the next one we expect.</p></header>
  <div class="cal-wrap reveal">{timeline_svg(tickers, data, today)}</div>
  <ul class="legend">
    <li><span class="legend__k"></span>10-K</li><li><span class="legend__q"></span>10-Q</li>
    <li><span class="legend__next"></span>Next filing, estimated one quarter after the last</li>
  </ul>
</section>"""
    return layout("FilingMind · Portfolio", tickers, None, body, today, generated)


# --------------------------------------------------------------------------- #
# Pàgina d'empresa
# --------------------------------------------------------------------------- #
def render_filing(ticker: str, form: str, record: dict[str, Any] | None, today: date,
                  is_default: bool) -> str:
    """Un filing: capçalera, xifres destacades, segell, índex i apartats."""
    anchor = f"f-{form}"
    default = " data-default" if is_default else ""
    if not record:
        return (f'<article class="filing filing--empty" id="{anchor}"{default}>'
                f'<p class="kicker">{FORM_LABELS[form]} · {form}</p>'
                '<p class="empty">No brief yet. It will be generated on the next run.</p>'
                "</article>")

    unverified = record.get("unverified") or []
    flagged = [u["token"] for u in unverified]
    lede, chapters = parse_summary(record.get("summary") or "")
    dek, notes = lede_parts(ticker, lede)
    day = parse_date(record.get("filed"))
    url = esc(record.get("url", ""))

    kpis = extract_kpis(chapters)
    kpi_html = ""
    if kpis:
        items = []
        for i, kpi in enumerate(kpis):
            delta = (f'<span class="delta delta--{kpi.direction}">{esc(kpi.delta)}</span>'
                     if kpi.delta else "")
            items.append(f'<div class="kpi" style="--i:{i}"><dt>{esc(kpi.label)}</dt>'
                         f'<dd><span class="kpi__value">{esc(kpi.value)}</span>{delta}</dd></div>')
        kpi_html = f'<dl class="kpis">{"".join(items)}</dl>'

    total, flagged_count = verification(record)
    if flagged_count:
        seal = (f'<div class="seal seal--flag"><span class="seal__big">{flagged_count}</span>'
                f'<span class="seal__text">of {total} {plural(total, "figure")} not found in the filing</span></div>')
    else:
        seal = (f'<div class="seal"><span class="seal__big">{total}/{total}</span>'
                f'<span class="seal__text">{plural(total, "figure")} checked against the filing</span></div>')

    audit = ""
    if unverified:
        items = "".join(f"<li><strong>{esc(u['token'])}</strong><span>…{esc(u['context'])}…</span></li>"
                        for u in unverified)
        audit = ('<aside class="audit"><h3>Audit note</h3>'
                 "<p>These figures could not be found in the original filing. Double-check them before relying on them.</p>"
                 f"<ul>{items}</ul></aside>")

    toc = "".join(f'<li><a href="#{anchor}-c{i}"><span>{i:02d}</span>{esc(c.label)}</a></li>'
                  for i, c in enumerate(chapters, 1))
    sections = []
    for i, chapter in enumerate(chapters, 1):
        sections.append(
            f'<section class="chapter chapter--{chapter.kind} reveal" id="{anchor}-c{i}">'
            f'<h3><span class="chapter__n">{i:02d}</span>{esc(chapter.title)}</h3>'
            f"{render_lines(chapter.lines, flagged)}</section>"
        )
    notes_html = render_lines(notes, flagged)

    return f"""<article class="filing" id="{anchor}"{default} aria-label="{form}">
  <header class="filing__head">
    <p class="kicker">{FORM_LABELS[form]} · {form}</p>
    <h2 class="filing__dek">{inline(dek) if dek else form}</h2>
    <p class="filing__meta">Filed {fmt_date(day)} <span class="dot">·</span> {fmt_age(day, today)}
      <span class="dot">·</span> <a href="{url}" target="_blank" rel="noopener">Original filing on EDGAR&nbsp;↗</a></p>
  </header>
  {kpi_html}
  <div class="filing__body">
    <div class="rail">
      {seal}
      <nav class="toc" aria-label="{form} sections"><ol>{toc}</ol></nav>
      {audit}
    </div>
    <div class="prose">{notes_html}{"".join(sections)}</div>
  </div>
</article>"""


def render_company(ticker: str, tickers: list[str], data: dict[str, Any], today: date,
                   generated: str) -> str:
    """Pàgina d'una empresa amb el 10-K i el 10-Q (per defecte, el més recent)."""
    records = data.get(ticker, {})
    name = company_name(ticker, [r for r in records.values() if r])
    latest = latest_record(records)
    default_form = latest["form_type"] if latest and latest.get("form_type") in FORM_TYPES else "10-K"

    switch = "".join(
        f'<button type="button" role="tab" data-target="f-{form}" '
        f'aria-selected="{"true" if form == default_form else "false"}">'
        f'<span class="switch__form">{form}</span>'
        f'<span class="switch__date">{fmt_date_short(parse_date(records[form].get("filed"))) if records.get(form) else "pending"}</span>'
        "</button>"
        for form in FORM_TYPES
    )
    filings = "".join(render_filing(ticker, form, records.get(form), today, form == default_form)
                      for form in FORM_TYPES)

    index = tickers.index(ticker)
    prev_t, next_t = tickers[index - 1], tickers[(index + 1) % len(tickers)]
    pager = ""
    if len(tickers) > 1:
        pager = (f'<nav class="pager" aria-label="Other companies">'
                 f'<a href="{page_name(prev_t)}" rel="prev"><small>Previous</small>{esc(prev_t)}</a>'
                 f'<a href="{page_name(next_t)}" rel="next"><small>Next</small>{esc(next_t)}</a></nav>')

    hero_ticker = (f'<h1 class="hero__ticker" style="view-transition-name:{vt_name(ticker)}">'
                   f"{esc(ticker)}</h1>")
    crumb = f'<p class="kicker"><a href="index.html">Portfolio</a> / {esc(ticker)}</p>'
    if not latest:
        body = (f'<section class="hero">{crumb}{hero_ticker}</section>'
                '<p class="empty">There is no brief for this company yet. '
                "It will be generated on the next run.</p>" + pager)
        return layout(f"{ticker} · FilingMind", tickers, ticker, body, today, generated)

    body = f"""<section class="hero">
  {crumb}
  {hero_ticker}
  {f'<p class="hero__name">{esc(name)}</p>' if name else ""}
  <div class="switch" role="tablist" aria-label="Filing">{switch}</div>
</section>
{filings}
{pager}"""
    title = f"{ticker} · {name} · FilingMind" if name else f"{ticker} · FilingMind"
    return layout(title, tickers, ticker, body, today, generated)


# --------------------------------------------------------------------------- #
# Generació
# --------------------------------------------------------------------------- #
def build(output: Path = DEFAULT_OUTPUT, now: datetime | None = None) -> list[Path]:
    """Genera totes les pàgines a `output` i en retorna les rutes."""
    tickers: list[str] = []
    for raw in load_json(PORTFOLIO_FILE, []):
        ticker = str(raw).strip().upper()
        if ticker and ticker not in tickers:
            tickers.append(ticker)
    data = load_json(DATA_FILE, {})
    now = now or datetime.now(timezone.utc)
    today = now.date()
    generated = f"{fmt_date(today)} at {now:%H:%M} UTC"

    # Es buida el directori perquè no quedin pàgines d'empreses que ja no hi són.
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    shutil.copytree(ASSETS_DIR, output / "assets")

    pages = {output / "index.html": render_index(tickers, data, today, generated)}
    for ticker in tickers:
        pages[output / page_name(ticker)] = render_company(ticker, tickers, data, today, generated)
    for path, content in pages.items():
        path.write_text(content, encoding="utf-8")
    return list(pages)


if __name__ == "__main__":
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    written = build(out_dir)
    print(f"Dashboard generat: {len(written)} pàgines a {out_dir}")
