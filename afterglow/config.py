"""
Settings storage for the clipping app.

Config lives at ~/.config/afterglow/config.toml
Clips/library lives at ~/Videos/Clips (configurable)

We use TOML because it's human-editable (useful for debugging / manual
fixes on a friend's machine) and there's a stable stdlib reader in 3.11+
(tomllib) plus a small writer dependency (tomli_w).
"""
from __future__ import annotations

import tomllib
import tomli_w
from dataclasses import dataclass, field, asdict
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "afterglow"
CONFIG_FILE = CONFIG_DIR / "config.toml"

DEFAULT_CLIPS_DIR = Path.home() / "Videos" / "Clips"
DEFAULT_SOUNDS_DIR = CONFIG_DIR / "sounds"


@dataclass
class OBSSettings:
    host: str = "localhost"
    port: int = 4455
    password: str = ""  # obs-websocket "server password", not an API key per se
    # Extra grace period AFTER OBS reports the replay buffer save as
    # finished (via its ReplayBufferSaved event), before afterglow treats
    # the file as ready to use. Defaults to 0 -- the event itself is
    # normally sufficient -- but is here as an escape hatch in case a
    # slower disk/filesystem needs a moment longer than OBS's own event
    # accounts for.
    wait_after_replay_buffer_finishes_sec: float = 0.0


@dataclass
class YouTubeSettings:
    # Filled in once we build the OAuth flow. Kept here now so the schema
    # is stable and we don't need a migration later.
    client_secret_path: str = ""       # path to the OAuth client_secret.json from Google Cloud
    token_path: str = str(CONFIG_DIR / "youtube_token.json")
    default_privacy: str = "unlisted"  # "unlisted" | "public" | "private"
    default_category_id: str = "20"    # YouTube category id, 20 = "Gaming"
    linked_account_email: str = ""     # informational, shown in Settings UI


@dataclass
class FilterDisplaySettings:
    show_filter_names: bool = True
    show_filter_icons: bool = True
    # "above" | "below" | "vtile_left" | "vtile_right"
    filter_icon_location: str = "above"


@dataclass
class AutoFilterRule:
    tag_name: str = ""
    # Substring matched (case-insensitive) against a running process's
    # name/cmdline (mode="open") or the focused window's title/process
    # name (mode="focused").
    app_match: str = ""
    # "open" = apply whenever a matching process is running at all;
    # "focused" = apply only while a matching window currently has focus.
    mode: str = "open"


@dataclass
class AppSettings:
    clips_dir: str = str(DEFAULT_CLIPS_DIR)
    default_sound_path: str = ""
    default_to_fullscreen: bool = False
    obs: OBSSettings = field(default_factory=OBSSettings)
    youtube: YouTubeSettings = field(default_factory=YouTubeSettings)
    filter_display: FilterDisplaySettings = field(default_factory=FilterDisplaySettings)
    auto_filters: list[AutoFilterRule] = field(default_factory=list)

    def clips_path(self) -> Path:
        return Path(self.clips_dir).expanduser()


def _ensure_dirs(settings: AppSettings) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
    settings.clips_path().mkdir(parents=True, exist_ok=True)


def load() -> AppSettings:
    if not CONFIG_FILE.exists():
        settings = AppSettings()
        _ensure_dirs(settings)
        save(settings)
        return settings

    with open(CONFIG_FILE, "rb") as f:
        raw = tomllib.load(f)

    obs = OBSSettings(**raw.get("obs", {}))
    youtube = YouTubeSettings(**raw.get("youtube", {}))
    filter_display = FilterDisplaySettings(**raw.get("filter_display", {}))
    auto_filters = [AutoFilterRule(**rule) for rule in raw.get("auto_filters", [])]
    top_level = {
        k: v for k, v in raw.items()
        if k not in ("obs", "youtube", "filter_display", "auto_filters")
    }
    settings = AppSettings(
        obs=obs, youtube=youtube, filter_display=filter_display,
        auto_filters=auto_filters, **top_level,
    )
    _ensure_dirs(settings)
    return settings


def save(settings: AppSettings) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = asdict(settings)
    with open(CONFIG_FILE, "wb") as f:
        tomli_w.dump(data, f)


if __name__ == "__main__":
    s = load()
    print(f"Config file: {CONFIG_FILE}")
    print(s)
