"""Step 4: write the short summary that goes in the email."""

from __future__ import annotations

import logging

from .models import DigestSection

log = logging.getLogger(__name__)

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "overall": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "summary": {"type": "string"},
                    "takeaways": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {"type": "integer"},
                                "takeaway": {"type": "string"},
                            },
                            "required": ["index", "takeaway"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["keyword", "summary", "takeaways"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["overall", "sections"],
    "additionalProperties": False,
}

SUMMARY_SYSTEM = (
    "You write a daily hydrology news briefing for a water-resources professional "
    "following the Colorado River Basin.\n\n"
    "Work only from the headlines and snippets given — do not invent numbers, dates, "
    "or findings that are not there, and do not claim to have read the full articles. "
    "Where the snippets disagree or are vague, say so plainly.\n\n"
    "overall: 2-4 sentences on what the day's coverage adds up to across both keywords.\n"
    "summary (per keyword): 3-5 sentences on the hydrologic picture in that group — "
    "storage and elevation, snowpack and runoff, drought status, allocation and policy.\n"
    "takeaways: one clause (under 20 words) per article, using the index given. "
    "No preamble, no bullet markers, no restating the headline verbatim."
)


def _render(sections: list[DigestSection]) -> tuple[str, dict[tuple[str, int], str]]:
    """Render the prompt body and a map from (keyword, index) back to article URLs."""
    lines: list[str] = []
    index_map: dict[tuple[str, int], str] = {}
    for section in sections:
        lines.append(f"### Keyword: {section.keyword}")
        for index, assessment in enumerate(section.assessments):
            article = assessment.article
            index_map[(section.keyword, index)] = article.url
            lines.append(
                f"[{index}] {article.title}\n"
                f"    publisher: {article.source or 'unknown'} | {article.published_str}\n"
                f"    topic: {assessment.topic or 'n/a'}\n"
                f"    snippet: {article.snippet or '(none)'}"
            )
        lines.append("")
    return "\n".join(lines), index_map


def llm_summarize(sections: list[DigestSection], model: str) -> str:
    """Fill in `summary`/`takeaways` on each section; returns the overall summary."""
    from .llm import complete_json  # imported lazily: optional dependency

    body, index_map = _render(sections)
    payload = complete_json(
        model=model,
        system=SUMMARY_SYSTEM,
        prompt=f"Summarize today's selected articles.\n\n{body}",
        schema=SUMMARY_SCHEMA,
        effort="medium",
        max_tokens=8000,
    )

    by_keyword = {str(item.get("keyword", "")).strip().lower(): item for item in payload.get("sections", [])}
    for section in sections:
        item = by_keyword.get(section.keyword.lower())
        if not item:
            continue
        section.summary = str(item.get("summary", "")).strip()
        for entry in item.get("takeaways", []):
            url = index_map.get((section.keyword, int(entry.get("index", -1))))
            if url:
                section.takeaways[url] = str(entry.get("takeaway", "")).strip()
    return str(payload.get("overall", "")).strip()


def heuristic_summarize(sections: list[DigestSection]) -> str:
    """Offline fallback: describe what was selected without inventing analysis."""
    for section in sections:
        topics = [a.topic for a in section.assessments if a.topic]
        publishers = sorted({a.article.source for a in section.assessments if a.article.source})
        parts = [
            f"{len(section.assessments)} hydrology-related "
            f"{'article' if len(section.assessments) == 1 else 'articles'} matched "
            f"“{section.keyword}” today."
        ]
        if topics:
            parts.append("Recurring themes: " + ", ".join(dict.fromkeys(topics)) + ".")
        if publishers:
            parts.append("Sources: " + ", ".join(publishers) + ".")
        parts.append("Headlines are listed below; summaries were generated without an LLM.")
        section.summary = " ".join(parts)
        for assessment in section.assessments:
            section.takeaways[assessment.article.url] = assessment.reason

    total = sum(len(s.assessments) for s in sections)
    keywords = ", ".join(f"“{s.keyword}”" for s in sections)
    return (
        f"{total} hydrology-related articles across {keywords}. "
        "Claude was unavailable, so this digest was assembled with keyword scoring only — "
        "skim the headlines rather than relying on the summaries."
    )


def summarize(sections: list[DigestSection], model: str, use_llm: bool = True) -> tuple[str, bool]:
    """Summarize the digest, returning (overall_summary, llm_was_used)."""
    populated = [s for s in sections if s.assessments]
    if not populated:
        return "No hydrology-related articles were found for today's keywords.", False
    if use_llm:
        from .llm import LLMUnavailable

        try:
            return llm_summarize(populated, model=model), True
        except LLMUnavailable as exc:
            log.warning("Falling back to template summaries: %s", exc)
    return heuristic_summarize(populated), False
