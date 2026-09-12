# afterglow -- handoff

## What this app does
OBS-triggered clip capture, a local clip library (PySide6 GUI), a trim
editor (embedded mpv), and YouTube upload (unlisted-library metadata
cached locally, upload flow itself not yet implemented). Settings and
Library pages are solid and confirmed working across multiple
machines. Recent sessions have focused on Library filtering/display
features and app-wide appearance tuning rather than the Editor itself.

All files compile and import cleanly as of this handoff. This
session covered a lot of ground and corrected three of its own earlier
claims after more rigorous testing (see below) -- all real bugs that
compiled fine and passed a shallower test, caught only by testing
through the *actual* mechanism (real Qt event dispatch, a real
`QTabWidget` layout pass, a real `QContextMenuEvent`, real widget
geometry measured after a real layout pass) instead of calling the
handler function directly or only checking that a signal fired/a value
got set. That pattern held up well enough this session -- three
separate times -- that it's worth stating plainly for next time:
**calling a handler method directly proves the method's logic works;
it does NOT prove the method actually gets called, that Qt's
surrounding machinery behaves the way the code assumes, or that the
result actually LOOKS right once laid out.** A test asserting
"signal X fired" or "field Y equals Z" can pass while the actual
on-screen rendering/geometry is completely broken (see item 8's
addendum below -- video_widget claiming ~20% of window height instead
of ~80%, first NOTICED from a screenshot, not caught by this session's
own "verified end-to-end" testing of that same feature). Prefer
`QTest`/real event dispatch, a real widget actually shown in a real
layout, AND explicitly checking the resulting geometry/proportions
when a change touches layout at all -- not just that construction
didn't crash and the pieces exist.

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

8. **Watch Speed + Editor prev/next arrows.** `MpvVideoWidget.set_speed()`
   (a plain `self._mpv.speed = ...` property set, same pattern as the
   existing `set_volume()`) backs a new "Watch Speed" `QDoubleSpinBox`
   (0.05x-4.00x) in the Editor -- preview-only, doesn't touch the saved
   file, and resets to 1.00x on every `load_video()` call rather than
   persisting across videos (a judgment call made with Max away from
   his PC -- flagged as an open question below in case that's not what
   was wanted). Prev/Next arrows now flank `video_widget` directly
   (`video_row`, a new `QHBoxLayout` wrapping it). They cycle according
   to whichever Library tab the video was actually opened FROM, and
   specifically that tab's CURRENT card order (i.e. its live
   sort/filter/search state, re-queried fresh every time, not a
   snapshot taken back when Edit was first clicked) -- achieved without
   changing the existing `edit_requested` signal's signature across its
   three hops (card -> tab -> LibraryPage -> MainWindow): LibraryPage
   now tracks which tab most recently emitted `edit_requested`
   (`_last_edit_tab`) and exposes `neighbors_for(video_id)`, which
   MainWindow hands to `EditorPage.set_neighbor_provider()` once at
   startup. `_VideoGridTab.neighbors(video_id)` does the actual lookup
   against its own `self._cards` order. Hovering an arrow shows the
   target video's title as a tooltip; both disable cleanly at either
   end of the list, when nothing is loaded, or when no neighbor
   provider was ever set (e.g. a standalone `EditorPage` in a test).
   Verified end-to-end: opening a video from a live `LibraryPage`,
   confirming correct prev/next ids and tooltip text, actually clicking
   through Next/Prev across all three videos in a real 3-video library,
   and confirming Watch Speed changes propagate to the (mocked) mpv
   instance's real `speed` property and reset correctly on video
   change.

   **Bug found afterward (Max caught it from a screenshot, not this
   session's own testing):** wrapping `video_widget` in `video_row` for
   the arrows broke the Editor's whole layout -- the video collapsed to
   a short band near the top (~20% of window height) with all the
   remaining space left empty below it, pushing the trim timeline and
   everything else down into a huge dead zone. Root cause:
   `MpvVideoWidget`'s own default size policy is Preferred/Preferred,
   not Expanding. That was harmless as long as `video_widget` was added
   directly to the outer `QVBoxLayout` via `addWidget(widget,
   stretch=1)` -- an explicit numeric stretch factor on a widget added
   straight to a box layout is obeyed regardless of that widget's own
   size policy. Once it moved into its own nested `video_row`
   `QHBoxLayout` (added to the outer layout via `addLayout(video_row,
   stretch=1)`), that stopped being true: whether a *sub-layout* can
   actually claim extra space from its parent layout depends on
   aggregating whether anything inside it reports wanting to expand,
   and neither the arrow buttons (Fixed/Fixed) nor `video_widget`
   (Preferred/Preferred) did -- so the row just sat at its natural
   minimum height. Fixed with one line,
   `self.video_widget.setSizePolicy(QSizePolicy.Expanding,
   QSizePolicy.Expanding)`, set explicitly right where `video_widget`
   is constructed. This is exactly the kind of bug this file's own
   top-of-page lesson warns about -- it compiled fine and every
   existing test (including this item's own "verified end-to-end"
   above) still passed, because none of them checked actual on-screen
   geometry/proportions, only that signals fired and values were set
   correctly. Re-verified this time by measuring `video_widget`'s real
   height as a fraction of the window at two different window sizes
   (2560x1440 and 1600x900) -- was 21%/unknown-but-visibly-broken
   before the fix, is 81%/70% after it -- and reran every other
   existing test suite from this session to confirm nothing else
   regressed.

9. **Icon size settings split -- the confirmed "Library Page Icons
   Size does the wrong thing" bug, fixed.** `AppearanceSettings.
   library_icon_size` is no longer the one shared value driving all
   three sidebar nav buttons at once -- `_ScalingIconButton` now takes
   an explicit, required `icon_size_percent` constructor argument
   instead of reading `appearance.library_icon_size` itself, and
   MainWindow passes each of its three buttons its own new dedicated
   field (`library_icon_size` narrows to just that one button now;
   `editor_icon_size`/`settings_icon_size` are new). Separately,
   `saved_videos_icon_size`/`uploaded_videos_icon_size` (new) now
   ACTUALLY control the Library page's own Local/Uploaded tab icons,
   which the old (mis-wired) setting never touched at all. The
   interesting part: QTabBar only exposes ONE shared `iconSize` for
   the whole bar, so two independently-sized tab icons aren't directly
   possible through that property alone -- solved with a new
   `_composite_tab_icon()` helper that pre-renders each tab's icon onto
   a transparent canvas at the SHARED (larger of the two) size, with
   the actual icon content scaled to its own smaller target and
   centered within that canvas; since both canvases are already
   exactly the shared iconSize, Qt's own icon-scaling-to-fit does
   nothing further at paint time, and each tab's visually distinct size
   just falls out of how much of its own canvas is transparent padding
   vs. actual icon. `config.load()` migrates an old config's single
   `library_icon_size` into the two new SIDEBAR fields only (NOT the
   two tab-icon fields, which this bug never touched and so have no
   prior value worth preserving). Verified: defaults and migration
   directly against `config`; `_ScalingIconButton` now genuinely
   independent per instance; the composited tab icons' actual opaque
   pixel bounding boxes measured directly from a real `LibraryPage` at
   two different settings (confirmed meaningfully different sizes
   despite one shared `QTabBar.iconSize()`), including after
   `apply_scale()`; and a full Settings-page save/load round trip for
   all five new fields plus a full `MainWindow` construction smoke test.

10. **Per-sidebar-border brightness multiplier** (one third of the
    border customization suite -- see "Next up" below for the other
    two thirds, not attempted this pass). Three new fields
    (`library_border_brightness_multiplier`/`editor_.../settings_...`,
    each 0-200%, default 100 = no change) multiply ON TOP OF the
    existing SHARED `active_border_brightness`/`inactive_border_
    brightness`, rather than replacing them with three fully
    independent brightness pairs -- a judgment call (see "Open
    questions"), chosen because "multiplier" in the request read more
    naturally as a scalar on an existing value than as a full
    replacement setting, and because it's non-destructive by
    default (100% changes nothing for someone who never touches the
    new settings). Values over 100% cap at "no darkening at all"
    rather than actually brightening past that, since the existing
    multiply-blend darkening technique can only ever darken a color,
    never lighten one -- confirmed this explicitly with a test rather
    than assuming it. Verified the darken-factor math directly at
    100%/50%/200%, and a full Settings-page save/load round trip.

11. **Border image-replacement + hue shift** (the other two thirds of
    the border customization suite, completed after all -- Max asked
    to continue on it while away from his PC, so the "needs a real
    display to confirm" concern from "Next up" got resolved with
    judgment calls instead, clearly flagged below rather than silently
    assumed). New shared `afterglow/gui/pixmap_effects.py`:
    `resolve_border_pixmap()` (an existing custom image file, if set
    and it still exists, in place of the built-in gradient -- silently
    falls back to the built-in one if the path is empty or the file's
    gone missing) and `hue_shift_pixmap()` (a genuine per-pixel HSV hue
    rotation, skipping fully-transparent and fully-achromatic pixels,
    confirmed correct with red-shifted-180-degrees-is-cyan and similar
    direct checks). Four new `AppearanceSettings` fields --
    `sidebar_border_image_path`/`sidebar_border_hue_shift` and
    `unedited_border_image_path`/`unedited_border_hue_shift` -- each
    ONE shared setting per border TYPE (not per sidebar button, unlike
    item 10's multipliers), matching how the original ask phrased
    these two specifically as "any border"/"all borders" rather than
    "each" one; a judgment call, reversible if that reading's wrong
    (see "Open questions"). The custom image reuses the EXACT same
    stretch-to-fill-then-clip-to-ring rendering the built-in gradients
    already get, rather than introducing tiling or aspect-aware
    scaling -- so an odd-aspect-ratio custom image will stretch the
    same way the built-in gradients already do; this was itself a
    judgment call (see "Open questions" for the alternative).
    Performance mattered here in a way it hadn't for anything else in
    this session: hue-shifting is a real per-pixel Python loop (no
    numpy dependency exists in this project's flake to vectorize it),
    measured directly at roughly half a second for a 512x512 image --
    fine as a ONE-TIME cost, but `VideoCard.__init__` runs once per
    video shown in the grid, so without caching, a non-default
    unedited-border hue shift would have re-paid that cost for every
    single card. Added `hue_shift_pixmap_cached()` (memoized by
    `(cache_key, degrees)`) specifically to prevent that, and verified
    directly: building a `LibraryPage` with 5 unedited-video cards and
    a non-zero hue shift produces exactly ONE cache entry, not five.
    Also verified: `resolve_border_pixmap`'s three cases (empty path,
    missing file, real custom image) directly; the sidebar buttons
    correctly sharing one cached result when they share the same
    custom image path, and correctly falling back to their own
    distinct built-in gradients (with their own separate cache
    entries) when no custom image is set; and a full Settings-page
    save/load round trip for all four new fields.

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
- **"Library Page Icons Size" bug is fixed** (was: wired to all three
  sidebar nav buttons at once instead of the Library page's own tab
  icons) -- see "This session" item 9 above. Left as a note here only
  because it's the kind of thing worth double-checking actually looks
  right once there's a real display to check it on.
- **The Local/Uploaded tab pulse animates both icons together**, not
  just the clicked one -- a QTabBar limitation (one shared iconSize
  for the whole bar), noted above. Their SIZES can now differ (this
  session's icon-size split), but a pulse still moves both at once.
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
- **Nothing from the last two sessions' worth of work is verified on
  real hardware yet** -- all of it was exercised at the widget/logic
  level under an offscreen Qt platform (see the top of this file for
  why that's not the same as verification, and for two cases where it
  actually mattered). In particular: whether the darkened-icon
  transparency and fullscreen-monitor fixes look right on a real
  multi-monitor KDE Plasma setup; how multi-select's shift-click
  range-selection feels with a real mouse (a drag across multiple
  cards wasn't tested at all, only discrete clicks); whether
  `QFileSystemWatcher` on the DB file actually fires promptly against
  the REAL daemon process's real write pattern (only a simulated
  same-process external writer was tested); whether right-click-to-
  block's confirmed-working behavior holds on whatever desktop
  environment is actually running; and, newest, whether the
  independently-sized Local/Uploaded tab icons (this session's
  compositing trick) and the border brightness multipliers actually
  look right rendered for real, not just measured correct in pixel
  data from an offscreen render.
- **The Filters submenu (per-video) and the Filters dropdown (search)
  now have two separate, near-identical pieces of
  category/checkbox-building code** (`VideoCard._build_filters_menu`
  vs. `_VideoGridTab._rebuild_filters_menu`) -- a dedup opportunity,
  not attempted since their checkbox semantics actually differ (one
  toggles tag-on-video, the other toggles include/exclude-from-search).

## Next up
Nothing outstanding from an explicit ask right now -- all four items
from the border/icon-size/speed/navigation backlog are done (see "This
session" above). See "Open questions" below for a few judgment calls
made along the way that are worth Max confirming when he's back,
though none of them block anything from working.

## Architecture pointers
- `afterglow/gui/pixmap_effects.py` -- NEW this session.
  `resolve_border_pixmap()`, `hue_shift_pixmap()`, and
  `hue_shift_pixmap_cached()` (the caching matters -- see item 11
  above for why an uncached hue shift would have been a real per-card
  performance problem). Shared by `main_window.py` and `video_card.py`.
- `afterglow/gui/mpv_widget.py` -- `MpvVideoWidget.set_speed()`, new
  this session, mirrors the existing `set_volume()`'s pattern exactly
  (a plain property set on the live mpv instance, no persistence).
- `afterglow/gui/editor_page.py` -- new this session:
  `speed_spin` (Watch Speed) reset in `load_video()`;
  `prev_video_btn`/`next_video_btn` flank `video_widget` in a new
  `video_row`; `set_neighbor_provider()`/`_go_to_prev_video()`/
  `_go_to_next_video()`; `_refresh_display()` now also queries
  `self._neighbor_provider` and updates both arrows' enabled state +
  tooltip every time it runs.
- `afterglow/gui/library_page.py` -- new this session:
  `_VideoGridTab.neighbors(video_id)` (prev/next `Video` from that
  tab's own current `self._cards` order); `LibraryPage._last_edit_tab`
  + `_on_tab_edit_requested()` (replacing the old direct
  `.edit_requested.connect(self.edit_requested.emit)` wiring) +
  `neighbors_for()`; `_composite_tab_icon()` (module-level helper) +
  `_rebuild_tab_icons()` (replacing the old single
  `tabs.setIconSize()` + `addTab(..., icon, ...)` construction-time-only
  approach) for the Local/Uploaded tab icons' now-independent sizing.
  Also (earlier this session): `_PulsingTabBar.freeze_size_hint()`;
  `_ensure_selected_for_context_menu()`; the `QFileSystemWatcher`-based
  live refresh.
- `afterglow/gui/main_window.py` -- `_ScalingIconButton` now takes
  `icon_size_percent` (required) and `border_brightness_multiplier`
  (optional, default 100) as explicit constructor arguments instead of
  reading `appearance.library_icon_size`/the shared brightness fields
  directly -- all three sidebar buttons now pass their own dedicated
  values at construction (both new this session).
- `afterglow/config.py` -- five new icon-size fields
  (`library_icon_size` narrowed to just the one button,
  `editor_icon_size`/`settings_icon_size`/`saved_videos_icon_size`/
  `uploaded_videos_icon_size` all new) and three new border-brightness-
  multiplier fields, both sets added this session with `load()`
  migration shims for the icon-size split (brightness multipliers
  needed no shim -- brand new fields with a neutral 100% default, no
  prior single value to redistribute).
- `afterglow/keyframes.py` -- `PIPELINE_KEYFRAMES`, the five named
  pipeline checkpoints in order; deliberately its own module so a
  future animation-trigger system can import the same list rather than
  inventing its own copy.
- `afterglow/gui/advanced_sound_dialog.py` -- `AdvancedSoundDialog`,
  all the per-keyframe sound/error-sound file pickers, opened from
  Settings > Clip Capture.
- `afterglow/clips.py` -- `trigger_clip()` restructured around a
  single try/except tracking a `stage` variable (set to the UPCOMING
  keyframe before attempting its work, not after -- matters for
  error-sound attribution); `_play_keyframe_sound()`/
  `_play_error_sound()`/`_resolve_keyframe_sound()`. Also (earlier
  session): snapshots `autofilter.compute_active_auto_tags()` at
  pipeline start.
- `afterglow/gui/video_card.py` -- `_show_context_menu` builds
  `target_ids` from the whole multi-selection (via `get_selected_ids`/
  `ensure_selected` constructor callables) instead of always just
  `self.video_id`; `_build_filters_menu()` (categorized `QCheckBox`-
  per-tag submenu) replaced the old `AddTagDialog` (removed entirely);
  `_bulk_set_favorite()`/`_bulk_copy_to_clipboard()`/`_bulk_delete()`.
  `mousePressEvent` calls `event.accept()` on left-button clicks (was
  the whole selection-not-sticking bug two turns ago). `set_selected()`
  + `_selected` flag; `paintEvent`'s first branch draws the flat gray
  selection border ahead of the unedited-highlight branch.
- `afterglow/gui/pulse_animation.py` -- shared `_animate_to()` helper
  backing `press()`/`release(is_hovered)`/`hover_enter()`/`hover_leave()`.
- `afterglow/gui/main.py` -- launch resolves
  `QGuiApplication.screenAt(QCursor.pos())` and moves the window there
  before honoring `startup_window_mode`.
- `autofilter.py` -- Auto Add Filter detection (process scanning +
  kdotool/hyprctl/swaymsg); `list_running_process_display_names()` for
  the Settings dropdown.
- `afterglow/obs_client.py` -- `save_replay_buffer()` wraps
  `_save_replay_buffer_locked()` in a cross-process `fcntl.flock` on
  `CONFIG_DIR/replay_buffer.lock`.
- `afterglow/library.py` -- `rename_video()` renames the actual file
  (`_sanitize_filename_stem`, `_unique_path`); category CRUD;
  `set_favorite`, `tag_icons`, `tag_category_ids`, `get_video`,
  `add_tag_to_video`/`remove_tag_from_video`.
- `afterglow/gui/filters_settings_page.py` -- `_MultiFilterSelectButton`
  for Auto Add Filter's multi-select; `refresh_dynamic_lists()`.

## Open questions / pending decisions
- Whether the README rewrite (anonymizing the whole changelog, not
  just new sections) should happen as its own dedicated pass.
- Whether categories need their own rename/delete UI.
- **Watch Speed persistence** -- currently resets to 1.00x on every
  `load_video()` (a judgment call made with Max away from his PC,
  since the alternative readings -- per-video, per-Editor-session
  without resetting, or a global Settings default -- all seemed at
  least as plausible from the original wording). Easy to change to
  any of those if 1.00x-always-resets isn't actually what's wanted --
  the reset is one self-contained block at the top of `load_video()`.
- **Border brightness multiplier design** -- implemented as a scalar
  ON TOP OF the existing shared active/inactive brightness (see item
  10 above), not as three fully independent brightness pairs. If the
  intent was actually the latter (mirroring the icon-size split's five
  fully independent fields more literally), that's a fairly small
  rework: replace the multiplier fields with
  `library_active_border_brightness`/`library_inactive_border_
  brightness` pairs (x3), remove the shared fields' role for the
  sidebar (Settings' own `settings_border_mode` combo would stay as
  ​is), and adjust `_ScalingIconButton` accordingly.
- Border image-replacement + hue shift's fill behavior and composition
  order -- resolved with judgment calls rather than left open (see
  item 11 above): the custom image stretches to fill exactly like the
  built-in gradients already do (no tiling/aspect-aware cropping), and
  hue shift is applied once at load time to whichever pixmap (built-in
  or custom) ends up in use, BEFORE the multiply-blend darken step
  runs at paint time -- so darkening still behaves the same way
  afterward regardless of hue. Worth Max actually looking at once
  there's a real display, same as anything else visual from this
  session -- if the stretch behavior looks bad with a real image he
  picks, aspect-aware scale-and-crop would be the fix, in
  `resolve_border_pixmap` or wherever it's consumed.
- **Border image path / hue shift granularity** -- like the brightness
  multiplier, implemented as ONE shared setting per border TYPE
  (`sidebar_border_image_path`/`sidebar_border_hue_shift`, covering all
  three sidebar buttons at once) rather than per-individual-button.
  Unlike the multiplier, this was based on the original wording using
  "any"/"all" rather than "each" for these two specifically -- but if
  per-button granularity was actually wanted for these too, the same
  three-separate-fields rework as the multiplier's alternative above
  would apply, plus giving `_ScalingIconButton` its own custom image
  path parameter instead of resolving one from a single shared field.
- The two now-parallel Filters-checkbox-menu implementations
  (`VideoCard._build_filters_menu` vs. `_VideoGridTab.
  _rebuild_filters_menu`) could probably share more code -- worth a
  dedicated look rather than doing it opportunistically mid-feature.

## Workflow reminder
Edits are committed via a `full-git-update` command, then a
`flake-update` command to bump the consuming system flake's lock
file, then a rebuild. The project zip is expected alongside every
message where changes are made, not just at explicit handoff time.
