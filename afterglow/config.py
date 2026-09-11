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
    # Multiple filters can be set as "the filter(s) of choice" for one
    # rule -- all of them get applied together whenever this rule
    # matches (was a single tag_name string; see load()'s
    # backward-compat handling for configs written before this change).
    tag_names: list[str] = field(default_factory=list)
    # Substring matched (case-insensitive) against a running process's
    # name/cmdline (mode="open") or the focused window's title/process
    # name (mode="focused").
    app_match: str = ""
    # "open" = apply whenever a matching process is running at all;
    # "focused" = apply only while a matching window currently has focus.
    mode: str = "open"


@dataclass
class CardInfoSettings:
    """What's shown on a Library card besides the thumbnail/title/
    favorite-star, toggled from the Library's "Info" dropdown (next to
    Sort By). Render order on the card is: title, then length + file
    size on one line (size after length), then creation date, then
    filters -- each independently toggleable here."""
    show_filters: bool = True
    show_length: bool = False
    show_file_size: bool = False
    show_creation_date: bool = False


@dataclass
class AppearanceSettings:
    """Everything under Settings > General (visual tuning of the
    sidebar border/highlight/icon sizes) -- split out from AppSettings'
    old flat "General" group, which got renamed to "Clipping" once this
    existed, since "General" now means appearance specifically rather
    than a catch-all.

    Brightness fields are a 0-100 percent (100 = full brightness/no
    darkening), matching how a person would think of a brightness
    slider, rather than the darken-factor fraction the code multiplies
    by internally (brightness/100.0).
    """
    resize_text_to_fit: bool = False
    inactive_border_width: int = 5
    inactive_border_brightness: int = 65
    active_border_width: int = 9
    active_border_brightness: int = 100
    # "disabled" | "only_settings" | "always" -- whether the Settings
    # nav button gets the same gradient-border treatment Library/Editor
    # already have, and if so, whether only while it's the active page
    # or all the time.
    settings_border_mode: str = "only_settings"
    unedited_highlight_width: int = 9
    unedited_highlight_brightness: int = 100
    filter_icon_size: int = 54
    # Sidebar nav icon scale, as a 0-200 percent of the button's own
    # available space (matches _ScalingIconButton.SCALE, previously a
    # hardcoded 0.85 i.e. 85%).
    library_icon_size: int = 85
    # "normal" | "maximized" | "fullscreen" -- replaces the old
    # top-level default_to_fullscreen bool (see load()'s backward-compat
    # shim), moved here from the Clipping tab into General since it's
    # an appearance/startup-appearance concern, and widened to a third
    # choice: a checkbox pair for fullscreen+maximized could produce a
    # contradictory both-checked state, so this is one combo box instead.
    startup_window_mode: str = "normal"


@dataclass
class AppSettings:
    clips_dir: str = str(DEFAULT_CLIPS_DIR)
    default_sound_path: str = ""
    obs: OBSSettings = field(default_factory=OBSSettings)
    youtube: YouTubeSettings = field(default_factory=YouTubeSettings)
    filter_display: FilterDisplaySettings = field(default_factory=FilterDisplaySettings)
    auto_filters: list[AutoFilterRule] = field(default_factory=list)
    card_info: CardInfoSettings = field(default_factory=CardInfoSettings)
    appearance: AppearanceSettings = field(default_factory=AppearanceSettings)

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

    auto_filters = []
    for rule in raw.get("auto_filters", []):
        rule = dict(rule)
        if "tag_name" in rule and "tag_names" not in rule:
            # Pre-multi-select config format -- one filter per rule.
            old_tag = rule.pop("tag_name")
            rule["tag_names"] = [old_tag] if old_tag else []
        auto_filters.append(AutoFilterRule(**rule))

    card_info = CardInfoSettings(**raw.get("card_info", {}))
    appearance_raw = dict(raw.get("appearance", {}))
    if "startup_window_mode" not in appearance_raw and raw.get("default_to_fullscreen"):
        # Pre-startup-window-mode config format -- the old top-level
        # fullscreen-only bool. Only a `true` value carries information
        # (the default was already False, matching "normal"), so a
        # missing or false value needs no conversion.
        appearance_raw["startup_window_mode"] = "fullscreen"
    appearance = AppearanceSettings(**appearance_raw)
    top_level = {
        k: v for k, v in raw.items()
        if k not in (
            "obs", "youtube", "filter_display", "auto_filters", "card_info", "appearance",
            "default_to_fullscreen",  # old field, folded into appearance.startup_window_mode above
        )
    }
    settings = AppSettings(
        obs=obs, youtube=youtube, filter_display=filter_display,
        auto_filters=auto_filters, card_info=card_info, appearance=appearance, **top_level,
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
