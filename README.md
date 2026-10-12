# FilingMind

Audita automàticament els nous filings **10-K** i **10-Q** de la SEC per a una cartera d'accions, en genera un resum executiu amb Claude (API d'Anthropic) i l'envia per Telegram. S'executa cada dia laborable amb GitHub Actions.

## Com funciona

1. `main.py` llegeix els tickers de `portfolio.json`.
2. Consulta el feed Atom d'EDGAR de cada ticker per a 10-K i 10-Q.
3. Si troba un filing que no és a `processed_filings.json`, en descarrega el document principal, el neteja (taules convertides a Markdown) i n'extreu les seccions *Risk Factors* i *MD&A*.
4. Claude en genera un resum: evolució del negoci, 3 riscos principals, canvis estratègics i alertes financeres.
5. El resum s'envia a Telegram (dividit en blocs de 4.096 caràcters si cal) i el filing es marca com a processat.
6. El workflow fa commit de `processed_filings.json` perquè l'estat es mantingui entre execucions.

### Verificació de xifres

Abans d'enviar cada resum, `verify_numbers.py` comprova que totes les xifres del resum apareguin al document original. Accepta arrodoniments i canvis d'escala: per exemple, "$391.0 billion" es dona per bo si el document diu "391,035" milions. S'ignoren els anys, els comptadors petits ("3 riscos") i les referències com "10-K" o "Item 1A".

- Si alguna xifra no surt al document, es demana a Claude que corregeixi el resum, només una vegada.
- Si després de la correcció encara en queda alguna, el resum s'envia igualment però amb un avís "⚠️ Xifres no verificades" que les llista.

La comprovació és determinista, sense cap LLM. Té dues limitacions: marca com a no verificades les xifres calculades pel model (el prompt li prohibeix calcular-ne), i les xifres petites, com els percentatges, gairebé sempre troben alguna coincidència en un document llarg. Per això és especialment útil per a imports.

El workflow executa els tests (`pytest tests`) abans de processar els filings.

Si un filing falla (xarxa, API, Telegram), no es marca com a processat i es torna a intentar a la següent execució.

## Dashboard web

Cada execució genera també un petit web estàtic amb `build_site.py`:

- **Inici:** una targeta per empresa amb la data de l'últim 10-K i 10-Q i l'estat de la verificació de xifres.
- **Una pàgina per empresa:** el resum complet del 10-K i del 10-Q, amb un enllaç al document de la SEC i un avís si hi ha xifres per revisar.

Les pestanyes i les pàgines surten de `portfolio.json`. Si hi afegeixes un ticker, el workflow s'executa automàticament: resumeix els seus filings, els envia per Telegram i afegeix la pàgina. Si en treus un, la seva pàgina desapareix.

Els resums es desen a `data/filings.json`. Els filings que ja s'havien enviat abans que existís el dashboard es resumeixen una vegada per omplir-lo, però no es tornen a enviar per Telegram.

### Veure el dashboard

- **Sense configurar res:** a cada execució, a la pestanya **Actions**, obre l'execució i descarrega l'artefacte **dashboard**. És un zip: descomprimeix-lo i obre `index.html`.
- **Com a web amb URL fixa (GitHub Pages):**
  1. Cal que el repo sigui públic, o tenir GitHub Pro si és privat. En tots dos casos, la pàgina publicada és pública.
  2. Ves a **Settings → Pages** i, a *Source*, tria **GitHub Actions**.
  3. A partir de la propera execució, el dashboard es publica a `https://<usuari>.github.io/filingmind/`.

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
| `PUBLISH_PAGES` | `false` per no publicar el dashboard a GitHub Pages (per defecte es publica). |
| `ANTHROPIC_WORKSPACE_ID` | Només si la clau de l'API no està associada a cap workspace (error 400 "not scoped to a workspace"). És l'ID del workspace (`wrkspc_...`) que surt a la consola d'Anthropic. |

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
