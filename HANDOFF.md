# afterglow -- handoff

## What this app does
OBS-triggered clip capture, a local clip library (PySide6 GUI), a trim
editor (embedded mpv), and YouTube upload (unlisted-library metadata
cached locally, upload flow itself not yet implemented). Settings and
Library pages are solid and confirmed working across multiple
machines. Recent sessions have focused on Library filtering/display
features and app-wide appearance tuning rather than the Editor itself.

## MAJOR EPIC: UI Update + Editor Update (multi-session, in progress)
Max handed over a huge combined spec for a UI overhaul AND a full
Editor rebuild (tracks, segments, filters, transitions -- effectively
a new NLE). Explicitly told to expect "many sessions with minor
changes." **We are doing the UI Update first; the Editor Update below
hasn't been started at all.** Read this whole section before touching
anything in this epic -- it's the actual spec plus every clarifying
decision made so far, not just a changelog.

**Backend decision (asked, answered):** staying on PySide6/QWidgets
rather than porting to Qt Quick/QML or another toolkit. Reasoning:
everything asked for is achievable in QWidgets (custom `QPainterPath`
corners, custom-painted buttons, animated popovers, `QGraphicsBlurEffect`
for blur); mpv embedding already works and is fragile enough
infrastructure that re-embedding it in a different framework would be
its own multi-session risk; the NixOS flake already pins PySide6.
Building a proper internal design-system layer (rounded-corner/theme
utilities, custom button base classes) instead of one-off styling.

### UI Update -- full spec (condensed, all decisions folded in)

**Rounded Corners** (Settings > General, on by default, radius in px
next to the toggle, default 24px -- raised from an initial 12px guess
once Max could actually see it rendered):
- Applies to: sidebar borders (not yet rounded -- no sidebar widget
  reads `rounded_corners_enabled` yet), Library page tab icons (DONE --
  the two tabs' touching inner corners stay sharp, outer three corners
  each round normally), video borders (the Library card's unedited/
  selected highlight border DONE, the video player/thumbnail's own
  corners DONE, the outer background box and inner info box DONE),
  text boxes if feasible (not yet touched -- QLineEdit/QTextEdit
  elsewhere in the app still have square corners), the video/audio
  segments in the future Editor (skip for now -- segments don't exist
  yet), the corners of the app window itself (Max said to SKIP this
  entirely -- window rounding should just be whatever the KDE/window-
  manager theme already does, not a custom frameless-window
  implementation).
- "Apple style" clarified by Max: just means smooth/eased into the
  curve, NOT literally circular -- does NOT need true superellipse/
  squircle math. Implemented via a tuned cubic-Bezier approximation
  (see Architecture pointers).
- Two touching corners (e.g. adjacent sidebar buttons, adjacent tab
  icons) should NOT be rounded on the touching side. DONE for the tab
  icons; sidebar buttons aren't rounded at all yet so this doesn't
  apply there yet either.
- "Assume rounded unless there's a reason not to" -- default to
  applying this broadly as it gets built out, not narrowly.

**Padding** -- ONE shared setting (`AppearanceSettings.ui_padding`,
default 14px) controlling every gap this update touches: between
Library cards, between a card's own edge and its inner boxes, between
those boxes, and (later) between sidebar panels. DONE. Supersedes the
original spec's separate "video padding" item below, which is the
same setting now.
- ~~Video padding~~ -- a setting controlling both the space between
  Library cards AND the space between cards and the grid's own edges
  (one shared value, not two separate settings). Superseded by the
  unified "Padding" setting immediately above.

**Custom Buttons** (Settings > General, on by default) -- replace
native/KDE-styled buttons (Filters, Sort By, Info, refresh, many in
Settings) with custom-painted ones. Colors come from the LIVE KDE
theme by default (Qt's `QApplication.palette()` already reflects the
active Plasma color scheme, confirmed nothing in this app currently
overrides `setStyle()`) -- independent of Afterglow Theme below.

**Afterglow Theme** (Settings > General for the on/off toggle, on by
default; Settings > Advanced for the actual editable hex values) --
overrides Custom Buttons' color SOURCE from the live KDE palette to
these fixed colors (confirmed by Max: Afterglow Theme on literally
overrides the KDE-sampled colors). Does nothing if Custom Buttons is
off. **Current hex codes (superseded once already -- these are the
CURRENT correct ones, from the "Real-screenshot feedback round" item
in "Currently being worked on" below; ignore any hex codes quoted
earlier in this section's own history)**:
- `#2161bb` (blue) -- most buttons (Filters, Sort By, Info), and the
  card info box (holds filters/info/title). DONE, wired into the info
  box; NOT yet wired into actual buttons (Custom Buttons hasn't been
  applied to any real button widget yet, only built as a settings/
  color-source pair).
- `#274162` (darker blue) -- the card background portrusion behind
  videos. DONE, wired into `VideoCard`'s own background.
- `#1d2c3d` (super dark blue) -- the Library pages' (Local/Uploaded)
  own background. DONE, wired into `LibraryPage`'s grid/scroll area.
- A DERIVED color (10% brighter, 15% more saturated than the library
  background above, computed via `colorsys` -- currently `#1b2e43`) --
  the app's own overall background. DONE, wired into `MainWindow`'s
  central widget.
- `#12b5c8` (turquoise) -- reassigned from the library-page role above
  (which moved to the dark blue) to a narrower one: the Local/Uploaded
  TAB ICONS' own background specifically. DONE for those two tabs;
  Max mentioned filters as a possible future use of turquoise too,
  explicitly not done yet ("for now leave it as just local and
  uploaded").
- Pre-existing gradients (sidebar borders, unedited-video highlight)
  stay as-is in shape/border, but with Afterglow Theme on: the
  background behind a sidebar button becomes a darkened/desaturated
  version of that button's own border gradient. **NOT yet done** --
  this specific darkened-gradient-background piece for sidebar buttons
  hasn't been built, only the video-card side of the highlight
  redesign below.
  For the unedited-video highlight specifically: **REVISED again in
  item 18 below (this session) -- read that for the current, correct
  behavior.** Two sessions ago, per Max's clarification at the time, it
  was BOTH a border around the video player AND a background overlay
  on the card's own background box, rendered behind everything. After
  actually seeing it rendered, Max asked for that background-overlay
  part to be removed -- the card's own background is now ALWAYS plain
  `card_background()`, and the gradient shows ONLY as the video
  thumbnail's own border. The separate "always-on thin outline for
  contrast" item mentioned below this session's earlier item 17 has
  also been absorbed into that same border -- it's not a separate
  layer anymore, it's the plain-color fallback for whenever the
  gradient doesn't apply (edited videos, highlighting disabled, or a
  selected card). The background box's generous padding (from
  `ui_padding`) is still relevant on its own merits -- it's what keeps
  a sliver of the (now-always-plain) card background visible around
  the video box and info box regardless of content.
- Light shading is planned for later (subtle gradients replacing some
  flat fills) -- Max confirmed this doesn't need a redesign as long as
  color application is centralized (it now is -- see Theme class).

**Card restructure** (Library grid) -- **DONE as of "This session" item
16 below, except the two explicitly-deferred pieces marked below:**
- Centered names under clips. -- done (title_label was already
  center-aligned; confirmed still true after the restructure).
- Outer "background" box (portrusion, per above) containing an inner
  "info" box (portrusion) which holds filters + info + title, plus the
  video player/thumbnail as its own element alongside the info box
  within the outer box. -- done (`_InfoBox` + `video_box`).
- ALL clips normalized to the same size. -- done, and verified to
  actually be the fix for the bug below, not just a restyle.
- **Confirmed bug, FIXED** (see "Currently being worked on" below):
  spam-clicking the sidebar Library button or the per-tab Refresh
  button left stale, still-visible orphaned card widgets on screen
  from `deleteLater()`'s deferred deletion not actually hiding them
  first -- this WAS the "duplicate clips / messed-up sizing" bug,
  confirmed by direct reproduction, not a guess.
- Clicking the **info box** opens a separate, smaller preview player
  (confirmed by Max: "similar to Medal, a separate smaller video
  player" -- NOT the same as the full Editor, and NOT the same as the
  hover-autoplay-in-grid feature below; three distinct playback
  contexts once this all exists). **NOT YET BUILT** -- see "Next up".
  What clicking the **thumbnail/video player part** itself does,
  specifically, still isn't pinned down -- currently unchanged
  (whole-card select/double-click-to-edit) --
  worth confirming when this phase actually starts (current selection/
  double-click-to-edit behavior might just carry over unchanged, but
  should be confirmed rather than assumed).

**Comfy UI** (Settings > General, on by default) -- enlarges smaller
chrome elements (filters, sort by, info, text boxes, etc.). Confirmed
by Max: stacks MULTIPLICATIVELY with the existing window-size-based
scale system (`scaling.py`), rather than being an independent
fixed-size override.

**Video Info settings tab** (Settings > General) -- per-info-type font
size (video length, etc., plus two new info types below).

**Info list additions** (both on by default): video title, video
thumbnail.

**Search bubble** -- replace the current search box with a magnifying-
glass icon; clicking it expands a text box DOWNWARD (confirmed
direction) below the icon without displacing other layout, ideally
with a speech-bubble-style visual connector between icon and box.
Ctrl+F opens it.

**Hamburger menu** -- replaces the Filters/Sort By/Info buttons with
one hamburger icon that opens a floating (not a new page) panel split
into three vertical columns (Filters | Sort By | Info), each spanning
the full height of the panel. Confirmed: clicking outside it closes it
(standard popover dismissal).

**Hover-autoplay-in-grid** (separate settings toggle, confirmed
distinct from the info-box preview player above): hovering a card
starts a muted-by-default autoplay preview; click toggles pause/
resume; small volume meter bottom-left (same icon as the Editor's) and
fullscreen button bottom-right (icon TBD, Max providing); an Edit
button (editor icon); loops from the beginning once it reaches the end
rather than stopping.

**Middle-click** anywhere in the Library deselects the whole current
multi-selection.

**Ctrl+R** -- in the Library, does what the Refresh button does; in
the Editor, closes and reopens the Editor with the same video loaded,
prompting "Would you like to save?" first if there are unsaved
changes.

**Card text style** (Settings > General) -- default on-card text color
`#9bcbff` with a `#3669a0` outline, applied to ALL on-card text (title,
info/date lines, tag names -- Max: "all on-card text"). DONE, with a
real font-size caveat discovered while building it: at this app's
actual 10px info/date/tag-name size, no outline width leaves any fill
color visible at all (glyph strokes are only ~1px wide there), so only
the larger title gets a genuine two-tone effect -- see "Currently being
worked on" below for the measured details. Not something further
setting-tuning can fix; it's a font-size floor.

**Filter Outline** (Settings > General, on by default) -- outlines a
filter icon's own actual shape (not a bounding square) in the card
text outline color above, with a per-tag override color settable in
Settings > Filters next to that tag's icon controls. DONE.

**Icons Max is providing later** (build with placeholder icons for
now, swap in real assets once dropped): magnifying glass (search),
refresh, a fullscreen-button icon (for the small preview player), a
hamburger-menu icon, a drag-handle icon (for reordering something
draggable -- see Editor Update's track reordering), a lock icon (for
the future Editor's segment-locking).

### Editor Update -- full spec (NOT STARTED, reference only)
A complete Editor rebuild into a track-based NLE, opened via a
bottom-right "Advanced Editor" entry point (with a "Always Open
Advanced Editor" General setting to skip straight to it). Kept in full
detail here since it's a much later phase and easy to lose track of
otherwise:
- Classic playhead: drag-to-scrub, click-to-move, scroll-to-adjust-
  timestep, Space to play/pause, red vertical line with a "bulky head"
  indicator.
- Multi-select: Ctrl toggles individual segments; Shift selects
  everything between two segments on the same track, or (if the two
  are on different tracks) everything between them on every track in
  between, inclusive.
- An "Import" button left of the name field -- edit any arbitrary
  file; saving makes an `xxxxxxxxx-edited.<ext>` copy of the original
  rather than overwriting it.
- Multiple vertical-scrollable tracks (4 shown at once by default).
  Track 2-from-top is video, 2-from-bottom is audio, by default; top
  and bottom tracks are otherwise untouched UNLESS something gets
  dragged onto them, which creates a new track past it (above for the
  top track, below for the bottom). Tracks reorderable via a 3-line
  handle on the left (a General > Editor setting flips it to the
  right instead). No audio/video track type distinction -- either can
  go on any track.
- Audio segments render as a volume-over-time graph: a center line,
  drag vertically to set volume (0%-200%), or start typing a number
  while holding it to type an exact percent; the graph itself is a
  waveform.
- Video segments render as a film-strip of tiled screenshots centered
  on each strip's timestamp (exact tile size/count left to
  implementation, Max will give feedback once it's visible).
- `S` splits, `C` combines (segments must be touching). Combining a
  video and an audio segment does an inclusive merge (e.g. long audio
  + short video = black footage with audio once the video runs out),
  with a visible/clickable split marker wherever that merge boundary
  falls.
- Snapping: segment starts/ends snap to other segments and to the
  playhead; the playhead snaps (lightly) to segments.
- Overlap handling: placing a segment where it'd overlap another kicks
  it to the nearest fully-open track on its respective side instead.
- Right-click context menu per track/segment. Segments can be: Locked
  (`L` -- gray border + gray overlay + a lock icon centered over it),
  Muted (`M`), visibility-toggled (`V`), Copied (`Ctrl+C`), Pasted at
  the playhead (`Ctrl+V`). Tracks can be collapsed.
- Additions: zoom filter (configurable zoom-in/zoom-out timing per
  segment); on-screen elements (text with font choice, pictures, gifs
  -- all resizable, shown in the track as a transparent-background
  segment labeled with the element's name or, for text, the actual
  text); arbitrary audio file overlay anywhere in the video.
- Segment properties panel (opens on the right when a segment is
  selected): fade-in time, fade-out time.
- Transitions between back-to-back segments: cross-fade, blur/focus,
  slide [destination/original/both] from [top/right/bottom/left], fade
  [destination/original/both] from [top/right/bottom/left].
- Undo/redo. Drag-and-drop files directly into the timeline, if
  feasible.
- Heavy inspiration from Filmora/Premiere Pro/Final Cut Pro for
  feature set and rough organization.
- Rounded-corners rule specific to this: segments/tracks get rounded
  corners UNLESS snapped to another segment.

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

**A fourth, related lesson from this same recurring pattern, added
later in this same session:** an unscoped Qt stylesheet property
(`setStyleSheet("background-color: X;")` with no type/class selector)
is treated by Qt's style engine as applying to the WHOLE descendant
widget subtree, not just the widget it's called on -- and this
sandbox's offscreen platform plugin doesn't reliably reproduce that
cascading the same way a real compositor does, so a pixel-perfect
passing test *in this sandbox* genuinely is not proof a background
color is safe on Max's real machine, specifically for anything
touching `setStyleSheet` with a bare (unscoped) property. See item 19
below for the concrete case (a background color meant for one
container bleeding into every VideoCard's own custom-painted
background) -- prefer `QPalette` over an unscoped stylesheet property
for single-widget background colors going forward, and always scope a
stylesheet rule with an explicit type/class/object-name selector
(`"QScrollArea { ... }"`, not `"background-color: ...;"` alone) when a
plain QSS rule is unavoidable.

## Currently being worked on
Seven consecutive batches of Library/Settings/appearance
features/bug fixes, given together each time. Newest first.

### This session
Four more items:

1. **Full custom-widget sweep, "make sure EVERYTHING in settings is
   custom."** Replaced essentially every remaining native
   `QPushButton`/text-mode `QToolButton` with `CustomButton`, and
   every remaining `QCheckBox` with `CustomCheckBox`, across
   settings_page.py, filters_settings_page.py, clip_config_row.py,
   advanced_sound_dialog.py, hotkey_record_dialog.py, and -- since it
   was still using native KDE checkboxes/a native "+" button in its
   own Filters context menu, caught in the same sweep -- video_card.py.
   Cleaned up every import left unused by these swaps. Found and fixed
   one real bug along the way: several buttons called `.setFlat(True)`
   after becoming `CustomButton`s, which crashed immediately --
   `setFlat()` is `QPushButton`-only (`CustomButton` subclasses
   `QToolButton`, which has no such method, and doesn't need it --
   `CustomButton` already paints its own flat/borderless look
   unconditionally). Caught by the FULL regression suite actually
   failing outright, not silently -- removed the now-meaningless calls.
   `QComboBox` deliberately NOT touched -- a genuine custom dropdown
   (its own popup list, not just recoloring the closed box) is a
   meaningfully bigger build than a coat of paint, flagged rather than
   attempted here.
2. **Clip Options' collapse arrows** -- new `collapse_toggle_button.py`,
   `CollapseToggleButton`. A circle containing a ">" that smoothly
   rotates 90 degrees (`QVariantAnimation`, 180ms, `OutCubic`) between
   pointing right (collapsed) and down (expanded), replacing the old
   `QToolButton` arrow-type indicator that just snapped between two
   different glyphs.
3. **Sort popover and Settings tabs now "grow out of" whichever button
   opened them**, instead of just appearing -- new `scale_reveal.py`,
   two different mechanisms for two different kinds of widget:
   - `SortPopover` is a genuine top-level `Qt.Popup` window, so
     `animate_popup_from_point()` animates its own geometry AND
     opacity directly (0 -> full size, 0 -> full opacity, both
     `OutCubic`) from a small point near the Sort button up to its
     real final position/size -- nothing else's layout depends on a
     popup's geometry, so animating it directly is both simpler and
     correct.
   - Settings' tab pages live inside a `QStackedWidget`, whose layout
     fully owns and re-asserts each page's real geometry -- animating
     that directly would just get fought and overridden on the very
     next layout pass. Instead `reveal_from_point()` switches the page
     instantly (unchanged `QStackedWidget.setCurrentIndex`, so nothing
     about the real page is ever delayed), THEN grabs a pixmap
     snapshot of the now-fully-correct page and overlays a temporary
     animated copy of it growing from the clicked tab button's
     position up to the page's real rect, deleting itself once the
     animation finishes. Verified the overlay actually appears
     immediately after the click, that the real page has ALREADY
     switched (not waiting on the animation), and that the overlay
     widget cleans itself up once the animation completes.
4. **Library scanning can now be offloaded to the background daemon**
   (new `AppSettings.offload_library_scan_to_daemon`, off by default,
   toggle in Settings > Advanced > Performance). Per Max's own
   suspicion that this is better for drive health: with it on,
   `LibraryPage` skips its own `scan_and_ingest_new_videos()`/
   `prune_missing_videos()`/`remove_stray_orig_entries()` calls
   entirely (both at construction and on every `refresh()`) -- that
   filesystem walk now happens in a NEW daemon thread
   (`_library_scan_loop`, every 5s) instead, reusing the exact same
   library.py functions. The GUI still calls each tab's own
   `refresh()` either way (a plain DB re-query, no filesystem walk),
   and picks up whatever the daemon just wrote via the EXISTING
   `QFileSystemWatcher` on the DB file -- no new plumbing needed there,
   since that watcher already exists for exactly this
   "something external changed the DB" case. The setting is re-read
   fresh every daemon loop iteration (config.load() is cheap), so
   toggling it in the GUI takes effect within one interval on the
   daemon side too, no restart needed either way -- same "reload
   without restart" pattern `_reload_loop` already uses for hotkey
   config changes. Verified in both directions: with the setting off,
   the GUI's own scan still runs exactly as before; with it on, the
   GUI's scan is confirmed skipped (a manually-dropped-in clip does
   NOT appear until something else ingests it), and a daemon-style
   scan followed by a plain `refresh()` picks it up correctly.

### Previous session
Seven more direct pieces of feedback on last session's work, all
implemented and pixel-verified:

1. **Sidebar rewritten for real this time.** Last session's "turquoise
   layer underneath the old border system" was explicitly NOT what Max
   wanted -- he asked again for "the same custom button type as
   switching between local/uploaded videos in the library." Done
   properly now: `_ScalingIconButton` (border-gradient images, hue
   shift, per-button brightness multipliers, icon darkening, its own
   pulse animation) is DELETED entirely, along with its `_darken_pixmap`
   helper. `library_page.py`'s `_LibraryTabButton` is renamed to
   `LibraryTabButton` (dropped the leading underscore since it's
   genuinely shared across modules now) and gained a third `position`
   value, `'full'` (rounds all four corners -- for a button that
   doesn't touch its neighbors, unlike Local/Uploaded's `'left'`/
   `'right'` pair). All three sidebar buttons (Library, Editor,
   Settings) now use it with `position='full'`, and the sidebar's own
   `QVBoxLayout` spacing/margins are set to `appearance.ui_padding` --
   the EXACT same value the Library grid uses between cards, per Max's
   explicit "ensure that the padding between them is the same padding
   between videos" (not a separately-tuned constant that happens to
   look similar). Verified pixel-level: the gap between buttons equals
   `ui_padding` exactly, and every corner of every button now rounds
   (previously Library/Editor shared a flush, unrounded seam -- gone
   now that real padding separates them).
2. **Search bubble tail -- found the actual geometric cause of the
   harsh cutoff.** The previous fix's horizontal-tangent control point
   sat at `base_y` (`_TAIL_OVERLAP` px INSIDE the body, added for the
   separate outline-seam fix) rather than at `_TAIL_HEIGHT` (the
   actual visible boundary where the tail meets the body). The
   VISIBLE part of the curve -- everything above `_TAIL_HEIGHT` -- was
   therefore cut off before ever reaching that horizontal tangent,
   meeting the body's flat edge at whatever slope it happened to have
   at that height. Fixed by splitting each side into a visible cubic
   (peak down to `(base_x, _TAIL_HEIGHT)`, with the tangent
   guaranteed horizontal exactly there since the control point shares
   that exact y) plus a separate, purely straight, entirely-hidden
   `lineTo` continuing the remaining `_TAIL_OVERLAP` px into the body
   for the outline-seam fix -- decoupling "looks smooth where it
   matters" from "genuinely overlaps for the union fix" instead of
   asking one single curve to do both. (A pixel-width-per-row
   measurement approach was tried first and initially looked like it
   showed a remaining "jump" -- turned out to be a red herring: any
   curve with a true horizontal tangent naturally concentrates most of
   its horizontal unfolding in the row(s) right before it goes flat,
   since dy/dt approaches zero there. That's the correct signature of
   smooth flattening, not evidence against it -- confirmed by checking
   the actual math (the control point's y exactly equals its
   endpoint's y, which is what guarantees a horizontal tangent for a
   cubic Bezier) rather than continuing to chase pixel measurements
   that don't actually mean what they first appeared to.)
3. **Enter now toggles the confirm checkbox** instead of independently
   firing `search_confirmed` -- `line_edit.returnPressed` connects to
   `confirm_checkbox.toggle()`, and the checkbox's `toggled` (not
   `clicked`) signal is what actually emits `search_confirmed`, so
   both a real click and a programmatic `.toggle()` fire it exactly
   once and the checkbox's own visible state always reflects whichever
   happened most recently.
4. **Default thumbnail outline color** (the plain fill for an edited,
   non-highlighted video) changed from `app_background()` to
   `accent()` -- matches the info box's own color now, per direct
   request (supersedes last session's confirmation that the old
   app_background color was "right" -- that was true then, this is a
   deliberate change now).
5. **Every text field in Settings is now `CustomLineEdit`** (new file),
   matching the search bar's own box style (rounded, card_background
   fill, accent outline) -- text editing itself (cursor, selection,
   IME) is untouched native QLineEdit behavior; only the background/
   border painting is replaced, via the same "transparent stylesheet +
   custom paintEvent drawn first" layering trick CustomButton and
   _InfoBox already use. Covers all 14 real QLineEdit instances
   across settings_page.py, filters_settings_page.py,
   clip_config_row.py, and advanced_sound_dialog.py (opened FROM
   Settings > Clipping, so in scope) -- verified by walking every
   QLineEdit-family widget in a real constructed SettingsPage and
   confirming each one is a CustomLineEdit (excluding QSpinBox's own
   internal `qt_spinbox_lineedit` children, which are a different
   widget category entirely, not something asked about). The Library
   search bar's own line edit deliberately did NOT change to this
   class -- it already sits inside SearchBubble's own custom-painted
   comic-bubble shape, so wrapping it in a SECOND box would just
   double up the background/border.
6. **Custom scroll bar for the Library grid** -- new
   `custom_scrollbar.py`, `CustomScrollBar(QScrollBar)`. 2x
   `PM_ScrollBarExtent` wide, handle in `accent()` (info/button blue),
   track in `card_background()` (video card blue). Uses
   `QStyleOptionSlider` + the current style's own `subControlRect()`
   to find the handle's actual geometry rather than computing it by
   hand, which is what keeps real drag/click/wheel behavior intact
   underneath the custom paint. Swapped in via
   `SmoothScrollArea.setVerticalScrollBar()` -- last session's wheel-
   animation logic (`wheelEvent`) needed zero changes since it already
   only touches `verticalScrollBar().value()`, not the bar widget's
   identity.
7. **The 4 video-card action buttons (Edit/Copy/Filters/Delete)** now
   fill with the card TEXT color instead of the usual accent, with
   their own stroked outline in the card text's own outline color --
   "similar to the text" -- and are roughly 2x their previous size
   (48px tall + a larger font, vs. 24px/default font). `CustomButton`
   gained `set_fill_color()`/`set_outline()` as opt-in overrides (both
   default to None/0, so every OTHER CustomButton in the app --
   Search/Refresh/Sort, the Sort popover's tabs, Settings' tabs --
   is completely unaffected); the label's own text color now contrasts
   against whichever fill is ACTUALLY in use (`contrast_text(bg)`,
   computed after any hover/press darkening) rather than always
   against the theme's plain `button_color()`, which matters now that
   a button's fill isn't always the same accent color for every
   CustomButton instance.

Everything verified with real widget construction under offscreen Qt
plus pixel-level checks, same discipline as every session before this
one -- not just "it compiles."

### Previous session
A huge combined batch -- eleven items given together, plus a real,
long-standing bug finally root-caused. Newest first isn't practical
here given the volume; grouped by area instead.

**The border-width bug -- ROOT CAUSE FOUND, not just bumped again.**
Max confirmed this is the SAME setting that gets replaced by the
unedited highlight (`unedited_selected_border_width`), rendering at
3px on his real machine despite the code default having been doubled
four times across past sessions (9 -> 18 -> 36 -> 72 -> 144). Traced
to a real bug: this field was renamed at some point from
`unedited_highlight_width` to its current name, and that rename
migration carries an old config's value over VERBATIM under the new
name -- so a config still holding the field under its ORIGINAL name
with its ORIGINAL value (almost certainly 3, this field's actual
oldest default) got renamed correctly but that carried-over value
never matched any number in the separate "stale value -> bump
forward" migration list, so it sailed through every one of those
untouched while the CODE default kept climbing in a direction his
real saved config could never follow. Every one of Max's past "please
double it" requests was reasonably based on what he actually SAW
rendered (a small, never-budging border), not the increasingly
disconnected code default -- which is exactly why the gap kept
growing instead of closing. Fixed: default reset to 6 (double the
confirmed 3px), and 3 (plus the now-understood-mistaken 144) added to
the stale-value migration list. Verified by simulating Max's exact
scenario -- the old field name, holding value 3 -- and confirming it
now correctly becomes 6, plus confirming a genuinely custom value
still survives untouched.

**Colors, text, icons:**
- `card_text_outline_color`: `#3669a0` -> `#24466d`, with migration.
- Info/date/tag text (all fixed at 10px, much smaller than the title)
  now gets a proportionally THINNER outline than the title
  (`SMALL_TEXT_OUTLINE_SCALE = 0.5` in video_card.py) -- using the
  same width on both made the small text's outline look
  disproportionately thick relative to its own glyph strokes.
- Search/Refresh/Sort icons are now retinted (new `tint_pixmap()` /
  `tint_pixmap_cached()` in pixmap_effects.py, a single
  CompositionMode_SourceIn pass preserving each icon's alpha shape)
  to the card text color darkened 15% (`.darker(115)`), rather than
  shown in their own original artwork colors.
- The Library grid's horizontal scrollbar (reported as appearing
  "sometimes") is now forced off outright via
  `setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)` on
  SmoothScrollArea, rather than chasing an exact off-by-a-pixel
  rounding difference between the column-count calculation and the
  grid's actual spacing/margins.

**Custom checkboxes -- new `custom_checkbox.py`, `CustomCheckBox`.**
Video-card-background box + accent outline + the checkmark icon Max
provided, replacing native QCheckBox rendering. Wired into Favorite,
Highlight Unedited, and all four Info-page checkboxes.
`FilterCheckBox` (library_page.py) now subclasses `CustomCheckBox`
and adds a third "blocked" state using the x icon -- confirmed this
is the ONLY place x appears; every other CustomCheckBox only ever
shows the checkmark or nothing.

**Search bubble, round two:**
- Tail is now a genuine POINT at the top (was a small rounded/flat
  tip), via two cubics per side with the first control point pulled
  HORIZONTALLY toward the peak so the curve leaves the body's flat
  top edge on a near-horizontal tangent ("flatten out as it reaches
  the text bubble") before swinging sharply back in to the point --
  the "bump"/arc shape Max described, replacing the old smoother
  curve.
- `_TAIL_GAP` (6px): the bubble now floats that far below the button
  instead of touching it -- just an offset added in `show_below()`'s
  `move()` call, since the tail's own tip already sits at y=0 of the
  widget's fixed geometry.
- Search no longer runs live-as-you-type. New `SearchBubble.
  search_confirmed` signal, fired by Enter (`returnPressed`) or a new
  `CustomCheckBox` confirm button next to the field. LibraryPage
  splits what used to be one handler into `_on_search_text_typed`
  (icon-swap only) and `_on_search_confirmed` (the one place that
  actually writes into the active tab's search_edit and runs the
  filter).
- All verified pixel-level: the tail's near-tip row is transparent a
  good distance either side of center (a real point, not a wide flat
  top); the actual gap in `show_below()`'s positioning matches
  `_TAIL_GAP` exactly; typing alone leaves the active tab's search
  text empty, `returnPressed` commits it.

**Context-menu actions as on-card buttons.** New
`CardInfoSettings.show_action_buttons` (default True). When on,
VideoCard adds an Edit/Copy/Filters/Delete button row below the
Filters section, each reusing the EXACT SAME handler methods the
right-click menu's single-video actions already use (`edit_requested.
emit`, `_bulk_copy_to_clipboard({self.video_id})`, a new
`_open_filters_menu_for_self` that just `exec()`s the same
`_build_filters_menu` result the right-click Filters submenu builds,
`_bulk_delete({self.video_id})`) -- one source of truth for what each
action does, not a parallel reimplementation. Text labels for now
(Max: icons for these later). Toggle lives on the Info popover page
alongside the other three card-info checkboxes.

**Sidebar -- turquoise added, NOT a full rewrite.** Max asked for the
sidebar (Library/Editor/Settings) to become "custom buttons akin to
the current library local and uploaded tabs, use turquoise." Given
how much carefully-tuned functionality `_ScalingIconButton` already
has (border-image customization, hue shift, per-button brightness
multipliers, active/inactive icon darkening, the pulse animation,
resize-driven icon scaling) -- built and verified across MANY past
sessions -- a ground-up replacement risked breaking all of that for a
purely visual ask. Instead: `_ScalingIconButton.paintEvent` now draws
a turquoise background layer FIRST (full turquoise when that's the
active page, `.darker(140)` when it isn't -- same convention as the
Library page's own Local/Uploaded buttons), with everything else
(border gradient, icon, darkening) still drawing on top of it exactly
as before. Corner rounding follows the same "don't round a touching
seam" rule: Library rounds only its top corners (bottom touches
Editor), Editor rounds only its bottom corners (top touches Library),
Settings -- fully separate from both -- rounds all four. Verified
pixel-level (with the border-gradient temporarily omitted from the
test config, since it paints over most of the turquoise layer and
would confound a corner check): checked/active fill matches
`Theme.turquoise()` exactly, unchecked/inactive matches
`.darker(140)` exactly, and each button's corners round exactly where
expected. Flagged as a scoped-down delivery, not deferred: if Max
wants the FULL Library-tab-button treatment (dropping the border-
customization suite entirely rather than layering turquoise
underneath it), that's a separate, larger follow-up.

**Import/Export Settings** (Settings > Advanced, new group). New
`config.export_to_file(path)` / `config.import_from_file(path)` --
export writes the CURRENT full config (every tab, not just Advanced)
to an arbitrary .toml file; import reads one and adopts it as the
real config, routed through the real CONFIG_FILE + `load()` so an
older exported file still gets every migration/rename `load()`
already knows how to do, rather than parsing directly into
AppSettings and failing on a since-renamed field. Verified round-trip
(change a setting, export, reset to fresh defaults, import, confirm
the setting came back).

**Settings page's own custom-tab restyling.** Replaced `QTabWidget`
with the same pattern as the Library header: a `QStackedWidget` +
one `CustomButton` per page (Clipping/General/Filters/Stats/
Advanced), each FULLY rounded (all 4 corners, unlike Library's
touching Local/Uploaded pair) with real spacing between them via
`addSpacing()` -- "these tabs can be completely separate, each having
their own rounding and a bit of padding in between them." Text for
now; CustomButton already supports `set_icon_pixmap()`/
`set_circular()` for when Max provides per-tab icons and asks for
these to become circles later, same as the Library header's Search/
Refresh/Sort buttons -- no new plumbing needed for that step.
Verified pixel-level that every corner of every tab button rounds
(unlike the Library pair's touching seam, which deliberately does
NOT round on one side each).

**Still not done, explicitly deferred given the scope of this batch:**
custom text fields app-wide (replacing every native QLineEdit with a
search-bar-style custom box) -- a much broader, more open-ended sweep
across many files than anything else in this batch, not started.

### Previous session
Max caught his own copy-paste mistake from two sessions ago: the
"everything is the same color" report from last session wasn't a code
bug after all (confirmed then, holds up now) -- he'd meant to send
three DIFFERENT hex values for accent/card_background/library and
accidentally pasted the same one into all three. Corrected:
- `afterglow_color_card_background`: `#152c4f` -> `#091e37`
- `afterglow_color_library`: `#152c4f` -> `#050f18`
- `afterglow_color_accent` ("button/info blue"): confirmed unchanged
  at `#152c4f` -- this one really was meant to be that value.
- Turquoise and app_background: confirmed correct as-is, untouched.

Migration added for configs still holding the mistaken all-`#152c4f`
values (same stale-default pattern as every prior palette change) --
scoped per-field, so a config where accent is `#152c4f` is left alone
(that's still correct), while card_background/library at `#152c4f`
specifically get bumped to their new correct values. Verified this
doesn't clobber a genuinely custom value on an unrelated field
(planted a custom `#ff00ff` card_background alongside no accent/
library entries and confirmed it survives a `load()` untouched) --
worth checking given how many fields this migration now touches.

### Previous session
Two more direct pieces of feedback, plus a real bug found while
investigating the second one:

1. **Search/Refresh/Sort made circular, 2x size, with real spacing.**
   `CustomButton.set_circular(diameter)` is new -- forces a perfect
   circle via `setFixedSize(diameter, diameter)` + a `radius = height/2`
   paint path, independent of the Afterglow Theme rounded-corner-radius
   setting (which still governs everything else). Used for exactly
   these three buttons in `LibraryPage`'s header (72px diameter, up
   from whatever an unstyled `QToolButton`'s own default sizeHint was
   landing on before -- roughly 36px), with 16px of real spacing
   between them via `header.addSpacing()` calls (the Local/Uploaded
   buttons on the other side of the header still touch with zero
   spacing, unaffected). Verified pixel-level that the shape is
   actually circular (corner pixel differs from center fill), not just
   "square with a big enough radius to look round from a distance."
2. **Found and fixed the REAL bug behind "changing the border
   thickness in the past doesn't seem to have worked."** Max reported
   this while asking for the unedited/selected border to double again
   (144px) -- investigated rather than just bumping the number again.
   The actual cause: `settings_page.py`'s
   `unedited_selected_border_width_spin` had its range capped at
   `(0, 50)` while the real default (config.py) had ALREADY grown past
   that twice, to 72, in earlier sessions. `QSpinBox.setValue()`
   silently CLAMPS an out-of-range value instead of raising -- so every
   time Settings was opened, this field silently displayed 50 instead
   of the real 72, and clicking Save for ANY reason (even touching an
   entirely unrelated field elsewhere on the page) would write that
   clamped 50 back over the real setting, permanently undoing however
   many times the default had been bumped. Fixed two ways: raised the
   range to `(0, 400)` (real headroom past the new 144 default, so the
   same mistake doesn't quietly recur the next time this needs to
   grow), and added `50` to the existing stale-default migration list
   for this field (alongside 9/18/36/72) -- since a saved config
   showing exactly 50 is almost certainly this bug's damage rather
   than a genuine choice, and gets bumped forward to 144 the same way
   the other stale defaults do. Verified three ways: the spinbox now
   shows 144 (not clamped) on a fresh default config; a saved config
   with any of the five known-stale values (including 50) migrates to
   144 on load; and `VideoCard.video_box`'s actual on-screen size
   reflects the new 144px margin directly (`THUMB_SIZE + 2*144`), not
   just that the config field holds the right number.

**Investigated, NOT changed, needs Max's input:** "everything in the
Library page is the same color now... this remains even after I reset
my colors." Checked `Theme.accent()` / `card_background()` /
`library_background()` directly -- each still correctly reads its own
distinct config field (`afterglow_color_accent` /
`afterglow_color_card_background` / `afterglow_color_library`
respectively; confirmed no copy-paste bug). The reason they all render
identically is that last session's palette request set all THREE of
those fields to the literal same hex (`#152c4f`) on purpose. "Revert to
Default Colors" is also working exactly as coded (`_revert_default_colors`
resets to `AppearanceSettings()`'s own defaults) -- it doesn't change
anything because the CURRENT default already IS that same flat scheme,
not because the button is broken. So: not a bug, but also clearly not
what Max wants now that he's seeing it rendered -- flagged rather than
guessed at, since picking new specific hex values isn't something to
do without his input. The one piece he confirmed as already correct
(the edited-video border's color, which uses `app_background()`) makes
sense under this explanation too: that field was NOT part of last
session's "make these three the same" request, so it's still visibly
distinct from the rest.

### Previous session
Direct follow-up feedback on last session's four items, plus one more
long-queued item (Local/Uploaded's own custom page headers) finally
tackled. Seven pieces, given together:

1. **Search/Refresh/Sort moved into the header row.** Per Max's direct
   instruction ("place the search, refresh, and sort up top with the
   pages in the empty space instead of having their own little space
   that enroaches on the videos"), these three buttons no longer have
   their own per-tab row above the grid -- they're now shared, living
   once in `LibraryPage`'s own header alongside the new Local/Uploaded
   buttons (see item 2), in the empty space next to them. Necessitated
   restructuring how search text and the Sort popover's content get
   attached: `_VideoGridTab` still owns ALL the underlying state
   (search text, active/excluded tags, sort order, highlight toggle,
   card-info fields) and the three popover-page-builder methods, but no
   longer creates its own buttons/popups -- `LibraryPage` now owns one
   shared `SearchBubble` and one shared `SortPopover`, each re-pointed
   at whichever tab (`self._stack.currentWidget()`) is currently
   active. `_VideoGridTab.search_edit` is now a plain, never-shown
   `QLineEdit` used purely for text storage + its existing
   `textChanged` wiring; the shared bubble's own visible line edit
   syncs into/out of whichever tab is active on every tab switch
   (`_sync_search_bubble_for_active_tab`), so each tab's search query
   stays independent even though there's only one visible text field.
   The Sort popover's content is now rebuilt LAZILY, only right before
   it's shown (`_open_sort_popover` -> `_VideoGridTab.
   rebuild_sort_popover_pages()`), not on every refresh() the way the
   old per-tab version did -- a nice side effect of the ask itself,
   since a popover that isn't even open doesn't need to be kept in
   sync with every background DB-watcher refresh.
2. **Local/Uploaded got their own custom page headers** -- asked for
   multiple times per HANDOFF's own "Next up" list, finally done. New
   `_LibraryTabButton(QAbstractButton)` in library_page.py replaces
   `QTabWidget`/`QTabBar`/`_PulsingTabBar` entirely with two plain
   custom buttons + a `QStackedWidget`. This incidentally also solves
   the OTHER half of item 1 above (nowhere left to put a corner widget
   even if `QTabWidget.setCornerWidget()` had been used instead -- a
   fully custom header was needed either way to get search/refresh/
   sort into the tab bar's own row). Each button now gets a genuinely
   independent icon size directly (`set_icon_target_size()`) -- the old
   `_composite_tab_icon()` canvas-compositing workaround for QTabBar's
   single shared iconSize is gone completely, deleted along with the
   function itself, since there's no more QTabBar to work around.
   Corner rounding verified pixel-level after the refactor (by
   comparing the corner's color against the button's own plain fill
   color, not alpha -- LibraryPage isn't a translucent top-level popup
   the way SortPopover/SearchBubble are, so a `.grab()` of it always
   comes back opaque and alpha alone can't tell "rounded away" from
   "opaque"): Local's outer top-left corner reads as whatever's behind
   it, not turquoise; the seam corners on both buttons read as solid
   turquoise all the way to the edge. Deliberately scoped down from
   HANDOFF's original ask, flagged as a gap for later: the press/hover
   PULSE animation `_PulsingTabBar` had (matching the sidebar nav
   buttons) was NOT reimplemented -- these two buttons only lighten
   slightly on hover and darken slightly on press, like `CustomButton`.
   `neighbors_for`/`_last_edit_tab`/prev-next-tab-tracking all carried
   over unchanged, since `self.local_tab`/`self.uploaded_tab` still
   exist as real `_VideoGridTab` instances, just displayed via a
   `QStackedWidget` instead of `QTabWidget` pages now.
3. **Real icons for Search/Refresh/Sort**, replacing the text-label
   placeholders. `CustomButton` gained `set_icon_pixmap()` (draws a
   centered, aspect-ratio-preserved icon instead of the text label when
   set) since the button already fully replaces native painting and
   had nothing to hang a normal `setIcon()` off of. The four icons Max
   provided are bundled as `afterglow/gui/resources/{search,refresh,
   sort,search_icon_active}.png` (also covered by pyproject.toml's
   existing `gui/resources/*.png` package-data glob, so no packaging
   change needed).
4. **Search button swaps to the "active search" icon whenever the
   active tab's search box holds text**, back to the plain icon once
   it's empty -- `LibraryPage._update_search_icon()`, called from both
   the shared bubble's `textChanged` and on every tab switch (so
   switching to a tab with its own leftover search text immediately
   shows the right icon, not just after the next keystroke).
5. **Search bubble outline/tail redesign**, per three direct
   corrections on the previous version:
   - The outline used to visibly continue through the tail as if the
     tail and body had separate outlines. Root cause: the tail's flat
     base sat exactly COINCIDENT with the body's top edge (touching,
     not overlapping) -- `QPainterPath.united()`'s boolean-op result
     can leave a stray seam exactly where two shapes only share an
     edge rather than genuinely overlapping. Fixed by extending the
     tail's base `_TAIL_OVERLAP` (8px) PAST the body's top edge, into
     its interior, so that seam sits fully inside the united region
     and never becomes part of what Qt actually strokes. Verified
     pixel-level: sampled the pixel just inside the body's nominal top
     edge, directly under the tail's center, and confirmed it reads as
     the FILL color (card_background), not the outline color (accent)
     -- an outline-colored pixel there would mean the seam was still
     leaking through.
   - The tail is now a curved shape (two cubic Beziers meeting at a
     narrow, rounded tip) instead of a straight-edged triangle, and
     wider at its base (`_TAIL_WIDTH` 44px, "attach to more of the text
     bubble").
   - The bubble now centers itself directly under the anchor button
     (`show_below` computes the anchor's horizontal CENTER and centers
     the bubble on that) instead of left-aligning to it, which is what
     was actually causing "below it and to the side" with a bubble
     wider than the button.
6. **New color palette given directly by Max**: accent, card_background,
   and library_background all set to the SAME hex (`#152c4f`);
   turquoise moved to `#0c8ea0`; app_background explicitly left alone
   ("keep the current app background color"). Same stale-default
   migration pattern as every previous palette change in this file --
   added four new `if appearance_raw.get(...)  == <previous default>`
   entries so a config still holding any of the immediately-previous
   values (`#1d61b5` / `#1f3a5f` / `#1d2c3d` / `#05a4b9`) gets bumped
   forward automatically, without touching a genuinely custom value.
7. **Scroll frame-rate fix.** Reported directly: "the scrolling is low
   frame rate, but the movement is smooth" -- i.e. last session's
   SmoothScrollArea animation itself was fine, but something per-frame
   was expensive. Diagnosis matched Max's own suggestion exactly
   (video cards only need to change on resize; thumbnails only on
   refresh): `VideoCard.paintEvent`, `_InfoBox.paintEvent`, and
   `OutlinedLabel.paintEvent` were all reconstructing rounded-rect
   paths / gradient composites / glyph-outline paths from scratch on
   EVERY repaint, including the ones scrolling triggers purely from a
   widget's position changing, not its actual appearance. All three
   now render into a cached `QPixmap` once and just blit it on
   subsequent paints, invalidated only by an actual change: `VideoCard`
   keys its cache on `(size, selected, should_show_highlight)` (checked
   fresh every paintEvent, so no separate invalidation calls needed
   anywhere selection/highlight state changes -- the key comparison
   itself catches it); `_InfoBox` and `OutlinedLabel` invalidate on a
   size change or, for the label, an actual `setText`/`set_colors`
   call. Verified three ways, not just "it still renders correctly":
   (1) the exact same cache object (`is`, not just equal) survives an
   unrelated repaint; (2) it's replaced after a real selection change
   or resize; (3) a rough timing comparison (20-iteration average) of
   a cold `_render_background()` call vs. a warm cached repaint showed
   roughly a 4x cost reduction per card, per frame.

Two smaller items from last session remain genuinely open (not
touched this round, still flagged in "Next up"): whether the Stats
tab's three length rows should show a percent after all, and whether
the Sort popover's own tabs want hover/press pulse. This session ADDS
a third: the Local/Uploaded buttons' own pulse animation (see item 2).

### Previous session
Four more items, given together: smooth scrolling, a new Settings >
Stats tab, the Sort popover's horizontal-tabs-as-pages redesign, and
the search bubble redesign (the last of these was flagged as not-yet-
built in "Next up" item 7 below for several sessions -- now done).
All verified with real widget construction + pixel-level checks under
an offscreen Qt platform (no PySide6/tomli_w/obsws-python/evdev/
python-mpv were pre-installed in this sandbox this time either --
installed fresh via pip before testing, same as always).

1. **Smooth scrolling** -- new `afterglow/gui/smooth_scroll_area.py`,
   `SmoothScrollArea(QScrollArea)`. QScrollArea's default wheel
   handling jumps the scrollbar by a fixed step per notch with no
   easing, which is exactly what read as "snappy." Overrides
   `wheelEvent` to animate the vertical scrollbar's `value` property
   with `QPropertyAnimation` (`OutCubic`, 220ms) instead of setting it
   directly. A fast flick (several wheel notches in quick succession)
   extends the SAME in-flight animation's target rather than starting
   a new animation from the current (mid-flight) position each time --
   tested directly (`_anim_target` after a wheel event matches the
   expected accumulated step), not just that *an* animation exists.
   Swapped in for the Library grid's `self.scroll` in
   `library_page.py` -- a drop-in replacement, no other call site
   needed to change since it only overrides wheel handling.
2. **Settings > Stats tab** -- new `afterglow/gui/stats_settings_page.py`
   (`StatsPage`) plus `library.compute_stats()` / `LibraryStats`.
   Shows total videos; videos-with-a-filter (count | percent-of-total);
   an indented per-filter breakdown under that (count | percent, one
   row per known tag, in the same alphabetical order as
   `all_known_tags()`); unedited/edited counts (count | percent each);
   and average/longest/shortest video length. Combined Local +
   Uploaded (`list_videos()` with no filters already returns both).
   Length stats use the CURRENT (possibly trimmed) `duration_sec`, per
   Max's direct answer, not the original pre-trim capture length.
   Percentages deliberately NOT shown on the three length rows --
   there's no sensible "percent of what" for a duration, only for a
   count-of-videos subset -- flagged as a judgment call in case Max
   actually wants a percent shown there anyway (e.g. against some
   fixed reference length), since the ask's own wording technically
   covered "all except total." Read-only tab, no save() hook (unlike
   every other Settings tab) -- deliberately only recomputes when its
   own Refresh button is clicked, per Max's explicit instruction to
   avoid a full library scan (every video's tags + duration) running
   on every "Settings became visible," which is how `FiltersSettingsPage`
   handles ITS dynamic lists. Verified against a real SQLite-backed
   library with real ffmpeg-generated clips of different lengths, one
   trimmed via `apply_trim`, two different tags on different subsets,
   one favorited -- all the count/percent math checked against hand-
   computed expected values, not just "it rendered something."
3. **Sort popover redesign** -- new `afterglow/gui/sort_popover.py`
   (`SortPopover`, `_PopoverTabButton`, `_RoundedContentArea`). Replaces
   the combined Filters/Sort By/Info `QMenu` (three labeled sections
   stacked vertically -- the "vertical tiling" Max wanted gone) with an
   actual custom popup: three horizontally-tiled tabs switching a real
   `QStackedWidget` page below, rather than a dropdown list. Rounding
   exactly as specified: the two OUTER tabs round only their own outer
   top corner (left tab: top-left only; right tab: top-right only); the
   MIDDLE tab has no rounding on any corner; no tab rounds a corner
   that touches a neighbor or the content area below, same "don't round
   a touching seam" rule already used for the Local/Uploaded tab icons.
   Verified PIXEL-LEVEL, not just by checking the boolean flags passed
   in: grabbed the whole (translucent-background) popover as a QImage
   and sampled each tab's two top corners directly -- left tab's top-
   left alpha=0 (rounded away) and top-right alpha=255 (square,
   touching middle); middle tab both alpha=255 (no rounding at all);
   right tab mirrors left. (Grabbing an individual CHILD widget alone
   rather than the translucent top-level popover was tried first and
   gave a false failure -- a bare child widget's own `.grab()` isn't
   guaranteed an alpha channel, so every pixel read back opaque
   regardless of what actually got painted; switched to grabbing the
   popover itself and mapping each button's corner into popover
   coordinates instead.) Colors: active tab + content panel border use
   `Theme.accent()`; inactive tabs + content panel fill use
   `Theme.card_background()` -- "the color scheme as mentioned," reusing
   the existing Afterglow Theme palette rather than a one-off choice.
   Added `contrast_text()` to `theme.py` as a standalone function
   (factored out of `Theme.button_text_color()`, which now just calls
   it) so the tab buttons' text color can be computed against whichever
   of the two fills is actually active, not just against
   `button_color()`. The old QMenu content (favorite checkbox, per-
   category groups -- now `QGroupBox`es instead of side-opening
   submenus, since a fixed page has nowhere for a submenu to open TO --
   uncategorized tags, +Add Filter, Highlight Unedited, the 8 sort
   options as `QRadioButton`s in a `QButtonGroup`, the 4 Info
   checkboxes) all moved over as real inline widgets built by three new
   `_build_filters_page()` / `_build_sort_page()` / `_build_info_page()`
   methods on `_VideoGridTab`, called from `_rebuild_toolbar_menu()`
   (name kept as-is despite no longer building a menu, to avoid
   touching every call site's name too) on the same every-refresh
   cadence as before. Each page is wrapped in a fixed-height (320px),
   frame-less `QScrollArea` so a long tag list can't grow the whole
   popover past the screen. Verified with a real categorized tag
   (`create_category` + `set_tag_category`) exercising the QGroupBox
   branch, not just the flat uncategorized-tags path.
4. **Search bubble redesign** -- new `afterglow/gui/search_bubble.py`
   (`SearchBubble`). Replaces the old `_toggle_search_visibility`
   placeholder (which just showed/hid a plain `QLineEdit` sitting
   beside the Search button in the same row) with an actual popup that
   appears UNDERNEATH the button and visually attaches to it via a
   small triangular tail, like a comic speech bubble -- `Qt.Popup` +
   `WA_TranslucentBackground`, painted as one unified
   `QPainterPath.united()` of a rounded body and the tail triangle so
   the outline strokes as one continuous shape rather than two
   overlapping ones. Outline color is `Theme.accent()` (the button
   color); fill is `Theme.card_background()` (the video card
   background color), per Max's explicit color pairing for this one --
   note this is the OPPOSITE pairing convention from the Sort popover's
   content panel above only in which theme color plays which role, not
   a new color source. Auto-closes on an outside click for free via
   `Qt.Popup`'s own mouse-grab behavior -- no separate "clicked
   elsewhere" handling needed, confirmed this is what Max wanted when
   asked directly. `search_edit` is now just an attribute alias
   pointing at `SearchBubble.line_edit` (`self.search_edit =
   self.search_bubble.line_edit`) so every existing reference
   elsewhere in `_VideoGridTab` (the `textChanged` connection,
   `refresh()`'s `search=self.search_edit.text()`) needed zero changes.
   Verified pixel-level the same way as the Sort popover: grabbed the
   whole bubble, confirmed its far corners are transparent (outside
   both the rounded body and the tail triangle), the body center is
   opaque, and -- the part actually specific to a speech bubble rather
   than a plain rounded box -- the tail's tip pixel (directly under
   where the button sits) is ALSO opaque, i.e. the tail is actually
   there and pointing at the right place, not just a rounded rectangle
   with a transparent gap where a tail should be. Also confirmed the
   fill pixel reads back as EXACTLY `card_background()`'s hex value,
   not just "some opaque color."

Two unresolved judgment calls from this batch, both flagged above and
worth a quick confirm next time: whether the three length-stat rows
in the Stats tab should actually show some form of percent despite it
not being a video-count subset, and whether per-button hover/pulse
feedback (like the sidebar nav buttons and the existing `CustomButton`
have) is wanted on the new Sort popover tabs -- they currently only
lighten slightly on hover via `_PopoverTabButton`'s own `underMouse()`
check, no pulse animation.

### Previous session
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

12. **Clear-hotkey button.** A small "\u2715" `QToolButton` next to
    "Record..." in each Clip Options row (Settings > Clip Capture) --
    `ClipConfigRow._clear_hotkey()` just empties `hotkey_edit` and fires
    the same `changed` signal a normal edit would, so it goes through
    the exact same save path as everything else in that row. A no-op
    (doesn't re-emit `changed`) if the hotkey's already empty. Verified
    directly against a real `ClipConfigRow`.

13. **Selection border swapped from flat gray to a gold/white
    gradient image**, per an image Max provided directly (now bundled
    as `afterglow/gui/resources/selected_border_gradient.png`).
    `VideoCard.paintEvent`'s selected branch now `drawPixmap`s this
    (stretched to fill, same technique as the unedited-highlight
    branch) instead of `fillRect`-ing `#999999`. Given `resolve_border_
    pixmap`/the custom-image-override pattern already existed from item
    11, extended it here too for consistency: new
    `AppearanceSettings.selected_border_image_path` (empty = use the
    new bundled default), with a matching Settings > General field --
    but deliberately NO hue-shift field for this one, since that
    wasn't part of what was asked. Verified: the default pixmap is
    genuinely the new bundled image (not the old gray), a SELECTED
    card's actual rendered pixel is a non-gray color matching the
    gradient (not `#999999`), a custom override path takes effect, and
    a full Settings-page save/load round trip. Also had to fix a
    since-stale assertion in an earlier session's own test (it checked
    for a neutral gray pixel, which this change correctly broke on
    purpose) -- a good reminder that "does the border still render"
    tests need to stay honest about WHAT they're checking for as the
    actual visual design keeps changing, not just keep passing.

14. **UI Update epic kicked off -- Phase 1 (design-system foundation)
    done.** See the new "MAJOR EPIC" section near the top of this file
    for the full spec and every clarifying decision. This session's
    actual work:
    - `afterglow/gui/rounded_rect.py` -- `rounded_rect_path()`, a
      smooth (Bezier-based, tuned flatter than a true quarter-circle
      per Max's "smooth, not immediately circular" clarification)
      rounded-rect path builder with per-corner skip flags for touching
      edges. Not yet APPLIED to anything real -- that's later phases --
      but its geometry is fully verified: a rounded corner's exact tip
      is excluded from the filled path, a skipped corner's is included,
      radius clamps sanely when it exceeds half the rect's smaller
      dimension, and the overall bounding box stays correct throughout.
    - `afterglow/gui/theme.py` -- `Theme`, the single source of truth
      for every themed color (`accent()`, `card_background()`,
      `app_background()`, `library_background()`, `button_color()`,
      `button_text_color()`), reading either the live `QApplication`
      palette or Afterglow Theme's fixed hex codes depending on
      settings, with a computed-contrast text color rather than a
      fixed one. Cheap to construct fresh wherever needed, same pattern
      as `config_module.load()` elsewhere in this codebase.
    - Six new `AppearanceSettings` fields (`rounded_corners_enabled`,
      `rounded_corner_radius`, `custom_buttons_enabled`,
      `afterglow_theme_enabled`, and the four `afterglow_color_*` hex
      fields) with Settings UI: three new toggles/spinbox in General,
      and a brand new **Advanced** settings tab holding the four color
      fields (each a hex `QLineEdit` + a "Pick..." button opening
      `QColorDialog`, kept in sync both ways). Invalid hex input blocks
      saving with a warning rather than silently corrupting the config.
    - Verified: the full settings round trip for all six new fields,
      the color-picker dialog updating its line edit, and invalid-hex
      rejection -- all against a real `SettingsPage`.

15. **Spam-click library bug -- found and fixed, not guessed at.**
    Max gave exact repro steps (spam-click the sidebar Library button,
    or the per-tab Refresh button) this time, which made this
    straightforward to actually reproduce rather than search blind.
    Root cause: `_VideoGridTab.refresh()` clears old cards via
    `layout.takeAt(0)` + `widget.deleteLater()` -- `takeAt()` stops the
    LAYOUT from managing the widget, but doesn't hide it, and
    `deleteLater()`'s actual deletion is a low-priority event Qt only
    processes once its queue is otherwise idle. A burst of clicks
    arriving faster than that -- an ordinary spam-click -- stacks up
    multiple generations of old, still-alive, still-VISIBLE card
    widgets sitting at stale positions, all overlapping the newest
    generation. This WAS the reported "duplicate clips / messed-up
    sizing," confirmed by direct reproduction (5 rapid `refresh()`
    calls left 15 stale-but-visible orphaned cards on screen alongside
    the 3 real ones) rather than assumed. Fix: `widget.hide()` right
    alongside the existing `deleteLater()`, so a stale card disappears
    immediately regardless of how many refreshes stack up before Qt
    gets around to actually deleting it. Verified: the exact
    reproduction above now shows zero visible orphans; confirmed
    synchronously (no `processEvents()` call at all) that stale widgets
    are hidden immediately rather than merely "eventually correct once
    the event loop catches up"; and reran the full existing suite to
    confirm this didn't disturb anything else.

16. **UI Update Phase 2: card restructure.** See "MAJOR EPIC" section
    for the spec. `afterglow/gui/video_card.py` had its whole layout
    construction and `paintEvent` rewritten:
    - **Size normalization (the actual "all clips should be the same
      size" fix, not just a restyle).** Root cause of the reported
      inconsistent sizing: (1) the title label word-wrapped, so a long
      title produced a taller card than a short one; (2) optional rows
      (info/date lines, filter icon row, tag-name line) were only
      created when THIS video's own data was non-empty, so e.g. a
      video with 0 matching tags produced a shorter card than one with
      tags, even under identical settings. Fixed both: title is now
      permanently single-line with `QFontMetrics.elidedText()` as a
      hard backstop (Resize Text to Fit's existing font-shrinking still
      runs FIRST when enabled, elision only kicks in if shrinking to
      its 6pt floor still doesn't fit); every optional row is now
      created whenever its GLOBAL setting is on, using a single-space
      placeholder when this particular video's data is missing, so the
      row's HEIGHT is always reserved regardless of per-video content.
      `_build_icon_row` specifically reserves `appearance.
      filter_icon_size` (the base setting) for its own height/width,
      NOT the count-adjusted `icon_size` `_icon_size_for_count`
      computes for how big the icons actually render within that
      space -- using the adjusted value for the reservation itself
      would have reintroduced the exact same bug (more tags -> smaller
      icons -> smaller reserved row -> shorter card). Verified directly:
      built cards for a 1-character-title/0-tag video, a long-wrapping-
      title/3-tag video, and a third video, and confirmed all three
      produce bit-for-bit identical `sizeHint()` -- both in isolation
      and inside a real `LibraryPage` grid.
    - **Nested box structure.** New `_InfoBox` class -- the inner box
      holding title + info/date lines + below-location filter icons +
      tag names, painted with its own rounded, `Theme.accent()`-colored
      background. The video thumbnail got its own `video_box` wrapper
      (`thumb_label` inset within it by `unedited_selected_border_
      width` on all sides) so there's an actual gap to draw a border
      into. `CARD_PADDING`/`BOX_GAP` (both 14px/10px) are the margins
      between the outer card edge and its two children, and between
      the two children themselves -- generous enough that the outer
      background portrusion (`Theme.card_background()`) stays visible
      on every side, everywhere, per Max's ask. Neither inner box's own
      internal children reach far enough into ITS corners to need any
      per-pixel child masking for the rounding to look right -- the
      padding itself keeps everything clear of the curved areas, which
      is what let this be done with plain clip-path-then-fill/draw
      calls in `paintEvent`, no `QWidget.setMask()` or render-to-pixmap
      tricks needed.
    - **Unedited highlight redefined**, per Max's clarification: now
      BOTH a background wash across the whole outer card (behind BOTH
      the video box and info box -- was previously the only place the
      highlight rendered at all) AND a separate border drawn
      specifically into `video_box`'s own margin. Selection was
      deliberately NOT redefined the same way (Max only asked for this
      on the unedited highlight) -- it stays a ring around the whole
      outer card, same as before, just now rounded-corner-aware.
    - **Rounded corners actually applied for the first time** (Phase
      1 built the math and the settings; nothing used it until now):
      the outer card, the info box, and the video-box border region
      all respect `rounded_corners_enabled`/`rounded_corner_radius`.
      Caught and fixed a real bug while testing this: the selected-
      border's inset fill used to unconditionally `fillRect(self.rect(),
      ...)` under a clip that was only actually SET when rounding was
      on -- with rounding off, that clip call never happened, so the
      fill would have covered the ENTIRE card instead of just the ring's
      inset area, erasing the border it was supposed to create a gap
      for. Fixed by using a plain `fillRect(inner_rect, ...)` (no clip
      needed at all) when rounding is off, and the clipped full-rect
      fill only for the rounded-corner case.
    - Verified extensively: `_InfoBox` genuinely paints `Theme.accent()`
      (not a hardcoded color); the outer card genuinely paints `Theme.
      card_background()`; the literal corner pixel is genuinely
      excluded from the fill when rounding is on, and genuinely
      included when it's off (this is also what caught the bug just
      above -- an early version of this test used the literal corner
      pixel as its sample point and got a false failure for the SAME
      reason as `test_selected_border_image.py` below, which is a good
      illustration of why "sample a point well clear of any corner
      unless you're specifically testing the corner" matters); the
      highlight wash and the video-box border both visibly differ from
      plain `card_background()` on an unedited video, and don't appear
      at all when highlighting is disabled; selection's inset-fill
      correctly returns to plain `card_background()` just past the
      border ring, both with rounding on and off. Also had to fix a
      second, pre-existing test (`test_selected_border_image.py`) that
      sampled the literal corner pixel and started failing now that
      rounded corners default to on -- not a regression, the same
      "corner pixel is legitimately excluded now" situation.
    - **Not done yet, deferred:** the info-box-click-opens-a-separate-
      smaller-preview-player feature (Max confirmed: distinct from both
      the Editor and the future hover-autoplay), and what clicking the
      thumbnail/video-box area itself should do now that it's visually
      separated from the info box (current selection/double-click-to-
      edit behavior was left exactly as-is, still bound to the whole
      card via `mousePressEvent`/`mouseDoubleClickEvent`, since neither
      was explicitly asked to change -- worth confirming rather than
      assuming this is right once the preview player actually gets
      built). Video padding setting also not done yet.

17. **Real-screenshot feedback round (first time Max actually saw this
    rendered) -- a big batch of fixes and new small features.** Full
    Q&A and every decision below is also folded into the "MAJOR EPIC"
    section's spec near the top of this file, since some of these
    revise things stated in earlier sessions (the Afterglow Theme
    hex codes specifically -- see that section for the current,
    correct values; don't trust hex codes quoted in older session
    entries below this one).

    - **The REAL vertical-padding bug, found from the screenshot.**
      Reported as "unedited videos next to edited videos" looking
      inconsistent -- root cause was actually `Resize Text to Fit`:
      the title row's height came from the ACTUAL (possibly-shrunk)
      font size chosen for that specific video's title, not a
      constant. A video with a long auto-generated timestamp title
      (common for never-renamed, i.e. still-unedited, clips) shrinks
      its font more than one with a short hand-picked title (common
      for renamed, i.e. edited, clips) -- so the correlation Max
      actually saw ("unedited next to edited") was real, just not
      caused by edit-state itself. Fixed by reserving the title row's
      height from the TARGET (un-shrunk) font size in both `__init__`
      and `set_font_scale()`, decoupled from whatever size shrinking
      actually landed on. Verified directly: two videos, one 1-char
      title and one title long enough to shrink drastically under
      Resize Text to Fit, end up with literally identical title-row
      pixel heights and identical overall `sizeHint()`, despite very
      different actual font sizes.
    - **New color palette**, given directly by Max, replacing the
      placeholders from two sessions ago: `#2161bb` (accent -- buttons,
      card info box), `#274162` (card background), `#1d2c3d` (library
      page background -- reassigned from turquoise), and turquoise
      itself (`#12b5c8`) reassigned to a NEW, narrower role: the Local/
      Uploaded tab icons' own background specifically (Max: "for now
      leave it as just local and uploaded" -- filters mentioned as a
      possible future use, not done). App background
      (`afterglow_color_app_background`) is no longer an independently
      Max-picked color -- its default is now COMPUTED from the library
      background (10% brighter, 15% more saturated in HSV, via
      `colorsys`) so it's reliably a bit lighter than the library page
      per Max's earlier ask, while staying a normal editable field
      afterward. Wired into real widgets for the first time this
      session: `MainWindow`'s central widget (app background) and
      `LibraryPage`'s scroll area + grid container (library
      background) -- previously these two `Theme` accessors existed
      but nothing actually read them.
    - **"Padding" setting** (`AppearanceSettings.ui_padding`, default
      14) replaces the hardcoded `CARD_PADDING`/`BOX_GAP`/
      `INFO_BOX_PADDING` constants from two sessions ago, AND now also
      drives the grid's own card-to-card spacing (`QGridLayout`'s
      horizontal/vertical spacing) -- one shared value for everything,
      per how this was actually asked for ("adjusts the pixels of
      padding used everywhere"), superseding the original spec's
      separate "video padding" item.
    - **Outlined on-card text** -- new `afterglow/gui/outlined_label.py`
      (`OutlinedLabel`, a `QLabel` replacement drawing text via a
      `QPainterPath` fill+stroke instead of QLabel's own plain
      rendering), wired into all four text elements per Max's answer
      ("all on-card text"): title, info line, date line, tag names.
      Default fill `#9bcbff`, outline `#3669a0`, both new Settings >
      General fields. **Found and fixed a real rendering bug while
      testing this**, not just building it: the initial version used
      `outline_width * 2` as the actual pen width, which completely
      swallowed the fill color at this app's real font sizes -- normal
      glyph strokes are only ~1-2px wide at 10-17pt, so a pen much
      above ~1px leaves nothing for the fill to show through, and
      TITLE text rendered as solid outline color with zero visible
      fill. Measured directly across several pen widths to find one
      that actually shows both colors (1.0, undoubled, for the title's
      ~17pt font) -- but at the smaller 10px info/date/tag-name size,
      NO pen width above 0 left any fill pixels at all (glyph strokes
      are only ~1px wide there, period) -- so `OutlinedLabel` now
      treats `outline_width <= 0` as "fill only, no stroke", and the
      three small text elements use that rather than force an outline
      that would just replace the fill color entirely. The title is
      the only one of the four that gets a genuine two-tone effect;
      this is a real font-size limitation, not a setting Max can tune
      around. Verified by rendering real labels and checking actual
      pixel colors match both the fill and outline hex values (title),
      and that the small labels render in the fill color with no
      forced outline swallowing it.
    - **"Filter Outline"** (Settings > General, on by default) -- new
      `silhouette_outline_pixmap()`/`_cached()` in `pixmap_effects.py`:
      outlines a filter icon's own alpha silhouette (a morphological
      dilation of the alpha channel, pure Python/no numpy, same
      constraint as the existing hue-shift code) rather than a bounding
      square, matching "not a square outline, but one that actually
      matches the filter's shape" exactly. Per-tag color override:
      new `outline_color` column on the `tags` table (migration
      tested against a pre-existing DB), `library.set_tag_outline_color()`/
      `tag_outline_colors()`, and a color field + picker added to each
      row in Settings > Filters, right next to the existing icon
      controls. Falls back to `card_text_outline_color` when a tag has
      no override. The outlined icon is composited back to the SAME
      overall size as the source icon (inset-then-outline, not grown
      outward) specifically so it doesn't silently break
      `_build_icon_row`'s fixed-size reservation from two sessions ago.
      Verified: stays the same size, follows the actual icon shape (not
      a box), transparent pixels stay transparent, and the caching
      actually prevents redundant recomputation across cards sharing
      the same icon+color.
    - **Thumbnail corners actually rounded, for the first time.** Two
      sessions ago's card-restructure work never actually did this
      despite planning to -- confirmed directly before starting this
      round of fixes. New `round_pixmap_corners()` in `rounded_rect.py`
      (bakes a transparent-cornered clip into a pixmap copy), called
      from `_load_pixmap()`.
    - **Always-on thumbnail contrast outline**, new and separate from
      the unedited-highlight border -- per Max's answer to "keep both,
      the thumbnail outline is for contrast": a thin (2px) stroke drawn
      at the thumbnail's own content boundary, in
      `card_text_outline_color`, regardless of edit/selection state.
      The unedited-highlight background wash and its own conditional
      video-box border (from two sessions ago) are UNCHANGED -- this
      is purely additive.
    - Verified everything above against real widgets and real pixel
      colors (not just construction/no-crash checks) -- 133 assertions
      across 32 offscreen test suites, all passing, including three
      pre-existing tests that needed updating for reasons that turned
      out to be correct, intentional consequences of this session's
      changes rather than regressions (the old radius default, the old
      hex codes, and the tab-icon-size test needing to distinguish the
      new turquoise background fill from the icon's own silhouette).
    - **Still not done**, per Max's own "if you get to those now"
      framing (explicitly optional this round): turquoise applied to
      filters as well as the Local/Uploaded tabs.

18. **Follow-up from a second screenshot** (Max: turquoise showing up
    in the wrong places, thumbnails not visibly rounded, and a
    deliberate redesign of the unedited-highlight).
    - **Investigated the turquoise/rounding reports first, before
      changing anything** -- both turned out to be verifiably CORRECT
      in the actual code, not bugs:
      - `Theme.accent()`/`card_background()`/`library_background()`/
        `turquoise()` were each tested directly against a completely
        FRESH config (no pre-existing saved settings) and produced
        exactly the right hex values, and a real rendered `VideoCard`'s
        info box and a real `LibraryPage`'s grid background both
        sampled back the EXACT correct colors pixel-for-pixel. Given
        `AppearanceSettings`' color fields have been reassigned twice
        now across sessions (turquoise moved from library-background to
        tab-icon-background just last session), and dataclass DEFAULT
        changes never retroactively update a value already written to
        an existing `config.toml`, the most likely explanation for
        what Max is seeing is a config file on his end still holding
        color values saved under an OLDER assignment -- worth checking
        Settings > Advanced directly to see what's actually saved
        there now, since editing/re-saving those fields (or deleting
        the relevant lines from the config file to fall back to the
        current code defaults) would resolve it either way. Flagged as
        a real open question below rather than guessed at further,
        since there's no way to inspect Max's actual local config file
        from here.
      - Thumbnail rounding: `round_pixmap_corners()` was verified
        directly against a real 16:9 test clip's actual generated
        thumbnail (not the placeholder, and not the earlier session's
        SQUARE synthetic test clips, which produce a misleadingly
        square/cropped result under `KeepAspectRatioByExpanding` and
        gave a false signal during this exact investigation) -- the
        resulting pixmap's corner pixels are genuinely transparent
        (alpha 0), confirming the rounding IS being applied correctly
        at the pixmap level in this sandbox. Since this can't be
        cross-checked against Max's actual on-screen KDE/Wayland
        rendering, this is flagged as unverified-on-real-hardware
        rather than "definitely fine" -- but there's no bug found in
        the actual rounding code itself.
    - **The redesign Max asked for outright (not a bug -- a deliberate
      change from what was confirmed two sessions ago): the unedited-
      highlight background wash is REMOVED.** The card's own background
      is now ALWAYS plain `card_background()`, regardless of edit
      state -- the wash that used to cover the whole outer card for
      unedited videos is gone. The unedited-highlight gradient now
      shows ONLY as the video-thumbnail's own border (reusing the
      existing fill-video_box-then-let-thumb_label-cover-the-center
      technique from two sessions ago, unchanged). This also
      absorbed/replaced last session's separate "always-on plain
      contrast outline" -- the two were doing conceptually overlapping
      jobs (both bordering the thumbnail), so they're now ONE mutually
      exclusive choice per card: the gradient for an unedited,
      highlighted, unselected video, or the plain
      `card_text_outline_color` stroke for everything else (edited,
      highlighting disabled, or selected). Selection's own ring around
      the whole outer card is unchanged either way.
    - Verified: an unedited+highlighted video's card background samples
      as plain `card_background()` (not the gradient) while its video-
      box border samples as the gradient; an edited video's (or
      highlighting-disabled) border samples as the plain outline color
      instead. Updated two other tests
      (`test_card_paint_layers.py`, `test_session_visual_features.py`)
      whose sample points were written against the OLD design (the
      whole-card wash, and the outline stroke's old position at the
      inset boundary rather than `video_box`'s own outer edge) --
      correct, expected updates given the design actually changed, not
      regressions.

19. **The turquoise-color bug -- found for real this time.** Max
    confirmed the two obvious explanations from item 18's investigation
    were BOTH ruled out (he'd rebuilt/redeployed, and both Custom
    Buttons and Afterglow Theme were confirmed on), which meant there
    was a genuine bug still to find rather than a stale config file.
    Root cause: **an unscoped `setStyleSheet("background-color: ...")`
    call is a well-known Qt gotcha** -- Qt's style engine treats a CSS
    property with no type/class selector as if it were written
    `* { property: value; }`, applying it across the WHOLE descendant
    widget subtree, not just the widget it was called on. Two sessions
    ago's background-wiring work (item 17) called
    `grid_container.setStyleSheet(f"background-color: {hex};")` and
    `central.setStyleSheet(f"background-color: {hex};")` with exactly
    this unscoped form -- meaning `MainWindow`'s central widget (the
    ancestor of literally everything else in the app) and the Library
    grid's own container could each cascade their background color
    down into every descendant widget's painting, including
    `VideoCard`'s and `_InfoBox`'s own fully custom `paintEvent`-drawn
    backgrounds, on a REAL compositor. This sandbox's offscreen Qt
    platform plugin doesn't reliably reproduce that same cascading
    behavior (consistent with the various other "this plugin does not
    support..." limitations already known from earlier sessions), which
    is exactly why direct pixel tests against fresh configs kept coming
    back correct in this environment despite Max seeing the wrong
    colors on his real machine -- a good concrete example of "verified
    in the sandbox" and "verified for Max" not being the same claim,
    worth remembering for any future background/stylesheet work
    specifically. Fixed by switching both to `QPalette` (`setPalette()`
    + `setAutoFillBackground(True)`), which sets a color on exactly one
    widget with no cascading path at all. Also found and fixed the
    SAME unscoped-background pattern in one more, unrelated place while
    auditing for it (`LibraryPage`'s `status_bar` label) -- lower risk
    in practice since a bare `QLabel` has no children, but fixed for
    consistency and to close out the pattern everywhere it appeared.
    New `test_no_stylesheet_cascade.py` walks every widget in a real
    `LibraryPage`/`MainWindow` tree checking for exactly this mistake
    (an unscoped rule that mentions `background`), so it can't quietly
    reappear.
    - **Also fixed in the same round: the video-thumbnail outline
      wasn't visibly rounded**, a real (if smaller) bug, also caught
      from a screenshot. The outline's corner radius was being
      capped at `min(radius, border_width)` -- with the default 24px
      corner radius vs. a 9px border width, that capped the
      thumbnail's own rounding down to just 9px, visibly less rounded
      than the rest of the card (which uses the full 24px) and easily
      read as "not rounded at all" at normal viewing size. There was
      no real geometric reason for that cap --
      `rounded_rect_path()` already clamps to half of whichever of the
      rect's own width/height is smaller, which is the only clamp
      actually needed. Now uses the same `radius` as everything else.
      Verified directly: with a real 16:9 thumbnail, the outline
      stroke is present along a straight edge and genuinely absent at
      the exact corner (rounded away), at the full 24px radius.
    - 35 test suites passing.

20. **A third screenshot round -- 7 more items, one confirmed real bug
    fix, a config-migration pattern established, and the biggest
    remaining piece of the UI Update epic (Local/Uploaded's native tab
    widget) deliberately deferred.**
    - **The library-background-turquoise bug, actually resolved.**
      Given the new clue that specifically the LIBRARY background (not
      the app background, which Max confirmed was already correct)
      was wrong, the far more likely explanation flipped back to a
      stale SAVED VALUE for that one specific field, from BEFORE
      `afterglow_color_library`'s role was reassigned (twice, across
      recent sessions) -- exactly the kind of thing a code-side default
      change can't retroactively fix. Added a targeted migration in
      `config.load()`: if a saved config's `afterglow_color_library`
      is still one of the two specific old values that field used to
      hold (`#2ee0a6`, the very original; `#12b5c8`, briefly considered
      for it too before turquoise got its own field), reset it to the
      current correct default -- a genuinely custom value is left
      alone. Same migration pattern applied preemptively to
      `afterglow_color_app_background` and `afterglow_color_accent`
      too, since BOTH of those defaults also changed again later in
      THIS SAME SESSION (see below) -- without this, anyone who'd
      already saved a config in the brief window before those changes
      would hit the exact same "my settings didn't update" experience
      all over again. Verified directly: old values of all three
      migrate correctly, genuinely custom values don't get touched.
    - **The filter-outline-invisible bug, found and fixed for real.**
      The outline was being composited onto the icon at its NATIVE
      resolution (a user-uploaded icon file can easily be 512x512 or
      larger) and only scaled down to the actual tiny display size
      afterward, inside `_FilterIconLabel` -- shrinking a 2px outline
      proportionally along with everything else, down to a small
      fraction of a pixel at typical icon sizes. Fixed by scaling the
      icon down to its real display size FIRST, then outlining at
      THAT size, so the configured width means something. Verified
      directly with a realistic 512px source icon scaled to a 54px
      display size: the old order produced ZERO visible outline
      pixels; the fixed order produced 256. This was a severe, real
      bug (not a subtle contrast issue), not something a mere color
      change would have fixed.
    - **Direct color/sizing adjustments, all on Max's explicit
      instruction, all applied to config defaults with the migration
      pattern above where a default actually changed:**
      `unedited_selected_border_width` doubled (9 -> 18 -- this is a
      SHARED setting, so it also doubles the selection ring's width,
      not just the thumbnail border specifically, since there's only
      the one field governing both); `unedited_highlight_brightness`
      darkened 35% (100 -> 65); the plain (non-gradient) thumbnail
      contrast outline's color source changed from
      `card_text_outline_color` to `Theme.app_background()`;
      `afterglow_color_accent` recomputed as the exact HSV-brightness
      midpoint between the old accent value and `card_background()`
      (`#194a8e`); `afterglow_color_app_background` darkened another
      25% in V on top of its already-computed value (`#142232`).
    - **New "Card Text Outline Width" setting** (`card_text_outline_width`,
      default 3.0 -- "3x what it is now", i.e. 3x the previous
      hardcoded 1.0), applied only to the title (the three smaller
      lines stay fill-only regardless, per last session's font-size
      finding). **Measured and flagged, not silently shipped:** at
      this exact default, the title's fill color is COMPLETELY
      invisible (0 fill pixels found, directly measured) -- a real,
      confirmed tradeoff of following the literal instruction, not a
      guess. The setting itself works correctly at smaller widths
      (verified: at 1.0, the fill is visible again), so this is a
      values/defaults question for Max to weigh in on, not a bug in
      `OutlinedLabel`.
    - **New `afterglow/gui/custom_button.py`** (`CustomButton`, a
      `QToolButton` subclass with fully custom rounded/theme-colored
      painting instead of native/KDE styling) -- the actual
      implementation of the "Custom Buttons" setting, applied to the
      Library's Search/Refresh/Filters/Sort By/Info row. All five are
      text-only for now (none has a real custom icon asset yet -- the
      previous native standard-library reload icon on Refresh doesn't
      count as "already have one" for this purpose). A new "Search"
      button toggles the existing search field's visibility as a
      lightweight stand-in for the eventual magnifying-glass-expands-
      to-a-bubble redesign (a separate, not-yet-built piece of the
      spec). Subclassing `QToolButton` specifically (not `QPushButton`)
      keeps Filters/Sort By/Info's existing `setPopupMode(InstantPopup)`
      + `setMenu()` wiring completely unchanged -- only the painting
      changed, not the click/popup behavior.
    - **A real debugging detour that turned out to be a false alarm,
      worth recording as a fourth example of this file's own "sample a
      point clear of the corner/text, not the literal corner" lesson:**
      `CustomButton` initially appeared completely broken -- every test
      showed plain default gray instead of the theme color, even
      though a call-counter confirmed `paintEvent` WAS running. Spent a
      full isolation pass (bare `fillRect` with no theme lookup: works;
      add the theme color lookup: still works; add the rounded-corner
      clip: "breaks") before recognizing the actual cause: the test was
      sampling pixel `(2, 2)` -- the LITERAL CORNER of a rounded
      button -- which is legitimately excluded from the fill by the
      button's own corner rounding, same as several corner-pixel test
      mistakes earlier in this file. `CustomButton` was correct the
      entire time; only the test's sample point was wrong. A SEPARATE,
      genuinely real nuance surfaced during the same investigation
      though: the button's hover-lighten effect (`underMouse()` is
      `True` even in this offscreen sandbox by default) means a plain
      exact-color-match assertion needs to account for that adjustment
      too, not just avoid the corner.
    - **Deliberately NOT attempted this round: item 4, replacing
      Local/Uploaded's underlying `QTabWidget`/`QTabBar` with fully
      custom page-switcher buttons** ("the page buttons up top have
      two buttons -- the 'page' button that attaches to the search bar
      area, and the actual icon... replace these with completely
      custom buttons"). This is a materially bigger and riskier change
      than anything else in this batch -- it means ripping out
      `_PulsingTabBar`/the tab-icon compositing trick/the click-pulse
      animation/the prev-next-navigation tab-tracking (`_last_edit_tab`)
      that ALL currently depend on the video actually being a
      `QTabWidget`, and rebuilding equivalent behavior on top of two
      plain `CustomButton`s + something to switch the visible page
      (a `QStackedWidget`, most likely). Given how much of this
      session was already spent and how much existing, working
      functionality that change would touch at once, it felt like the
      wrong thing to rush in the same batch as everything else here --
      flagged clearly as the next concrete piece of work instead of
      attempted partially.
    - 36 test suites passing (including 4 pre-existing ones from
      earlier this session that needed updating for the SAME reason as
      before -- a hex code or color-source genuinely changed on
      purpose, not a regression).

21. **Three more reports from the same round: the app taking 15-30s to
    open (was instant before), the spam-click bug still happening, and
    a sidebar pulse-timing mismatch. All three turned out to share ONE
    root cause, plus one deliberate additional defense.**
    - **The startup slowdown -- a real, severe bug I introduced myself,
      in THIS SAME SESSION's earlier filter-outline fix (item 20
      above).** That fix's cache key included the per-video, count-
      adjusted `icon_size` (`_icon_size_for_count` shrinks it as a
      video's own matching-tag count grows) -- meaning two videos with
      different tag counts got DIFFERENT cache keys for the exact same
      underlying icon file, so the cache almost never actually hit
      across different cards, and the expensive per-pixel outline
      computation re-ran for nearly every card in the library instead
      of once per unique icon. Fixed by outlining at a STABLE reference
      size (`appearance.filter_icon_size`, the base setting, identical
      for every card regardless of that card's own tag count) instead,
      letting `_FilterIconLabel`'s own subsequent `.scaled()` call
      handle any further per-video downscaling -- cheap regardless of
      how many times it happens, since it's a single native scale, not
      a per-pixel Python loop. Measured directly, not assumed: the OLD
      approach took 5.1s for just the outline step across a realistic
      30-video/varying-tag-count/512px-icon workload; the FIXED
      approach does the entire `VideoCard` construction (outline
      included) for all 30 in 2.05s.
    - **The spam-click bug "still existing" -- a real, additional
      defense added, not a re-fix of the same thing.** The
      hide()-before-deleteLater() fix from several sessions ago is
      still correct and still there -- it makes a stale card
      invisible immediately once a rebuild DOES run. What it never
      addressed is a burst of clicks queuing up MANY FULL REBUILDS
      back to back in the first place, and on a slow-enough render (the
      startup-slowdown bug above made EVERY rebuild slower, widening
      the window for exactly this), enough queued rebuilds can still
      look like "multiplying and messing up scaling" even with that
      fix in place. Added a LEADING-EDGE debounce (750ms) to
      `_VideoGridTab.refresh()`, per Max's own suggested fix -- the
      first call in any 750ms window acts immediately (Refresh should
      feel instant on a single click), every call after that until the
      cooldown clears is silently dropped. Deliberately NOT the same
      pattern as the existing DB-file-watcher debounce
      (`_refresh_debounce`, a TRAILING-edge "wait for a burst to settle
      then act once" debounce, right for background writes, wrong
      here -- it would make even a single Refresh click feel laggy,
      waiting 750ms to see anything happen at all). Renamed the actual
      rebuild logic to `_do_refresh()`; `refresh()` is now a thin
      debounced wrapper around it. Search-as-you-type
      (`search_edit.textChanged`) was deliberately rewired to call
      `_do_refresh()` directly, bypassing the debounce entirely --
      every keystroke needs to filter immediately, that's the whole
      feature, and routing it through the leading-edge debounce would
      have made only the FIRST character of a fast-typed query actually
      filter anything.
    - **The sidebar pulse-timing mismatch -- investigated, and it
      turned out to already be fixed by the startup-slowdown fix
      above, not a separate bug in the pulse code.** Traced the actual
      mechanism: a sidebar nav button's `mouseReleaseEvent` calls
      `self._pulse.release(...)` (starts the pulse-up animation)
      immediately, correctly, THEN calls `super().mouseReleaseEvent()`,
      which SYNCHRONOUSLY fires Qt's `clicked`/`idClicked` signal --
      and `_on_nav_clicked` calls `self.library_page.refresh()`
      directly inline, on the SAME call stack, BEFORE control ever
      returns to the event loop. Qt can't process ANY paint events
      (including the pulse animation's own queued frames) while that
      synchronous chain is running -- so however long
      `library_page.refresh()` took (which, before the fix above, could
      be seconds) is exactly how long the pulse-up animation appeared
      to "wait" before showing anything, even though the animation
      itself started immediately in code. The Library page's own
      Local/Uploaded tab-icon pulsing was never affected by this,
      because switching between tabs WITHIN an already-open Library
      page doesn't trigger anything nearly as expensive -- which is
      exactly why Max's own framing ("match the way the library pages
      pulse") pointed at the right root cause. No changes were needed
      to `pulse_animation.py` or the sidebar button's event handlers --
      they were already correct and already consistent with the
      Library tab icons' own wiring. Verified directly: with the
      startup-slowdown fix in place, the exact synchronous chain a
      sidebar Library click triggers takes ~124ms on a realistic
      30-video library (down from however long it took with the
      caching bug present) -- should read as instant or very close to
      it. Measured honestly, not just claimed fixed: at 100 videos the
      same chain still takes ~640ms, better than before but not
      perfectly instant for a very large library -- a deeper
      architectural question (lazily loading/virtualizing the grid
      rather than rebuilding every card synchronously on every
      refresh) that's out of scope for this fix specifically.
    - Updated three existing tests whose specific numbers depended on
      the OLD "refresh() always runs" assumption, now genuinely wrong
      given the new debounce -- `test_bulk_context.py`,
      `test_live_refresh.py`, and `test_spam_click_fix.py` (the last of
      these also gained a NEW check specifically confirming the
      stronger guarantee: 8 rapid Refresh clicks now never even create
      a second generation of cards, not just "stale ones get hidden
      promptly"). All three needed an explicit
      `tab._clear_refresh_cooldown()` call before the specific
      refresh they depend on, since test setup/construction usually
      already consumes the leading-edge allowance before the actual
      check runs.
    - 38 test suites passing.

22. **Sixth screenshot-feedback round: two real rendering bugs fixed
    at the technique level (not just tuned), the color palette updated
    again, and Filters/Sort By/Info merged into one button.**
    - **The text-outline-bleeding bug -- fixed at the actual technique
      level, not just re-tuned.** `OutlinedLabel` used to draw the
      outline and fill in ONE combined `drawPath()` call with both a
      pen and brush set -- Qt strokes a path CENTERED on it, eating
      into the fill from both sides equally, which is exactly why
      raising `card_text_outline_width` (last session) made the title
      render as solid outline color with the fill completely gone.
      Reported directly as "the outline bleeds onto the text, making
      the text just the color of the outline." Fixed by splitting this
      into two SEPARATE passes: stroke-only underneath (pen set, brush
      `NoBrush`), then fill-only on top (brush set, pen `NoPen`) --
      the fill pass draws the exact, untouched original glyph shape,
      completely covering the inward half of the stroke pass below it,
      so what remains visible is only the outward-facing half of the
      outline as a clean border around a fully-intact fill. This is
      robust regardless of outline width now (verified: even the
      default 3.0 shows the fill clearly, unlike before), so the three
      smaller info/date/tag-name lines no longer need the fill-only
      (`outline_width=0`) fallback from last session -- they use
      `card_text_outline_width` like the title now, and were verified
      to keep a healthy fill pixel count even at that width.
    - **The edited-video outline "a few pixels off" bug -- a real
      positioning bug, found and fixed.** It was a thin, fixed 2px
      stroke drawn at `video_box`'s own OUTER edge -- but the actual
      thumbnail content sits `border_width` pixels further in, so the
      stroke and the thumbnail were never touching; the gap between
      them just showed plain `card_background()`. Fixed by using the
      exact same fill-the-whole-margin technique the unedited-gradient
      branch already used correctly (fill `video_box`'s full area with
      `app_background()`, let `thumb_label` cover the center as a
      child widget drawn afterward) -- guarantees both branches match
      in position AND width by construction, rather than needing their
      geometry kept in sync by hand. This also naturally satisfies
      "give it the same width as the unedited outline" for free.
    - **The border width setting doubled again** (18 -> 36) directly on
      request -- same migration pattern as before for anyone whose
      config still has 9 or 18 saved.
    - **The filter-outline "floating 1-2px off the icon" bug -- found
      and fixed, plus closed-area filling added.** The floating was
      caused by `silhouette_outline_pixmap` SHRINKING the icon inward
      to make room for the outline within a fixed-size canvas -- the
      icon itself ended up visibly smaller than its "real" size, with
      the outline occupying the freed ring, which reads as detached
      from where the icon's true edge should be. Fixed by GROWING the
      canvas outward by `width` on each side instead, placing the icon
      at its own full, untouched size -- the outline now hugs the
      icon's real edge by construction. The result is now larger than
      the input (by `2*width` per dimension); `_FilterIconLabel`'s
      existing `.scaled()` call already handles scaling the whole
      composite down to the actual display size regardless of this
      pixmap's own size, so no caller-side change was needed beyond
      that. Also added, per a direct follow-up: a flood fill from the
      canvas border identifies genuinely EXTERIOR transparent pixels;
      anything transparent NOT reached by it (an enclosed hole, like
      the counter of a letter "O") gets filled with the outline color
      too, rather than staying transparent. Verified with a donut-
      shaped test icon: the hole fills, the true exterior doesn't.
    - **New color palette**, given directly, with the same migration
      pattern for the previous round's values: `#1d61b5` (accent),
      `#1f3a5f` (card background), `#0d1621` (app background),
      `#05a4b9` (turquoise). Library background untouched (not part of
      this round's given values).
    - **Filters, Sort By, and Info merged into one button** (Max: "all
      now in one tab referred to in the code as Sort, with placeholder
      text until i give you the icon"). `self.filters_btn` and
      `self.info_btn` are gone; `self.sort_btn` (labeled "Sort",
      placeholder pending a real icon) now opens ONE combined `QMenu`
      laid out as three sections (disabled header actions read
      "Filters" / "Sort By" / "Info", each followed by that section's
      own content) rather than three separate popups. All the
      underlying logic (tag checkboxes, categories-as-submenus, sort
      selection, info toggles) is unchanged -- only the menu
      CONSTRUCTION was merged, into one `_rebuild_toolbar_menu()`
      replacing the three separate `_rebuild_filters_menu()`/
      `_build_sort_menu()`/`_build_info_menu()` methods, called both at
      construction and on every `_do_refresh()` (since the Filters
      section depends on which tags currently exist; rebuilding the
      more static Sort By/Info sections too on every refresh is
      harmless).
    - Also worth flagging, NOT yet acted on: with the border width now
      36px and `ui_padding` still 14px, the selection ring (which
      shares the same width setting) can extend further than the gap
      between the card's outer edge and `video_box`, meaning a
      selected card's ring may visually reach into or past where
      `video_box` itself sits, rather than sitting cleanly in the
      outer padding area the way it did at smaller border widths. Not
      reported as a problem yet, but flagged here since it was directly
      observed while fixing an unrelated test's sample point (see
      "Currently being worked on" test-file notes) -- worth watching
      if the border width grows any further.
    - 40 test suites passing.
    - **Still not started this round** (explicitly acknowledged, not
      forgotten): the Local/Uploaded `QTabWidget` -> custom-buttons
      replacement (asked for again this round -- "you still didn't
      make the saved and uploaded their own custom tabs"), lazy-loading
      the grid (36 videos then load-more-on-scroll), and the custom
      scroll bar (turquoise handle, app-background track, card-color
      outline). All three are queued as the very next work -- see
      "Next up".

23. **Quick follow-up batch before a handoff**: `unedited_selected_
    border_width` doubled a third time (36 -> 72, same migration
    pattern extended to cover the just-superseded 36 too); the grid's
    own OUTER edges (not just the gaps between cards) now use
    `ui_padding` as well, via `grid_layout.setContentsMargins(...)` --
    previously this was whatever Qt's own default happened to be,
    unrelated to the Padding setting at all, per a direct follow-up
    ("the padding between videos should be applied to videos and the
    edges of the library 'container'"). Verified directly: a
    distinctive `ui_padding` value shows up as the grid_layout's actual
    contentsMargins, and the first card's on-screen position is inset
    by exactly that amount from the container's edge.
    - **New recovery tools, added directly on request** ("add a
      'Revert to Default Colors' button, and a 'Revert to Default
      Settings' button before assuming there is a bug") -- a new
      "Reset" group in Settings > Advanced with two buttons.
      **"Revert to Default Colors"** resets just the five Afterglow
      Theme colors plus the two card-text colors to their dataclass
      defaults, updates the visible line edits, and saves immediately
      -- verified it does NOT touch any other (non-color) setting in
      the same save. **"Revert to Default Settings"** resets the WHOLE
      `AppearanceSettings` section (padding, border widths, rounding,
      brightness, everything) to fresh defaults and saves, then tells
      the person to reopen Settings -- deliberately does NOT try to
      live-refresh every widget across all four Settings tabs in
      place, since that's a lot of surface area for what's meant to be
      a quick diagnostic/recovery tool, not a polished feature. Given
      how many rounds of "my colors don't seem to be applying" have
      turned out to be a stale saved value rather than a code bug,
      this gives a direct, no-explanation-needed way to rule that out
      before assuming otherwise -- exactly the intent behind the
      request. Both verified against a real `SettingsPage` (mocking
      the confirmation dialog, which otherwise blocks waiting for
      input in an offscreen test -- caught this directly from a hung
      test run, not guessed).
    - 42 test suites passing.

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
**One item flagged this session, not attempted:**
- **Custom `QComboBox` styling.** Every native button/checkbox/text
  field is now a custom widget (see "This session" above), but
  `QComboBox` (category picker, filter-display mode, startup window
  mode, etc.) is still native/KDE-styled -- a real custom dropdown
  needs its own popup list, not just recoloring the closed box, which
  is a meaningfully bigger build than anything else in this sweep.
  Worth confirming whether Max wants this before starting it.

**Nothing else explicitly re-requested is still outstanding** -- the
previous round's two open items (custom text fields app-wide, and the
sidebar's full custom-button treatment) are both done; see the
"This session" entry above that one.

**Still queued from earlier sessions:**
1. **Lazy-load the grid**: load the first ~36 videos so the app opens
   immediately, then load more as the user scrolls further down,
   rather than building every card synchronously up front. Explicitly
   OK with videos still loading in the background per Max ("its okay
   if the videos are still loading as the app opens"). Needs: an
   initial batch-limited `_do_refresh()`, and a scroll-position
   listener on `_VideoGridTab.scroll` that appends the next batch when
   the user nears the bottom of what's currently loaded.

**Open questions from recent sessions, still unconfirmed:**
- Whether the Stats tab's three length rows (average/longest/shortest)
  should show some form of percent despite not being a video-count
  subset.
- Whether the Sort popover's own tab buttons, the Settings page's new
  tab buttons, and/or the sidebar want a fuller pulse animation (like
  the sidebar's own pre-existing one) rather than the simple hover-
  lighten/press-darken feedback they currently have.
- Icons for the Settings tabs (Clipping/General/Filters/Stats/
  Advanced) and the four action buttons (Edit/Copy/Filters/Delete) --
  Max said he'll provide these later, at which point both should also
  become circles (Settings tabs) matching the Search/Refresh/Sort
  treatment.

**Everything else, unordered:**
- **Confirm what clicking the thumbnail/video-box should do now**
  that it's visually separated from the info box (currently unchanged
  -- still whole-card select/double-click-to-edit). Cheap to answer,
  worth doing before building the preview player next, since that's
  the other half of "what do the two boxes each do when clicked."
- **The info-box-click -> separate smaller preview player** (Medal-
  style, confirmed distinct from both the Editor and hover-autoplay).
  This is a real new feature (a second mpv-embedded player, this time
  in a lightweight modal/dialog rather than a full page) -- probably
  deserves to be scoped as its own sub-phase rather than a quick
  add-on.
- **Wire `Theme`/`CustomButton` into the sidebar nav buttons too** --
  `CustomButton` exists and is used by the Library's top row now, but
  the sidebar (Library/Editor/Settings) still uses the older
  `_ScalingIconButton`/gradient-image system, and the sidebar's own
  gradient-darkened-background piece of the Afterglow Theme spec
  isn't built either.
- **Rounded corners on text boxes** -- the utility exists and is
  proven correct, just not yet applied to `QLineEdit`/`QTextEdit`

   elsewhere in the app.
6. Turquoise applied to filters too (Max: optional, "if you get to
   those now").
7. Everything else in the UI Update spec not yet touched: Comfy UI,
   Video Info settings tab, the hamburger popover, hover-autoplay-in-
   grid, middle-click-deselects, Ctrl+R. (The search bubble itself is
   now done -- see "This session" above.)
8. **New from this session:** confirm whether the Stats tab's three
   length rows (average/longest/shortest) should show a percent of
   some kind despite not being a video-count subset, and whether the
   Sort popover's tab buttons want the same press/hover pulse
   animation the sidebar nav and existing CustomButton have (they
   currently only lighten slightly on hover, no pulse).

Given how large this epic is, expect this list to keep growing/
reordering as each phase actually lands -- treat it as "what's next,"
not a fixed roadmap.

## Architecture pointers
- **New this most recent session** (sidebar rewrite, search bubble
  taper fix, custom text fields, custom scroll bar, action button
  styling):
  - `afterglow/gui/library_page.py` -- `_LibraryTabButton` renamed to
    `LibraryTabButton` (dropped the leading underscore -- genuinely
    shared across modules now) and gained a third `position` value,
    `'full'` (rounds all four corners, for a button that doesn't touch
    its neighbors).
  - `afterglow/gui/main_window.py` -- `_ScalingIconButton` and
    `_darken_pixmap` are GONE (deleted, not deprecated). The sidebar's
    three nav buttons are `LibraryTabButton(icon, 'full')` now.
    `resizeEvent` computes each button's icon size directly (width-
    based, same formula the old `icon_size_for_width` used) and calls
    `set_icon_target_size()` -- no more per-button size_basis
    distinction, since all three are narrow enough that width is
    always the limiting dimension now.
  - `afterglow/gui/custom_line_edit.py` -- NEW. `CustomLineEdit`,
    applied everywhere a visible QLineEdit exists in Settings-adjacent
    UI (settings_page.py, filters_settings_page.py,
    clip_config_row.py, advanced_sound_dialog.py). NOT applied to
    `_VideoGridTab.search_edit` (never shown -- pure text storage) or
    `SearchBubble.line_edit` (already sits inside that widget's own
    custom-painted bubble shape).
  - `afterglow/gui/custom_scrollbar.py` -- NEW. `CustomScrollBar`,
    swapped into `SmoothScrollArea` via `setVerticalScrollBar()`.
  - `afterglow/gui/custom_button.py` -- `CustomButton` gained
    `set_fill_color()` / `set_outline()`, both opt-in (None/0 by
    default, so every other CustomButton is unaffected). The default-
    path text color is now `contrast_text(bg)` computed against
    whichever fill is ACTUALLY rendering (post hover/press darkening),
    not always `button_text_color()`'s plain `button_color()` basis.
  - `afterglow/gui/video_card.py` -- the plain (non-highlighted)
    thumbnail border fill changed from `app_background()` to
    `accent()`. `_build_action_buttons_row()` now calls
    `set_fill_color(card_text_color)` / `set_outline(card_text_outline_
    color, card_text_outline_width)` on each of its 4 buttons, plus
    `setMinimumHeight(48)` and a larger font.
- **New this session** (an earlier one -- kept for reference; note the
  `_LibraryTabButton` name it mentions is now just `LibraryTabButton`,
  per the rename directly above):
  - `afterglow/gui/library_page.py` -- `_composite_tab_icon()` and
    `_PulsingTabBar` are GONE (deleted, not deprecated) -- replaced by
    `_LibraryTabButton(QAbstractButton)`, a genuinely independent-
    icon-size custom button. `LibraryPage` no longer has a `self.tabs`
    (`QTabWidget`) at all; it has `self._stack` (`QStackedWidget`,
    still holding the same `self.local_tab`/`self.uploaded_tab`
    `_VideoGridTab` instances as before) and `self.local_btn`/
    `self.uploaded_btn`. `_VideoGridTab.search_edit` is now a plain,
    never-added-to-any-layout `QLineEdit` (text storage only); the
    tab no longer creates its own `search_btn`/`refresh_btn`/
    `sort_btn`/`search_bubble`/`sort_popover` -- those are all
    `LibraryPage`-level now (`self.search_btn` etc.), shared across
    both tabs. `_VideoGridTab._rebuild_toolbar_menu()` was renamed to
    `rebuild_sort_popover_pages(popover)` (now takes the popover as a
    parameter rather than owning one) and is no longer called from
    `_do_refresh()` -- only lazily, right before `LibraryPage` shows
    the shared popover.
  - `afterglow/gui/custom_button.py` -- `CustomButton.set_icon_pixmap()`
    is new: draws a centered, aspect-preserved pixmap instead of the
    text label when set (`None` reverts to text). Search/Refresh/Sort
    all use this now instead of text labels.
  - `afterglow/gui/search_bubble.py` -- rewritten. Tail is now two
    cubic Beziers (`_build_path()`), not a `QPolygonF` triangle;
    `_TAIL_OVERLAP` (8px) is the fix for the stray-outline-seam bug --
    see "This session" above for the full reasoning. `show_below()`
    now centers the bubble on the anchor's horizontal midpoint instead
    of left-aligning to it.
  - `afterglow/gui/video_card.py` -- `VideoCard` gained `_bg_cache`/
    `_bg_cache_key` and `_render_background(cache_key)` (the old
    `paintEvent` body, now cache-key-gated); `_InfoBox` gained
    `_bg_cache` the same way. `afterglow/gui/outlined_label.py` --
    `OutlinedLabel` gained `_cache` (invalidated by `setText`/
    `set_colors`, or a size change caught lazily in `paintEvent`).
    None of these need explicit invalidation calls scattered around
    the codebase for state that already funnels through their own
    existing setters -- see each one's own comment for exactly what
    its cache key covers.
  - `afterglow/config.py` -- new migration entries for the four
    color fields' immediately-previous defaults (`#1d61b5` /
    `#1f3a5f` / `#1d2c3d` / `#05a4b9`), same pattern as every earlier
    palette migration in this file.
- `afterglow/gui/custom_button.py` -- `CustomButton` (`QToolButton`
  subclass, fully custom rounded/theme-colored paint), used by the
  Library's Search/Refresh/Sort row. Subclasses `QToolButton`
  specifically (not `QPushButton`) so `setPopupMode(InstantPopup)` +
  `setMenu()` keep working unchanged when needed elsewhere -- only the
  painting is replaced. `sort_btn` no longer uses that popup-menu path
  as of this session (see `sort_popover.py` below) -- it's now a plain
  `clicked` connection opening a `SortPopover` instead. NOT yet used
  for the sidebar nav buttons (see "Next up").
- `afterglow/gui/smooth_scroll_area.py` -- NEW this session.
  `SmoothScrollArea(QScrollArea)`, animates wheel-scroll instead of
  jumping per-notch. Swapped in for `_VideoGridTab.scroll`; nothing
  else needed to change since only `wheelEvent` is overridden.
- `afterglow/gui/stats_settings_page.py` -- NEW this session.
  `StatsPage`, added to `SettingsPage`'s tab bar as `self.stats_page`.
  Read-only, no `save()` -- `SettingsPage._save()` was NOT changed to
  call one. Pulls from `library.compute_stats()` (`LibraryStats`
  dataclass in `library.py`), only on its own Refresh button press.
- `afterglow/gui/sort_popover.py` -- NEW this session. `SortPopover`
  (the popup itself, `Qt.Popup` + translucent background),
  `_PopoverTabButton` (each of the 3 tabs, custom rounded paint per
  position: `left`/`middle`/`right`), `_RoundedContentArea` (the
  `QStackedWidget` subclass painting the rounded-bottom-corners card
  behind whichever page is showing). `_VideoGridTab.sort_popover` is
  built once at construction; `_rebuild_toolbar_menu()` (name kept
  despite no longer building a `QMenu`) calls
  `sort_popover.set_page_widget(index, widget)` for each of the three
  pages on every refresh, preserving whichever page is currently
  showing.
- `afterglow/gui/search_bubble.py` -- NEW this session. `SearchBubble`
  (`Qt.Popup` + translucent background, custom-painted rounded body +
  triangular tail via `QPainterPath.united()`). Owns the actual
  `QLineEdit` (`SearchBubble.line_edit`); `_VideoGridTab.search_edit`
  is just `self.search_bubble.line_edit`, an alias, not a separate
  widget -- every existing reference to `self.search_edit` elsewhere
  in that class needed zero changes.
- `afterglow/gui/theme.py` -- `contrast_text(bg: QColor) -> QColor` is
  now a standalone module-level function (was inline logic inside
  `Theme.button_text_color()`, which now just calls it), so other
  custom-painted widgets whose fill isn't always `button_color()` (the
  Sort popover's tab strip, specifically) can get the same contrast
  behavior against whichever color they're actually painting.
- `afterglow/gui/library_page.py` -- `_VideoGridTab.refresh()` is now a
  thin leading-edge-debounced (750ms) wrapper around the actual rebuild
  logic, renamed to `_do_refresh()`. Any NEW caller that wants "act
  immediately, every time, no debounce" (the way `search_edit`'s
  `textChanged` does) should call `_do_refresh()` directly, not
  `refresh()`. `_composite_tab_icon()` gained `bg_color`/`radius`/
  per-corner-skip parameters for the turquoise tab-icon backgrounds.
- `afterglow/gui/video_card.py` -- `_build_icon_row`'s filter-icon
  outline is computed at the STABLE `reserved` (base setting) size, not
  the per-video count-adjusted `icon_size` -- this distinction is a
  genuine, measured performance requirement now (see item 21 above),
  not just a style choice; using `icon_size` in the outline's cache key
  again would silently reintroduce the exact startup-slowdown bug.
- `afterglow/gui/outlined_label.py` -- NEW this session. `OutlinedLabel`,
  used for all four on-card text elements. `outline_width <= 0` means
  "fill only, no stroke" -- see item 17 above for the real font-size
  reason the three smaller text elements need this while the title
  doesn't.
- `afterglow/gui/pixmap_effects.py` -- `silhouette_outline_pixmap()`/
  `_cached()`, new this session, for Filter Outline. Same "pure Python,
  cache it" pattern as the existing `hue_shift_pixmap`/`_cached`.
- `afterglow/gui/rounded_rect.py` -- `round_pixmap_corners()`, new this
  session, used by `video_card.py`'s `_load_pixmap` to actually round
  the thumbnail image's own corners (previously planned but never
  implemented, despite the rest of Phase 2's rounding work).
- `afterglow/gui/theme.py` -- `Theme.turquoise()`, new this session,
  used only by the Library tab icons so far.
- `afterglow/gui/main_window.py` -- `MainWindow.__init__` now sets the
  central widget's background via `Theme.app_background()` -- the
  first real (non-video-card) use of `Theme` anywhere in the app.
- `afterglow/gui/library_page.py` -- the grid's `scroll`/
  `grid_container` now use `Theme.library_background()`;
  `_composite_tab_icon()` gained a `bg_color`/`radius`/per-corner-skip
  parameters for the turquoise tab-icon backgrounds; grid spacing now
  reads `ui_padding` instead of a hardcoded `2`.
- `afterglow/db.py` / `afterglow/library.py` -- new `outline_color`
  column on `tags` (migrated), `set_tag_outline_color()`/
  `tag_outline_colors()`.
- `afterglow/gui/filters_settings_page.py` -- `_TagIconRow` gained an
  outline-color field + picker alongside its existing icon controls.
- `afterglow/gui/video_card.py` -- extensively rewritten two sessions
  ago (Phase 2). New `_InfoBox` class; `video_box` (thumbnail's
  bordered wrapper); `_full_title_text` (the un-elided source of truth
  for the title, elision is applied only at display time in
  `set_font_scale`, which now handles both font-shrinking AND eliding
  together, AND -- new this session -- reserves the title row's fixed
  height from the un-shrunk target font size, which is what actually
  fixed the real padding bug); `_build_icon_row` reserves constant
  space regardless of per-video tag count; `paintEvent` built around
  `rounded_rect.py` + `theme.py`; padding constants replaced by
  `appearance.ui_padding` this session.
- `afterglow/gui/resources/selected_border_gradient.png` -- the
  gold/white image Max provided directly, now the selection border's
  bundled default.
- `afterglow/gui/clip_config_row.py` -- `clear_hotkey_btn`/
  `_clear_hotkey()`.

- `afterglow/gui/pixmap_effects.py` --
  `resolve_border_pixmap()`, `hue_shift_pixmap()`, and
  `hue_shift_pixmap_cached()` (the caching matters -- see item 11
  above for why an uncached hue shift would have been a real per-card
  performance problem). Shared by `main_window.py` and `video_card.py`.
- `afterglow/gui/library_page.py` -- `_VideoGridTab.refresh()` now
  calls `widget.hide()` before `deleteLater()` when clearing old cards
  (item 15 above) -- if this pattern (remove-from-layout-then-defer-
  delete) ever gets copy-pasted elsewhere in this codebase, the hide()
  needs to come along with it.
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
