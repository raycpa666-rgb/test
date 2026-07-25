# Colorado River / Lake Mead hydrology news agent

Searches the day's news for **"Colorado River"** and **"Lake Mead"**, keeps only the
articles that are genuinely about hydrology, picks the best 5 per keyword, writes a
short summary, and drafts (or sends) an email with every link.

```
search → assess relevance → select top 5 → summarize → draft/send email
```

## Quick start

```bash
pip install -r requirements.txt          # only dependency: anthropic
cp .env.example .env                     # fill in your keys
set -a; source .env; set +a

python -m news_agent                     # drafts digest.txt + digest.html
python -m news_agent --send              # ...and emails it
```

Nothing is emailed unless you pass `--send`. Without it the agent writes
`digest.txt` and `digest.html` and prints the digest to stdout, so you can read the
draft before anything leaves your machine.

## How each step works

| Step | What happens |
| --- | --- |
| **1. Search** | Google News RSS, exact-phrase, time-boxed with `when:1d`. No API key needed. Results are de-duplicated by URL and by headline+publisher. |
| **2. Assess** | Claude (`claude-opus-5`) judges each headline+snippet against a hydrology definition and returns `is_hydrology`, a 0–1 confidence, a topic label, and a one-line reason. Policy and litigation stories count when they concern water supply, allocation, or reservoir operations; a casino near Lake Mead or a boat crash does not. |
| **3. Select** | Top 5 hydrology articles per keyword by confidence, then recency. If fewer than 5 clear the confidence bar, the next-best fill the slots so a thin news day still yields 5. |
| **4. Summarize** | One Claude call writes an overall summary, a per-keyword summary, and a one-clause takeaway per article — grounded only in the snippets, with no invented numbers. |
| **5. Email** | Plain-text + HTML multipart message with every article's title, publisher, date, takeaway, and link. Sent over SMTP with STARTTLS (or implicit TLS via `SMTP_SSL=true`). |

**No API key? It still works.** Steps 2 and 4 fall back to a weighted hydrology-keyword
scorer and template summaries, and the digest says so in its footer. Useful for testing,
and it means a Claude outage degrades the digest instead of breaking it.

## Options

```
-k, --keyword KEYWORD      Search keyword; repeatable (default: Colorado River, Lake Mead)
-n, --per-keyword N        Articles to select per keyword (default: 5)
    --window 1d            How far back to search: 1d, 2d, 7d ...
    --max-candidates N     Max search results assessed per keyword (default: 30)
    --articles-file PATH   Read candidates from JSON instead of searching the web
    --model ID             Claude model id (default: claude-opus-5)
    --no-llm               Skip Claude; use keyword scoring + template summaries
    --no-resolve-links     Keep news.google.com redirects instead of publisher URLs
-o, --out PATH             Where to write the draft (default: digest.txt)
    --send                 Actually send the email
-q, --quiet                Only log warnings and errors
```

Examples:

```bash
# Weekly digest, 8 articles per keyword, extra keywords
python -m news_agent --window 7d -n 8 \
  -k "Colorado River" -k "Lake Mead" -k "Lake Powell snowpack"

# Preview without touching the network or Claude
python -m news_agent --no-llm --articles-file candidates.json
```

### `--articles-file` format

For plugging in a different search backend (NewsAPI, an internal feed) or running
where outbound RSS is blocked. A JSON list — `title` and `url` are required, the rest
optional. Entries without a `keyword` are matched to one by phrase.

```json
[
  {
    "keyword": "Lake Mead",
    "title": "Lake Mead Water Levels Set to Break Record Low",
    "url": "https://www.newsweek.com/lake-mead-water-levels-break-record-low-12078699",
    "source": "Newsweek",
    "published": "2026-07-21T11:00:00Z",
    "snippet": "Lake Mead is projected to fall to 1,035.86 feet in November..."
  }
]
```

## Running it daily

A GitHub Actions workflow is included at `.github/workflows/daily-digest.yml`; it runs
at 13:00 UTC each morning and needs these repository secrets: `ANTHROPIC_API_KEY`,
`SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO`.

Locally, cron works just as well:

```cron
0 6 * * *  cd /path/to/repo && . .env && /usr/bin/python3 -m news_agent --send --quiet
```

## Tests

No network, no API key:

```bash
python -m unittest discover -s tests -v
```

## Limitations

- Assessment reads **headlines and snippets only** — it does not fetch article text, so a
  story whose hydrology angle is buried below the fold can be missed. Raising
  `--max-candidates` helps more than lowering the confidence bar.
- Google News RSS returns roughly the last 100 results per query and rate-limits heavy
  polling; once a day per keyword is comfortably within that.
- Publisher-link resolution is best-effort. When Google's interstitial can't be
  unwrapped, the `news.google.com` link is kept — it still opens correctly in a browser.
- Summaries are grounded in snippets, not full articles. Treat them as triage, and open
  the links before citing a number.
