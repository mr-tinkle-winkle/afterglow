"""Loading studio_steps.json (the data half of the Studio driver).  Pure: no Qt.

``AFTERGLOW_STUDIO_STEPS`` points at a replacement file (tests use a local fake Studio with the
same selectors; a user can hot-fix a Studio redesign without rebuilding)."""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from importlib import resources
from pathlib import Path


def _read_default() -> dict:
    return json.loads(resources.files(__package__).joinpath("studio_steps.json").read_text("utf-8"))


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict:
    data = json.loads(Path(path).read_text("utf-8")) if path else _read_default()
    return data


def load() -> dict:
    override = os.environ.get("AFTERGLOW_STUDIO_STEPS", "")
    mtime = 0.0
    if override:
        try:
            mtime = Path(override).stat().st_mtime
        except OSError:
            override = ""
    return _load(override, mtime)


def selectors(data: dict, name: str, **fmt) -> "list[str]":
    sels = data["selectors"].get(name)
    if sels is None:
        raise KeyError(f"studio_steps.json has no selector '{name}'")
    if isinstance(sels, str):
        sels = [sels]
    return [s.replace("{PRIVACY}", fmt.get("PRIVACY", "")) for s in sels]


def patterns(data: dict, name: str) -> "list[re.Pattern]":
    return [re.compile(p, re.IGNORECASE) for p in data.get("status", {}).get(name, [])]


def matches(text: str, pats: "list[re.Pattern]") -> bool:
    return any(p.search(text or "") for p in pats)


def percent(data: dict, text: str) -> "int | None":
    pat = data.get("status", {}).get("progress_percent")
    if not pat:
        return None
    m = re.search(pat, text or "")
    if not m:
        return None
    try:
        return max(0, min(100, int(m.group(1))))
    except (ValueError, IndexError):
        return None


def url(data: dict, key: str, **fmt) -> str:
    return data[key].format(**fmt)


def flow(data: dict, name: str) -> "list[dict]":
    return list(data["flows"][name])
