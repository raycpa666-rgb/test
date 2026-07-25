"""The pipeline: search -> assess -> select -> summarize -> draft email."""

from __future__ import annotations

import logging

from .config import Config
from .models import Article, Assessment, Digest, DigestSection
from . import relevance, sources, summarize

log = logging.getLogger(__name__)


def _load_candidates(config: Config) -> dict[str, list[Article]]:
    """Read every candidate article from the JSON file, grouped by keyword.

    Entries with no keyword — or one that isn't being digested — are matched to
    a keyword by phrase, so a hand-assembled file doesn't need the field.
    """
    grouped: dict[str, list[Article]] = {keyword: [] for keyword in config.keywords}
    for article in sources.load_json_file(config.articles_file or ""):
        if article.keyword in grouped:
            grouped[article.keyword].append(article)
            continue
        blob = article.text_blob()
        for keyword in config.keywords:
            if keyword.lower() in blob:
                article.keyword = keyword
                grouped[keyword].append(article)
                break
        else:
            log.warning("Dropping %r: matches none of the keywords", article.title)
    return grouped


def _search_and_assess(config: Config, keyword: str) -> tuple[list[Assessment], bool]:
    """Search and assess, widening the time window until enough articles qualify.

    An exact-phrase search over 24 hours frequently returns fewer than
    `per_keyword` hydrology articles — especially for a narrower phrase like
    "Lake Mead". Rather than ship a short digest, fall back to progressively
    wider windows. Articles already assessed are never re-assessed.
    """
    windows = [config.window] + [w for w in config.fallback_windows if w != config.window]
    seen: set[str] = set()
    assessments: list[Assessment] = []
    llm_used = False

    for position, window in enumerate(windows):
        found = sources.dedupe(
            sources.search(keyword, window=window, limit=config.max_candidates)
        )
        fresh = [article for article in found if article.url not in seen]
        seen.update(article.url for article in fresh)
        log.info("%r: %d candidates in %s window (%d new)", keyword, len(found), window, len(fresh))

        if fresh:
            new, used = relevance.assess(fresh, model=config.model, use_llm=config.use_llm)
            llm_used = llm_used or used
            assessments.extend(new)

        qualifying = sum(1 for a in assessments if a.is_hydrology)
        log.info("%r: %d hydrology-related so far", keyword, qualifying)
        if qualifying >= config.per_keyword or position == len(windows) - 1:
            break
        log.info("%r: fewer than %d found; widening the search window", keyword, config.per_keyword)

    return assessments, llm_used


def run(config: Config) -> Digest:
    """Build today's digest for every configured keyword."""
    sections: list[DigestSection] = []
    llm_used = False
    from_file = _load_candidates(config) if config.articles_file else None

    for keyword in config.keywords:
        # 1. Gather the day's news and 2. assess hydrology relevance.
        if from_file is not None:
            articles = sources.dedupe(from_file.get(keyword, []))[: config.max_candidates]
            log.info("%r: %d candidate articles", keyword, len(articles))
            if not articles:
                sections.append(DigestSection(keyword=keyword))
                continue
            assessments, used = relevance.assess(
                articles, model=config.model, use_llm=config.use_llm
            )
        else:
            assessments, used = _search_and_assess(config, keyword)
        llm_used = llm_used or used
        if not assessments:
            sections.append(DigestSection(keyword=keyword))
            continue

        # 3. Keep the top N.
        selected = relevance.select(
            assessments, limit=config.per_keyword, min_confidence=config.min_confidence
        )
        if config.resolve_links:
            for assessment in selected:
                assessment.article.url = sources.resolve_url(assessment.article.url)
        sections.append(DigestSection(keyword=keyword, assessments=selected))

    # 4. Summarize.
    overall, used = summarize.summarize(sections, model=config.model, use_llm=config.use_llm)
    llm_used = llm_used or used

    return Digest(
        generated_at=sources.utcnow(),
        sections=sections,
        overall=overall,
        llm_used=llm_used,
    )
