# afterglow -- handoff

## What this app does
OBS-triggered clip capture, a local clip library (PySide6 GUI), a trim
editor (embedded mpv), and YouTube upload (unlisted-library metadata
cached locally, upload flow itself not yet implemented). Settings and
Library pages are solid and confirmed working across multiple
machines. Recent sessions have focused on Library filtering/display
features and app-wide appearance tuning rather than the Editor itself.

All files compile and import cleanly as of this handoff, and the
GUI/DB/config layers have been exercised under an offscreen Qt
platform (categories, icons, favoriting, click-to-filter, info display
toggles, settings save round-trips, rename-renames-the-file,
replay-buffer lock behavior, appearance settings round-tripping)
since there's no real display or GPU in this sandbox to run the app
against directly. A segfault was observed on interpreter teardown
during one such offscreen test, after every functional assertion in
it had already printed successfully -- consistent with this sandbox's
pre-existing lack of a real GL context (see "Still unverified" below)
rather than a logic bug, but worth knowing about if it recurs.

## Currently being worked on
Three consecutive batches of Library/Settings/appearance features,
given together each time. Newest first.

### This session
- Bug fixes:
  - The Editor's "Select a video." prompt (shown when redirected there
    with nothing loaded) now clears when Library is clicked directly,
    rather than lingering indefinitely.
  - **Renaming a video now renames the actual file on disk**
    (sanitized filename, collision-safe via " (2)"/" (3)" suffixing),
    not just the DB title -- applies to both the Editor's rename and
    the Library context menu's, since both call the same
    `library.rename_video`.
  - **Fixed a real OBS replay-buffer race**: triggering a clip while a
    previous one's SaveReplayBuffer request hadn't yet been confirmed
    complete used to silently do nothing (OBS doesn't queue a second
    save while one's in flight). `obs_client.save_replay_buffer()` now
    holds a cross-process file lock for the full request-through-
    confirmed duration; a second caller (same process or a separate
    `afterglow-cli trigger` invocation) blocks until the first
    releases, then issues its own fresh save. Verified the blocking
    behavior directly (not against real OBS, which isn't available
    here).
- Library context menu: added **Copy**, which copies the clip file
  itself to the system clipboard (`text/uri-list`, the same mechanism
  a file manager's Ctrl+C uses) so it can be pasted directly into
  another app (Discord, etc.) -- no in-app paste target exists or is
  needed.
- Auto Add Filter rules can now apply **multiple filters per rule**
  (a checkbox multi-select dropdown, `_MultiFilterSelectButton` in
  filters_settings_page.py) instead of one filter name typed as text.
  `AutoFilterRule.tag_name: str` became `tag_names: list[str]`, with
  backward-compatible parsing in `config.load()` for configs written
  before this change. The dropdown also has a refresh hook so it
  doesn't go stale while the app keeps running and new filters get
  created elsewhere.
- New `config.AppearanceSettings`, covering what used to be hardcoded
  constants: sidebar border widths/brightness (active + inactive
  separately), whether/when the Settings nav button gets a border,
  the unedited-clip highlight's width/brightness, the filter icon
  size, the sidebar icon scale, and a "Resize Text to Fit" toggle for
  clip titles (shrinks the font to fit one line instead of wrapping).
  Settings' old flat "General" section (OBS/clips-dir/clip-options) is
  now a tab named **"Clipping"**; a new **"General"** tab holds these
  ten appearance controls. Sidebar-related settings only take effect
  on next launch (those buttons are built once at startup).
- Sidebar nav icons (not the gradient border, which already had its
  own separate darkening) are now 45% darker while not the active
  page -- done by precomputing a darkened `QIcon` variant per button
  and swapping on `toggled`, not by trying to paint over Qt's own icon
  rendering.
- Click-pulse animation: sidebar nav buttons and the Library's
  Local/Uploaded tab icons now ease down to ~82% size on mouse-down
  and ease back to full size on mouse-up (`gui/pulse_animation.py`,
  shared between the two despite them being different widget types --
  QToolButton vs QTabBar). The tab-bar case pulses both Local/Uploaded
  icons together rather than just the one clicked, since QTabBar only
  exposes one icon size for the whole bar, not per-tab -- see that
  file's docstring.
- Filter categories (previous session) got their "below"-location
  icon placement and general layout carried through unchanged; this
  session's work sits alongside it rather than touching it.

### Two sessions ago
- Filter categories as side-opening submenus, 3x larger/centered/
  auto-shrinking filter icons, the "Below" icon location moved to
  after the title, hover-tooltip + click-to-filter/block on card
  icons, sidebar gradient-border darkening (35%, distinct from this
  session's icon darkening), the "Info" dropdown (filters/length/
  size/date toggles), and the running-process dropdown for Auto Add
  Filter's app-match field.

### Three sessions ago
- Favoriting, block/exclude filters, "+ Add Filter" embedded in both
  dropdowns, the Library's Add Filter dialog redesigned as
  dropdown+"+", Editor clips pausing (not unloading) on tab switch, a
  fullscreen-on-launch setting + default window size fix, the original
  Settings > Filters and Auto Add Filter tabs, Auto Add Filter
  detection (`autofilter.py`, KDE Plasma via `kdotool`) hooked into
  clip capture, a `--filter` CLI flag on `trigger`, and the planned
  YouTube "unlisted library" behavior written into README.md.

## What's not working / not finished
- **Auto Add Filter "focused" detection via kdotool is still
  unverified on real hardware** -- there's been no live KDE Plasma/
  KWin session available in this sandbox across any of these
  sessions to test kdotool's actual output against. This remains the
  single biggest open unknown.
- **"Library Page Icons Size" was an interpretive call.** There's no
  other "icon" concept on the actual Library page distinct from the
  sidebar nav icons and the per-clip filter icons (which already has
  its own "Filter Icons Size" setting) -- it's wired to the sidebar
  nav icon scale (previously a hardcoded 0.85/85%). Flag it if that's
  not what was meant.
- **The Local/Uploaded tab pulse animates both icons together**, not
  just the clicked one -- a QTabBar limitation (one shared iconSize
  for the whole bar), noted above.
- **Per-card config/DB reads are still not cached** (`VideoCard.__init__`
  calls `config_module.load()` / `library.tag_icons()` per card, every
  grid rebuild) -- fine at current scale, flagged previously too.
- **README.md as a whole still isn't in the documented anonymous/
  impersonal style** -- only the "Planned: YouTube unlisted library
  behavior" section is. Rewriting the whole changelog is a separate,
  sizeable task.
- Category management still has no dedicated rename/delete-a-whole-
  category UI -- only per-tag membership changes, via a tag's
  "+ New Category..." dropdown entry in Settings > Filters.

## Architecture pointers
- `autofilter.py` -- Auto Add Filter detection (process scanning +
  kdotool/hyprctl/swaymsg); `list_running_process_display_names()` for
  the Settings dropdown.
- `afterglow/clips.py` -- `trigger_clip()` snapshots
  `autofilter.compute_active_auto_tags()` at the start of the
  pipeline and applies matching tags to the resulting video.
- `afterglow/obs_client.py` -- `save_replay_buffer()` now wraps
  `_save_replay_buffer_locked()` in a cross-process `fcntl.flock` on
  `CONFIG_DIR/replay_buffer.lock`.
- `afterglow/config.py` -- `AppearanceSettings` (new this session),
  `CardInfoSettings`, `FilterDisplaySettings`, `AutoFilterRule`
  (`tag_names: list[str]`, was `tag_name: str` -- see load()'s
  backward-compat shim).
- `afterglow/library.py` -- `rename_video()` now renames the actual
  file (`_sanitize_filename_stem`, `_unique_path`); category CRUD;
  `set_favorite`, `tag_icons`, `tag_category_ids`, etc. from earlier
  sessions.
- `afterglow/gui/pulse_animation.py` -- new file; shared press/release
  icon-size easing used by both the sidebar and the Library tab bar.
- `afterglow/gui/main_window.py` -- `_ScalingIconButton` now takes an
  `AppearanceSettings` instance and a `border_mode`; icon darkening via
  precomputed `QIcon` swap; pulse wired into mouse press/release.
- `afterglow/gui/library_page.py` -- `_PulsingTabBar` (QTabBar
  subclass) drives the Local/Uploaded tab pulse; `FilterCheckBox` and
  category submenus from earlier sessions unchanged.
- `afterglow/gui/video_card.py` -- `_copy_to_clipboard` (Copy context
  menu action); `set_font_scale` now implements Resize Text to Fit via
  `QFontMetricsF`; icon size and the unedited-highlight width/
  brightness now read from `AppearanceSettings` instead of hardcoded
  constants.
- `afterglow/gui/settings_page.py` -- "Clipping" + "General" +
  "Filters" tabs; `refresh_dynamic_lists()` passthrough to
  `FiltersSettingsPage`, called by MainWindow whenever Settings is
  navigated to (since it's built once at startup, not per-visit).
- `afterglow/gui/filters_settings_page.py` -- `_MultiFilterSelectButton`
  for Auto Add Filter's multi-select; `refresh_dynamic_lists()`.

## Open questions / pending decisions
- Whether the README rewrite (anonymizing the whole changelog, not
  just new sections) should happen as its own dedicated pass.
- Whether categories need their own rename/delete UI.
- Whether "Library Page Icons Size" should mean something other than
  the sidebar nav icon scale (see "What's not finished" above).

## Workflow reminder
Edits are committed via a `full-git-update` command, then a
`flake-update` command to bump the consuming system flake's lock
file, then a rebuild. The project zip is expected alongside every
message where changes are made, not just at explicit handoff time.
