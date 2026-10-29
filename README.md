# FilingMind

**LLM summaries of SEC filings that check their own numbers.**

FilingMind watches a stock portfolio for new **10-K** and **10-Q** filings on SEC EDGAR, has Claude (Anthropic API) write an executive summary of each one, verifies every figure in that summary against the original filing, and delivers it to Telegram and a static web dashboard. It runs every weekday on GitHub Actions.

*Versió en català: [README.ca.md](README.ca.md).*

## Why it exists

LLMs are good at condensing a 100-page filing into one page, but they can misquote numbers, and in a financial summary a wrong number is worse than no summary. FilingMind treats the model's output as untrusted: a deterministic checker (no LLM involved) confirms that every figure the model cites actually appears in the source document before the summary goes out.

## Results so far

- The portfolio tracks AAPL, MSFT and AMZN. The latest 10-K and 10-Q for each, 6 filings in all, have been summarized ([`data/filings.json`](data/filings.json)).
- Each summary cites dozens of figures (revenue by segment and region, margins, operating expenses, tax rates, and so on).
- **None of the 6 summaries ended with an unverified figure** after the checker and its single correction pass.
- 30 unit tests cover the number checker, the dashboard builder and the main processing loop. They run in CI before any filing is processed.

## How it works

```
portfolio.json ─► EDGAR Atom feeds ─► new 10-K / 10-Q?
                                          │
                                          ▼
                     download main document, clean HTML,
                     convert tables to Markdown, extract
                     Item 1A (Risk Factors) + MD&A
                                          │
                                          ▼
                     Claude writes executive summary
                                          │
                                          ▼
                     verify_numbers.py: every figure in the
                     summary must appear in the filing ──fail──► ask Claude to correct (once)
                                          │                               │
                                          ▼                               ▼
                     Telegram message + static dashboard   still failing → send with a
                     + processed_filings.json committed     "⚠️ Unverified figures" warning
```

1. `main.py` reads the tickers from `portfolio.json`.
2. It polls the EDGAR Atom feed for each ticker's 10-K and 10-Q filings.
3. For each filing not yet listed in `processed_filings.json`, it downloads the main document, cleans it (tables become Markdown) and extracts the *Risk Factors* and *MD&A* sections.
4. Claude writes a summary covering business performance, the three main risks, strategic changes and financial red flags.
5. The summary is sent to Telegram (split into 4,096-character chunks if needed) and the filing is marked as processed.
6. The workflow commits `processed_filings.json` so state persists between runs. If anything fails (network, API, Telegram), the filing is not marked and is retried on the next run.

### Number verification

`verify_numbers.py` checks that every figure in the summary appears in the source document. It tolerates rounding and changes of scale: "$391.0 billion" passes if the filing says "391,035" (in millions). Years, small counters ("3 risks") and references such as "10-K" or "Item 1A" are ignored.

- If a figure is missing from the document, Claude is asked to correct the summary, once.
- If any figure is still unverified after that, the summary is sent anyway with a "⚠️ Unverified figures" warning that lists them.

Known limitations: figures the model computes itself are flagged as unverified (the prompt forbids it from computing them), and small numbers such as percentages almost always find some match in a long document. The check is therefore strongest for monetary amounts.

## Dashboard

Every run also builds a small static site with `build_site.py`:

- **Portfolio:** the latest 10-K and 10-Q for each company, a headline figure and its verification status, the latest watch points, and a filing calendar with the next estimated filing date.
- **One page per company:** 10-K and 10-Q (with a selector), key figures, the verification badge, a section index and a link to the filing on EDGAR. Unverified figures are highlighted in red.

The design lives in `site_assets/` (light/dark mode, page transitions, reduced-motion support) and the pages also work without JavaScript. Adding or removing a ticker in `portfolio.json` triggers the workflow and adds or removes its page.

To view it, either download the **dashboard** artifact from any run under the **Actions** tab, or enable GitHub Pages (**Settings → Pages → Source: GitHub Actions**) to publish it at `https://<user>.github.io/filingmind/`.

## Tech stack

Python 3.11 · Anthropic API (Claude, adaptive thinking) · feedparser · requests · BeautifulSoup · pytest · GitHub Actions · GitHub Pages · Telegram Bot API

## Setup

### 1. Create a Telegram bot

1. Message [@BotFather](https://t.me/BotFather), send `/newbot` and save the **token**.
2. Send any message to your bot.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the `chat.id` value. That is the **chat ID**.

### 2. Add GitHub secrets

**Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key ([console.anthropic.com](https://console.anthropic.com)). |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather. |
| `TELEGRAM_CHAT_ID` | Chat that receives the summaries. |
| `SEC_USER_AGENT` | Identification the SEC requires, with name and email: `First Last email@example.com`. Without it the SEC returns 403. |

Optional, under the **Variables** tab:

| Variable | Description |
|---|---|
| `ANTHROPIC_MODEL` | Claude model. Defaults to `claude-sonnet-5-5`. |
| `PUBLISH_PAGES` | `false` to skip publishing the dashboard to GitHub Pages (published by default). |
| `ANTHROPIC_WORKSPACE_ID` | Only needed if the API key is not tied to a workspace (error 400 "not scoped to a workspace"). |

### 3. Workflow permissions

The workflow declares `permissions: contents: write`. If your repository or organization restricts default permissions, check **Settings → Actions → General → Workflow permissions** is set to *Read and write permissions*.

### 4. Portfolio

Edit `portfolio.json`:

```json
["AAPL", "MSFT", "GOOGL", "NVDA"]
```

## Running

- **Scheduled:** Monday to Friday at 22:00 UTC.
- **Manual:** **Actions → SEC Filings Monitor → Run workflow**.
- **Locally:**

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... SEC_USER_AGENT="Name email@example.com"
python main.py
pytest tests
```

## Notes

- Each run only checks the most recent filing per ticker and form type (`MAX_ENTRIES_PER_FEED`, default 1), so the first run summarizes the latest 10-K and 10-Q of each company, not the full history.
- The default model uses adaptive thinking instead of a fixed temperature.
- Each filing is one API call with up to 250,000 characters of text, so keep cost in mind for large portfolios.
