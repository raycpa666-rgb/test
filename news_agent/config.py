"""Configuration, sourced from CLI flags and environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_KEYWORDS = ["Colorado River", "Lake Mead"]

# Claude model used for relevance assessment and summarization.
DEFAULT_MODEL = "claude-opus-5"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass
class Config:
    # --- search ---
    keywords: list[str] = field(default_factory=lambda: list(DEFAULT_KEYWORDS))
    per_keyword: int = 5
    max_candidates: int = 30
    window: str = "1d"  # Google News `when:` filter — 1d, 2d, 7d ...
    # A 24-hour window on an exact phrase often yields fewer than `per_keyword`
    # hydrology articles. When that happens, re-search over these wider windows
    # rather than shipping a short digest.
    fallback_windows: list[str] = field(default_factory=lambda: ["3d", "7d"])
    resolve_links: bool = True
    # Optional: read candidates from a JSON file instead of searching the web.
    articles_file: str | None = None

    # --- assessment / summarization ---
    model: str = DEFAULT_MODEL
    use_llm: bool = True
    min_confidence: float = 0.5

    # --- email ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_ssl: bool = False
    email_from: str = ""
    email_to: list[str] = field(default_factory=list)
    send: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        recipients = [
            addr.strip()
            for addr in os.environ.get("EMAIL_TO", "").replace(";", ",").split(",")
            if addr.strip()
        ]
        return cls(
            model=os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL),
            smtp_host=os.environ.get("SMTP_HOST", ""),
            smtp_port=_env_int("SMTP_PORT", 587),
            smtp_user=os.environ.get("SMTP_USER", ""),
            smtp_password=os.environ.get("SMTP_PASSWORD", ""),
            smtp_ssl=_env_bool("SMTP_SSL", False),
            email_from=os.environ.get("EMAIL_FROM", "") or os.environ.get("SMTP_USER", ""),
            email_to=recipients,
        )

    def missing_email_settings(self) -> list[str]:
        """Names of the settings required to actually send mail that aren't set."""
        missing = []
        if not self.smtp_host:
            missing.append("SMTP_HOST")
        if not self.smtp_user:
            missing.append("SMTP_USER")
        if not self.smtp_password:
            missing.append("SMTP_PASSWORD")
        if not self.email_from:
            missing.append("EMAIL_FROM")
        if not self.email_to:
            missing.append("EMAIL_TO")
        return missing
