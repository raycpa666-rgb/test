"""Step 5: draft (and optionally send) the email."""

from __future__ import annotations

import html
import logging
import smtplib
from email.message import EmailMessage

from .config import Config
from .models import Digest

log = logging.getLogger(__name__)


def build_subject(digest: Digest) -> str:
    return (
        f"Colorado River & Lake Mead — hydrology news digest, "
        f"{digest.generated_at.strftime('%b %d, %Y')}"
    )


def build_text(digest: Digest) -> str:
    lines = [
        build_subject(digest),
        "=" * len(build_subject(digest)),
        "",
        digest.overall,
        "",
    ]
    for section in digest.sections:
        lines.append(f"{section.keyword.upper()} ({len(section.assessments)} articles)")
        lines.append("-" * 60)
        if section.summary:
            lines.extend([section.summary, ""])
        for number, assessment in enumerate(section.assessments, start=1):
            article = assessment.article
            lines.append(f"{number}. {article.title}")
            meta = " | ".join(filter(None, [article.source, article.published_str, assessment.topic]))
            if meta:
                lines.append(f"   {meta}")
            takeaway = section.takeaways.get(article.url)
            if takeaway:
                lines.append(f"   {takeaway}")
            lines.append(f"   {article.url}")
            lines.append("")
        lines.append("")

    engine = "Claude" if digest.llm_used else "keyword scoring (Claude unavailable)"
    lines.append(f"-- Assembled by the hydrology news agent using {engine}.")
    return "\n".join(lines)


def build_html(digest: Digest) -> str:
    esc = html.escape
    parts = [
        "<div style=\"font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"
        "max-width:680px;line-height:1.5;color:#1c2b36\">",
        f"<h1 style=\"font-size:20px;margin:0 0 4px\">{esc(build_subject(digest))}</h1>",
        f"<p style=\"margin:0 0 20px\">{esc(digest.overall)}</p>",
    ]
    for section in digest.sections:
        parts.append(
            f"<h2 style=\"font-size:16px;border-bottom:2px solid #d6e2ea;padding-bottom:4px;"
            f"margin:24px 0 8px\">{esc(section.keyword)} "
            f"<span style=\"font-weight:400;color:#5a7080\">"
            f"({len(section.assessments)} articles)</span></h2>"
        )
        if section.summary:
            parts.append(f"<p style=\"margin:0 0 12px\">{esc(section.summary)}</p>")
        parts.append("<ol style=\"padding-left:20px;margin:0\">")
        for assessment in section.assessments:
            article = assessment.article
            meta = " &middot; ".join(
                esc(bit)
                for bit in filter(None, [article.source, article.published_str, assessment.topic])
            )
            takeaway = section.takeaways.get(article.url, "")
            parts.append(
                f"<li style=\"margin-bottom:12px\">"
                f"<a href=\"{esc(article.url, quote=True)}\" "
                f"style=\"color:#12557a;font-weight:600;text-decoration:none\">"
                f"{esc(article.title)}</a>"
                f"<div style=\"font-size:12px;color:#5a7080\">{meta}</div>"
                + (f"<div style=\"font-size:13px\">{esc(takeaway)}</div>" if takeaway else "")
                + "</li>"
            )
        parts.append("</ol>")

    engine = "Claude" if digest.llm_used else "keyword scoring (Claude unavailable)"
    parts.append(
        f"<p style=\"margin-top:28px;font-size:12px;color:#5a7080\">"
        f"Assembled by the hydrology news agent using {esc(engine)}.</p></div>"
    )
    return "\n".join(parts)


def build_message(digest: Digest, config: Config) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = build_subject(digest)
    message["From"] = config.email_from or "news-agent@localhost"
    message["To"] = ", ".join(config.email_to) or "undisclosed-recipients:;"
    message.set_content(build_text(digest))
    message.add_alternative(build_html(digest), subtype="html")
    return message


def send(digest: Digest, config: Config) -> None:
    """Send the digest over SMTP. Raises if the connection or auth fails."""
    missing = config.missing_email_settings()
    if missing:
        raise RuntimeError(f"cannot send email, missing settings: {', '.join(missing)}")

    message = build_message(digest, config)
    log.info("Sending digest to %s via %s:%s", ", ".join(config.email_to), config.smtp_host, config.smtp_port)
    if config.smtp_ssl:
        with smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=30) as server:
            server.login(config.smtp_user, config.smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as server:
            server.starttls()
            server.login(config.smtp_user, config.smtp_password)
            server.send_message(message)
    log.info("Digest sent.")
