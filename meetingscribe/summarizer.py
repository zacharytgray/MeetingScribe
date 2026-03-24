from __future__ import annotations

import re
import datetime
from pathlib import Path
from typing import Optional

CLAUDE_MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 2048

SYSTEM_PROMPT = """\
You are MeetingScribe. Given a meeting transcript, output a structured markdown summary.

Your response MUST begin with exactly this line (no leading text before it):
SLUG: <snake_case_slug_under_40_chars>

Then output the markdown document with this exact structure:

# <Meeting Title>
**Date:** <date and time>
**Duration:** <duration>

## ✅ Action Items
- [ ] <item> — @<person or TBD>

## 📋 Summary
<2–4 paragraph prose summary>

## 🗣️ Key Discussion Points
- <bullet points>

## 👥 Participants
- <speaker names or labels>

---
*Transcribed and summarized by MeetingScribe*

Rules:
- The SLUG line must come first, before any markdown.
- Use only the information in the transcript.
- If participants cannot be identified, list them as "Speaker 1", "Speaker 2", etc.
- Keep the slug to lowercase letters, digits, and underscores only.
- Each bullet point MUST be on its own line. Never put two list items on the same line.
"""


def summarize(
    transcript: str,
    api_key: str,
    meeting_date: Optional[datetime.datetime] = None,
    duration_seconds: Optional[float] = None,
    openrouter_api_key: str = "",
    openrouter_model: str = "",
    user_name: str = "",
) -> tuple[str, str]:
    """
    Summarize via Claude API (if api_key set) or OpenRouter (if openrouter_api_key set).
    Returns (slug, markdown_content). Raises on failure.
    """
    date_str = (meeting_date or datetime.datetime.now()).strftime("%B %-d, %Y at %-I:%M %p")
    duration_str = _fmt_duration(duration_seconds) if duration_seconds is not None else "unknown"

    context_lines = [f"Meeting date: {date_str}", f"Duration: {duration_str}"]
    if user_name:
        context_lines.append(
            f'The person who recorded this meeting is "{user_name}". '
            f'Their lines are labeled [{user_name}] in the transcript. '
            f"Assign action items they committed to under @{user_name}. "
            f"Write the summary from their perspective. "
            f"Do NOT include {user_name} in the Participants section — their presence is implied."
        )
    context_lines.append(f"\nTranscript:\n{transcript}")
    user_content = "\n".join(context_lines)

    if openrouter_api_key:
        raw = _call_openrouter(user_content, openrouter_api_key, openrouter_model)
    else:
        raw = _call_anthropic(user_content, api_key)

    return _parse_response(raw, meeting_date)


def _call_anthropic(user_content: str, api_key: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    return message.content[0].text


def _call_openrouter(user_content: str, api_key: str, model: str) -> str:
    import httpx
    from .config import OPENROUTER_DEFAULT_MODEL
    response = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model or OPENROUTER_DEFAULT_MODEL,
            "max_tokens": MAX_TOKENS,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        },
        timeout=60.0,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _parse_response(raw: str, meeting_date: Optional[datetime.datetime]) -> tuple[str, str]:
    first_line, _, rest = raw.partition("\n")
    slug_match = re.match(r"^SLUG:\s*([a-z0-9_]+)", first_line.strip())
    if slug_match:
        markdown = rest.lstrip("\n")
    else:
        slug = (meeting_date or datetime.datetime.now()).strftime("meeting_%Y_%m_%d")
        markdown = raw
        return slug, _fix_list_formatting(markdown)

    return slug_match.group(1)[:40], _fix_list_formatting(markdown)


def _fix_list_formatting(markdown: str) -> str:
    """Ensure each markdown list item starts on its own line."""
    # Insert a newline before any list item that immediately follows non-whitespace text
    return re.sub(r"(?<!\n)([ \t]*- )", r"\n\1", markdown)


def save_summary(
    slug: str,
    markdown: str,
    output_dir: Path,
    meeting_date: Optional[datetime.datetime] = None,
    transcript: str = "",
) -> Path:
    """
    Write markdown to output_dir/YYYY-MM-DD_<slug>.md.
    Appends raw transcript at the bottom if provided.
    Handles filename collisions by appending _2, _3, etc.
    Returns the path written.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    date_prefix = (meeting_date or datetime.datetime.now()).strftime("%Y-%m-%d")
    base_name = f"{date_prefix}_{slug}"

    path = output_dir / f"{base_name}.md"
    if path.exists():
        i = 2
        while True:
            path = output_dir / f"{base_name}_{i}.md"
            if not path.exists():
                break
            i += 1

    content = markdown
    if transcript.strip():
        # Double-space between lines so each renders as its own line in markdown
        transcript_md = "\n\n".join(line for line in transcript.splitlines() if line.strip())
        content += f"\n\n---\n\n## 📝 Raw Transcript\n\n{transcript_md}\n"

    path.write_text(content, encoding="utf-8")
    return path


def save_raw_transcript(transcript: str, output_dir: Path, meeting_date: Optional[datetime.datetime] = None) -> Path:
    """Fallback: save raw transcript when Claude API is unavailable."""
    output_dir.mkdir(parents=True, exist_ok=True)
    date_prefix = (meeting_date or datetime.datetime.now()).strftime("%Y-%m-%d")
    path = output_dir / f"{date_prefix}_transcript.md"
    i = 2
    while path.exists():
        path = output_dir / f"{date_prefix}_transcript_{i}.md"
        i += 1
    path.write_text(f"# Meeting Transcript\n\n```\n{transcript}\n```\n", encoding="utf-8")
    return path


def _fmt_duration(seconds: float) -> str:
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"
