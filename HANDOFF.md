# afterglow -- handoff

## What this app does
OBS-triggered clip capture, a local clip library (PySide6 GUI), a trim
editor (embedded mpv), and YouTube upload (unlisted-library metadata
cached locally, upload flow itself not yet implemented). Settings and
Library pages are solid and confirmed working across multiple
machines. Recent sessions have focused on Library filtering/display
features and app-wide appearance tuning rather than the Editor itself.

All files compile and import cleanly as of this handoff. This
session's four fixes were exercised under an offscreen Qt platform
against real widget instances (not just compiled/imported) -- a
`_ScalingIconButton` and a full `LibraryPage` (backed by a real
scratch SQLite DB and config dir) were actually constructed and driven
through hover/press/release events and icon-size changes, and
`_darken_pixmap()` was run against a hand-built transparent-icon
QImage and checked pixel-by-pixel. One correction came out of that
testing that isn't reflected in the "next up" writeup below (now
folded into "This session" instead): fixing only the initial
transparent-fill format wasn't sufficient on its own -- Qt's alpha
compositing math makes a full-canvas `CompositionMode_Multiply` fill
opaque everywhere regardless of pixmap-vs-image format (`alpha_out =
alpha_src + alpha_dst*(1-alpha_src)`, which is 1 wherever the opaque
gray fill lands), so a `CompositionMode_DestinationIn` re-clip against
the source icon's own alpha was also needed to keep the background
actually transparent. Earlier sessions' GUI/DB/config-layer exercises
(categories, icons, favoriting, click-to-filter, info display toggles,
settings save round-trips, rename-renames-the-file, replay-buffer lock
behavior, appearance settings round-tripping) since there's no real
display or GPU in this sandbox to run the app against directly. A
segfault was observed on interpreter teardown during one such
offscreen test in an earlier session, after every functional assertion
in it had already printed successfully -- consistent with this
sandbox's pre-existing lack of a real GL context rather than a logic
bug, but worth knowing about if it recurs.

## Currently being worked on
Five consecutive batches of Library/Settings/appearance features/bug
fixes, given together each time. Newest first.

### This session
All four items carried over from last handoff's "next up" list,
implemented and verified (not just compiled -- see the offscreen
widget-level testing note above):

1. **Inactive sidebar icons' white-box background** -- `_darken_pixmap()`
   in main_window.py now builds its result via `QImage` in
   `Format_ARGB32_Premultiplied` instead of a bare `QPixmap`, which
   isn't guaranteed to carry an alpha channel on every platform/format.
   Turned up a second bug while verifying the first: filling the whole
   canvas under `CompositionMode_Multiply` makes it opaque everywhere
   regardless of format (Qt's alpha math forces `alpha_out = 1`
   wherever an opaque color is painted, independent of blend mode), so
   a final `CompositionMode_DestinationIn` pass re-clips the darkened
   result back down to the source icon's own alpha shape. Verified
   pixel-by-pixel against a hand-built transparent icon: background
   corners stay at alpha 0, opaque icon pixels darken correctly.

2. **Tab bar height jump during the Local/Uploaded pulse** -- added
   `LibraryPage._fix_tab_bar_height()`, which locks the tab bar's
   height via `setFixedHeight(tabBar().sizeHint().height())` at
   construction and again in `apply_scale()` whenever the base icon
   size legitimately changes. The pulse animation's per-frame
   `setIconSize()` calls now only change how big the icon renders
   inside a constant-height bar. Verified directly: the tab bar's
   height doesn't move even when `setIconSize()` is called down to a
   tiny size, before or after `apply_scale()`.

3. **Hover downsize effect** -- `PulseAnimator` (gui/pulse_animation.py)
   is now built around a shared `_animate_to(target_fraction,
   duration_ms)` helper, tracking a `_current_fraction` so a new
   animation started mid-flight eases from wherever the icon actually
   is rather than snapping. `press()` -> 82%, `hover_enter()` -> 93%,
   `hover_leave()` -> 100%, and `release(is_hovered: bool)` -> 93% if
   the pointer's still over the widget or 100% if not. Wired into
   `_ScalingIconButton`'s `enterEvent`/`leaveEvent`/`mouseReleaseEvent`
   (passing its own `underMouse()` into `release()`) and
   `_PulsingTabBar`'s equivalents. Verified both widget types directly
   -- hover/press/release event handlers actually invoked against real
   `_ScalingIconButton` and `LibraryPage` instances, fraction values
   checked after each.

4. **Startup Window Mode combo box + wrong-monitor fullscreen** --
   `AppearanceSettings.startup_window_mode: str` (`"normal"` /
   `"maximized"` / `"fullscreen"`) replaces the old top-level
   `AppSettings.default_to_fullscreen: bool`, exposed as a combo box
   (not a checkbox pair, which could produce a contradictory
   both-checked state) in Settings > General; the old checkbox is gone
   from Clipping. `config.load()` migrates old `default_to_fullscreen
   = true` configs to `startup_window_mode = "fullscreen"` the same
   way `AutoFilterRule.tag_name` was handled previously. Separately,
   `main.py` now resolves the screen under the cursor at launch
   (`QGuiApplication.screenAt(QCursor.pos())`, falling back to
   `primaryScreen()`) and calls `window.setScreen(...)` +
   `window.move(screen.availableGeometry().topLeft())` before
   requesting fullscreen/maximized, since `setScreen()` alone doesn't
   reliably relocate the window first. Verified: defaults, the
   backward-compat migration from an old config file, and a
   save-then-load round trip of the new field, all against the actual
   `config` module (not reimplemented logic).

### Three sessions ago
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

### Four sessions ago
- Filter categories as side-opening submenus, 3x larger/centered/
  auto-shrinking filter icons, the "Below" icon location moved to
  after the title, hover-tooltip + click-to-filter/block on card
  icons, sidebar gradient-border darkening (35%, distinct from a
  later session's icon darkening), the "Info" dropdown (filters/length/
  size/date toggles), and the running-process dropdown for Auto Add
  Filter's app-match field.

### Five sessions ago
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
- **This session's four fixes are unverified on real hardware.** All
  four were exercised at the widget level under an offscreen Qt
  platform (see the top of this file), which catches logic bugs but
  can't confirm the actual visual result -- in particular, whether the
  darkened-icon transparency fix looks right at real screen DPI/
  scaling, and whether the fullscreen-monitor fix picks the correct
  monitor on an actual multi-monitor KDE Plasma setup.

## Architecture pointers
- `afterglow/gui/pulse_animation.py` -- generalized this session:
  shared `_animate_to(target_fraction, duration_ms)` helper backing
  `press()`/`release(is_hovered)`/`hover_enter()`/`hover_leave()`, with
  a tracked `_current_fraction` so animations started mid-flight ease
  from the current position instead of snapping.
- `afterglow/gui/main_window.py` -- `_darken_pixmap()` rebuilt this
  session on `QImage.Format_ARGB32_Premultiplied` +
  `CompositionMode_DestinationIn` re-clip (was a plain `QPixmap`, see
  "This session" above for why that alone wasn't enough);
  `_ScalingIconButton` gained `enterEvent`/`leaveEvent` wired to the
  pulse animator's new hover methods.
- `afterglow/gui/library_page.py` -- `LibraryPage._fix_tab_bar_height()`
  (new this session, called from `__init__` and `apply_scale()`);
  `_PulsingTabBar` gained `enterEvent`/`leaveEvent` and its `release`
  callback now passes `underMouse()` through.
- `afterglow/config.py` -- `AppearanceSettings.startup_window_mode:
  str` (new this session, replaces top-level
  `AppSettings.default_to_fullscreen: bool`); `load()`'s backward-
  compat shim for it sits alongside the existing `AutoFilterRule.
  tag_name` one.
- `afterglow/gui/main.py` -- launch now resolves
  `QGuiApplication.screenAt(QCursor.pos())` and moves the window there
  before honoring `startup_window_mode` (new this session).
- `autofilter.py` -- Auto Add Filter detection (process scanning +
  kdotool/hyprctl/swaymsg); `list_running_process_display_names()` for
  the Settings dropdown.
- `afterglow/clips.py` -- `trigger_clip()` snapshots
  `autofilter.compute_active_auto_tags()` at the start of the
  pipeline and applies matching tags to the resulting video.
- `afterglow/obs_client.py` -- `save_replay_buffer()` now wraps
  `_save_replay_buffer_locked()` in a cross-process `fcntl.flock` on
  `CONFIG_DIR/replay_buffer.lock`.
- `afterglow/library.py` -- `rename_video()` now renames the actual
  file (`_sanitize_filename_stem`, `_unique_path`); category CRUD;
  `set_favorite`, `tag_icons`, `tag_category_ids`, etc. from earlier
  sessions.
- `afterglow/gui/video_card.py` -- `_copy_to_clipboard` (Copy context
  menu action); `set_font_scale` now implements Resize Text to Fit via
  `QFontMetricsF`; icon size and the unedited-highlight width/
  brightness now read from `AppearanceSettings` instead of hardcoded
  constants.
- `afterglow/gui/settings_page.py` -- "Clipping" + "General" +
  "Filters" tabs; the fullscreen checkbox moved out of Clipping this
  session and a `startup_window_mode_combo` added to General instead;
  `refresh_dynamic_lists()` passthrough to `FiltersSettingsPage`,
  called by MainWindow whenever Settings is navigated to (since it's
  built once at startup, not per-visit).
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
