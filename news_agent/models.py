"""Data structures passed between the agent's stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Article:
    """A single news article surfaced by a search."""

    title: str
    url: str
    source: str = ""
    published: datetime | None = None
    snippet: str = ""
    keyword: str = ""

    @property
    def published_str(self) -> str:
        return self.published.strftime("%Y-%m-%d %H:%M UTC") if self.published else "unknown date"

    def text_blob(self) -> str:
        """Everything we know about the article, for keyword scoring."""
        return " ".join([self.title, self.snippet, self.source]).lower()


@dataclass
class Assessment:
    """The hydrology-relevance verdict for one article."""

    article: Article
    is_hydrology: bool
    confidence: float
    topic: str = ""
    reason: str = ""

    @property
    def score(self) -> float:
        """Rank key: non-hydrology articles always sort below hydrology ones."""
        return self.confidence if self.is_hydrology else -1.0


@dataclass
class DigestSection:
    """The selected articles and summary for a single keyword."""

    keyword: str
    assessments: list[Assessment] = field(default_factory=list)
    summary: str = ""
    takeaways: dict[str, str] = field(default_factory=dict)  # url -> one-line takeaway

    @property
    def articles(self) -> list[Article]:
        return [a.article for a in self.assessments]


@dataclass
class Digest:
    """The finished product: everything needed to render the email."""

    generated_at: datetime
    sections: list[DigestSection] = field(default_factory=list)
    overall: str = ""
    llm_used: bool = False

    @property
    def article_count(self) -> int:
        return sum(len(s.assessments) for s in self.sections)
