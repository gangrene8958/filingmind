# FilingMind

Audita automàticament els nous filings **10-K** i **10-Q** de la SEC per a una cartera d'accions, en genera un resum executiu amb Claude (API d'Anthropic) i l'envia per Telegram. S'executa cada dia laborable amb GitHub Actions.

## Com funciona

1. `main.py` llegeix els tickers de `portfolio.json`.
2. Consulta el feed Atom d'EDGAR de cada ticker per a 10-K i 10-Q.
3. Si troba un filing que no és a `processed_filings.json`, en descarrega el document principal, el neteja (taules convertides a Markdown) i n'extreu les seccions *Risk Factors* i *MD&A*.
4. Claude en genera un resum: evolució del negoci, 3 riscos principals, canvis estratègics i alertes financeres.
5. El resum s'envia a Telegram (dividit en blocs de 4.096 caràcters si cal) i el filing es marca com a processat.
6. El workflow fa commit de `processed_filings.json` perquè l'estat es mantingui entre execucions.

Si un filing falla (xarxa, API, Telegram), no es marca com a processat i es torna a intentar a la següent execució.

## Configuració

### 1. Crear el bot de Telegram

1. Parla amb [@BotFather](https://t.me/BotFather), envia `/newbot` i desa el **token**.
2. Envia qualsevol missatge al teu bot.
3. Obre `https://api.telegram.org/bot<TOKEN>/getUpdates` i copia el valor de `chat.id`: és el **chat ID**.

### 2. Configurar els GitHub Secrets

Al repositori: **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Descripció |
|---|---|
| `ANTHROPIC_API_KEY` | Clau de l'API d'Anthropic ([console.anthropic.com](https://console.anthropic.com)). |
| `TELEGRAM_BOT_TOKEN` | Token del bot que t'ha donat BotFather. |
| `TELEGRAM_CHAT_ID` | ID del xat on s'enviaran els resums. |
| `SEC_USER_AGENT` | Identificació obligatòria per a la SEC, amb nom i correu: `Nom Cognom correu@exemple.com`. Sense això la SEC retorna 403. |

Opcional, a la pestanya **Variables** (no secrets):

| Variable | Descripció |
|---|---|
| `ANTHROPIC_MODEL` | Model de Claude. Per defecte `claude-sonnet-5-5`. |

### 3. Permisos del workflow

El workflow ja declara `permissions: contents: write`. Si el repositori o l'organització restringeixen els permisos per defecte, comprova a **Settings → Actions → General → Workflow permissions** que estigui marcat *Read and write permissions*.

### 4. Cartera

Edita `portfolio.json`:

```json
["AAPL", "MSFT", "GOOGL", "NVDA"]
```

## Execució

- **Automàtica:** de dilluns a divendres a les 22:00 UTC.
- **Manual:** pestanya **Actions → SEC Filings Monitor → Run workflow**.
- **En local:**

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... SEC_USER_AGENT="Nom correu@exemple.com"
python main.py
```

## Notes

- A cada execució només es revisa el filing més recent de cada ticker i tipus (variable `MAX_ENTRIES_PER_FEED`, per defecte 1). Així, la primera execució envia un resum de l'últim 10-K i l'últim 10-Q de cada empresa, no de tot l'historial.
- El model per defecte fa servir raonament adaptatiu (*adaptive thinking*) en lloc de fixar la temperatura, ja que els models actuals no admeten temperatures personalitzades amb el raonament activat.
- Cada filing és una crida a l'API amb fins a 250.000 caràcters de text; tingues-ho en compte pel cost si la cartera és gran.
