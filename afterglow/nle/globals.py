"""
Global presets (text styles) and global audio -- shared by every project.

- Global text presets: CONFIG_DIR/editor_globals/text_presets.json, a list
  of {"name", "text", "props"} where props is an ops.copy_properties()
  payload (JSON-safe). Giving a text element a preset name saves how it
  looks and behaves (font, colors, bubble, effects...) so the same look
  can be dropped into any project -- e.g. one friend always in one color.
  Elements made from / given a preset remember its name
  (TextStyle.global_preset), so updating the preset can restyle them.
- Global audio: CONFIG_DIR/editor_globals/audio/ holds a COPY of each file
  (so it keeps working if the original moves or is deleted), listed in
  audio.json as {"name", "file"}.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
from dataclasses import asdict, is_dataclass
from pathlib import Path

from ..config import CONFIG_DIR

GLOBALS_DIR = CONFIG_DIR / "editor_globals"
PRESETS_FILE = GLOBALS_DIR / "text_presets.json"
AUDIO_DIR = GLOBALS_DIR / "audio"
AUDIO_FILE = GLOBALS_DIR / "audio.json"


def _read(path: Path) -> list:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write(path: Path, data: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


def _jsonable(payload: dict) -> dict:
    out = copy.deepcopy(payload)
    t = out.get("transition")
    if is_dataclass(t):
        out["transition"] = asdict(t)
    return out


# ---- text presets ----------------------------------------------------------

def list_presets() -> list[dict]:
    return _read(PRESETS_FILE)


def get_preset(name: str) -> "dict | None":
    return next((p for p in list_presets() if p.get("name") == name), None)


def save_preset(name: str, text: str, props: dict, position=(0.0, 0.0)) -> None:
    """Create or overwrite the preset called `name`. `position` (Transform
    x, y) is where new elements made from it start."""
    name = name.strip()
    if not name:
        raise ValueError("A preset needs a name.")
    presets = [p for p in list_presets() if p.get("name") != name]
    presets.append({"name": name, "text": text, "props": _jsonable(props),
                    "position": [float(position[0]), float(position[1])]})
    presets.sort(key=lambda p: p["name"].lower())
    _write(PRESETS_FILE, presets)


def rename_preset(old: str, new: str) -> None:
    new = new.strip()
    presets = list_presets()
    if not new or any(p.get("name") == new for p in presets if p.get("name") != old):
        raise ValueError(f"There's already a preset called “{new}”." if new else "A preset needs a name.")
    for p in presets:
        if p.get("name") == old:
            p["name"] = new
    presets.sort(key=lambda p: p["name"].lower())
    _write(PRESETS_FILE, presets)


def delete_preset(name: str) -> None:
    _write(PRESETS_FILE, [p for p in list_presets() if p.get("name") != name])


# ---- audio -------------------------------------------------------------------

def list_audio() -> list[dict]:
    """[{"name", "file" (absolute path of the stored copy)}] whose file exists."""
    out = []
    for a in _read(AUDIO_FILE):
        f = AUDIO_DIR / a.get("file", "")
        if a.get("file") and f.exists():
            out.append({"name": a.get("name") or f.stem, "file": str(f)})
    return out


def is_global_audio(path: str) -> bool:
    try:
        return Path(path).resolve().parent == AUDIO_DIR.resolve()
    except OSError:
        return False


def add_audio(source: str, name: "str | None" = None) -> dict:
    """Copy `source` into the global audio folder (once) and list it."""
    src = Path(source)
    if not src.exists():
        raise FileNotFoundError(source)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    entries = _read(AUDIO_FILE)
    if is_global_audio(str(src)):
        stored = src
    else:
        stem, ext = src.stem, src.suffix
        stored = AUDIO_DIR / f"{stem}{ext}"
        n = 2
        while stored.exists():
            if stored.stat().st_size == src.stat().st_size and _same(stored, src):
                break                          # the same file was added before
            stored = AUDIO_DIR / f"{stem} ({n}){ext}"
            n += 1
        if not stored.exists():
            shutil.copy2(src, stored)
    entry = next((e for e in entries if e.get("file") == stored.name), None)
    if entry is None:
        entry = {"name": (name or src.stem).strip() or src.stem, "file": stored.name}
        entries.append(entry)
        entries.sort(key=lambda e: e["name"].lower())
        _write(AUDIO_FILE, entries)
    return {"name": entry["name"], "file": str(stored)}


def _same(a: Path, b: Path) -> bool:
    import hashlib

    def digest(p):
        h = hashlib.sha1()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.digest()
    try:
        return digest(a) == digest(b)
    except OSError:
        return False


def rename_audio(file: str, new: str) -> None:
    new = new.strip()
    if not new:
        raise ValueError("Give it a name.")
    entries = _read(AUDIO_FILE)
    for e in entries:
        if e.get("file") == Path(file).name:
            e["name"] = new
    entries.sort(key=lambda e: e["name"].lower())
    _write(AUDIO_FILE, entries)


def remove_audio(file: str) -> None:
    """Take it off the list. The stored copy is kept (projects that already
    use it keep working)."""
    _write(AUDIO_FILE, [e for e in _read(AUDIO_FILE) if e.get("file") != Path(file).name])
