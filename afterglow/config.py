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
    # Doubles as the selection border's thickness (a selected card's
    # border replaces whatever it would otherwise show -- unedited
    # gradient or nothing -- with a plain gray border at this same
    # width), hence the name; was `unedited_highlight_width` before
    # selection existed (see load()'s backward-compat shim).
    unedited_selected_border_width: int = 9
    unedited_highlight_brightness: int = 100
    filter_icon_size: int = 54
    # Sidebar nav icon scale, as a 0-200 percent of each button's own
    # available space (matches _ScalingIconButton.SCALE, previously a
    # hardcoded 0.85 i.e. 85%). Three SEPARATE fields, one per sidebar
    # button -- library_icon_size used to (incorrectly) drive all three
    # at once; see load()'s backward-compat shim for how an old config's
    # single value becomes the starting point for the two new ones too.
    library_icon_size: int = 85
    editor_icon_size: int = 85
    settings_icon_size: int = 85
    # The Library page's OWN Local ("Saved Videos") / Uploaded tab
    # icons -- entirely distinct from the three sidebar fields above
    # (this is what "Library Page Icons Size" was actually supposed to
    # mean before the mis-wiring). 100 = the existing fixed baseline
    # size (style's default tab-bar icon size * 4.5, see
    # LibraryPage.__init__), not a fresh arbitrary default -- these two
    # settings didn't exist before, so 100% preserves the prior
    # (unaffected-by-any-setting) look for anyone upgrading.
    saved_videos_icon_size: int = 100
    uploaded_videos_icon_size: int = 100
    # Per-sidebar-button multiplier (0-200%, 100 = no change) applied ON
    # TOP of the shared active_border_brightness/inactive_border_brightness
    # above -- e.g. 50% here on top of the shared 65% inactive brightness
    # gives this ONE button an effective 32.5% (darker still), while a
    # value above 100% only gets you back toward "no darkening at all"
    # (capped there), never actually brighter -- the underlying multiply-
    # blend darkening technique (_darken_pixmap-style) can only darken,
    # not lighten, a color; see _ScalingIconButton's own comment on this.
    library_border_brightness_multiplier: int = 100
    editor_border_brightness_multiplier: int = 100
    settings_border_brightness_multiplier: int = 100
    # Custom-image replacement + hue shift, for BOTH border types
    # (sidebar nav buttons and the unedited-video-clip highlight) --
    # unlike the per-button brightness multipliers above, these are
    # each ONE shared setting per border TYPE, not per individual
    # sidebar button, since the original ask phrased these two as "any
    # border"/"all borders" rather than "each" one. An empty image path
    # means "keep using the built-in gradient"; a hue shift of 0 means
    # no shift.
    sidebar_border_image_path: str = ""
    sidebar_border_hue_shift: int = 0
    unedited_border_image_path: str = ""
    unedited_border_hue_shift: int = 0
    # The selection border's own image override -- defaults to empty,
    # meaning "use the bundled selected_border_gradient.png" (a gold/
    # white diagonal streak, chosen directly by Max to replace the
    # original flat gray selection fill). No hue-shift field for this
    # one -- wasn't asked for, unlike the other two border types.
    selected_border_image_path: str = ""
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
    default_sound_path: str = ""  # legacy -- specifically the "replay buffer completed" keyframe's sound (see keyframes.py)
    # "Advanced Sound": a distinct sound for each pipeline checkpoint in
    # keyframes.PIPELINE_KEYFRAMES, keyed by its keyframe id. All optional
    # -- an unset keyframe just plays nothing. default_sound_path above
    # keeps working unchanged as the REPLAY_BUFFER_COMPLETED keyframe's
    # fallback specifically (for configs from before this existed), not
    # as a fallback for the other four, which are purely new additions
    # with no prior behavior to preserve.
    advanced_sounds: dict[str, str] = field(default_factory=dict)
    # "Error Noise": played instead of (never in addition to) a
    # keyframe's normal sound if that pipeline stage raises. Falls back
    # to default_error_sound_path when a keyframe has no specific one.
    error_sounds: dict[str, str] = field(default_factory=dict)
    default_error_sound_path: str = ""
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
    if "unedited_selected_border_width" not in appearance_raw and "unedited_highlight_width" in appearance_raw:
        # Pre-selection config format -- same field, old name.
        appearance_raw["unedited_selected_border_width"] = appearance_raw.pop("unedited_highlight_width")
    if "editor_icon_size" not in appearance_raw and "library_icon_size" in appearance_raw:
        # Pre-split config format -- library_icon_size used to
        # (incorrectly) drive ALL THREE sidebar nav buttons at once, so
        # an old config's single value becomes the starting point for
        # the two new sidebar-only fields too, preserving the old
        # visual size. saved_videos_icon_size/uploaded_videos_icon_size
        # are NOT backfilled from it -- those two are a different
        # setting entirely (the Library page's own tab icons, which
        # this bug never actually touched), so they just take their own
        # fresh defaults.
        shared = appearance_raw["library_icon_size"]
        appearance_raw.setdefault("editor_icon_size", shared)
        appearance_raw.setdefault("settings_icon_size", shared)
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
