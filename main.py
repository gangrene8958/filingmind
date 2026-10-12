"""FilingMind: auditoria automàtica de filings SEC (10-K i 10-Q).

Flux d'execució:
    1. Llegeix la cartera (`portfolio.json`) i l'estat (`processed_filings.json`).
    2. Consulta el feed Atom d'EDGAR per a cada ticker i tipus de formulari.
    3. Per a cada filing nou, descarrega el document principal, el neteja i
       n'extreu les seccions Item 1A (Risk Factors) i MD&A.
    4. Genera un resum executiu amb l'API d'Anthropic.
    5. Envia el resum per Telegram i desa l'estat actualitzat.

Variables d'entorn:
    ANTHROPIC_API_KEY      Clau de l'API d'Anthropic (obligatòria).
    TELEGRAM_BOT_TOKEN     Token del bot de Telegram (obligatòria).
    TELEGRAM_CHAT_ID       Xat de destinació (obligatòria).
    SEC_USER_AGENT         Capçalera User-Agent per a la SEC, p. ex.
                           "Nom Cognom correu@exemple.com" (obligatòria).
    ANTHROPIC_MODEL        Model a utilitzar (opcional, per defecte claude-sonnet-5-5).
    ANTHROPIC_WORKSPACE_ID ID del workspace (opcional; només cal si la clau de l'API
                           no està associada a cap workspace).
    MAX_ENTRIES_PER_FEED   Filings més recents a revisar per feed (opcional, per defecte 1).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import anthropic
import feedparser
import requests
from bs4 import BeautifulSoup, Comment

# ---------------------------------------------------------------------------
# Configuració
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
PORTFOLIO_FILE = BASE_DIR / "portfolio.json"
STATE_FILE = BASE_DIR / "processed_filings.json"

SEC_FEED_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
    "&CIK={ticker}&type={form_type}&dateb=&owner=include&count=10&output=atom"
)
SEC_BASE_URL = "https://www.sec.gov"
FORM_TYPES = ("10-K", "10-Q")

DEFAULT_MODEL = "claude-sonnet-5-5"
# Models que admeten el paràmetre de fallback del servidor ("default").
FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}

MAX_TEXT_CHARS = 250_000
TELEGRAM_MAX_CHARS = 4096
REQUEST_TIMEOUT = 30
SEC_REQUEST_DELAY = 0.2  # La SEC limita a 10 peticions/segon.

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("filingmind")

SYSTEM_PROMPT = """Ets un analista financer sènior especialitzat en l'anàlisi de \
documents reglamentaris de la SEC (10-K i 10-Q). Rebràs el text extret d'un \
filing, principalment les seccions de Risk Factors i Management's Discussion \
and Analysis (MD&A).

Redacta un resum executiu en català, concís i basat exclusivament en el \
document, amb aquesta estructura:

1. Evolució del negoci i dels ingressos: xifres clau, creixement i segments.
2. Tres riscos principals: els més rellevants o nous respecte a períodes anteriors.
3. Canvis estratègics: adquisicions, reestructuracions, nous productes o mercats.
4. Alertes financeres (red flags): deteriorament de marges, liquiditat, deute, \
litigis, canvis comptables, advertències de going concern o qualsevol altre senyal \
preocupant. Si no n'hi ha cap de rellevant, digues-ho explícitament.

Fes servir text pla amb guions per a les llistes (sense taules ni Markdown \
complex), perquè el resum es llegirà a Telegram. No inventis dades que no \
apareguin al document."""


class ConfigError(Exception):
    """Error de configuració (variables d'entorn o fitxers absents)."""


# ---------------------------------------------------------------------------
# Gestió d'estat i cartera
# ---------------------------------------------------------------------------

def get_env(name: str) -> str:
    """Retorna una variable d'entorn obligatòria o llança ConfigError."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Falta la variable d'entorn obligatòria {name}")
    return value


def load_json(path: Path, default: Any) -> Any:
    """Carrega un fitxer JSON; si no existeix o està buit retorna `default`."""
    try:
        text = path.read_text(encoding="utf-8").strip()
        return json.loads(text) if text else default
    except FileNotFoundError:
        log.warning("No s'ha trobat %s; s'utilitza el valor per defecte", path.name)
        return default
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path.name} no és un JSON vàlid: {exc}") from exc


def load_portfolio() -> list[str]:
    """Llegeix la llista de tickers de `portfolio.json`."""
    data = load_json(PORTFOLIO_FILE, [])
    if not isinstance(data, list) or not all(isinstance(t, str) for t in data):
        raise ConfigError("portfolio.json ha de ser una llista de tickers, p. ex. [\"AAPL\"]")
    return [t.strip().upper() for t in data if t.strip()]


def load_state() -> dict[str, list[str]]:
    """Llegeix l'estat de filings processats de `processed_filings.json`."""
    data = load_json(STATE_FILE, {"processed": []})
    if not isinstance(data, dict) or not isinstance(data.get("processed"), list):
        raise ConfigError("processed_filings.json ha de tenir el format {\"processed\": []}")
    return data


def save_state(state: dict[str, list[str]]) -> None:
    """Desa l'estat de forma atòmica per evitar fitxers corruptes."""
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(STATE_FILE)


# ---------------------------------------------------------------------------
# Detecció de filings (SEC EDGAR)
# ---------------------------------------------------------------------------

def sec_session(user_agent: str) -> requests.Session:
    """Crea una sessió HTTP amb la capçalera User-Agent obligatòria de la SEC."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": user_agent,
        "Accept-Encoding": "gzip, deflate",
    })
    return session


def sec_get(session: requests.Session, url: str) -> requests.Response | None:
    """Fa un GET a la SEC respectant el límit de peticions. Retorna None si falla."""
    time.sleep(SEC_REQUEST_DELAY)
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        log.error("Error descarregant %s: %s", url, exc)
        return None


def fetch_filings(session: requests.Session, ticker: str, form_type: str,
                  max_entries: int) -> list[dict[str, str]]:
    """Consulta el feed Atom d'EDGAR i retorna els filings més recents.

    Cada filing és un diccionari amb `id`, `ticker`, `form_type`, `title`,
    `updated` i `index_url`. Les esmenes (p. ex. 10-K/A) es descarten.
    """
    url = SEC_FEED_URL.format(ticker=ticker, form_type=form_type)
    response = sec_get(session, url)
    if response is None:
        return []

    try:
        feed = feedparser.parse(response.content)
    except Exception as exc:  # feedparser és tolerant, però per si de cas.
        log.error("No s'ha pogut parsejar el feed de %s %s: %s", ticker, form_type, exc)
        return []

    if feed.bozo and not feed.entries:
        log.warning("Feed buit o invàlid per a %s %s (%s)", ticker, form_type,
                    getattr(feed, "bozo_exception", "desconegut"))
        return []

    filings = []
    for entry in feed.entries:
        terms = {t.get("term", "") for t in entry.get("tags", [])}
        if form_type not in terms:
            continue
        filing_id = entry.get("id") or entry.get("link")
        if not filing_id:
            continue
        filings.append({
            "id": filing_id,
            "ticker": ticker,
            "form_type": form_type,
            "title": entry.get("title", ""),
            "updated": entry.get("updated", ""),
            "index_url": entry.get("link", ""),
        })
        if len(filings) >= max_entries:
            break
    return filings


def find_primary_document(session: requests.Session, index_url: str,
                          form_type: str) -> str | None:
    """Troba l'URL del document principal (HTML) a la pàgina índex del filing."""
    response = sec_get(session, index_url)
    if response is None:
        return None

    try:
        soup = BeautifulSoup(response.content, "lxml")
        table = soup.find("table", class_="tableFile")
        if table is None:
            log.error("No s'ha trobat la taula de documents a %s", index_url)
            return None
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 4 or cells[3].get_text(strip=True) != form_type:
                continue
            link = cells[2].find("a")
            if link and link.get("href"):
                # Els documents iXBRL s'enllacen com /ix?doc=/Archives/...
                href = link["href"].replace("/ix?doc=", "")
                return urljoin(SEC_BASE_URL, href)
    except Exception as exc:
        log.error("Error parsejant l'índex %s: %s", index_url, exc)
    return None


# ---------------------------------------------------------------------------
# Neteja i preprocessat d'HTML
# ---------------------------------------------------------------------------

REMOVABLE_TAGS = ("script", "style", "head", "meta", "svg", "img", "ix:header", "ix:hidden")

ITEM_PATTERNS = {
    "10-K": [
        ("Item 1A. Risk Factors",
         r"item\s*1a\.?\s*[\-–—:]?\s*risk\s+factors",
         r"item\s*1b\.?|item\s*1c\.?|item\s*2\.?\s*[\-–—:]?\s*properties"),
        ("Item 7. Management's Discussion and Analysis",
         r"item\s*7\.?\s*[\-–—:]?\s*management[’']?s\s+discussion",
         r"item\s*7a\.?|item\s*8\.?\s*[\-–—:]?\s*financial\s+statements"),
    ],
    "10-Q": [
        ("Item 2. Management's Discussion and Analysis",
         r"item\s*2\.?\s*[\-–—:]?\s*management[’']?s\s+discussion",
         r"item\s*3\.?\s*[\-–—:]?\s*quantitative|item\s*4\.?\s*[\-–—:]?\s*controls"),
        ("Item 1A. Risk Factors",
         r"item\s*1a\.?\s*[\-–—:]?\s*risk\s+factors",
         r"item\s*2\.?\s*[\-–—:]?\s*unregistered|item\s*5\.?\s*[\-–—:]?\s*other|item\s*6\.?\s*[\-–—:]?\s*exhibits"),
    ],
}


def _cell_text(cell) -> str:
    """Text d'una cel·la de taula, sense salts de línia ni barres verticals."""
    text = cell.get_text(" ", strip=True).replace("|", "\\|")
    return re.sub(r"\s+", " ", text)


def table_to_markdown(table) -> str:
    """Converteix una taula HTML a Markdown conservant la relació fila/columna.

    Les cel·les buides (molt habituals en els filings, que les fan servir com a
    separadors) es descarten i les files es reomplen fins a la mateixa amplada.
    """
    rows = []
    for tr in table.find_all("tr"):
        cells: list[str] = []
        pending_prefix = ""
        for cell in tr.find_all(["td", "th"]):
            text = _cell_text(cell)
            if not text:
                continue
            # Els filings posen "$" i "%" / ")" en cel·les pròpies: s'enganxen al número.
            if text in ("$", "€", "£"):
                pending_prefix = text
            elif text in ("%", ")", ")%") and cells:
                cells[-1] += text
            else:
                cells.append(pending_prefix + text)
                pending_prefix = ""
        if cells:
            rows.append(cells)
    if not rows:
        return ""

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n" + "\n".join(lines) + "\n"


def normalize_text(text: str) -> str:
    """Normalitza espais especials i redueix salts de línia consecutius."""
    text = text.replace("\xa0", " ").replace("&nbsp;", " ").replace("​", "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_section(text: str, start_pattern: str, end_pattern: str) -> str:
    """Extreu una secció entre dos patrons.

    Com que l'índex del document també conté els títols dels items, es
    proven totes les coincidències d'inici i es queda la secció més llarga.
    """
    flags = re.IGNORECASE | re.DOTALL
    best = ""
    for start in re.finditer(start_pattern, text, flags):
        end = re.search(end_pattern, text[start.end():], flags)
        stop = start.end() + end.start() if end else len(text)
        candidate = text[start.start():stop].strip()
        if len(candidate) > len(best):
            best = candidate
    # Una "secció" de menys de 500 caràcters és gairebé segur una entrada de l'índex.
    return best if len(best) >= 500 else ""


def clean_sec_html(html: str | bytes, form_type: str = "10-K") -> str:
    """Neteja l'HTML d'un filing i en retorna el text rellevant.

    Retorna prioritàriament les seccions de Risk Factors i MD&A. Si no les
    troba, retorna tot el text net truncat a MAX_TEXT_CHARS caràcters.
    """
    soup = BeautifulSoup(html, "lxml")

    for tag_name in REMOVABLE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    for table in soup.find_all("table"):
        table.replace_with(soup.new_string(table_to_markdown(table)))

    text = normalize_text(soup.get_text("\n"))

    sections = []
    for title, start_pattern, end_pattern in ITEM_PATTERNS.get(form_type, ITEM_PATTERNS["10-K"]):
        section = extract_section(text, start_pattern, end_pattern)
        if section:
            sections.append(f"===== {title} =====\n{section}")
        else:
            log.info("No s'ha trobat la secció '%s'", title)

    result = "\n\n".join(sections) if sections else text
    if len(result) > MAX_TEXT_CHARS:
        log.info("Text truncat de %d a %d caràcters", len(result), MAX_TEXT_CHARS)
        result = result[:MAX_TEXT_CHARS]
    return result


# ---------------------------------------------------------------------------
# Anàlisi amb l'API d'Anthropic
# ---------------------------------------------------------------------------

def summarize_filing(client: anthropic.Anthropic, model: str, filing: dict[str, str],
                     text: str) -> str | None:
    """Genera el resum executiu d'un filing amb Claude. Retorna None si falla."""
    user_content = (
        f"Empresa: {filing['ticker']}\n"
        f"Tipus de filing: {filing['form_type']}\n"
        f"Títol: {filing['title']}\n"
        f"Data: {filing['updated']}\n\n"
        f"<document>\n{text}\n</document>"
    )

    params: dict[str, Any] = {
        "model": model,
        "max_tokens": 16000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content}],
        # Raonament adaptatiu: el model decideix quant pensar abans de respondre.
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
    }
    if model in FALLBACK_MODELS:
        # Si un classificador de seguretat rebutja la petició, el servidor la
        # torna a executar amb el model alternatiu recomanat.
        params["betas"] = ["server-side-fallback-2026-07-01"]
        params["fallbacks"] = "default"

    try:
        with client.beta.messages.stream(**params) as stream:
            message = stream.get_final_message()
    except anthropic.RateLimitError as exc:
        log.error("Límit de peticions d'Anthropic superat: %s", exc)
        return None
    except anthropic.APIStatusError as exc:
        log.error("Error de l'API d'Anthropic (%s): %s", exc.status_code, exc.message)
        return None
    except anthropic.APIConnectionError as exc:
        log.error("No s'ha pogut connectar amb l'API d'Anthropic: %s", exc)
        return None

    if message.stop_reason == "refusal":
        log.error("El model ha rebutjat analitzar %s", filing["id"])
        return None
    if message.stop_reason == "max_tokens":
        log.warning("La resposta s'ha tallat per max_tokens")

    summary = "\n".join(b.text for b in message.content if b.type == "text").strip()
    return summary or None


# ---------------------------------------------------------------------------
# Notificació per Telegram
# ---------------------------------------------------------------------------

def split_message(text: str, limit: int = TELEGRAM_MAX_CHARS) -> list[str]:
    """Divideix un text en blocs de com a màxim `limit` caràcters.

    Talla preferentment per paràgrafs, després per línies i, en últim cas,
    pel límit exacte.
    """
    chunks: list[str] = []
    remaining = text.strip()
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut <= 0:
            cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = remaining.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    """Envia un missatge (dividit en blocs si cal). Retorna True si tot s'envia."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = split_message(text)
    for i, chunk in enumerate(chunks, start=1):
        try:
            response = requests.post(
                url,
                json={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            # No es registra l'URL perquè conté el token del bot.
            status = getattr(exc.response, "status_code", "sense resposta")
            log.error("Error enviant el bloc %d/%d a Telegram (%s)", i, len(chunks), status)
            return False
        if len(chunks) > 1:
            time.sleep(1)  # Evita el límit de missatges per segon de Telegram.
    return True


# ---------------------------------------------------------------------------
# Orquestració
# ---------------------------------------------------------------------------

def process_filing(filing: dict[str, str], session: requests.Session,
                   client: anthropic.Anthropic, model: str,
                   telegram_token: str, telegram_chat_id: str) -> bool:
    """Descarrega, resumeix i notifica un filing. Retorna True si s'ha completat."""
    log.info("Nou filing: %s %s (%s)", filing["ticker"], filing["form_type"], filing["updated"])

    doc_url = find_primary_document(session, filing["index_url"], filing["form_type"])
    if not doc_url:
        log.error("No s'ha trobat el document principal de %s", filing["index_url"])
        return False

    response = sec_get(session, doc_url)
    if response is None:
        return False

    try:
        text = clean_sec_html(response.content, filing["form_type"])
    except Exception as exc:
        log.error("Error netejant l'HTML de %s: %s", doc_url, exc)
        return False
    if not text:
        log.error("El document %s no conté text", doc_url)
        return False

    summary = summarize_filing(client, model, filing, text)
    if not summary:
        return False

    header = (
        f"📄 {filing['ticker']} · {filing['form_type']} · {filing['updated'][:10]}\n"
        f"{doc_url}\n\n"
    )
    return send_telegram(telegram_token, telegram_chat_id, header + summary)


def main() -> int:
    """Punt d'entrada. Retorna el codi de sortida del procés."""
    try:
        sec_user_agent = get_env("SEC_USER_AGENT")
        telegram_token = get_env("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = get_env("TELEGRAM_CHAT_ID")
        get_env("ANTHROPIC_API_KEY")  # El client la llegeix de l'entorn.
        model = os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL
        max_entries = int(os.environ.get("MAX_ENTRIES_PER_FEED", "1"))
        tickers = load_portfolio()
        state = load_state()
    except (ConfigError, ValueError) as exc:
        log.error("Configuració invàlida: %s", exc)
        return 1

    processed = set(state["processed"])
    session = sec_session(sec_user_agent)
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    client = anthropic.Anthropic(
        default_headers={"anthropic-workspace-id": workspace_id} if workspace_id else None,
    )
    errors = 0
    new_count = 0

    log.info("Revisant %d tickers amb el model %s", len(tickers), model)
    for ticker in tickers:
        for form_type in FORM_TYPES:
            for filing in fetch_filings(session, ticker, form_type, max_entries):
                if filing["id"] in processed:
                    continue
                new_count += 1
                if process_filing(filing, session, client, model,
                                  telegram_token, telegram_chat_id):
                    processed.add(filing["id"])
                    state["processed"].append(filing["id"])
                    save_state(state)  # Es desa després de cada èxit.
                else:
                    errors += 1

    save_state(state)
    log.info("Fet: %d filings nous, %d amb errors", new_count, errors)
    # Els filings amb error no es marquen com a processats i es reintenten a la
    # propera execució. El procés acaba amb èxit perquè l'estat es pugui desar.
    return 0


if __name__ == "__main__":
    sys.exit(main())
