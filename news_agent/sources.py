"""Step 1: search the day's news for a keyword.

Uses the Google News RSS search endpoint, which needs no API key. Only the
standard library is required so the agent can run anywhere Python 3.10+ is
installed.
"""

from __future__ import annotations

import html
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from .models import Article

log = logging.getLogger(__name__)

RSS_ENDPOINT = "https://news.google.com/rss/search"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
TAG_RE = re.compile(r"<[^>]+>")
# Google News wraps publisher links; these attributes carry the real destination.
PUBLISHER_URL_RE = re.compile(r'data-n-au="(https?://[^"]+)"')
ANCHOR_URL_RE = re.compile(r'<a[^>]+href="(https?://(?!(?:\w+\.)*google\.com)[^"]+)"')


def build_query_url(keyword: str, window: str = "1d", lang: str = "en-US", country: str = "US") -> str:
    """Build the Google News RSS URL for an exact-phrase, time-boxed search."""
    query = f'"{keyword}" when:{window}'
    params = urllib.parse.urlencode(
        {"q": query, "hl": lang, "gl": country, "ceid": f"{country}:{lang.split('-')[0]}"}
    )
    return f"{RSS_ENDPOINT}?{params}"


def _get(url: str, timeout: float = 20.0) -> tuple[str, bytes]:
    """Fetch a URL, returning the final (post-redirect) URL and the body."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.geturl(), response.read()


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", text or ""))).strip()


def parse_feed(xml_bytes: bytes, keyword: str) -> list[Article]:
    """Parse a Google News RSS document into Articles."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        log.warning("Could not parse feed for %r: %s", keyword, exc)
        return []

    articles: list[Article] = []
    for item in root.iterfind(".//item"):
        title = _clean(item.findtext("title", default=""))
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue

        published = None
        raw_date = item.findtext("pubDate")
        if raw_date:
            try:
                published = parsedate_to_datetime(raw_date).astimezone(timezone.utc)
            except (TypeError, ValueError):
                published = None

        source = _clean(item.findtext("source", default=""))
        snippet = _clean(item.findtext("description", default=""))
        # Google's description repeats the headline; drop that prefix if present.
        if snippet.startswith(title):
            snippet = snippet[len(title) :].strip(" -–—")
        # The trailing " - Publisher" suffix on titles is noise for scoring.
        if source and title.endswith(f"- {source}"):
            title = title[: -len(f"- {source}")].strip()

        articles.append(
            Article(
                title=title,
                url=link,
                source=source,
                published=published,
                snippet=snippet,
                keyword=keyword,
            )
        )
    return articles


def search(keyword: str, window: str = "1d", limit: int = 30, timeout: float = 20.0) -> list[Article]:
    """Return up to `limit` articles published in the given window for `keyword`."""
    url = build_query_url(keyword, window=window)
    log.info("Searching news for %r (window=%s)", keyword, window)
    try:
        _, body = _get(url, timeout=timeout)
    except (urllib.error.URLError, TimeoutError) as exc:
        log.error("News search failed for %r: %s", keyword, exc)
        return []
    return parse_feed(body, keyword)[:limit]


def resolve_url(url: str, timeout: float = 10.0) -> str:
    """Best-effort: turn a news.google.com redirect into the publisher's URL.

    Google serves an interstitial for RSS article links. We follow redirects and,
    if we still land on Google, look for the publisher URL embedded in the page.
    The original link stays as the fallback — it resolves fine in a browser.
    """
    if "news.google.com" not in urllib.parse.urlparse(url).netloc:
        return url
    try:
        final_url, body = _get(url, timeout=timeout)
    except (urllib.error.URLError, TimeoutError) as exc:
        log.debug("Could not resolve %s: %s", url, exc)
        return url

    if "google.com" not in urllib.parse.urlparse(final_url).netloc:
        return final_url

    page = body.decode("utf-8", errors="replace")
    for pattern in (PUBLISHER_URL_RE, ANCHOR_URL_RE):
        match = pattern.search(page)
        if match:
            return html.unescape(match.group(1))
    return url


def load_json_file(path: str) -> list[Article]:
    """Load candidate articles from a JSON file instead of searching.

    Accepts either a bare list or `{"articles": [...]}`. Each entry needs
    `title` and `url`; `source`, `published` (ISO 8601), `snippet`, and
    `keyword` are optional. Use this to plug in another search backend
    (NewsAPI, an internal feed) or to run where RSS is unreachable.
    """
    import json

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = payload.get("articles", []) if isinstance(payload, dict) else payload

    articles: list[Article] = []
    for entry in entries:
        title = _clean(str(entry.get("title", "")))
        url = str(entry.get("url", "")).strip()
        if not title or not url:
            log.warning("Skipping article with no title or url: %r", entry)
            continue

        published = None
        raw_date = entry.get("published")
        if raw_date:
            try:
                published = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except ValueError:
                log.warning("Unparseable date %r on %r", raw_date, title)

        articles.append(
            Article(
                title=title,
                url=url,
                source=_clean(str(entry.get("source", ""))),
                published=published,
                snippet=_clean(str(entry.get("snippet", ""))),
                keyword=str(entry.get("keyword", "")).strip(),
            )
        )
    log.info("Loaded %d candidate articles from %s", len(articles), path)
    return articles


def title_key(title: str) -> str:
    """Normalized headline, for matching the same story across publishers."""
    return re.sub(r"[^a-z0-9]+", "", title.lower())


def dedupe(articles: list[Article]) -> list[Article]:
    """Drop repeats of the same story.

    Keys on the URL and on the normalized headline alone — deliberately *not*
    headline+publisher. Wire stories are republished verbatim under many
    mastheads, so including the publisher let the same CNN piece appear again
    under WLKY. Two genuinely different articles sharing a headline character
    for character are rare enough that collapsing them is the better trade.
    """
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    unique: list[Article] = []
    for article in articles:
        key = title_key(article.title)
        if article.url in seen_urls or (key and key in seen_titles):
            continue
        seen_urls.add(article.url)
        seen_titles.add(key)
        unique.append(article)
    return unique


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
