"""
The upload description template (Settings > YouTube).  Placeholders:

  {title}          the clip's afterglow title
  {clip_type}      the clip type that captured it ("" for imported clips)
  {date} {time}    the ORIGINAL capture date / time (local time)
  {length}         the clip's length, m:ss (h:mm:ss past an hour)
  {size}           the file size ("12.4 MB")
  {filters}        the clip's filters as #hashtags (sanitised: letters / digits / _ only)
  {filters_plain}  the clip's filters as plain text, comma separated

Unknown placeholders are left as typed; a literal brace is written ``{{`` / ``}}``.  Blank lines
left behind by an empty placeholder (no filters, say) are collapsed so the description never
starts with a gap.  Pure: no Qt, no DB.
"""
from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone

DEFAULT_TEMPLATE = "{filters}\n\n{clip_type} -- captured {date} {time}\nLength {length} · {size}"

PLACEHOLDERS = (
    ("title", "the clip's title"),
    ("clip_type", "the clip type"),
    ("date", "capture date"),
    ("time", "capture time"),
    ("length", "clip length"),
    ("size", "file size"),
    ("filters", "filters as #hashtags"),
    ("filters_plain", "filters as text"),
)


@dataclass
class ClipFacts:
    title: str = ""
    clip_type: str = ""
    created_at: str = ""            # ISO timestamp (UTC or naive)
    duration_sec: "float | None" = None
    size_bytes: "int | None" = None
    filters: list = field(default_factory=list)


def hashtag(name: str) -> str:
    """'Clutch 1v3!' -> '#Clutch1v3'.  '' when nothing usable is left."""
    cleaned = re.sub(r"[^\w]", "", name or "", flags=re.UNICODE)
    return f"#{cleaned}" if cleaned else ""


def format_length(seconds: "float | None") -> str:
    if seconds is None:
        return ""
    total = int(round(max(0.0, float(seconds))))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_size(n: "int | None") -> str:
    if n is None:
        return ""
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000.0
    return f"{value:.1f} GB"


def _local_dt(created_at: str) -> "datetime | None":
    try:
        dt = datetime.fromisoformat(created_at)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


class _Keep(dict):
    def __missing__(self, key):            # unknown placeholder: leave it as typed
        return "{" + key + "}"


def values_for(facts: ClipFacts) -> dict:
    dt = _local_dt(facts.created_at)
    tags = [hashtag(f) for f in facts.filters]
    return {
        "title": facts.title or "",
        "clip_type": facts.clip_type or "",
        "date": dt.strftime("%Y-%m-%d") if dt else "",
        "time": dt.strftime("%H:%M") if dt else "",
        "length": format_length(facts.duration_sec),
        "size": format_size(facts.size_bytes),
        "filters": " ".join(t for t in tags if t),
        "filters_plain": ", ".join(f for f in facts.filters if f),
    }


def _format_line(line: str, vals: dict) -> "str | None":
    """One template line, or None to drop it: a line whose known placeholders ALL came out empty
    (no filters, an imported clip with no type...) is dropped rather than left as stray
    punctuation like " -- captured"."""
    names = [fname for _, fname, _, _ in string.Formatter().parse(line) if fname]
    known = [n for n in names if n in vals]
    if known and not any(vals[n] for n in known):
        return None
    try:
        return string.Formatter().vformat(line, (), _Keep(vals))
    except (ValueError, IndexError, KeyError):
        for k, v in vals.items():           # an unbalanced brace: plain replacement only
            line = line.replace("{" + k + "}", v)
        return line


def expand(template: str, facts: ClipFacts) -> str:
    vals = values_for(facts)
    out = []
    for raw in (template or "").split("\n"):
        try:
            names_ok = True
            list(string.Formatter().parse(raw))
        except ValueError:
            names_ok = False
        line = _format_line(raw, vals) if names_ok else _format_line_plain(raw, vals)
        if line is not None:
            if line.strip() and line != raw:
                # punctuation left dangling by an empty placeholder ("-- captured", "0:05 ·")
                line = re.sub(r"^[\s\-–—·|:,]+(?=\S)", "", line)
                line = re.sub(r"(?<=\S)[\s\-–—·|:,]+$", "", line)
            out.append(line.rstrip())
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip("\n")
    return text


def _format_line_plain(line: str, vals: dict) -> str:
    for k, v in vals.items():
        line = line.replace("{" + k + "}", v)
    return line


def sanitize_for_youtube(text: str, max_bytes: "int | None" = None, max_chars: "int | None" = None) -> str:
    """YouTube rejects ``<`` and ``>`` in titles and descriptions; they become look-alikes."""
    text = (text or "").replace("<", "‹").replace(">", "›")
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars]
    if max_bytes is not None:
        raw = text.encode("utf-8")
        if len(raw) > max_bytes:
            text = raw[:max_bytes].decode("utf-8", errors="ignore")
    return text


def problems(title: str, description: str) -> "list[str]":
    """What would make Studio refuse these (shown in the quick dialog)."""
    from . import TITLE_MAX, DESCRIPTION_MAX
    out = []
    if not (title or "").strip():
        out.append("The title is empty.")
    if len(title or "") > TITLE_MAX:
        out.append(f"The title is {len(title)} characters; YouTube allows {TITLE_MAX}.")
    if len((description or "").encode("utf-8")) > DESCRIPTION_MAX:
        out.append(f"The description is longer than YouTube's {DESCRIPTION_MAX} bytes.")
    if any(c in (title or "") + (description or "") for c in "<>"):
        out.append("'<' and '>' are not allowed by YouTube (they will be replaced).")
    return out


def facts_for_video(video, clip_type_name: str = "") -> ClipFacts:
    """ClipFacts from a library.Video (size from the file, or the size remembered at upload time)."""
    from pathlib import Path
    size = None
    try:
        size = Path(video.path).stat().st_size
    except (OSError, TypeError):
        size = getattr(video, "file_size_bytes", None)
    return ClipFacts(title=video.title, clip_type=clip_type_name, created_at=video.created_at,
                     duration_sec=video.duration_sec, size_bytes=size, filters=list(video.tags))


def clip_type_name(video) -> str:
    if getattr(video, "clip_config_id", None) is None:
        return ""
    try:
        from .. import clips
        return clips.get_clip_config(video.clip_config_id).name
    except Exception:  # noqa: BLE001 -- a deleted clip type: no name
        return ""


def describe(video, template: str) -> str:
    return expand(template, facts_for_video(video, clip_type_name(video)))
