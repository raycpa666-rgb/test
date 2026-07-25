"""Step 2: decide which articles are actually about hydrology.

Claude does the judging when it's reachable; a weighted keyword heuristic is the
fallback so the agent still produces a useful digest without an API key.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from .models import Article, Assessment

log = logging.getLogger(__name__)

_EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)

HYDROLOGY_DEFINITION = (
    "Hydrology is the science of water in the environment: streamflow and runoff, "
    "snowpack and snowmelt, precipitation and drought, groundwater and aquifers, "
    "reservoir storage and elevation, evaporation, flood and flow forecasting, "
    "water supply and allocation, and the models and measurements behind them."
)

# Weighted terms for the offline fallback. Higher weight == more decisive.
STRONG_TERMS: dict[str, float] = {
    "snowpack": 3.0,
    "snowmelt": 3.0,
    "runoff": 3.0,
    "streamflow": 3.0,
    "aquifer": 3.0,
    "groundwater": 3.0,
    "reservoir": 2.5,
    "acre-feet": 3.0,
    "acre feet": 3.0,
    "drought": 2.5,
    "watershed": 2.5,
    "hydrology": 3.0,
    "hydrologic": 3.0,
    "water level": 2.5,
    "lake level": 2.5,
    "inflow": 2.5,
    "outflow": 2.5,
    "dead pool": 3.0,
    "water supply": 2.0,
    "reclamation": 2.0,
    "evaporation": 2.5,
    "flood": 2.0,
    "precipitation": 2.0,
    "rainfall": 2.0,
    "megadrought": 3.0,
    "water year": 2.5,
    "elevation": 1.5,
    "storage": 1.5,
    "allocation": 1.5,
    "shortage": 1.5,
    "conservation": 1.0,
    "cutback": 1.5,
    "irrigation": 1.5,
    "basin": 1.5,
    "dam": 1.5,
    "glen canyon": 2.0,
    "hoover dam": 2.0,
    "colorado river compact": 2.5,
    "forecast": 1.0,
    "usgs": 2.0,
    "noaa": 1.5,
}

# Terms that suggest the keyword was matched for an unrelated reason
# (a casino, a road race, a housing development named "Lake Mead ...").
NEGATIVE_TERMS: dict[str, float] = {
    "casino": 2.0,
    "shooting": 2.0,
    "arrested": 2.0,
    "nfl": 2.0,
    "nba": 2.0,
    "concert": 1.5,
    "restaurant": 1.5,
    "real estate": 1.5,
    "stock": 1.5,
    "crypto": 2.0,
    "movie": 1.5,
    "recipe": 2.0,
    "body found": 1.5,
    "crash": 1.0,
    # Landmarks on the river attract history and travel writing that has
    # nothing to do with water resources.
    "nazi": 3.0,
    "world war": 3.0,
    "hitler": 3.0,
    "tourist": 1.5,
    "haunted": 2.0,
}

# Score at which the heuristic calls an article hydrology-related.
HEURISTIC_THRESHOLD = 2.5


def _compile(terms: dict[str, float]) -> dict[str, re.Pattern[str]]:
    # Word boundaries matter: without them "nfl" matches inside "inflow"
    # and "dam" matches inside "damage".
    return {term: re.compile(rf"\b{re.escape(term)}\b") for term in terms}


_STRONG_RE = _compile(STRONG_TERMS)
_NEGATIVE_RE = _compile(NEGATIVE_TERMS)


def _drop_subsumed(hits: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Keep only the most specific of overlapping terms.

    "Did the Nazis Really Try to Blow Up the Hoover Dam?" matched both
    "hoover dam" and "dam", and the two weights summed past the threshold.
    A phrase and the word inside it are one signal, not two.
    """
    terms = [term for term, _ in hits]
    return [
        (term, weight)
        for term, weight in hits
        if not any(other != term and term in other for other in terms)
    ]


def heuristic_assess(article: Article) -> Assessment:
    """Score an article on hydrology vocabulary alone (no network calls)."""
    blob = article.text_blob()
    hits = _drop_subsumed(
        [(term, weight) for term, weight in STRONG_TERMS.items() if _STRONG_RE[term].search(blob)]
    )
    penalties = [
        (term, weight) for term, weight in NEGATIVE_TERMS.items() if _NEGATIVE_RE[term].search(blob)
    ]

    score = sum(weight for _, weight in hits) - sum(weight for _, weight in penalties)
    is_hydrology = score >= HEURISTIC_THRESHOLD
    # Map the open-ended score onto a 0-1 confidence for ranking.
    confidence = max(0.0, min(1.0, score / 9.0))
    matched = ", ".join(term for term, _ in sorted(hits, key=lambda h: -h[1])[:4])

    return Assessment(
        article=article,
        is_hydrology=is_hydrology,
        confidence=round(confidence, 2),
        topic=matched.split(",")[0].strip() if matched else "",
        reason=(f"keyword match: {matched}" if matched else "no hydrology terms found")
        + (f"; penalized for: {', '.join(t for t, _ in penalties)}" if penalties else ""),
    )


def heuristic_assess_all(articles: list[Article]) -> list[Assessment]:
    return [heuristic_assess(article) for article in articles]


ASSESSMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "is_hydrology": {"type": "boolean"},
                    "confidence": {"type": "number"},
                    "topic": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "is_hydrology", "confidence", "topic", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["assessments"],
    "additionalProperties": False,
}

ASSESSMENT_SYSTEM = (
    "You screen news articles for a hydrologist who tracks the Colorado River Basin.\n\n"
    f"{HYDROLOGY_DEFINITION}\n\n"
    "For each article decide whether it is genuinely about hydrology or water resources, "
    "based only on the headline, publisher, and snippet provided. Articles that merely "
    "mention a place name (a casino near Lake Mead, a crime story on Colorado River Road, "
    "a sports team) are NOT hydrology. Policy, litigation, and negotiation stories DO count "
    "when they concern water supply, allocation, or reservoir operations.\n\n"
    "confidence is 0.0-1.0 and expresses how central hydrology is to the article. "
    "topic is a two-to-four word label such as 'reservoir storage' or 'snowpack outlook'. "
    "reason is one short sentence. Return exactly one assessment per article, using the "
    "index you were given."
)


def _render_articles(articles: list[Article]) -> str:
    lines = []
    for index, article in enumerate(articles):
        lines.append(
            f"[{index}] title: {article.title}\n"
            f"    publisher: {article.source or 'unknown'}\n"
            f"    published: {article.published_str}\n"
            f"    snippet: {article.snippet or '(none)'}"
        )
    return "\n".join(lines)


def llm_assess(articles: list[Article], model: str, batch_size: int = 20) -> list[Assessment]:
    """Ask Claude to judge each article. Raises LLMUnavailable on any failure."""
    from .llm import LLMUnavailable, complete_json  # imported lazily: optional dependency

    assessments: list[Assessment] = []
    for start in range(0, len(articles), batch_size):
        batch = articles[start : start + batch_size]
        prompt = (
            f"Search keyword: {batch[0].keyword!r}\n\n"
            f"Assess these {len(batch)} articles:\n\n{_render_articles(batch)}"
        )
        payload = complete_json(
            model=model,
            system=ASSESSMENT_SYSTEM,
            prompt=prompt,
            schema=ASSESSMENT_SCHEMA,
            effort="low",
            max_tokens=8000,
        )
        by_index = {int(item["index"]): item for item in payload.get("assessments", [])}
        if not by_index:
            raise LLMUnavailable("Claude returned no assessments")
        for offset, article in enumerate(batch):
            item = by_index.get(offset)
            if item is None:
                # Claude skipped one; fall back for that article only.
                assessments.append(heuristic_assess(article))
                continue
            assessments.append(
                Assessment(
                    article=article,
                    is_hydrology=bool(item["is_hydrology"]),
                    confidence=max(0.0, min(1.0, float(item["confidence"]))),
                    topic=str(item.get("topic", "")).strip(),
                    reason=str(item.get("reason", "")).strip(),
                )
            )
    return assessments


def assess(articles: list[Article], model: str, use_llm: bool = True) -> tuple[list[Assessment], bool]:
    """Assess `articles`, returning (assessments, llm_was_used)."""
    if not articles:
        return [], False
    if use_llm:
        from .llm import LLMUnavailable

        try:
            return llm_assess(articles, model=model), True
        except LLMUnavailable as exc:
            log.warning("Falling back to keyword scoring for relevance: %s", exc)
    return heuristic_assess_all(articles), False


def select(assessments: list[Assessment], limit: int, min_confidence: float = 0.5) -> list[Assessment]:
    """Pick the top `limit` hydrology articles, best first.

    If fewer than `limit` clear the confidence bar, the next-best hydrology
    articles fill the remaining slots so the digest still has `limit` entries
    when the day's coverage is thin.
    """
    hydrology = sorted(
        (a for a in assessments if a.is_hydrology),
        key=lambda a: (a.confidence, a.article.published or _EPOCH),
        reverse=True,
    )
    selected = [a for a in hydrology if a.confidence >= min_confidence][:limit]
    chosen_urls = {a.article.url for a in selected}
    for candidate in hydrology:
        if len(selected) >= limit:
            break
        if candidate.article.url not in chosen_urls:
            selected.append(candidate)
            chosen_urls.add(candidate.article.url)
    return selected
