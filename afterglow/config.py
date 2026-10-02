"""
Settings storage for the clipping app.

Config lives at ~/.config/afterglow/config.toml
Clips/library lives at ~/Videos/Clips (configurable)

We use TOML because it's human-editable (useful for debugging / manual
fixes on a friend's machine) and there's a stable stdlib reader in 3.11+
(tomllib) plus a small writer dependency (tomli_w).
"""
from __future__ import annotations

import copy
import os
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
class ClipIndicatorSettings:
    """The clip indicator ("the clapper"): a movie clapper that slides onto the screen when a clip
    hotkey fires, claps when OBS confirms the save, then shows processing until the clip is in the
    library (and its input overlay is rendered).  Edited under Settings > Clip Capture > Clip
    Indicator; per-clip-type colours / icon / clap sound live on the clip type (clip_configs).
    Values are validated by the helper (indicator/model.py Style.from_dict) -- an unknown anchor or
    animation name falls back to the default rather than breaking the indicator."""
    enabled: bool = True
    style: str = "clapper"                    # "clapper" | "hands"
    # one of top_left top top_right left center right bottom_left bottom bottom_right
    anchor: str = "bottom_right"
    padding_x: int = 32                       # px from the screen edge (unused for top / bottom / center: centred)
    padding_y: int = 32                       # px from the screen edge (unused for left / right / center: centred)
    size: int = 156                           # px, the clapper's width
    enter_animation: str = "slide"            # slide drop pop swing spin toss flip peek fade
    exit_animation: str = "slide"             # slide zip fall shrink spin toss flip fade bow
    processing: str = "circle"                # "circle": exit then a loading circle; "stay": clapper stays
    circle_color: str = "#9a9a9a"             # while the clip is processed
    overlay_circle_color: str = "#9b5cff"     # while the input overlay is rendered
    circle_opacity: float = 0.55
    clapper_opacity: float = 1.0              # the clapper's own opacity (0.05 - 1)
    ring_pulse: bool = True                   # the ring pulses as each stage completes
    clap_sound: str = ""                      # JUST the clapper's noise ("" = the clip's usual sound)
    screen: str = "focused"                   # "focused": the screen holding the focused window | "primary"


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
    filters, then (if enabled) the action-buttons row -- each
    independently toggleable here."""
    show_filters: bool = True
    show_length: bool = False
    show_file_size: bool = False
    show_creation_date: bool = False
    # Edit/Copy/Filters/Delete as their own clickable buttons directly
    # on the card, below the filters section, rather than only reachable
    # via the right-click context menu. On by default per the request.
    show_action_buttons: bool = True


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
    # When True, every place a video's date is shown (the Library card
    # info box, the video previewer's header) uses the full timestamp
    # (date + hours/minutes/seconds, whatever's in created_at) instead
    # of just the date -- per the direct request.
    extended_dates: bool = False
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
    # selection existed (see load()'s backward-compat shim). Doubled
    # three times now, each directly on the request once he could
    # see it rendered for real -- 9 -> 18 -> 36 -> 72 -- this also
    # doubles the selection ring's width every time, since all of
    # these (the unedited-highlight border, the plain edited-video
    # border, and the selection ring) share this one setting.
    unedited_selected_border_width: int = 6
    # Darkened 35% (100 -> 65) directly on the request.
    unedited_highlight_brightness: int = 65
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
    # white diagonal streak, chosen directly  to replace the
    # original flat gray selection fill). No hue-shift field for this
    # one -- wasn't asked for, unlike the other two border types.
    selected_border_image_path: str = ""
    # ---- UI update (Phase 1: design-system foundation) ----
    # "Rounded Corners" (Settings > General) -- a single on/off toggle
    # plus one shared radius (px) for every rounded element (cards,
    # nested card boxes, sidebar borders, tab icons, text boxes, the
    # video player) -- not a per-element radius, per how it was asked
    # for ("a customizable radius next to the toggle", singular).
    rounded_corners_enabled: bool = True
    rounded_corner_radius: int = 24
    # "Padding" (Settings > General) -- ONE shared pixel value for
    # every gap this update touches: between video cards, between a
    # card's own edge and its two inner boxes, between those two boxes,
    # and (later) between sidebar panels -- rather than each spacing
    # having its own separate setting, per how this was actually asked
    # for ("adjusts the pixels of padding used everywhere"). Also
    # supersedes the earlier separate "video padding" ask from the
    # original UI Update spec -- that's just this same setting now.
    ui_padding: int = 14
    # Default on-card text style -- applies to the title AND the
    # info/date/tag-name lines (a judgment call: Max said "the default
    # text on videos" without specifying just the title; flagged in
    # HANDOFF.md in case just the title was actually meant).
    card_text_color: str = "#9bcbff"
    card_text_outline_color: str = "#24466d"
    # 3x the previous hardcoded value (1.0), now an actual setting --
    # only ever applied to the TITLE specifically (the three smaller
    # info/date/tag-name lines stay at 0 -- fill only, no stroke --
    # regardless of this setting, since even the thinnest usable
    # outline swallows their whole glyph interior at that small a font
    # size; see OutlinedLabel's own docstring/comment for the measured
    # reasoning).
    card_text_outline_width: float = 3.0
    # "Filter Outline" (Settings > General) -- outlines a filter icon's
    # own silhouette (not a bounding square) in card_text_outline_color
    # by default, overridable per-tag in Settings > Filters (see
    # library.py's tag outline_color column).
    filter_outline_enabled: bool = True
    # "Custom Buttons" (Settings > General) -- whether buttons across
    # the app are custom-painted (via the new theme system below)
    # instead of left as native Qt/KDE-styled widgets. Independent of
    # Afterglow Theme: with this on and Afterglow Theme off, buttons
    # are still custom-painted, just in colors sampled from the live
    # KDE/Qt palette instead of the fixed hex codes below.
    custom_buttons_enabled: bool = True
    # "Afterglow Theme" (Settings > General for the toggle, Settings >
    # Advanced for the actual hex values) -- overrides the color
    # SOURCE custom-painted elements read from (KDE palette -> these
    # fixed hex codes); it doesn't do anything on its own if
    # custom_buttons_enabled is off, since there's nothing custom-
    # painted left to recolor. Chosen directly :
    # blue/dark-desaturated-blue/super-dark-desaturated-blue/turquoise.
    afterglow_theme_enabled: bool = True
    # Exact hex given directly , most recently. Previous values
    # (computed derivations, etc.) are documented in this file's git
    # history / HANDOFF.md rather than here now that this is simply a
    # literal value he specified. The immediately-previous round of
    # this (all three of accent/card_background/library set to the
    # same #152c4f) was a copy-paste mistake in the values as originally sent, not an
    # intentional "make them all identical" choice -- corrected here to
    # the actual three distinct values he meant to send.
    afterglow_color_accent: str = "#152c4f"
    afterglow_color_card_background: str = "#091e37"
    # Given directly , most recently -- meant to eventually
    # become the actual app-wide background (not yet wired everywhere
    # -- see HANDOFF.md).
    afterglow_color_app_background: str = "#0d1621"
    afterglow_color_library: str = "#050f18"
    # Used for the Local/Uploaded tab icons' own background
    # specifically (not the library page background above, despite the
    # similar name -- see Theme.turquoise()'s own docstring for why).
    afterglow_color_turquoise: str = "#0c8ea0"
    # "normal" | "maximized" | "fullscreen" -- replaces the old
    # top-level default_to_fullscreen bool (see load()'s backward-compat
    # shim), moved here from the Clipping tab into General since it's
    # an appearance/startup-appearance concern, and widened to a third
    # choice: a checkbox pair for fullscreen+maximized could produce a
    # contradictory both-checked state, so this is one combo box instead.
    startup_window_mode: str = "normal"
    # Advanced Editor: which side the track headers (with the 3-line
    # reorder handle) sit on -- "left" | "right".
    editor_track_handle_side: str = "left"


@dataclass
class AppSettings:
    clips_dir: str = str(DEFAULT_CLIPS_DIR)
    # When True, the GUI skips its own filesystem scan/ingest/prune on
    # every open and refresh -- that work happens continuously in the
    # background daemon instead (see daemon.py's _library_scan_loop),
    # and the GUI just re-queries the DB (already-fast, no filesystem
    # walk) and relies on its existing DB-file-watcher to pick up
    # whatever the daemon just wrote. Per the initial suspicion that
    # this is better for drive health (one process doing the
    # filesystem walking instead of both the GUI and daemon
    # potentially doing it independently) -- default False (GUI does
    # its own scan, the original/unchanged behavior) since this is a
    # genuinely new code path he wants to be able to compare against,
    # not something to switch to blind.
    offload_library_scan_to_daemon: bool = False
    # When True (the default), Copying a video whose file isn't
    # already .mp4 exposes a .mp4-named copy to the clipboard instead
    # of the original extension -- a PURE rename via a fresh copy, no
    # remuxing or re-encoding of any kind, so it isn't guaranteed to
    # actually be valid, standards-conformant MP4 if the underlying
    # container/codec genuinely isn't MP4-compatible -- just renamed
    # to look like one for whatever's receiving the paste (many chat
    # apps and file managers only preview/embed a video reliably when
    # its extension says .mp4, regardless of the real container).
    auto_copy_as_mp4: bool = True
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
    # Input overlay (Puppetry): global default placement per overlay piece,
    # {piece: {"x", "y", "w", "rotation", "visible"}} (fractions of the
    # video; rotation in degrees). Pieces not listed use the built-in
    # defaults in input_overlay.DEFAULT_PLACEMENT. A clip type can override
    # single pieces (clip_configs.overlay_placements). Edited on
    # Settings > Input Overlay.
    overlay_placements: dict[str, dict[str, float]] = field(default_factory=dict)
    # The previewer's input-overlay toggle: ONE global, saved on/off for every
    # clip (flipped by the previewer's overlay button, remembered across
    # clips and restarts).
    preview_input_overlay: bool = True
    obs: OBSSettings = field(default_factory=OBSSettings)
    clip_indicator: ClipIndicatorSettings = field(default_factory=ClipIndicatorSettings)
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


def _load_uncached() -> AppSettings:
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
    # Only known keys: an older / newer build's config.toml must never stop the app (or the
    # daemon) from starting because of a field this build doesn't have.
    _ci_known = set(ClipIndicatorSettings.__dataclass_fields__)
    clip_indicator = ClipIndicatorSettings(**{k: v for k, v in raw.get("clip_indicator", {}).items() if k in _ci_known})

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
    # afterglow_color_library's role was reassigned TWICE across recent
    # sessions -- originally the actual turquoise color (#2ee0a6),
    # briefly considered as the newer turquoise value too (#12b5c8)
    # before that got its own separate field, and is now the dark blue
    # library-page background. A config saved under either OLD
    # assignment still has one of those two turquoise-ish hex codes
    # sitting in this field, and a code-side default change never
    # retroactively touches an already-saved value -- reported directly
    # as "the library background is turquoise" while every OTHER
    # Afterglow Theme color came through correctly, which is exactly
    # what you'd see from one specific stale field rather than a
    # systemic bug. Reset it back to the current correct default
    # whenever it's still holding one of those two specific old values.
    if appearance_raw.get("afterglow_color_library") in ("#2ee0a6", "#12b5c8"):
        appearance_raw["afterglow_color_library"] = AppearanceSettings.afterglow_color_library
    # Same reasoning as the library-color migration just above --
    # app_background's default was computed differently (an extra 25%
    # darkening step) later in the same session it was introduced, so a
    # config saved in the brief window between those two defaults would
    # otherwise keep showing the pre-darkened value forever.
    if appearance_raw.get("afterglow_color_app_background") == "#1b2e43":
        appearance_raw["afterglow_color_app_background"] = AppearanceSettings.afterglow_color_app_background
    if appearance_raw.get("afterglow_color_accent") == "#2161bb":
        # Same reasoning again -- accent's default became a computed
        # halfway-brightness value later in the same session it was
        # introduced.
        appearance_raw["afterglow_color_accent"] = AppearanceSettings.afterglow_color_accent
    if appearance_raw.get("afterglow_color_accent") == "#194a8e":
        # And again -- Max gave a new literal hex directly the very
        # next round.
        appearance_raw["afterglow_color_accent"] = AppearanceSettings.afterglow_color_accent
    if appearance_raw.get("afterglow_color_card_background") == "#274162":
        appearance_raw["afterglow_color_card_background"] = AppearanceSettings.afterglow_color_card_background
    if appearance_raw.get("afterglow_color_app_background") == "#142232":
        appearance_raw["afterglow_color_app_background"] = AppearanceSettings.afterglow_color_app_background
    if appearance_raw.get("afterglow_color_turquoise") == "#12b5c8":
        appearance_raw["afterglow_color_turquoise"] = AppearanceSettings.afterglow_color_turquoise
    # New palette given directly  this session: accent,
    # card_background, and library_background were all set to the SAME
    # hex (#152c4f) and turquoise moved to #0c8ea0 -- app_background is
    # untouched ("keep the current app background color"). Same
    # stale-default reasoning as every migration above: only resets a
    # config still holding the immediately-previous default, never a
    # genuinely custom value.
    if appearance_raw.get("afterglow_color_accent") == "#1d61b5":
        appearance_raw["afterglow_color_accent"] = AppearanceSettings.afterglow_color_accent
    if appearance_raw.get("afterglow_color_card_background") == "#1f3a5f":
        appearance_raw["afterglow_color_card_background"] = AppearanceSettings.afterglow_color_card_background
    if appearance_raw.get("afterglow_color_library") == "#1d2c3d":
        appearance_raw["afterglow_color_library"] = AppearanceSettings.afterglow_color_library
    if appearance_raw.get("afterglow_color_turquoise") == "#05a4b9":
        appearance_raw["afterglow_color_turquoise"] = AppearanceSettings.afterglow_color_turquoise
    # The above (#152c4f for all three of accent/card_background/
    # library) turned out to be a copy-paste mistake in the values as originally sent,
    # not an intentional "make them identical" choice -- corrected to
    # the three actual distinct values he meant to send. accent's
    # correct value happens to also be #152c4f, so it needs no
    # migration of its own here (nothing to change); card_background
    # and library do. Same stale-default reasoning as every migration
    # above -- only resets a config still holding that specific
    # previous (mistaken) value, never a genuinely custom one.
    if appearance_raw.get("afterglow_color_card_background") == "#152c4f":
        appearance_raw["afterglow_color_card_background"] = AppearanceSettings.afterglow_color_card_background
    if appearance_raw.get("afterglow_color_library") == "#152c4f":
        appearance_raw["afterglow_color_library"] = AppearanceSettings.afterglow_color_library
    if appearance_raw.get("card_text_outline_color") == "#3669a0":
        appearance_raw["card_text_outline_color"] = AppearanceSettings.card_text_outline_color
    # FOUND THE ACTUAL ROOT CAUSE of "asked to double this ~4 times and
    # it either hasn't worked or hasn't happened": Confirmed
    # is the SAME field that gets replaced by the unedited highlight --
    # i.e. exactly this one, unedited_selected_border_width -- and that
    # it's rendering at 3px on his real machine, nowhere near any of
    # the values below. The rename migration two blocks above
    # ("unedited_highlight_width" -> "unedited_selected_border_width")
    # carries an old config's value over VERBATIM under the new field
    # name -- so a config still on the pre-rename name with its
    # original small value (almost certainly 3, this field's actual
    # oldest default) got renamed correctly, but that carried-over
    # value never matched any number in the list below, so it sailed
    # through every one of these migrations untouched while the CODE
    # default kept climbing (9 -> 18 -> 36 -> 72 -> 144) in a direction
    # his real saved config could never follow. Every one of the past
    # "please double it" requests was reasonably based on what he
    # ACTUALLY saw rendered (a small few-pixel border that never
    # budged), not on the increasingly large code default -- which
    # explains the ever-growing gap. 3 is now added to this list so a
    # config carrying that original value finally gets carried forward
    # too, and the actual default is reset to a sane small number (6,
    # exactly double the reported 3px) instead of the wildly
    # disconnected 144 several sessions had climbed to without ever
    # actually being seen. 144 itself is ALSO added here so anyone
    # (this sandbox's own test configs included) sitting on that
    # now-understood-to-be-mistaken value gets corrected too.
    if appearance_raw.get("unedited_selected_border_width") in (3, 9, 18, 36, 50, 72, 144):
        # 50 is included even though it was never an actual code
        # default: the Settings page's border-width spinbox had its
        # range capped at 0-50 while the real default had already
        # grown past that, so QSpinBox silently CLAMPED the displayed/
        # saved value to 50 on every Settings page visit + Save --
        # meaning 50 in a saved config is almost certainly that bug's
        # damage, not a real choice. A GENUINELY custom value (anything
        # other than these seven specific numbers) is left alone.
        appearance_raw["unedited_selected_border_width"] = AppearanceSettings.unedited_selected_border_width
    appearance = AppearanceSettings(**appearance_raw)
    top_level = {
        k: v for k, v in raw.items()
        if k not in (
            "obs", "youtube", "filter_display", "auto_filters", "card_info", "appearance", "clip_indicator",
            "default_to_fullscreen",  # old field, folded into appearance.startup_window_mode above
        )
    }
    settings = AppSettings(
        obs=obs, clip_indicator=clip_indicator, youtube=youtube, filter_display=filter_display,
        auto_filters=auto_filters, card_info=card_info, appearance=appearance, **top_level,
    )
    _ensure_dirs(settings)
    return settings


# ---------------------------------------------------------------- load cache
#
# load() used to re-open and re-parse config.toml from scratch on EVERY
# call -- and it's called from constructors and even paintEvents all over
# the GUI (a single Library refresh with 80 clips measured 401 calls,
# ~0.39s of a ~0.55s refresh, i.e. over half the entire rebuild was TOML
# parsing the same unchanged file over and over). Now: a cheap os.stat()
# per call, and the file is only actually re-parsed when its mtime/size
# changed (so edits from the daemon process, a Settings save, an import,
# or a hand edit are all still picked up immediately). Callers get a
# deepcopy, never the cached object itself -- several callers load(),
# mutate fields, then save(); handing out a shared instance would let an
# unsaved mutation leak into every other caller's view of the config.
_cache_key: "tuple[int, int] | None" = None
_cache_value: "AppSettings | None" = None


def _stat_key() -> "tuple[int, int] | None":
    try:
        st = os.stat(CONFIG_FILE)
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size)


def load() -> AppSettings:
    global _cache_key, _cache_value
    key = _stat_key()
    if key is not None and key == _cache_key and _cache_value is not None:
        return copy.deepcopy(_cache_value)
    settings = _load_uncached()
    _cache_key = _stat_key()
    _cache_value = copy.deepcopy(settings)
    return settings


def load_readonly() -> AppSettings:
    """Same as load(), but returns the SHARED cached instance with no
    copy. For the (very common) read-only call sites -- constructors and
    paintEvents that just read a value, e.g. `load_readonly().appearance`.
    Never mutate the returned object; use load() for anything that edits
    fields and save()s."""
    global _cache_key, _cache_value
    key = _stat_key()
    if key is None or key != _cache_key or _cache_value is None:
        load()
    return _cache_value


def invalidate_cache() -> None:
    global _cache_key, _cache_value
    _cache_key = None
    _cache_value = None


def export_to_file(path: "Path | str") -> None:
    """Write the current settings out to an arbitrary file (not the
    normal CONFIG_FILE location) -- for backing up a configuration or
    handing a "new default settings" file to someone else, per Max's
    own request. Same TOML format as the real config file."""
    settings = load()
    data = asdict(settings)
    with open(path, "wb") as f:
        tomli_w.dump(data, f)


def import_from_file(path: "Path | str") -> AppSettings:
    """Read settings from an arbitrary file and adopt them as the app's
    actual current config. Routed through the real CONFIG_FILE + load()
    (rather than parsing the picked file directly into an AppSettings)
    so an exported file from an older build still goes through every
    migration/rename load() already knows how to do, instead of failing
    outright on a since-renamed or removed field."""
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "wb") as f:
        tomli_w.dump(raw, f)
    invalidate_cache()
    return load()


def save(settings: AppSettings) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = asdict(settings)
    with open(CONFIG_FILE, "wb") as f:
        tomli_w.dump(data, f)
    # Belt-and-braces: the mtime/size check alone would catch this, but
    # two saves inside one filesystem timestamp tick with an identical
    # byte count would otherwise look unchanged.
    invalidate_cache()


if __name__ == "__main__":
    s = load()
    print(f"Config file: {CONFIG_FILE}")
    print(s)
