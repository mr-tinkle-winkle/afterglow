# afterglow -- handoff

## What this app does
OBS-triggered clip capture, a local clip library (PySide6 GUI), a trim
editor (embedded mpv), and YouTube upload (unlisted-library metadata
cached locally, upload flow itself not yet implemented). Settings and
Library pages are solid and confirmed working across multiple
machines. Recent sessions have focused on Library filtering/display
features and app-wide appearance tuning rather than the Editor itself.

All files compile and import cleanly as of this handoff. This
session covered a lot of ground and corrected two of its own earlier
claims after more rigorous testing (see below) -- both real bugs that
compiled fine and passed a shallower test, caught only by testing
through the *actual* mechanism (real Qt event dispatch, a real
`QTabWidget` layout pass, a real `QContextMenuEvent`) instead of
calling the handler function directly. That pattern held up well
enough this session that it's worth calling out explicitly for next
time: **calling a handler method directly proves the method's logic
works; it does NOT prove the method actually gets called, or that Qt's
surrounding machinery behaves the way the code assumes.** Prefer
`QTest`/real event dispatch, and a real widget actually shown in a
real layout, wherever plausible.

## Currently being worked on
Seven consecutive batches of Library/Settings/appearance
features/bug fixes, given together each time. Newest first.

### This session
Multi-select landed this session too (standard file-manager
conventions -- plain click selects one and sets an "anchor"; ctrl+click
toggles one card and moves the anchor to it; shift+click selects the
contiguous range from the anchor to the clicked card without moving
the anchor, so repeated shift-clicks re-range from the same fixed
point; clicking empty grid space clears the selection). A selected
card's border always wins over the unedited-highlight gradient (or
nothing, if edited) -- a flat gray fill at the same width as that
highlight, via a renamed, now dual-purpose
`AppearanceSettings.unedited_selected_border_width` (was
`unedited_highlight_width`, with the usual load()-time backward-compat
shim). Everything below happened in the same session as follow-up,
starting from three explicit asks (bulk context menu, a "Filters" side
panel, right-click-to-block not closing the menu) plus a report that
selection wasn't visibly working and the tab-bar-pulse layout shift
was still happening despite two sessions ago's fix -- then grew to
include a full Advanced Sound feature partway through. In order:

1. **Selection wasn't visibly working -- root cause found and fixed.**
   `VideoCard.mousePressEvent` emitted the `clicked` signal (correctly
   selecting the card) but then called `super().mousePressEvent(event)`,
   which leaves the event un-accepted -- Qt then bubbles it up to the
   parent, where `_SelectionClearingContainer`'s own `mousePressEvent`
   fired `background_clicked`, clearing the selection that had just
   been set, all within the same click. Fixed by calling
   `event.accept()` instead. This is exactly the kind of bug direct
   handler calls can't catch -- last session's tests called
   `_on_card_clicked` and `mousePressEvent` directly, which proved the
   selection logic itself was right but never exercised the real event
   bubbling that was undoing it. Verified this time via `QTest.mouseClick`
   on the actual `thumb_label`/`title_label` child widgets, confirming
   the selection now survives the same real click that sets it, and via
   `card.grab()` that the border pixel is genuinely gray afterward.

2. **Tab-bar pulse was STILL shifting page layout -- a second, deeper
   bug under the one already "fixed."** `QTabWidget`'s own internal
   layout sizes the tab bar vs. the page content below it using
   `tabBar().sizeHint()`, NOT the bar's actual rendered height --
   `setFixedHeight()` (two sessions ago's fix) only constrains the
   latter. The animation's per-frame `setIconSize()` calls kept
   changing `sizeHint()` regardless, so the content area's height still
   visibly moved every frame even though the bar itself didn't. Fixed
   with `_PulsingTabBar.freeze_size_hint()`, which caches `sizeHint()`
   at the un-animated icon size and overrides `sizeHint()` to always
   return that frozen value. Verified by actually stepping a real
   `QVariantAnimation` via `QTest.mousePress`/real timer ticks and
   reading `local_tab.geometry()` mid-pulse -- it changed from 792px to
   805px before the fix, stays at 792px throughout after it. As a
   side effect the bar's *width* is now frozen too (previously it also
   visibly narrowed during the pulse) -- not asked for, but strictly an
   improvement, not a behavior change anyone would want reverted.

3. **Bulk context-menu actions.** Right-clicking a card that's part of
   the current multi-selection now applies Favorite/Unfavorite,
   Upload, Copy, and Delete to the WHOLE selection; right-clicking a
   card that ISN'T part of it first replaces the selection with just
   that card (standard file-manager convention) via
   `_VideoGridTab._ensure_selected_for_context_menu`, called from
   `VideoCard._show_context_menu` before it builds the menu. Edit and
   Rename stay single-card-only (hidden, not just disabled, when 2+
   are selected) since neither has a sensible bulk meaning. `VideoCard`
   takes two new optional constructor callables, `get_selected_ids`
   and `ensure_selected`, wired up in `_VideoGridTab.refresh()`; a
   card built without them (e.g. directly, outside a `_VideoGridTab`)
   falls back to acting on just itself. Copy now puts one `QUrl` per
   selected file on the clipboard instead of one. Verified against a
   real multi-selection: right-click-keeps-selection vs.
   right-click-replaces-it, and that each bulk action (favorite, tag
   add/remove, delete) touches exactly the target set and nothing else.

4. **"Filters" side panel replaces "Add Filter."** The old
   single-tag `AddTagDialog` (a combo box + OK button) is gone --
   right-click > Filters now opens a hover-opening submenu, categorized
   the same way as the Library search's own Filters dropdown, with a
   plain `QCheckBox` per tag: checked when EVERY targeted video already
   has that tag, toggling adds/removes it across all of them at once
   (this is also how the bulk case above applies filters). A "+ Add
   Filter" button at the bottom creates a new tag globally, same
   one-shot behavior as the search dropdown's own "+" (doesn't apply it
   to anything, shows up next time a Filters menu is opened). Verified
   the "all have it -> checked, mixed -> unchecked" logic directly
   against real `library.Video` objects, and the create-new-tag flow
   with `QInputDialog` mocked.

5. **Right-click-to-block investigated -- confirmed it already works,
   NOT a bug.** First test attempt (`QTest.mouseClick(checkbox,
   Qt.RightButton, ...)`) showed the block state never changing and the
   menu closing -- looked like confirmation of the reported bug. But
   this was a **false negative from the test method, not a real bug**:
   `QTest.mouseClick` only synthesizes `MouseButtonPress`/
   `MouseButtonRelease`, and does NOT synthesize the separate
   `QContextMenuEvent` that `customContextMenuRequested` actually fires
   from -- that event is normally generated by the platform's native
   input handling on a genuine right-click, which this offscreen
   sandbox's synthetic clicks don't replicate. Re-tested by posting a
   real `QContextMenuEvent` directly (matching what native right-click
   input actually produces) -- the existing `FilterCheckBox` correctly
   set its blocked state AND the enclosing `QMenu` stayed open,
   confirming the feature already does what was asked. No code change
   made here. Flagging the test-methodology lesson at the top of this
   file since it's the second time this session a shallow test gave a
   false result in one direction or the other (see item 1's opposite
   case: a passing direct-call test that hid a real bug).

6. **Live library refresh.** The hotkey-triggered clip pipeline runs in
   a completely separate process (`daemon.py`), so there's no
   in-process signal for the GUI to connect to -- `LibraryPage` now
   watches `db.DB_PATH` itself via `QFileSystemWatcher`, debounced
   400ms (`_refresh_debounce`, a singleShot `QTimer`) to coalesce a
   burst of writes (one clip capture is a video-row insert plus one
   insert per auto-applied tag) into a single `refresh()`. Relies on
   SQLite's default rollback-journal mode writing to the main `.db`
   file directly on commit -- would need reworking if the app ever
   switches to WAL mode, where writes mostly go to a `-wal` sidecar a
   watch on the main file wouldn't see. Verified with a simulated
   external writer (a second, independent `sqlite3`/`library` call
   sequence into the same DB file, standing in for the daemon actually
   being a different OS process) -- confirmed one debounced refresh
   fires per burst, and the refreshed grid reflects both the new video
   AND the tag applied in the same burst.

7. **Advanced Sound + Error Noise.** New `afterglow/keyframes.py`
   defines `PIPELINE_KEYFRAMES`, the five named pipeline checkpoints
   (`hotkey_received`, `replay_buffer_sent`, `replay_buffer_completed`,
   `trim_finished`, `cleaned_up_moved`) in pipeline order -- deliberately
   its own module, not living in `clips.py` or `config.py`, since it's
   meant to be reused as the same ordered checkpoint list for a future
   animation-trigger system once that exists, per how this was framed
   when asked for. `AppSettings` gained `advanced_sounds: dict[str,
   str]` (keyframe -> sound path) and `error_sounds: dict[str, str]`
   (keyframe -> error sound path, played INSTEAD of the normal one if
   that stage's work raises) plus `default_error_sound_path` as the
   fallback for a keyframe with no specific error sound.
   `default_sound_path` (pre-existing) keeps its old meaning
   unchanged -- it's specifically `replay_buffer_completed`'s legacy
   fallback (chain: per-clip-config `sound_path` -> new
   `advanced_sounds[replay_buffer_completed]` -> old
   `default_sound_path`), NOT a fallback for the other four keyframes,
   which have no prior behavior to preserve. `clips.trigger_clip()`
   tracks a `stage` variable, set to the upcoming keyframe BEFORE
   attempting that keyframe's actual work (not after) so a failure
   during, say, the trim itself is correctly attributed to
   `trim_finished`'s error sound rather than whichever keyframe came
   before it -- confirmed this distinction mattered by writing a test
   for the wrong (after-the-fact) version first and watching it
   correctly fail. A single try/except wraps the whole pipeline rather
   than nesting one per stage. New `AdvancedSoundDialog`
   (gui/advanced_sound_dialog.py) opened via an "Advanced Sound..."
   button in Settings > Clip Capture, holding all ten-plus file
   pickers rather than cramming them into the main form. Verified: full
   keyframe-sound-order pipeline run (mocked OBS/trim/probe_duration,
   real `ClipConfig`/DB), the `replay_buffer_completed` legacy fallback
   chain including the per-clip-config override, error-sound stage
   attribution on both a mid-pipeline failure and a specific-vs.-fallback
   case, the dialog's read-back, and a full Settings-page save/load
   round trip including old configs with none of these fields at all.

### Two sessions ago
All four items carried over from that session's "next up" list,
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

### Five sessions ago
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

### Six sessions ago
- Filter categories as side-opening submenus, 3x larger/centered/
  auto-shrinking filter icons, the "Below" icon location moved to
  after the title, hover-tooltip + click-to-filter/block on card
  icons, sidebar gradient-border darkening (35%, distinct from a
  later session's icon darkening), the "Info" dropdown (filters/length/
  size/date toggles), and the running-process dropdown for Auto Add
  Filter's app-match field.

### Seven sessions ago
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
- **"Library Page Icons Size" is a confirmed bug, not just an
  interpretive call anymore** -- it's wired to the sidebar nav icon
  scale, but was actually meant to control the Library page's OWN
  icons (the Local/Uploaded tab icons). See "Next up" below -- the
  fix is to replace this one setting with five separate ones (Library,
  Editor, Settings, Saved Videos, Uploaded Videos), not just repoint
  the existing single setting at a different icon.
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
- **Nothing from this session or two sessions ago is verified on real
  hardware yet** -- all of it was exercised at the widget/logic level
  under an offscreen Qt platform (see the top of this file for why
  that's not the same as verification, and for two cases this session
  where it actually mattered). In particular: whether the
  darkened-icon transparency and fullscreen-monitor fixes (two
  sessions ago) look right on a real multi-monitor KDE Plasma setup;
  how multi-select's shift-click range-selection feels with a real
  mouse (a drag across multiple cards wasn't tested at all, only
  discrete clicks); whether `QFileSystemWatcher` on the DB file
  actually fires promptly against the REAL daemon process's real
  write pattern (only a simulated same-process external writer was
  tested); and whether right-click-to-block's confirmed-working
  behavior (item 5, this session) holds on whatever desktop
  environment is actually running, since the difference between the
  synthetic-click false negative and the real behavior came down to
  exactly the kind of platform-level input plumbing this sandbox can't
  fully replicate either way.
- **The Filters submenu (per-video) and the Filters dropdown (search)
  now have two separate, near-identical pieces of
  category/checkbox-building code** (`VideoCard._build_filters_menu`
  vs. `_VideoGridTab._rebuild_filters_menu`) -- a dedup opportunity,
  not attempted this session since their checkbox semantics actually
  differ (one toggles tag-on-video, the other toggles
  include/exclude-from-search).

## Next up -- explicitly asked for, not yet started
Four items, all from the same message, none begun:

1. **Watch speed setting.** A playback-speed multiplier field in the
   Editor (e.g. `0.1x` = 10% speed) -- presumably an mpv property
   (`speed`) set from a new spinbox/combo near the existing playback
   controls. Unclear yet whether this should persist as a per-video
   setting, a per-session Editor default, or a global Settings default
   -- worth clarifying rather than guessing.

2. **Editor prev/next-video arrows.** Left/right arrows flanking the
   video in the Editor that cycle to the previous/next video according
   to whatever the Library's current sort order is, with a hover
   tooltip showing which video you'd land on. Needs the Editor to know
   "what list am I in and at what position" -- presumably passed in
   from wherever `edit_requested` currently gets emitted (LibraryPage/
   MainWindow), rather than the Editor re-deriving the list itself.

3. **Border customization suite** -- three asks, scoped to sidebar
   borders and unedited-video borders specifically (not the new
   selection border):
   - Border brightness multiplier PER sidebar border/button (currently
     one shared `active_border_brightness` + one shared
     `inactive_border_brightness` for ALL sidebar buttons in
     `AppearanceSettings` -- would become a per-button dict or five
     named fields, mirroring the icon-size split below).
   - Replace any border (sidebar or unedited-video) with a custom
     image instead of the current gradient/flat-color rendering.
   - Hue shift option for both border types.

4. **Icon size settings split.** Replace the single (currently
   mis-wired, see above) `library_icon_size` with five independent
   settings: Library, Editor, Settings (all three currently share the
   sidebar's one scale), Saved Videos, and Uploaded Videos (the two
   Library tab icons, currently `LibraryPage._current_tab_icon_size`,
   one shared value for both tabs).

## Architecture pointers
- `afterglow/keyframes.py` -- NEW this session. `PIPELINE_KEYFRAMES`,
  the five named pipeline checkpoints in order; deliberately its own
  module so a future animation-trigger system can import the same list
  rather than inventing its own copy (see item 7 above).
- `afterglow/gui/advanced_sound_dialog.py` -- NEW this session.
  `AdvancedSoundDialog`, all the per-keyframe sound/error-sound file
  pickers, opened from Settings > Clip Capture.
- `afterglow/clips.py` -- `trigger_clip()` restructured this session
  around a single try/except tracking a `stage` variable (set to the
  UPCOMING keyframe before attempting its work, not after -- see item
  7's note on why that ordering specifically matters for error-sound
  attribution); new `_play_keyframe_sound()`/`_play_error_sound()`/
  `_resolve_keyframe_sound()`. Also (earlier session): snapshots
  `autofilter.compute_active_auto_tags()` at pipeline start.
- `afterglow/config.py` -- `AppSettings` gained `advanced_sounds`,
  `error_sounds`, `default_error_sound_path` this session (all plain
  dict/str fields, no nested dataclass, so no special `load()` shim
  needed beyond the default empty-dict/empty-string). Also (two
  sessions ago): `AppearanceSettings.unedited_highlight_width` renamed
  to `unedited_selected_border_width` (now serves both the
  unedited-highlight border AND the selection border), with `load()`'s
  usual backward-compat shim alongside the
  `startup_window_mode`/`AutoFilterRule.tag_name` ones.
- `afterglow/gui/video_card.py` -- `_show_context_menu` rewritten this
  session to build `target_ids` from the whole multi-selection (via
  two new optional constructor callables, `get_selected_ids` and
  `ensure_selected`) instead of always just `self.video_id`; Edit/
  Rename hidden (not disabled) when 2+ selected. Old `AddTagDialog`
  class removed entirely, replaced by `_build_filters_menu()` (a
  categorized `QCheckBox`-per-tag submenu, checked = every target
  video has that tag) plus `_create_new_filter()`. New
  `_bulk_set_favorite()`/`_bulk_copy_to_clipboard()`/`_bulk_delete()`
  replace the old single-video `_toggle_favorite()`/
  `_copy_to_clipboard()`/`_confirm_delete()` (removed). `mousePressEvent`
  now calls `event.accept()` on left-button clicks instead of falling
  through to `super()` -- see item 1 above for why that specific line
  was the whole selection bug. `set_selected()` + `_selected` flag;
  `paintEvent`'s first branch draws the flat gray selection border
  ahead of the unedited-highlight branch, which it takes priority
  over. `set_font_scale` implements Resize Text to Fit via
  `QFontMetricsF`.
- `afterglow/gui/library_page.py` -- `_PulsingTabBar` gained
  `freeze_size_hint()`/an overridden `sizeHint()` this session (see
  item 2 above -- `setFixedHeight()` alone, from two sessions ago,
  wasn't sufficient). `_VideoGridTab` gained
  `_ensure_selected_for_context_menu()` this session, called from
  `VideoCard` right before it builds its context menu. `LibraryPage.
  __init__` gained a `QFileSystemWatcher` on the DB file +
  `_refresh_debounce` (a singleShot `QTimer`) for live refresh.
  `_selected_ids`/`_selection_anchor_index`/`_on_card_clicked`/
  `_clear_selection`/`_apply_selection_visuals`/
  `_SelectionClearingContainer` (multi-select) from earlier this same
  session, described in the preface above.
- `afterglow/gui/pulse_animation.py` -- generalized two sessions ago:
  shared `_animate_to(target_fraction, duration_ms)` helper backing
  `press()`/`release(is_hovered)`/`hover_enter()`/`hover_leave()`.
- `afterglow/gui/main_window.py` -- `_darken_pixmap()` rebuilt two
  sessions ago on `QImage.Format_ARGB32_Premultiplied` +
  `CompositionMode_DestinationIn` re-clip; `_ScalingIconButton` gained
  `enterEvent`/`leaveEvent` wired to the pulse animator's hover
  methods. **This is also where the icon-size split (Next up #4) and
  the per-sidebar-border brightness/image/hue options (Next up #3)
  will mostly land**, alongside `AppearanceSettings` and
  `settings_page.py`.
- `afterglow/gui/main.py` -- launch resolves
  `QGuiApplication.screenAt(QCursor.pos())` and moves the window there
  before honoring `startup_window_mode` (two sessions ago).
- `autofilter.py` -- Auto Add Filter detection (process scanning +
  kdotool/hyprctl/swaymsg); `list_running_process_display_names()` for
  the Settings dropdown.
- `afterglow/obs_client.py` -- `save_replay_buffer()` wraps
  `_save_replay_buffer_locked()` in a cross-process `fcntl.flock` on
  `CONFIG_DIR/replay_buffer.lock`.
- `afterglow/library.py` -- `rename_video()` renames the actual file
  (`_sanitize_filename_stem`, `_unique_path`); category CRUD;
  `set_favorite`, `tag_icons`, `tag_category_ids`, `get_video`,
  `add_tag_to_video`/`remove_tag_from_video` (the last two now also
  used by `VideoCard._build_filters_menu`'s bulk toggle).
- `afterglow/gui/filters_settings_page.py` -- `_MultiFilterSelectButton`
  for Auto Add Filter's multi-select; `refresh_dynamic_lists()`.

## Open questions / pending decisions
- Whether the README rewrite (anonymizing the whole changelog, not
  just new sections) should happen as its own dedicated pass.
- Whether categories need their own rename/delete UI.
- Watch speed (Next up #1): does it persist per-video, per-Editor-
  session, or as a global Settings default?
- Border customization (Next up #3): per-sidebar-border brightness as
  a dict keyed by which button, or five named fields (mirroring
  whichever approach the icon-size split ends up using, for
  consistency between the two)? Also unclear whether "replace with an
  image" and "hue shift" should be mutually exclusive per border (an
  image doesn't really have a meaningful "hue shift" over a flat/
  gradient color fill) or independent toggles that happen to interact
  oddly if both are set.
- The two now-parallel Filters-checkbox-menu implementations
  (`VideoCard._build_filters_menu` vs. `_VideoGridTab.
  _rebuild_filters_menu`) could probably share more code -- worth a
  dedicated look rather than doing it opportunistically mid-feature.

## Workflow reminder
Edits are committed via a `full-git-update` command, then a
`flake-update` command to bump the consuming system flake's lock
file, then a rebuild. The project zip is expected alongside every
message where changes are made, not just at explicit handoff time.
