# afterglow -- handoff

## What this app does
OBS-triggered clip capture, a local clip library (PySide6 GUI), quick
trimming in the video previewer (embedded mpv), a track-based Advanced
Editor (the Editor page; afterglow/nle engine + afterglow/gui/
advanced_editor UI), and YouTube upload (unlisted-library metadata
cached locally, upload flow itself not yet implemented). Settings and
Library pages are solid and confirmed working across multiple machines.
The keyboard/mouse/controller **input overlay** (captured from Puppetry
with each clip) is BUILT -- see "Input overlay (Puppetry integration) --
BUILT" directly below, and the **clip indicator ("the clapper")** is BUILT too (code + tests; still
unverified on real hardware) -- see "Clip indicator ("the clapper") -- BUILT".

**Tests:** `tests/` (Qt widget tests with real events). Headless:
`Xvfb :99 -screen 0 1920x1080x24 +extension GLX & DISPLAY=:99 openbox &`
then `DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/<suite>.py` (the
previewer suites need GL for mpv); the `tests/test_nle_*.py` engine
suites run with `QT_QPA_PLATFORM=offscreen`. Every suite prints PASS/FAIL
lines and exits non-zero on failure. All 34 suites pass at handoff (incl. the overlay suites listed
under the Input overlay status, the five clip-indicator suites (`test_indicator_*`, `test_clip_indicator_ui`)
and `test_themed_dialogs`; `test_overlay_mpv`
and the previewer / advanced-editor suites need libmpv + Xvfb (with `libxcb-cursor0` for the xcb platform;
they hang or fail under offscreen); `test_nle_render`'s preview-
speed check is timing-based, run it on its own, not alongside other suites).

## Input overlay (Puppetry integration) -- BUILT (this is the design + status)

### Status (newest session)
Plan steps 1-9 are implemented and tested. Where the code lives:
`afterglow/input_overlay.py` (vendored, byte-identical to the prep package
-- never edit it), `afterglow/overlay_support.py` (afterglow glue:
rotation in placements, sidecar move/copy/delete/trim/backup/restore,
the previewer's mpv graph), capture hook = `clips._OverlayCapture`
(+ `OBSClient.save_replay_buffer(on_sent=...)`), Settings: the per-clip-type
Input overlay row + "..." dialog and the "Input Overlay" tab
(`gui/overlay_options.py`), previewer toggle (`video_preview_dialog.py`,
`mpv_widget.set_overlay`), Editor model/ops/render (`nle/model.py`
OverlayPiece, `nle/ops.py` set_overlay/detach_overlay, `nle/render.py`
`overlays=` flag + `frame_piece`), export sidecar (`nle/overlay_export.py`,
called from `nle/save.py`), Editor UI (Properties "Input Overlay" group,
canvas handles in `preview.py`, preview "Overlay" toggle, timeline
"Detach Input Overlay", `EditorController.detach_overlay/set_overlay/
add_overlay_piece/pick_overlay`).
Findings / deviations worth knowing:
- **Pieces are attached to `Part.overlays`, not `Segment.overlays`** (the
  plan said Segment): `combine` merges several parts into one segment, and
  each part has its own source clip/pieces. `ops.set_overlay` applies a
  placement edit to that piece name on every part of the segment (one
  layout per clip, as decided).
- **qtrle is NOT all-intra by default** (ffmpeg keeps a keyframe every 12
  frames), so "stream copy is frame-exact" is false unless encoded `-g 1`.
  `overlay_support.cut_piece` stream-copies only when every packet is a
  keyframe, else re-cuts losslessly (qtrle, argb, -g 1).
- mpv graph (external files + lavfi-complex, runtime toggle, rotation) was
  verified in a REAL libmpv 0.37 (vo=image), incl. audio.
- `Project.video_duration` ignores overlay segments: they never lengthen
  the exported video (export uses it; `duration` still includes them).
- Derived (exported) sidecars store REAL per-piece placements when the
  layout is static (same placement on plain, untransformed, canvas-sized
  clips; rebuilt at native size, stream-copied for a single plain clip);
  animated/transformed/detached pieces are canvas-sized, full-frame.
- Save/Export writes the output's own `<output>.input/` sidecar, so the
  placements edited in the Editor travel with the output file (there is no
  separate "write back to the old manifest" step).
- The Library clipboard Copy deliberately does not carry the sidecar.
- Editor canvas: click a piece (selected clip) to pick it, drag = move,
  corners = size about the centre, top handle = rotate (Shift = 15 deg);
  clicking the bare video clears the pick. The Properties "Render" row
  re-renders a piece from the recorded input (`aio.rerender`) or adds one
  that wasn't captured; not possible for exported (derived) clips.
- Tests: `tests/fake_puppetry_overlay.py` (fake CLI), `test_input_overlay`,
  `test_puppetry_integration` (skips without Puppetry), `test_overlay_
  settings_ui`, `test_overlay_mpv` (needs libmpv + Xvfb), `test_overlay_
  previewer` (Xvfb), `test_overlay_nle`, `test_overlay_editor_ui`.
- Library card: a plain click on the thumbnail/video box opens the
  preview player (the old hit-test used the wrong coordinate space);
  `tests/test_card_click_preview.py`.

### Fix: "the input overlay doesn't show up" (newest session, tested against the REAL Puppetry)
Puppetry's source was provided, so the capture pipeline was run against the
real `puppetry-overlay` (not the fake) -- `tests/test_puppetry_capture_real.py`
(set PUPPETRY_OVERLAY; skips otherwise). Findings:
- **The capture rendered the WHOLE replay length.** `aio.start_clip` renders
  each piece over [t_save - replay_length - 2 s, t_save + 0.5 s], then
  `finish_clip` cuts it down. With a 1200 s buffer that is ~72,000 frames per
  piece; the real renderer does ~90-330 fps (busy vs idle frames), i.e.
  several minutes to a quarter hour, and `trigger_clip` blocked on it BEFORE
  `add_video` (the daemon's 120 s CAPTURE_TIMEOUT just logged "stuck").
  Now: `on_sent` only freezes the input (`overlay_support.freeze_input`);
  after the clip is added to the library, `overlay_support.capture_clip`
  renders ONLY the clip's span at the clip's own fps, straight into
  `<clip>.input/` (via `<clip>.input.new/`), pieces in parallel, on a
  background thread (`_OverlayCapture.finish_in_background`;
  `clips.wait_for_overlays()` for tests/CLI). A 15 s 60 fps clip: ~3 s.
  The vendored module is untouched (still byte-identical).
- The real `align` stream-copies Puppetry's qtrle, which is NOT all-intra
  (keyframe every 12 frames), so `-ss ... -c copy` snapped to an earlier
  keyframe: up to ~0.2 s misalignment. The new path has no align step.
  **Puppetry-side suggestion (not done -- Puppetry's code):** add `-g 1` to
  `overlay_render._encoder_args` for .mov so its own `align` is exact.
- Also fixed: the CLEANED_UP_MOVED success sound had been replaced by the
  INPUT_OVERLAY sound (stage was reassigned before add_video).
- A rename during the background render: the finished sidecar follows the
  clip (`finish_in_background` checks the video's current path).
- Previewer: a clip opened while its overlay is still rendering (the
  `.input.new` dir exists) polls every 1.5 s and reloads in place when the
  sidecar lands -- the toggle/overlay appear without reopening.
- Settings > Input Overlay now says which clip types capture the overlay,
  or warns that none does (Capture is OFF by default per clip type).

### Original design (kept for reference)
Puppetry (a separate Linux keyboard/mouse macro daemon)
keeps a rolling in-RAM record of all keyboard, mouse and controller input
("Layered Replay Buffer", as long as OBS's replay buffer) and renders it
as transparent video. This epic makes afterglow capture that record with
each clip and show it as an **input overlay**: toggleable in the
previewer, editable in the Editor, and NEVER burned into the video --
exports write a matching overlay sidecar for the output instead (see
decision 5).

**Source material (read first):** `integrations/puppetry_input_overlay/`
is the Puppetry side's prep package (v3), copied in unchanged:
- `README.md` -- the intended afterglow-side design (settings, capture
  hook, previewer, editor, export, availability).
- `FORMAT.md` -- the full interface: buffer/status files, the
  `puppetry-overlay` CLI, transparency formats, the `<clip>.input/`
  sidecar and its `manifest.json`, the mpv graph, the timing model and
  offset calibration.
- `afterglow_input_overlay.py` -- a stdlib-only drop-in module wrapping
  all of it (`start_clip`, `finish_clip`, `load_sidecar`,
  `placements_of`, `save_placements`, `piece_rect`,
  `preview_overlay_args`, `mpv_overlay_args`, `export`, `rerender`,
  `available`, `resolve_placements`, `OverlayError`).
- `test_afterglow_input_overlay.py` -- integration test against the real
  `puppetry-overlay` + ffmpeg; skips when Puppetry isn't installed (it
  isn't in the sandbox).
- `sample/` -- example buffer, status and sidecar manifest.

### Decisions (asked and answered before writing this section)
1. **The Editor treats the overlay like a clip's audio.** A clip's
   overlay pieces (keyboard, mouse, controller, ...) are ATTACHED to its
   video segment by default: they move, trim, split, speed-change,
   delete and export with it. While attached, each piece's position,
   rotation and size *within the video* can be edited (and it can be
   shown/hidden). A **Detach Input Overlay** action (mirroring Detach
   Audio) turns the pieces into independent video-only elements on the
   track(s) above, same timing, with their own transform, keyframes and
   effects -- but they REMAIN overlay elements (decision 8).
2. **Trimming follows the video.** Because the overlay is attached,
   whatever trims the clip trims the overlay: the previewer's Save Trim
   cuts the sidecar pieces with the same in/out (the pieces are
   all-intra qtrle, so a stream copy is frame-exact), and Undo Edits
   restores the original pieces with the original clip.
3. **Settings location:** each clip type's row in Settings > Clip
   Capture gets an "Input overlay" toggle plus a "…" button (pieces,
   show by default, timing offset, per-piece placement overrides or
   "use global"), with `aio.available()`'s reason shown beside the
   toggle when unavailable. The global default placements go on a new
   Settings > Input Overlay page.
4. **Delivered as a full afterglow handoff** (this repo + this section),
   not a standalone document.
5. **Export never burns the overlay in.** This overrides the prep
   package's `aio.export()` / `puppetry-overlay layer` design. An
   Editor export (Replace, Save Separately, or an import's "-edited"
   file) writes clean video and gives the output its own
   `<output>.input/` sidecar, so the overlay stays toggleable in the
   previewer and editable in the Editor for the new file too (Save
   Separately's new clip included). How: plan step 7b.
6. **Rotation shows in the previewer too** (a `rotate=` step in the mpv
   graph), matching the Editor.
7. **No keyframes on attached pieces.** Attached pieces have one fixed
   layout per clip; Detach Input Overlay is the way to animate them
   (detached pieces get full keyframes).
8. **The overlay is always separate from the video, attached or
   detached.** Every overlay element is left out of the exported video
   exactly as if it were hidden, and exported instead as transparent
   (alpha) video in the output's `<output>.input/` sidecar, which is
   what the previewer's toggle and the Editor's editing use. Nothing
   ever burns an overlay into the video.

### Where the prep package's assumptions differ from afterglow now
- **The Editor does not use mpv.** The prep's editor path
  (`mpv_overlay_args`, "drag a Qt outline over the mpv widget, rebuild
  `lavfi-complex` on release") predates the Advanced Editor, which
  decodes with PyAV and composites with QPainter (`nle/render.py`). Only
  the previewer plays through mpv. So: the previewer uses
  `preview_overlay_args` as designed; the Editor draws the pieces itself
  (see plan step 7) and never touches `mpv_overlay_args`.
- **Rotation is not in Puppetry's placement schema** (`{x, y, w,
  visible}`, fractions of the video). afterglow adds a `"rotation"` key
  (degrees) to each piece's placement in `manifest.json`; Puppetry's
  own tools ignore unknown keys. The previewer's mpv graph gets a
  `rotate=a=<rad>:c=none:ow=rotw(<rad>):oh=roth(<rad>)` step per rotated
  piece (an afterglow-side variant of `preview_overlay_args`; the
  overlay x/y then offset by half the size growth so the piece rotates
  about its center, matching the Editor).
- **`aio.export()` is not used** (decision 5): nothing is burned in.
- **The previewer never edits placements** (per the prep's own
  decision); only the Editor does.

### Implementation plan (in order)
1. **Vendor the module.** Copy `afterglow_input_overlay.py` to
   `afterglow/input_overlay.py` (keep it byte-identical where possible so
   future Puppetry-side versions drop in); adapt its test into
   `tests/test_input_overlay.py`, plus sandbox tests that build FAKE
   sidecars without Puppetry -- e.g. transparent qtrle pieces via
   `ffmpeg -f lavfi -i color=c=red@0.5:s=320x100,format=argb -t 5 -c:v
   qtrle keyboard.mov` next to a clip, with `sample/manifest.json`
   adapted.
2. **Clip-type settings.** Clip types live in the DB
   (`clips.ClipConfig`, table `clip_configs`), not config.toml: add
   columns via a `db.py` migration -- `overlay_enabled`,
   `overlay_pieces` (JSON list), `overlay_visible_default`,
   `overlay_offset_ms`, `overlay_placements` (JSON, per-piece overrides).
   Global placements: a config.py field (dict piece -> placement), edited
   on Settings > Input Overlay. New clips start at
   `aio.resolve_placements(global, clip_type)`.
3. **Capture hook** (`clips.trigger_clip`). `start_clip` must run the
   moment SaveReplayBuffer is SENT (it freezes the input and starts
   rendering while OBS writes). `obs_client.save_replay_buffer()` both
   sends and waits (and may first wait on another save's lock), so give
   it an `on_sent(t_save)` callback invoked right after the request goes
   out, and call `aio.start_clip` from there when the clip type has the
   overlay on. After the file is written: `aio.finish_clip(job, path)`
   -> `<clip>.input/`, then `job.cleanup()`. On `OverlayError`: keep the
   clip, play the error noise (a new keyframe in `keyframes.py`, e.g.
   "input overlay", fits the Advanced Sound system), log the reason.
   A missing overlay must never cost a clip.
4. **Sidecar lifecycle -- every file operation on a clip must carry
   `<clip>.input/` along:** the capture pipeline's move/cleanup step,
   `library.rename_video` (renames the file), `library.delete_video`,
   the Library's copy action, and any import/move path. Quick trim
   (`editor.commit_trim` via `library.apply_trim`): back up the sidecar
   beside the clip's Edit Backup (e.g. `<backup>.input/`), then trim
   every piece with the same `-ss`/`-t` (stream copy); `editor.undo_trim`
   / `library.undo_edit` restore it; `clear_edit_backup` removes the
   backup copy.
5. **Previewer** (`gui/video_preview_dialog.py`, mpv via
   `gui/mpv_widget.py`). An "Input overlay" icon toggle in the header
   row beside Favorite/Filters, shown only when `aio.load_sidecar(clip)`
   isn't None, initial state `manifest["visible_by_default"]`. On load
   set mpv `external-files`; on load and every toggle set
   `lavfi-complex` (MpvVideoWidget needs a small property passthrough).
   Only the whole overlay toggles here. Not verified in a running mpv
   by the Puppetry side -- verify early on the real machine.
6. **Editor model** (`nle/model.py`, schema 3 + migration). New
   `Segment.overlays: list[OverlayPiece]` where `OverlayPiece` = piece
   name, source `.mov`, `x`, `y`, `w`, `rotation`, `visible`. The pieces
   share the main video part's source timeline (they are cut to the
   untrimmed clip), so a piece's frame at segment-local time `t` is at
   the main part's `source_time(t)`: trims, splits (`ops.split_segment`
   must copy the list), speed changes and deletes all follow for free.
   `nle/save.new_project_for_file` attaches pieces from
   `aio.load_sidecar` + `aio.placements_of`. `ops.detach_overlay`
   (modelled on `ops.detach_audio`) moves each piece to its own
   video-only segment on the nearest free track above, converting its
   placement into that segment's Transform (x/y/scale/rotation), and
   clears `Segment.overlays`. Detached pieces carry
   `Segment.overlay_piece = "<piece name>"` ("" for ordinary elements),
   which marks them as overlay for the renderer, the export and the
   UI (e.g. a distinct timeline color and an overlay icon).
7. **Editor rendering** (`nle/render.py`). `_draw_segment` draws each
   visible overlay piece after the segment's picture, inside the same
   segment transform (so moving/scaling/rotating the video carries the
   overlay), at `piece_rect` + rotation. Pieces are ARGB qtrle;
   `media.frame_to_qimage` already converts to BGRA/ARGB32, so alpha
   should survive -- confirm with a fake sidecar.
   These overlay draws are for the Editor preview only: the export
   render skips ALL overlay content -- attached pieces and detached
   overlay segments (`overlay_piece != ""`) alike, treated as hidden --
   via a `Renderer(project, overlays=False)` flag, so
   `Renderer.passthrough_frame` stays valid and the video stays clean.
   The Editor preview's overlay toggle (Properties / a toolbar button)
   hides them all at once, like the previewer's.
7b. **Export writes an overlay sidecar for the output.** After the video
   is written, for each piece name used by visible overlay content
   (attached pieces AND detached overlay segments, with their keyframes
   and effects), render a transparent track at the canvas size and
   project fps with the Renderer drawing ONLY that piece's content
   (frame for frame, whichever segment is active), encoded as
   qtrle ARGB `.mov` (mostly-transparent frames compress well with
   RLE). Write `<output>.input/manifest.json` with each such piece at a
   full-frame placement (`x=0, y=0, w=1, rotation=0`), the
   `visible_by_default` of the source clip, and `"derived": true` plus
   no `inputs` (a timeline-built overlay can't be re-rendered from raw
   input; the Editor project keeps the original per-clip pieces for
   that). Fast path: when the timeline is exactly one untransformed
   clip with its pieces, cut the source pieces with the same in/out
   (stream copy) and keep their real placements instead of rendering.
   The previewer's toggle then shows/hides the whole overlay on the
   exported file, and reopening it in the Editor attaches these pieces
   again.
   **Replace** overwrites the clip, so before writing the new sidecar,
   move the clip's original `<clip>.input/` into Edit Backups beside the
   `.orig` backup and re-point the project's piece sources there (the
   same job `store.stabilize_sources` does for the clip's own file);
   Undo Edits restores it. **Save Separately** gives the new library
   clip the new sidecar; the original clip keeps its own.
8. **Editor UI.** Properties: an "Input Overlay" group on segments that
   have one -- master show/hide, per piece: visible, X, Y, Size,
   Rotation, "Re-render from input" (`aio.rerender`, e.g. after changing
   Puppetry's look) and an "Add piece" for pieces not captured
   (controller on a keyboard-only clip). Preview canvas: when such a
   segment is selected, its pieces get their own handles (click a piece
   to pick it; drag = move, corner = size, top handle = rotate) --
   reuse `PreviewCanvas`'s handle code and the `live_preview` drag path.
   Timeline right-click + Audio-style button: "Detach Input Overlay".
   On Save Edits / Export, write the attached placements back to the
   clip's manifest (`aio.save_placements`, with the extra `rotation`
   key) so the previewer shows the same layout.
9. **Availability** (`aio.available()`): shown next to each clip type's
   toggle; the capture hook skips (without an error noise) when the
   overlay is off, but plays it when the overlay is on and unavailable.

### Open questions for this epic
- (Answered) Derived sidecars store per-piece placements when the layout
  is static -- see the status block above.

### Known unverified (from the Puppetry side)
mpv itself (graphs were validated through ffmpeg only), real OBS and real
input devices (controller stick/trigger ranges vary), and the real
alignment offset (a few frames at most; `offset_ms` corrects it).

## Clip indicator ("the clapper") -- BUILT (status first, then the original spec)

### Status (newest session)
Build steps 1-5 of the plan below are implemented and tested. Step 6 (verification on a
real KDE Plasma Wayland desktop) has not happened: everything was exercised under the offscreen Qt
platform, which is not the same as verification (see "Unverified on real hardware" below).

Where the code lives:
- `afterglow/indicator/` -- `draw.py` (clapper + hands + circle + every enter/exit/fail animation as
  pure `animate(phase, kind, t, anchor, Space) -> Xform`), `layout.py` (surface size, stack slots,
  badge, X11 placement), `model.py` (pure state machine per capture + stacking, driven by an explicit
  `now`), `paint.py` (paints a stack of frames; shared by the overlay and the Settings preview),
  `focus.py` (focused window -> screen: kdotool / hyprctl / swaymsg), `layershell.py` (ctypes binding
  of the shim), `surface.py` (the Qt overlay window), `helper.py` (socket server + event dispatch +
  surface lifecycle), `__main__.py` (the process: `python -m afterglow.indicator` /
  `afterglow-indicator`).
- `native/afterglow_layershell.cpp` + `native/CMakeLists.txt` -- the C shim (see below).
- `afterglow/indicator_client.py` -- the fire-and-forget client used by `clips.py`, `daemon.py`, the
  CLI and the Settings Test button.
- Hooks: `clips.trigger_clip` (`clap` right after `save_replay_buffer` returns; `done` / `overlay` when
  the clip is in the library; `fail` in the `except`), `clips._OverlayCapture` (`overlay_done` /
  `overlay_fail`, plus a 10 minute render timeout), `daemon.ClipDaemon` (`_on_hotkey` starts the
  indicator; the 120 s capture timeout and a failure before `trigger_clip` got going send `fail`),
  `clips._resolve_keyframe_sound` (clap sound).
- Config / DB: `config.ClipIndicatorSettings` (`AppSettings.clip_indicator`), `clip_configs`
  columns `indicator_colors` / `indicator_icon_path` / `indicator_clap_sound` (migrated like the
  overlay columns).
- Settings UI: `gui/clip_indicator_settings.py` (the "Clip Indicator" group under Settings >
  Clipping, the per-clip-type dialog, `ColorSwatch`), `gui/indicator_preview.py` (`IndicatorPreview`
  live loop + `AnchorPicker`), the "Indicator" row in `gui/clip_config_row.py`.
  `CustomComboBox` gained `itemHovered(int)` / `popupHidden()` signals (hover preview).
- Packaging: `flake.nix` builds the shim (`mkLayerShell`: cmake, `qt6.qtbase`,
  `kdePackages.layer-shell-qt`) and the wrapper sets `AFTERGLOW_LAYERSHELL_LIB`;
  `pyproject.toml` has the `afterglow-indicator` script.
- Tests: `tests/test_indicator_model.py`, `test_indicator_draw.py`, `test_indicator_helper.py` (incl.
  the real helper process over its real socket), `test_indicator_pipeline.py` (hooks, fake OBS),
  `test_clip_indicator_ui.py`. All run with `QT_QPA_PLATFORM=offscreen`.

Deviations from the plan and judgment calls (change freely):
- **Socket, not stdin.** The helper listens on a Unix socket (`$XDG_RUNTIME_DIR/afterglow-indicator.sock`,
  override `AFTERGLOW_INDICATOR_SOCKET`), JSON lines, same messages as planned. Reason: the daemon,
  the CLI and the GUI's Test button all have to reach one helper, and the helper has to outlive any of
  them. A second helper finds the socket owned and exits; a stale socket file is replaced. The client
  spawns the helper on demand (display variables are taken from `systemctl --user show-environment`
  when missing) and the daemon starts it at startup.
- **The capture id is allocated at the key press**, on the evdev thread (`indicator_client.begin()` only
  queues), so the clapper appears even while an earlier capture is still being processed.
  `trigger_clip(clip_config_id, indicator_id=_AUTO)`: the daemon passes its id in; CLI / GUI triggers
  start the indicator themselves; `None` = none.
- **Surface:** fixed size (3 item widths x stack + 2 item heights; 4 widths for top / bottom), created
  when a stack's first capture appears and destroyed when the last is gone, so nothing sits above a
  fullscreen game between clips. Left / right anchors with five stacked slots can overflow a screen
  shorter than ~800 px. `drop` / `fall` fade out where the surface edge is not a screen edge.
- **Helper watchdog** is 12 minutes (overlay timeout 10 min + 2), so a lost event cannot leave
  anything on screen.
- **No layer-shell on Wayland** -> the visual is skipped (logged once); sounds are unaffected (the
  indicator never plays sound itself).
- **Clap sound:** `indicator_clap_sound` is used only while the indicator is enabled; otherwise the old
  chain (clip sound -> advanced sound -> default sound) applies unchanged. Played once, by `clips.py`.
- Hover-previewing an animation in the Settings dropdowns restarts the preview with that animation;
  closing the dropdown restores the chosen one. The preview is a 1280x720 stand-in screen drawn to
  scale. `circle_opacity` has a spin box in Settings although the plan did not list one.
- Test button: sends start -> clap -> processing -> overlay -> overlay_done with the current (unsaved)
  widget values through the real helper.
- Environment flags: `AFTERGLOW_INDICATOR_SOCKET`, `AFTERGLOW_LAYERSHELL_LIB`,
  `AFTERGLOW_INDICATOR_ALLOW_OFFSCREEN` (`spawn_helper` refuses to start a helper under the offscreen /
  minimal Qt platform otherwise, so existing tests never spawn one).
- Debugging: `python -m afterglow.indicator --socket /tmp/x.sock --log-events /tmp/x.jsonl -v` logs every
  event with the resulting stack state; any JSON line sent to the socket is accepted
  (`echo '{"id":"a","event":"start","style":{}}' | socat - UNIX-CONNECT:/tmp/x.sock`).

Unverified on real hardware (plan step 6, all still open):
- The C shim (`native/afterglow_layershell.cpp`) was only syntax-checked against stub headers; it has
  never been linked against a real LayerShellQt, nor built in the flake. Same-Qt requirement applies.
- Layer-shell surface behaviour: above a fullscreen game (and no stutter while shown), correct monitor,
  click-through, no focus steal, HiDPI, two monitors with different scales, all eight anchors with padding.
- `kdotool` / `hyprctl` / `swaymsg` output parsing for the focused screen (unit-tested on sample output).
- The X11 fallback window flags.
- Real timing of every animation and the clap; a burst of 3+ hotkeys; an overlay clip (gray -> purple);
  a forced failure (launch).

### Original spec and plan (kept as the design reference)

Asked for verbatim (abridged): a movie clapper slides onto the screen when a
clip hotkey fires, claps (with a sound) when afterglow receives the clip,
then shows processing until the clip is done; colours / icon / sound per
clip type; selectable animations; optional "hands" style (Mickey-glove line
art clapping) instead of the clapper. Every question has been answered --
every item below was built as written unless the deviations above say otherwise.

### Decisions (all from the user)
- **Which screen:** the one in use -- the screen holding the focused window.
- **When it claps:** when OBS confirms the save (`REPLAY_BUFFER_COMPLETED`).
  Processing runs from the clap until the clip is in the library
  (`CLEANED_UP_MOVED`).
- **On failure:** "clapper gets launched into the air and falls offscreen".
- **Icon below the lines:** custom images only; none by default. In hands
  mode the icon goes **on the back of the glove**.
- **Position:** any of 8 anchors -- every corner and the middle of every
  edge (`top_left`, `top`, `top_right`, `left`, `right`, `bottom_left`,
  `bottom`, `bottom_right`); default `bottom_right`. User-set X and Y
  padding from the screen edges (X is unused for `top`/`bottom`, which are
  centred horizontally; Y is unused for `left`/`right`, centred vertically).
- **Animations:** the way it comes in and the way it goes away are chosen
  **separately** (two settings); the set of animations was left to us --
  see "Animations" below.
- **Processing, two modes:** (default) the clapper leaves and a small
  semi-transparent loading circle fades in where it was; OR the clapper
  stays on screen until processing is done.
- **Loading circle colour:** **gray** by default while the clip is being
  processed, then **purple** while the input overlay is being rendered
  (the circle stays up through the overlay render). Both colours are
  settings with those defaults.
- **Rapid repeats:** a second hotkey while the first is still going shows
  a **second clapper above the first** (stacked).
- **Clap sound:** the user's choice; defaults to the user's global clip sound.

### Event timeline (hooks into `clips.trigger_clip`, keyed by keyframes.py)
| pipeline moment | indicator event |
|---|---|
| `HOTKEY_RECEIVED` | `start`: pick the screen, play the chosen **enter** animation (~250-550 ms) |
| `REPLAY_BUFFER_COMPLETED` (after `save_replay_buffer` returns) | `clap`: clap animation + clap sound. If the enter animation hasn't landed yet, the clap waits for it (OBS can confirm in <300 ms) |
| (~400 ms after the clap) | `processing`: default mode = the **exit** animation, then the gray circle fades in at the clapper's spot; "stay" mode = clapper stays (subtle idle bob) |
| `CLEANED_UP_MOVED` (clip is in the library) | clip type WITHOUT input overlay: `done` -> circle fades out (stay mode: exit animation). WITH input overlay: `overlay` -> circle cross-fades gray -> purple (300 ms) and keeps spinning (stay mode: the clapper exits now and the purple circle fades in) |
| overlay finished (`_OverlayCapture`'s background thread, where it plays the `INPUT_OVERLAY` keyframe sound) | `overlay_done`: purple circle fades out |
| overlay failed (same thread, where it plays the `INPUT_OVERLAY` Error Noise) | `overlay_fail`: circle flashes red, small shake, fades out -- NOT the launch: the clip itself is safe |
| any exception in `trigger_clip` (the `except` that plays the Error Noise) | `fail`: launched into the air with a spin, falls off the bottom of the screen. If the circle was showing, the clapper pops back in at the circle's spot first, then gets launched |

Timeouts: the daemon's 120 s capture timeout sends `fail`; the overlay
render gets its own (e.g. 10 min -> `overlay_fail`); the helper also drops
any indicator with no event for that long, so nothing hangs on screen.

### Stacking (rapid repeats)
- Each capture id gets its own indicator in a stack at the anchor. Slot 0
  is at the anchor; each later slot sits one clapper height + 12 px further
  from the anchored edge: **upward** for bottom anchors and `left`/`right`,
  **downward** for top anchors ("above the first" from the user's bottom-
  right default).
- A new clapper enters straight into the next free slot. When an indicator
  finishes (its circle fades out / it exits / it's launched), the ones
  above it slide down to close the gap (200 ms ease).
- A slot is held for the whole life of its capture: clapper, then circle
  (gray, then purple), so the circles stack the same way.
- Cap of 5 visible; a 6th+ collapses into a "+N" badge on the top slot.

### Process model
- The GUI may not be running, so the indicator belongs to the **daemon**.
  The daemon is headless (evdev + worker threads, no Qt), so it runs a
  small Qt helper, `python -m afterglow.indicator`, started at daemon start
  when the indicator is enabled (so the first clap is instant) and
  respawned on demand if it died. Talk over the helper's stdin as JSON
  lines: `{"id": <capture id>, "event": "start"|"clap"|"processing"|"done"|
  "overlay"|"overlay_done"|"overlay_fail"|"fail", "style": {...}}` (`style`
  only on `start`: resolved colours, icon path, anchor, padding, size,
  enter/exit animations, mode, circle colours, screen hint). Fire-and-
  forget from `trigger_clip` / `_OverlayCapture` through a tiny
  `indicator_client.py` (never raises, never blocks the capture -- same
  rule as `play_sound`). Capture ids: a counter in the daemon.
- The daemon's worker runs captures one at a time, but a capture's overlay
  render continues in the background and the next hotkey's `start` can
  arrive while the previous one is still processing -- so stacks do happen
  in practice.
- Environment: the helper needs `WAYLAND_DISPLAY`/`XDG_RUNTIME_DIR` (or
  `DISPLAY`). Plasma 6 imports them into the systemd user manager, but a
  daemon started before the session won't have them -- read them from
  `systemctl --user show-environment` when spawning if they're missing.

### Wayland overlay window (the main technical risk)
- A normal Qt window can't place itself on Wayland and won't sit above a
  fullscreen game. It has to be a **layer-shell surface** on the `overlay`
  layer: keyboard interactivity none, exclusive zone 0 (doesn't push other
  windows), input region empty (clicks pass through:
  `Qt.WindowTransparentForInput` + `WA_TransparentForMouseEvents`), shown on
  the chosen output.
- **Surface geometry:** the surface is anchored to the anchor's edge(s) with
  margin 0 (NOT the padding) and the padding is applied in the drawing, so
  enter/exit animations can start/finish truly off-screen at the edge
  instead of popping out of thin air at the padding line. It is sized to
  hold the stack plus travel room (corner anchors: ~3 clapper widths x the
  stack height + 2 clapper heights; edge anchors: the same along that
  edge). Toss/launch arcs are designed to stay inside it; the fall is
  clipped at the bottom of the screen, which is where it should vanish
  anyway. The surface is hidden whenever no indicator is alive, so it
  can't interfere with a fullscreen game's direct scanout between clips
  (verify on hardware that it doesn't stutter the game while shown).
  Layer-shell anchors: corners = two edges; `top`/`bottom`/`left`/`right`
  = that one edge (the compositor centres it along the edge).
- KDE's implementation is **LayerShellQt** (nixpkgs `kdePackages.layer-shell-qt`).
  It has a C++ API only -- no Python bindings exist (KDE Discuss "Python
  bindings for layer-shell-qt?": "not possible right now"). Plan: a ~40-line
  C++ shim built in the flake (pybind11 or a plain C ABI loaded with
  ctypes) exposing `configure(qwindow_ptr, layer, anchors, margins,
  keyboard, screen_name)` that calls `LayerShellQt::Window::get(window)`;
  Python passes `shiboken6.getCppPointer(widget.windowHandle())[0]`. Must be
  called after `winId()`/`create()` but before the first `show()`. The shim
  and the helper must use the SAME Qt as PySide6 (the plugin only loads into
  the Qt it was built against -- build both from the same nixpkgs).
  `QT_WAYLAND_SHELL_INTEGRATION` must NOT be set globally (it would turn
  every window of the process into a layer surface).
  Alternative if the shim is a problem: GTK4 + `gtk4-layer-shell` through
  PyGObject (pure Python, but a second toolkit and the drawing code would be
  Cairo, not the shared QPainter code). Recommended: the shim.
- **Fallbacks:** X11 session -> frameless, always-on-top, tool, transparent-
  for-input window positioned by geometry. Non-KDE wlroots compositors work
  with the same layer-shell path. No layer-shell available -> log once and
  skip the visual (sounds still play).
- **Focused screen:** Wayland clients can't read the cursor position. Reuse
  the `kdotool` path from `autofilter.py`: `kdotool getactivewindow
  getwindowgeometry` -> the screen containing the window's centre ->
  match by geometry to a `QScreen`; Hyprland/Sway equivalents as autofilter
  does; fallback primary screen. Resolved at `start`; a stack lives on one
  screen, so a capture that starts on another screen gets its own surface
  and stack there.
- HiDPI: size and padding are logical px (scaled per screen by Qt).

### Drawing (shared QPainter code, so Settings can preview it)
- New `afterglow/indicator/draw.py`: pure functions `draw_clapper(p, rect,
  state, style)`, `draw_hands(...)`, `draw_circle(...)`, and
  `animate(kind, t, anchor, rect) -> (offset, rotation, scale, opacity,
  clip)` for every enter/exit/fail animation -- no window code, testable
  offscreen, reused by the Settings preview widget.
- Clapper parts, each a style colour: top stick (two-tone diagonal stripes:
  `stripe_a`/`stripe_b`), hinge, board (`board`), the chalk lines on the
  board (`lines`), outline (`outline`). Defaults: classic black/white with
  a dark-grey outline. The **icon** (custom image, optional) is drawn
  centred in the board area below the lines, fit inside ~45% of the board
  height, keeping aspect; missing/unreadable file = no icon.
- **Clap**: the top stick rotates open ~28 deg about the hinge (ease-out,
  ~120 ms), snaps shut (~60 ms) with a 2-3 px squash on the board and a few
  short impact lines at the tip, then settles.
- **Hands mode**: two white Mickey-style gloves (line art: black outline,
  three stitch lines on the back, puffy cuff), clapping together at the
  clap with the same impact lines. The front glove shows its BACK to the
  viewer; the **custom icon sits on the back of that glove**, centred just
  below the three stitch lines, fit inside ~40% of the glove width, clipped
  to the glove shape. Reuse the glove construction from `nle/comic.py`
  (`_glove` -- the thumbs-up redo) so both read as the same character.
  Per-clip colours map to glove fill / outline / cuff / stitches.
- **Loading circle**: ~28 px ring arc at ~55% opacity, centred where the
  clapper's centre was, fades in over 200 ms after the clapper is fully
  gone, rotates ~1 turn/s. Colour: `circle_color` (default gray `#9a9a9a`)
  while processing the clip, `overlay_circle_color` (default purple
  `#9b5cff`) while the input overlay renders, 300 ms cross-fade between.
- 60 fps QTimer while anything moves; idle when nothing does.

### Animations (enter and exit chosen separately; fail is fixed)
"The edge" below = the anchor's own edge: right for `right`/`top_right`/
`bottom_right`, left for the left-side anchors, top for `top`, bottom for
`bottom`. All are drawing-only inside the fixed surface. Each is a function
of t in [0, 1] plus a duration, in `draw.animate`.

**Enter** (default `slide`):
- `slide` -- slides in from the edge, slight overshoot, settles (300 ms).
- `drop` -- falls from above the screen into place, squash on landing, two
  small bounces (450 ms).
- `pop` -- scales 0 -> 1.15 -> 0.95 -> 1 at its spot (300 ms).
- `swing` -- hangs from a pivot off-screen at the edge and swings in like a
  pendulum, damped, rights itself (500 ms).
- `spin` -- slides in from the edge while spinning one full turn (400 ms).
- `toss` -- thrown in from off-screen along an arc with a tumble, lands
  with a little hop (450 ms).
- `flip` -- flips in like a card turning over (horizontal scale 0 -> 1 with
  a slight skew), at its spot (300 ms).
- `peek` -- creeps half in from the edge, pauses a beat, then hops fully
  into place (550 ms).
- `fade` -- fades in while rising 12 px (250 ms).

**Exit** (default `slide`):
- `slide` -- slides back out through the edge, small wind-up first (300 ms).
- `zip` -- pulls back a little (anticipation), then zips out through the
  edge fast (250 ms).
- `fall` -- the floor gives way: drops straight down off the screen with a
  slight tilt (400 ms).
- `shrink` -- scales down to nothing with a twist and fades (250 ms).
- `spin` -- spins out through the edge (400 ms).
- `toss` -- hops up and is tossed off-screen along an arc away from the
  centre of the screen, tumbling (500 ms).
- `flip` -- flips away like a card (horizontal scale 1 -> 0) (250 ms).
- `fade` -- fades out while sinking 12 px (250 ms).
- `bow` -- dips forward in a little bow, then slides out through the edge
  (550 ms).

**Fail** (not selectable): launched up and away from the edge with a fast
spin, gravity takes over, falls off the bottom of the screen (~900 ms).

### Settings + data
- Global, `AppSettings.clip_indicator` (dataclass, config.toml):
  `enabled` (default True), `style` "clapper"|"hands", `anchor` (default
  "bottom_right"), `padding_x`, `padding_y` (default 32, 32), `size`
  (default 96 px), `enter_animation` (default "slide"), `exit_animation`
  (default "slide"), `processing` "circle"|"stay" (default "circle"),
  `circle_color` (default "#9a9a9a"), `overlay_circle_color` (default
  "#9b5cff"), `circle_opacity` (0.55), `screen` "focused"|"primary"
  (default "focused").
- Per clip type, new `clip_configs` columns (same migration pattern as the
  overlay columns): `indicator_colors` (JSON dict, {} = defaults),
  `indicator_icon_path` ("" = no icon), `indicator_clap_sound` ("" =
  inherit).
- **Sound**: the clap IS the `REPLAY_BUFFER_COMPLETED` moment, which already
  plays `clip_cfg.sound_path -> advanced_sounds[...] -> default_sound_path`
  ("the global clip sound"). So with the indicator on, `clips.py` plays
  `indicator_clap_sound` if set, else that same chain -- once, not twice
  (the indicator does NOT play sound itself; the daemon already does it at
  the right moment). Error Noise / other keyframe sounds are unchanged.
- UI: Settings > Clip Capture gets a "Clip Indicator" group: enable, style,
  position (a 3x3 grid picker with the centre disabled), padding X/Y, size,
  enter animation, exit animation, processing mode, the two circle colours,
  a live preview that loops enter -> clap -> exit -> circle, and a "Test"
  button that sends start -> clap -> processing -> overlay -> overlay_done
  through the real helper on the real screen. Hovering a row in an
  animation dropdown plays it in the preview. The per-clip-type row's "..."
  dialog gets an "Indicator" section: colour swatches (the themed colour
  picker), icon picker (themed file picker, images), clap sound picker,
  preview.

### Build steps
1. `indicator/draw.py` + tests (every style, every enter/exit/fail
   animation at several t for every anchor, icon fit on the board and on
   the glove, colours applied, circle colours) -- offscreen.
2. `indicator/__main__.py` helper: JSON-lines reader on a thread -> Qt
   signals; one surface per (screen, anchor), holding the stack; a state
   machine per capture id (entering -> waiting_clap -> clapping ->
   processing(gray) -> overlay(purple) -> leaving | failing) driven by a
   clock that tests can fake; stack slots + gap closing + "+N" cap.
3. Layer-shell shim (C++, in the flake) + X11 fallback.
4. `indicator_client.py` (spawn/keepalive/env, never raises) + calls in
   `trigger_clip` at the keyframes above and in its `except`, in
   `_OverlayCapture`'s background finish (overlay / overlay_done /
   overlay_fail), and in the daemon's capture timeout (`fail`).
5. Config + DB columns + Settings UI + preview + Test button.
6. Verify on the real KDE Plasma Wayland desktop: above a fullscreen game
   (and no stutter while shown), correct monitor, click-through, no focus
   steal, HiDPI, two monitors with different scales, all 8 anchors with
   padding, every animation, a burst of 3+ hotkeys (stacking), an overlay
   clip (gray -> purple), and a forced failure (launch).

### Small calls made without asking (change freely)
- "Stay" mode + input overlay: the clapper stays until the clip is in the
  library, then exits and the purple circle takes over for the overlay.
- An overlay failure gets the red flash + shake on the circle, not the
  launch (the clip itself is fine).
- Circle colours are global settings, not per clip type.
- The indicator also runs when the afterglow window itself is focused.

## MAJOR EPIC: UI Update + Editor Update (multi-session, in progress)
A huge combined spec arrived for a UI overhaul AND a full
Editor rebuild (tracks, segments, filters, transitions -- effectively
a new NLE). Explicitly told to expect "many sessions with minor
changes." **The UI Update came first and is largely done. The Editor
Update's engine (afterglow/nle/) and its full UI (afterglow/gui/
advanced_editor/, the Editor page) are built and tested; phase 4
(polish) is next -- see its "Phase plan".** Read this whole section before touching
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
once it could actually be seen rendered):
- Applies to: sidebar borders (not yet rounded -- no sidebar widget
  reads `rounded_corners_enabled` yet), Library page tab icons (DONE --
  the two tabs' touching inner corners stay sharp, outer three corners
  each round normally), video borders (the Library card's unedited/
  selected highlight border DONE, the video player/thumbnail's own
  corners DONE, the outer background box and inner info box DONE),
  text boxes if feasible (not yet touched -- QLineEdit/QTextEdit
  elsewhere in the app still have square corners), the video/audio
  segments in the future Editor (skip for now -- segments don't exist
  yet), the corners of the app window itself (explicitly SKIP this
  entirely -- window rounding should just be whatever the KDE/window-
  manager theme already does, not a custom frameless-window
  implementation).
- "Apple style" clarified : just means smooth/eased into the
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
these fixed colors (confirmed: Afterglow Theme on literally
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
  A mention of filters as a possible future use of turquoise too,
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
  behavior.** Two sessions ago, per the clarification at the time, it
  was BOTH a border around the video player AND a background overlay
  on the card's own background box, rendered behind everything. After
  actually seeing it rendered, Requested: that background-overlay
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
  flat fills) -- Confirmed doesn't need a redesign as long as
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
  (confirmed: "similar to Medal, a separate smaller video
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
: stacks MULTIPLICATIVELY with the existing window-size-based
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
fullscreen button bottom-right (icon TBD, to be provided); an Edit
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
info/date lines, tag names -- request: "all on-card text"). DONE, with a
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

**Icons to be provided later** (build with placeholder icons for
now, swap in real assets once dropped): magnifying glass (search),
refresh, a fullscreen-button icon (for the small preview player), a
hamburger-menu icon, a drag-handle icon (for reordering something
draggable -- see Editor Update's track reordering), a lock icon (for
the future Editor's segment-locking).

### Editor Update -- full spec (phases 1-3 DONE; phase 4 polish next)
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
  implementation, feedback expected once it's visible).
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

#### Decisions made when the Editor Update started (answers to clarifying questions)
- **Saving a library clip:** overwrite the clip, keep the original in
  "Edit Backups" (the same `.orig` backup the quick trim uses, so Undo
  Edits works for both kinds of edit).
- **Edits stay editable:** the full timeline is stored per clip and
  reopened with tracks/segments/effects intact. Re-saving re-renders from
  the original footage, so quality never degrades across re-edits.
- **Layout: Filmora-style panels.** Browser (Media / Text / Audio /
  Transitions / Effects) top-left, preview top-center, properties
  top-right, timeline across the bottom with a toolbar (undo/redo,
  split, combine, timecode, zoom).
- **Extras approved:** per-segment speed, transform/crop (drag handles in
  the preview), ripple delete, keyframes.
- **Ripple delete, as specified:** a gap between two segments is marked
  with a very slight line; hovering the middle of the gap opens a small
  space there and shows a trash can; clicking it closes the gap
  (shifts the later segments on that track left). Shift+Delete also
  ripple-deletes selected segments.
- **"Add anything else that fits"** -- added to the plan (veto any):
  detach audio from video, duplicate (Ctrl+D), dragging segment edges to
  trim/reveal, J/K/L shuttle playback, timeline zoom (Ctrl+wheel) + zoom
  to fit, snapping on/off (N), text elements, project autosave +
  unsaved-changes marker, export progress with cancel, frame-by-frame
  stepping (, and .), Delete key.
- **Previewer trimming:** the video previewer got the same trim as the
  basic Editor (done this session -- see "This session").

#### Architecture (see afterglow/nle/)
- `model.py` -- pure-Python timeline: Project > Track > Segment > Part.
  Tracks listed top to bottom; no video/audio track types. A Segment
  holds Parts (normally one; "combine" makes several = the inclusive
  merge, part boundaries = the clickable split markers). Parts carry
  their own gain/visibility so combining never changes the output.
  JSON round-trip with a schema version.
- `ops.py` -- every editing rule from the spec: buffer tracks (top and
  bottom always empty; landing on one creates a new one past it),
  overlap "kick" to the nearest fully free track on the preferred side,
  split (S), combine (C), locked/muted/visible toggles (L/M/V, group
  toggling), copy/paste at playhead, duplicate, detach audio, gap close
  (trash can), ripple delete, edge trims bounded by source length and
  neighbors, fades, speed (limited so it never overlaps the next
  segment), transform, zoom filter, keyframes, snapping (segment edges
  snap to edges + playhead; playhead snaps to edges at 40% strength).
- `history.py` -- snapshot undo/redo; drags are one step via
  begin()/end(); failed edits roll back; saved/dirty tracking.
- `media.py` -- PyAV: probe, frame-accurate decoding (forward decode
  during playback, keyframe seek for jumps), whole-file audio decode
  (mono duplicated at full level -- the default upmix is -3 dB).
- `render.py` -- ONE renderer for preview AND export, so the preview is
  exactly what's saved: compositing (upper tracks on top, letterbox fit,
  crop, x/y/scale/rotation, zoom filter, fades, opacity/scale/x/y/
  rotation/volume keyframes, text elements, still images), audio mixing
  (volume 0-200%, mute, fades, speed, per-part gain), H.264/AAC export to
  a temp file moved into place only when complete; cancellable.
- `store.py` / `save.py` -- per-clip project files in
  `~/.config/afterglow/projects/`. Before the clip is overwritten, any
  part referencing it is re-pointed at a stable source (the `.orig`
  backup, or a `<stem>.srcN` snapshot when the clip had already been
  quick-trimmed). Undo Edits, Clear Edit Backup and a quick trim discard
  the project (it no longer describes the file). Import saves
  `<name>-edited.<ext>` beside the original, which is never modified.
- **New dependencies:** PyAV (`av`) and `numpy` -- added to
  `pyproject.toml` and the flake's `propagatedBuildInputs`
  (`python.pkgs.av`, `python.pkgs.numpy`). Not verified with a real Nix
  build in the sandbox; check `nix build` first.
- Measured: a 1080p60 source previews at 960x540 in ~6 ms/frame (under
  the 16.7 ms real-time budget); full 1080p compositing ~11 ms/frame.

#### Phase plan
1. **Engine (DONE):** model, ops, undo, renderer, export, save/reopen,
   library integration.
2. **Timeline UI (DONE):** see "This session" below.
3. **Additions (DONE):** see "This session" below.
4. **Polish (NEXT, not started):** pitch-preserving speed, streaming
   audio for very long sources, proxy decoding if 4K sources preview too
   slowly, rendering in a worker thread if heavy transitions drop
   preview frames.

#### Open questions -- answered
- "Scroll to adjust the timestep": confirmed -- wheel over the timeline
  steps one frame, Shift+wheel one second, Ctrl+wheel zooms.
- Export settings: confirmed -- the canvas is the first clip's size/fps;
  Save opens a small dialog (resolution: original or smaller presets;
  quality: CRF 18/23/28).
- Speed changes pitch like a tape for now (phase 4 item).
- **Editor entry point (decided):** the Editor page IS the Advanced
  Editor; the old basic editor was removed (quick trim lives in the
  previewer). "Always Open Advanced Editor" is therefore moot and was not
  added. Entry points: Library Edit / double-click, and the previewer's
  bottom-right "Advanced Editor" button.
- **J/K/L shuttle (dropped):** L is Lock in the spec, so shuttle keys
  would collide. Frame stepping is , and . (and Left/Right); Shift steps
  one second.

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
color is safe on the real machine, specifically for anything
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

### This session (newest -- no native/KDE dialogs or widgets left)
Asked: "the delete prompt on videos is still in the old kde style --
generally just go through and try to find ANY vanilla kde things and
replace them". Then the clapper spec (section above the MAJOR EPIC).
- **Delete prompt** (video card / multi-select): `ask_confirm(..., "Delete",
  danger=True)` -- red Delete button, Cancel focused so a stray Enter
  doesn't delete.
- **`gui/themed_dialogs.py`** (new): `ThemedDialog` base (frameless,
  rounded, library background, accent outline, drag anywhere via
  `startSystemMove`, works on Wayland) and drop-in replacements with the
  same return shapes as the Qt statics:
  - `ask_text` / `get_text` (QInputDialog.getText);
  - `get_color` (QColorDialog.getColor; invalid QColor on cancel): SV
    square, hue strip, optional alpha strip (`alpha=True`, #rrggbbaa),
    old/new preview, hex field (3/6/8 digits), 14 swatches + "Recent"
    (CONFIG_DIR/recent_colors.json);
  - `get_open_file_name(s)` / `get_save_file_name` / `get_existing_directory`
    (QFileDialog): places (Home, Desktop..., Clips, Computer), back/up,
    editable path bar, folders-first list with drawn glyphs (folder/video/
    audio/image/text) + size/date, filter dropdown, Show hidden (Ctrl+H),
    multi-select (quoted names), save adds the filter's extension and asks
    before replacing, folder mode lists folders only, remembers the last
    folder for the session. A bare suggested name ("afterglow-settings.toml")
    starts in the last/home folder.
- `CustomMessageDialog` now IS a ThemedDialog (accent outline + drag);
  `ask_confirm(danger=)`. advanced_editor `_Dialog` = ThemedDialog;
  `NameDialog`/`ask_name` are aliases of `TextInputDialog`/`ask_text`.
  HotkeyRecordDialog is themed (it had a native title bar).
- **`gui/custom_combo_box.py`** (new): `CustomComboBox(QComboBox)` paints
  itself (`paint_dropdown_field`, shared with Settings > Filters'
  multi-filter picker) and opens its own rounded popup
  (`_ComboPopup` + `ThemedListWidget`): hover/selected highlight, per-row
  fonts (font picker), separators, icons, above/below placement, Escape,
  `activated`/`textActivated` emitted on a pick, editable mode keeps a
  transparent line edit. Every `QComboBox()` in the app is now one.
- **`gui/themed_list.py`** (new): `ThemedListWidget` + delegate + `draw_glyph`.
- **`gui/app_chrome.py`** (new, installed in MainWindow next to wheel_guard):
  app-styled tooltip bubble for every tooltip (widget or list-item
  ToolTipRole); any QMenu with no stylesheet (the Cut/Copy/Paste menu of
  every text field and spin box, stray submenus) gets `_menu_stylesheet`;
  any scroll area still on native bars gets slim `CustomScrollBar`s
  (`extent=` param added). The filter removes itself on aboutToQuit/atexit
  (a Python event filter still installed while QApplication is destroyed
  segfaulted at exit).
- `gui/themed_frame.py` (new): `ThemedFrame` replaces `QFrame.StyledPanel`
  (clip-type rows, Auto Add Filter rows).
- Not replaced, on purpose: the MAIN WINDOW's title bar (KWin's server-side
  decoration). Replacing it means client-side decorations (own title bar,
  `startSystemMove/Resize`, snapping/maximize behaviour) -- a separate
  decision, not done unasked. Tooltip/menus of mpv's own OSD aren't Qt.
Tests: tests/test_themed_dialogs.py (new; also a static scan that fails on
any QMessageBox / QInputDialog / QColorDialog / QFileDialog /
QDialogButtonBox / raw `QComboBox()` / StyledPanel in afterglow/).

### Puppetry prep v6 (latest drop)
The vendored module and its test are unchanged from v5 (still byte-identical).
Docs updated in integrations/puppetry_input_overlay/: the `mouse` piece
includes the layout's movement view(s) again (it was the mouse alone in
v4/v5, so clips captured with keyboard + mouse then show no movement --
re-render them from input: Editor > Properties > Input Overlay > Render
"Mouse + movement", i.e. `aio.rerender(clip, sc, "mouse")`). The piece's
label is now "Mouse + movement".

### This session (newest -- performance: clip capture + Editor saves)
Measured on a 1080p60 x264 source (2 s keyframes) in a 2-core sandbox;
on a real desktop everything scales down further.
**Clip capture (OBS save -> trimmed clip in the library): ~42 s -> ~1 s.**
- The trim was a full re-encode of the whole clip (frame-exact). It is now a
  SMART CUT (`afterglow/smartcut.py`): only the frames before the first
  keyframe inside the clip (and, for a mid-file trim, after the last one)
  are re-encoded with x264; every whole GOP in between is copied bit for bit.
  The two encoders' streams coexist via distinct SPS/PPS ids (x264
  `sps-id`, picked not to clash with the source's): the avcC holds both
  sets and each piece also carries its sets in-band on its first frame.
  DTS are restamped (decode index - max reorder delay). Requirements:
  H.264 8-bit 4:2:0 with avcC, CFR, IDR keyframes without leading pictures
  at the cut points (`eligible`, `cut_points`); anything else (HEVC/AV1,
  10-bit, open GOP, VFR...) falls back to the old full re-encode.
  Keyframes are found from the container index around the two ends only
  (`scan_gops` / `cut_points`), never by scanning the whole 20-minute buffer.
- Audio is COPIED (`AudioCut`), not re-encoded: two warm-up packets before
  the cut are kept with negative timestamps (MP4 edit list / MKV
  timestamps), and the audio is aligned to the first VIDEO frame of the
  cut, so A/V sync is sample-exact. Falls back to an AAC re-encode if the
  copy fails. All audio tracks are kept (the old trim kept only one).
- `editor.commit_trim(..., output_path=)`: the capture trims straight into
  the clips folder, then deletes the raw file (no move/copy between disks).
- The flat 2 s "post-save settle" sleep is now a size/mtime stability check
  (0.15 s of no change, max 2 s; OBS's event already means "closed").
- `_find_keyframe_at_or_before` uses the index (seek + 1 packet) instead of
  ffprobe decoding every keyframe of the file. add_video takes the known
  duration (one ffprobe fewer).
- The Library's Save Trim (frame-perfect) gets the same smart cut.
**Editor saves (`render.export` -> `nle/smart_export.py`):**
- COPY: runs of the timeline that are one untouched source clip (canvas
  size, normal speed, same fps/colour; `Renderer.passthrough_part`) are
  copied from the source GOP by GOP. Saving a plain trim of a 30 s clip:
  ~38 s -> ~1 s.
- REUSE ("reuse unchanged pieces of the previous video"): rendered parts
  are encoded as independent ~2 s chunks on a fixed grid; each chunk gets a
  fingerprint (every element overlapping it, relative to the chunk start,
  incl. transitions' outgoing clip, source files' size+mtime, project
  settings, export settings, and a hash of the drawing code). The
  fingerprints of the last export of a path are kept in
  CONFIG_DIR/render_cache/<sha1(path)>.json together with the output's
  size+mtime; on the next save any chunk with the same fingerprint is
  copied out of the previous output file instead of rendered. Editing one
  caption re-renders ~2 s; saving again unchanged ~0.5 s. The audio has its
  own fingerprint (volume/mute changes re-render only the audio) and is
  copied from the source when the sound is one untouched clip.
- Rendered chunks run in parallel (one Renderer per worker).
- Colour fix (was a real bug): rendered frames were converted RGB->YUV with
  swscale's BT.601 default while HD sources are BT.709, so every frame with
  text/effects shifted colour vs the plain frames around it. Rendering now
  uses the source's own matrix/range (`smartcut.to_output_yuv`) and the
  stream is tagged like the source -- in the smart path AND the full one.
- `export(..., smart=False)` forces the old full render; any failure in
  the smart path (logged "smart export not used ...") falls back to it.
  `render.LAST_EXPORT_STATS` reports copied / reused / rendered frames.
- Edit backups / stable source snapshots are reflinks on copy-on-write
  filesystems (`fileutil.clone_or_copy`), plain copies elsewhere.
Tests: tests/test_smartcut.py (bit-exactness of copied frames, PSNR of
re-encoded ones, sample-exact audio sync, MKV/full range, open-GOP and VP9
fallbacks, capture end to end, export copy/reuse/audio/colour/cache
invalidation, speed vs full render).

### Previous session (feedback round 16: comic polish)
- Scribblenado grows from its BOTTOM tip (path revealed bottom-up, scaled
  about the bottom) and unwinds back into it.
- Anger vein: pops + pulses only (no rotation).
- "!" and "!?": just pop into place (no twist, no side lines); "!" keeps its
  quiver, "!?" wobbles fast (9 deg at 17 rad/s). "?" unchanged.
- Paired effects (fuming steam, blush): new TextStyle `effect_side` (""/
  "left"/"right") draws one side (comic.PAIRED: single-side aspect + offset);
  Properties > Comic Effect "Side" + "Split into left + right"
  (`ops.split_pair` / `EditorController.split_pair`): the pair becomes two
  elements on separate tracks, each where its half was (transform + x/y
  keyframes shifted, rotation/scale respected) -- independently keyframed.
- Beating heart: cartoon heart beating out of a chest. `effect_variant`
  "separate" (heart in its color bursting out on a pinched shirt membrane
  joined by two lines, hole in the chest), "covered" (the shirt bulges out
  heart-shaped, stretch folds), "plain"; `effect_color2` = shirt color
  (Properties "Style" + "Shirt color"; comic.EFFECT_VARIANTS /
  EFFECT_COLOR2).
- Thumbs up/down: classic white cartoon glove (Mickey-style) -- one soft
  fist+thumb shape, curled finger rolls, 3 stitch lines, rolled cuff, black
  ink; thumbs down = thumbs up MIRRORED top-to-bottom.
- Removed: speed lines, gloom lines, impact burst (effects; old projects
  just don't draw them), OOF and BRUH (onomatopoeia presets).
- Comic-style redraws: `comic.comic_cloud_path` / `draw_comic_cloud`
  (scalloped bumps, inked outline, shaded underside, inner curls) used by
  the rain cloud (+ slanted rain streaks), poof (big cloud + flung puffs that
  break up on exit), the steam puffs and the BOOM backdrop (irregular
  explosion cloud with a hot lighter core); SPLAT backdrop = lumpy body with
  club-tipped arms and flung droplets.
- Puppetry prep v5: module unchanged (still byte-identical); docs/sample/
  test in integrations/ and tests/test_puppetry_integration.py updated (the
  default layout's movement view is now a Mousepad with id "movement").
Tests: tests/test_comic_text.py (round 16 section).

### Previous session (feedback round 15: expressions library, onomatopoeia, transitions, global overlay toggle)
1. **Previewer input-overlay toggle is ONE global, saved setting**
   (`AppSettings.preview_input_overlay`, default on): the previewer's
   button flips and saves it; every clip opens in that state (the clip's
   own `visible_by_default` now only matters in the Editor). Settings' Save
   re-reads it from disk first so it never clobbers a previewer change.
2. **Anger vein redrawn to match the comic mark** the user sent: four
   thick, flat-capped bent strokes (bend toward the centre, the left arm
   steeper -> pinwheel), `comic._vein_piece`.
3. **Expressions library** (`nle/comic.py` EFFECTS registry: key -> label,
   group, draw fn, aspect, default color, default size). 30 effects in 10
   groups: Anger (scribblenado, vein, fuming steam, flames), Unease
   (unease lines, sweat drop, nervous sweat), Sad (gloom lines, rain cloud),
   Bored (dots, Zzz), Surprise (!, ?, !?, shock lines, dizzy spiral, dizzy
   stars), Love & joy (floating hearts, beating heart, music notes,
   sparkles), Idea (bulb), Embarrassed (blush), Reactions (thumbs up/down,
   skull), Action (focus lines, speed lines, impact burst, poof). Each draw
   fn takes (t = idle time, g = in/out progress, out) -- EVERY effect has
   its own entrance/exit (scribble winds up/unwinds, unease lines grow
   out/pull in, vein pops, gloom lines drop down, thumbs spring up with a
   twist...) and an idle loop (vein throbs, rain falls, hearts float...).
   New TextStyle fields `effect_in` / `effect_out` (s); `effect_speed` is
   now "Idle speed". All deterministic in t (12 fps "boil" re-seeding).
4. **Onomatopoeia / comic lettering** (TextStyle `fill2` gradient,
   `extrude` 3D block shadow + `extrude_color`, `skew`, `jumble`,
   `backdrop` = burst / jagged / boom / cloud / splat / flash +
   `backdrop_fill` / `backdrop_outline`; Properties > "Lettering"). 18
   presets in the bundled Bangers font (POW!, BAM!, BOOM!, KAPOW!, WHAM!,
   CRASH!, ZAP!, BZZT, BONK!, SPLAT!, THUD, WHOOSH, SNAP!, DING!, GULP,
   OOF, BRUH, YEET!), each with its own entrance/exit/idle. A backdrop
   boils while the idle is Shake/Jitter and pops in/out with per-letter
   animations (`comic.backdrop_scale`).
5. **More text transitions** (any text): "Animate in" (pop, slam, spin,
   stretch, drop, zoom, flip, letters pop in, letters rain in), "Animate
   out" (pop, shrink, spin, fly up, fall, flip, letters pop out, explode)
   with lengths, and "Idle" (shake, pulse, wobble, float, swing, wave,
   jitter) with strength + speed. `comic.text_motion` now returns
   (dx, dy, rot, sx, sy, alpha); per-letter styles go through
   `comic.letter_motion` in the rewritten `render._draw_text_body` (which
   also does gradient/extrude/skew, extrusions drawn before all faces).
   Bounce + Animate-in run together, Jiggle in after them; Animate out and
   Jiggle out end together; all scaled to fit short elements.
6. **Comic tab** in the Editor's browser (Media, Text, **Comic**, Audio,
   Transitions, Effects): every expression grouped by feeling + the
   onomatopoeia; they're TEXT_PRESETS entries (`COMIC_PRESET_GROUPS`) but
   hidden from the Text tab.
7. **Puppetry input visualizer v4** (prep package `input_visualizer_prep_v4`,
   vendored byte-identical into `afterglow/input_overlay.py`;
   `integrations/puppetry_input_overlay/` updated, incl. `sample/`):
   Puppetry's visualizer is now a user-arranged LAYOUT. New pieces
   `comet` / `mousepad` / `joystick` (movement views) and single layout
   elements `el:<id>` (file `el-<id>.mov`, `aio.piece_file`). afterglow:
   `overlay_support.layout_elements()` (cached `aio.elements()`),
   `all_pieces()`, `piece_label()`, `element_types()`; the clip type
   "..." dialog and Settings > Input Overlay list every layout element (an
   element stored on a clip type that left the layout is still shown); the
   capture, previewer graph, Editor (attach / Properties / Render from
   input) and the exported sidecar all handle `el:` pieces; an element no
   longer in the layout fails alone (logged), never costing the clip.
   tests/test_overlay_layout_v4.py (fake Puppetry with a layout);
   tests/test_puppetry_integration.py is now the v4 prep test -- it needs
   the UPDATED Puppetry (the copy tested here was pre-v4, so its el:comet
   checks fail against it; skips without Puppetry).

Tests: tests/test_comic_text.py (all 30 effects draw / have in+out /
idle / determinism; every Animate in/out style and Idle; lettering;
backdrop pop; presets; Properties), test_overlay_previewer.py (global
toggle). Demo renders were made with the real Renderer (not in the repo).
Open: no "Bounce out" yet (only asked for in); the Editor preview's
handles show the rest position during motion.

### Previous session (feedback round 14: comic effects, bounce/jiggle, overlay color)
1. **Comic effects** (`nle/comic.py`, drawn by `render._draw_content`): procedural,
   animated, deterministic drawings in a text element's PICTURE SLOT
   (`TextStyle.comic_effect` = "scribblenado" | "wiggle_lines" | "vein"; it
   replaces the picture while set; `image_place` is shared). Fields:
   `effect_color` ("" = the effect's own: near-black / slate blue / red),
   `effect_size` (fraction of canvas height), `effect_speed` (0 = still).
   Scribblenado = one continuous looping stroke narrowing into a funnel,
   spinning, re-jittered 12x/s ("boil"); wiggle lines = 11 wavy lines
   radiating over the top 3/4 of a circle, waves crawling outward; vein =
   four tapered curved brackets bulging out around a "+" gap, throbbing.
   An element can be effect-only (empty text). Text presets "Anger
   scribble" / "Unease lines" / "Anger vein" under a "Comic effects" header
   in the Text tab (named after the preset). Properties > "Comic Effect"
   group (effect, color + Default, size, animation speed, place).
   `effect_color` is part of Copy/Paste Colors.
2. **Bounce in / Jiggle in-out** (Properties > Text Transitions;
   `comic.text_motion`, applied in `render._draw_segment` as a screen-space
   offset + extra rotation on text parts, on top of the transform/keyframes):
   `bounce_in` (s) + `bounce_from` ("down"/"up"/"left"/"right" = comes from
   below/above/left/right) starts 1.25 canvases away (off-screen), eases in
   with an ease-out-back overshoot (~10%) and lands exactly in place.
   `jiggle_in` (s) wobbles (+-11 deg, small shifts, decaying) while settling
   -- it starts when the bounce lands, if there is one; `jiggle_out` (s)
   builds up a wobble before the end. All three are scaled down together to
   fit a too-short element. Preview handles show the rest position.
3. **Previewer overlay toggle changed the video's brightness/saturation.**
   Measured in real mpv: the graph's `overlay=...:format=auto` turned the
   whole video into ARGB (swscale's own matrix) -- mean |diff| ~3.6/765 vs
   no graph. Now the blend happens in the clip's own pixel format
   (`overlay_support.video_color()` -> overlay `format=yuv420|yuv420p10|
   yuv422|...|gbrp`), and the RGB pieces are converted with the clip's own
   matrix/range first (`scale=out_color_matrix=..:out_range=..`): on, off and
   no graph are now pixel-identical (8-bit BT.709, 10-bit and full range
   checked in tests/test_overlay_mpv.py).
Tests: tests/test_comic_text.py (new), tests/test_overlay_mpv.py (color checks).

### Previous session (feedback round 12)
1. **Animated GIFs in text / bubbles.** `render._load_picture` reads every
   frame with QImageReader (its own per-frame delay; <20 ms counts as
   100 ms like browsers; frames downscaled to GIF_MAX_SIDE=640; max 1000
   frames), cached per (path, mtime) in `_picture_frames` behind a lock.
   `bubble_image_at(path, t)` picks the frame for t (looping forever);
   `_draw_content` uses t = element-local time * `TextStyle.image_speed`
   (new, default 1.0). Layout still sizes from the first frame
   (`bubble_image`). `image_is_animated(path)`. Properties > Text: "GIF
   speed" (0.1-5x, enabled only for an animated picture).
Tests: tests/test_round13.py (new).

### Previous session (feedback round 11)
1. **Pictures in text / bubbles (any kind).** TextStyle.image_path ("" =
   none), image_place ("above" | "below" | "left" | "right" of the words),
   image_size (height as a share of the canvas height, default 0.18).
   `render.text_layout` lays the picture and the words out as one centered
   block: `rect` is the whole block (so `bubble_body`, every bubble shape
   and the preview's selection box grow to hold it), `text_offset` is where
   the words' center moved to, `image_rect`/`image` the picture.
   `Renderer._draw_content` draws the picture then the words; with a
   type/delay text effect the picture fades in (0.25 s) as the words start,
   otherwise it follows the bubble's text fade. Picture-only elements (no
   words) work. `bubble_image()` caches loaded images by (path, mtime)
   behind a lock (export threads); GIFs animate (round 12); a missing
   file is ignored. Properties > Text: Picture (Add Picture… / Change… /
   Remove), Place, Picture size. The picture is part of Copy Properties and
   of global presets.
Tests: tests/test_round12.py (new).

### Previous session (feedback round 10)
1. **Global text presets** (`nle/globals.py`,
   CONFIG_DIR/editor_globals/text_presets.json): {name, text, props
   (ops.copy_properties payload, JSON-safe), position}. Save from one
   selected text element (Text tab "Save as Global Preset…", or timeline
   right-click; `ctl.save_global_preset(name)`); an existing name updates
   the preset AND restyles every element in the open project linked to it
   (`TextStyle.global_preset` = preset name, set when made from / given a
   preset; excluded from Copy Properties). Text tab lists them under
   "Global presets" (★): double-click/drag adds one (its saved text as the
   starting words, its position as the start), right-click = Add at
   Playhead / Apply to Selected / Update from Selected Text / Rename /
   Delete. Timeline right-click on text: "Save as Global Preset…" and an
   "Apply Global Preset" submenu. Applying keeps each element's words.
2. **Global audio** (CONFIG_DIR/editor_globals/audio/ + audio.json):
   `gl.add_audio` stores a COPY (dedup by content) so it survives the
   original moving; Audio tab: "Add Global Audio…", sections "In this
   project" / "Global audio" (★), right-click = Add at Playhead, Make
   Global (project audio), Rename / Remove (global; the stored copy is
   kept so projects using it still work). Timeline right-click on an
   audio-only segment: "Make Audio Global…". `ctl.globals_changed`
   refreshes both lists.
3. `page.NameDialog` / `page.ask_name()`: themed one-line name prompt.
4. `ops.paste_style` accepts a transition stored as a dict (from JSON).
Tests: tests/test_round11.py (new).

### Previous session (feedback round 9)
1. **Canvas drags don't reload the panels.** Dragging an element in the
   preview (move/scale/rotate/crop/tail -- including one that creates a
   keyframe) now goes through `ctl.live_preview()`, which emits the new
   `previewed` signal instead of `changed`: only the preview re-renders
   and Properties updates its X/Y/scale/rotation boxes
   (`_refresh_keyframe_values`); the keyframe list, timeline and undo
   history update once, on release (`ctl.end()`). Each drag step restores
   only the dragged element's keyframes/transform/tail (was: the whole
   project from a dict every mouse move).
2. **Save Edits vs Export.** Header: Discard Changes | Save Edits | Export |
   Filters. Save Edits (Ctrl+S, `ctl.save_edits()`) writes the project and
   its `.saved.json` baseline, clears "unsaved", renders nothing. Export
   (Ctrl+E, the old Save: `page.save()`, dialog titled "Export") renders
   into the clip / new clip / import output, then `mark_rendered()`.
   `Project.export_pending` + `ctl.export_pending` track edits not yet
   exported (set on any history change; older projects: pending if they
   had unsaved changes); the header shows "● Unsaved changes" (orange) or
   "● Saved, not exported" (blue). Autosave still runs (crash safety) but
   leaves the project marked unsaved; Discard goes back to the last Save
   Edits / Export.
3. **Clean Up** (timeline toolbar, `ops.clean_up`, undoable): pictures
   settle down toward the middle, audio-only up toward it, greedy "gravity"
   that never changes times or layering (a unit lands one level past the
   highest thing it overlaps); transition-joined back-to-back chains move
   as one; hidden tracks are kept as-is above the pictures; empty tracks go
   (normalize_tracks keeps the top/bottom drop tracks and MIN_TRACKS).
4. **Copy / Paste Colors and Properties.** `ops.copy_colors` (text, outline,
   bubble fill/outline + their transparencies, shadow color),
   `ops.copy_properties` (all TextStyle fields except text/word keys;
   fades, volume, zoom, shadow, scale/rotation/crop, transition -- not
   position or timing), `ops.paste_style` applies only what fits (no crop
   or volume onto text, style kept valid for the bubble kind). UI: a
   Copy/Paste Colors + Copy/Paste Properties grid at the top of Properties,
   and in the timeline and preview right-click menus
   (`timeline.add_style_actions`). Clipboard: `ctl.style_clipboard`.
5. **Properties panel no longer wider than its column** (it was clipping
   ~80 px on the right): combo boxes size to 8 characters
   (AdjustToMinimumContentsLengthWithIcon), spin boxes min 74 px, shorter
   labels ("Fill transparency", "Typing cursor ( | )", "Per-word timing").
Tests: tests/test_round10.py (new); test_round9 gained the drag check.

### Previous session (feedback round 8)
1. **Smoother harsh motion.** Spiky speech, Angry thought and the Intercom
   tail used to step (a new random shape 14-16x/s, held in between). Now
   `_jitter(t, rate, *key)` picks new random targets (FLICKER_HZ=20 for
   spikes, 16 for the tail) and snaps to each with a smoothstep, so the
   shape changes on every rendered frame while staying jittery. The tail's
   "reversed kink" is now a jittered amplitude that can dip below zero.
2. **Uncertain wiggle ~70% speed:** phase 7.7*t (was 11) for body and tail.
3. **Squarer boxes:** Intercom corner radius 0.04*min(w,h) (was 0.12),
   Electronic 0.027 (was 0.08).
4. **Uncertain tail tip holds its shape:** the tail's edge wobble fades to
   nothing over the half nearest the tip (`min(1, to_tip/0.5)**1.5`).
Tests: tests/test_round9.py updated (every-frame motion, square corners,
steady tail tip).

### Previous session (feedback round 7)
1. **Harsh spike flicker.** Spiky speech and Angry (jagged) thought: each
   spike jumps to new lengths in/out from the center (superseded in round 8:
   smoothed per-frame via `_jitter`).
2. **Uncertain (wiggly):** faster (phase 11*t), uneasier outline
   (`_wiggle`: three clashing waves, 72-point polygon), and its own wobbly
   tail (both tail edges wave, pinned at base and tip).
3. **Animation speed:** `TextStyle.bubble_anim_speed` (default 1.0);
   render uses `anim_t = local * speed` for shapes and dash offsets.
   Properties > Bubble > "Animation speed" (0.1-5x; greyed when there's
   nothing to animate or Animated is off).
4. **Wheel never changes a number box, app-wide:** wheel_guard now covers
   QAbstractSpinBox (and the line edit inside spin/combo boxes), forwarding
   the wheel to the enclosing scroll area.
5. **Intercom tail:** more, sharper zig-zags (random amplitude, jittered
   along the line, the odd reversed kink), re-rolled 16x/s when animated.
6. **Neutral thought** gets a gentle slow wiggle when Animated (the
   Animated box is enabled for neutral thought, not neutral speech).
7. **Electronic** box pulses when the pulse running up its square trail
   arrives (same phase sequence, next "order" after the last square).
8. **Discard Changes** (header, left of Save): `ctl.discard_changes()`
   restores `<project>.saved.json` (written by `mark_rendered`, i.e. every
   Save; kept in sync by repoint/discard in store.py), or, if never saved
   from the editor, a fresh project from the clip/import as it is. Asks to
   confirm (`custom_message_dialog.ask_confirm`); enabled only when unsaved.
Tests: tests/test_round9.py (new).

### Previous session (feedback round 6)
1. **Thought cloud waits for its trail, then grows outward.** Trail circles
   sprout tip-first (circle k starts at TRAIL_STEP=0.25*k/n of the grow,
   lasts TRAIL_LEN=0.14); the cloud starts at `thought_cloud_start(n)` (the
   circle next to the cloud ~70% grown) and ends at THOUGHT_CLOUD_END=0.9.
   Bumps travel out from the center as before, the trail side leading only
   slightly (`u = _window(cg, 0.3*d, 0.7)`), so every side is growing by a
   third of the cloud's time. THOUGHT_TEXT_IN_AT = 0.55.
2. **Bubble styles + Animated.** `TextStyle.bubble_variant` ("" = neutral)
   and `bubble_animated` (default True). `render.BUBBLE_VARIANTS`:
   speech -- spiky ("Surprise / anger": star burst, spike tips flicker),
   whisper (dashed outline, dashes march), wiggly ("Uncertain": wavy
   outline, wave travels), intercom (rounded box + zig-zag connection line
   to the tip, re-jittered 12x/s); thought -- wobbly ("Worried": bumps and
   trail circles tremble), dreamy (dotted outline, dots drift), jagged
   ("Angry": spikes between the bumps, flicker), electronic (rounded box +
   trail of squares that pulse in sequence). Shapes live in `bubble_shape(...,
   variant, t, animated)` (t = element-local seconds, so preview and export
   agree; `_hash01` gives deterministic jitter); dashes come from
   `bubble_dash`. `valid_variant` ignores a style that isn't the kind's.
   Properties > Bubble: "Style" dropdown (per kind) + "Animated" (greyed for
   Neutral); switching Speech/Thought resets the style.
3. The round-5 bubble reset turned out to be an unsaved session (no bug).
Tests: tests/test_round8.py (new).

### Previous session (feedback round 5)
1. **Speech text starts early in the grow.** `render.TEXT_IN_AT = 0.2` (was
   0.55): the body only scales, so type/delay/fade start 20% into grow-in;
   the text's own alpha ramps over g in [0.2, 0.5].
2. **Thought clouds grow uniformly out of their middle.** The bump-by-bump
   sweep from the trail side is gone: every bump moves out from the center
   and swells at the same pace over g in [THOUGHT_CLOUD_START=0.15,
   THOUGHT_CLOUD_END=0.8]; the trail still sprouts tip-first. Thought text
   starts at THOUGHT_TEXT_IN_AT=0.4 (while the cloud is still growing).
3. **Save progress bar never blank.** Cause: a QProgressBar starts at value
   -1 (draws no text), and export() encoded all the audio before the first
   frame. Now ProgressDialog starts at 0 with "Preparing…" until the first
   fraction arrives, and export() always runs the video piece(s) AND the
   audio on their own threads from the start (audio -> `<out>.audio.m4a`),
   the calling thread only reports progress (audio weighted 8%), then
   `_join_parts` remuxes. `_join_parts` now merges packets by time
   (heapq.merge) instead of writing all video then all audio.
4. **Bubble settings reset (investigating).** Project load/migration keeps
   colors/fonts (verified). Found and fixed one loss path: an open grouped
   edit (Properties spin-box burst) made `autosave_now` skip, so quitting or
   leaving the page right after such an edit dropped it. Page shutdown/hide
   now call `ctl.flush_edits()` first. Also note: a previewer quick trim or
   Undo Edits discards the clip's editor project (by design, see
   nle/store.py), which resets everything made in the editor.
5. **Filters in the editor header (top right).** `page.filters_btn` opens
   the previewer's PreviewFiltersPanel for the open library clip (disabled
   for imported files); closes on hide / clip change.
6. **No side padding around the video.** Previewer: `VideoPreviewContent`
   probes the clip's display aspect (`_video_aspect_of`), pins the bordered
   frame to the video's shape (`_VideoFrame.set_fit`, centered by stretches
   in the row), and `VideoPreviewOverlay._layout_content` sizes the box
   around the video: CONTENT_WIDTH wide (min CONTENT_MIN_WIDTH=760) and as
   tall as the video needs up to 97% of the window (CONTENT_HEIGHT is no
   longer a cap). Fullscreen un-pins the frame. Editor: PreviewCanvas draws
   only the video frame (no dark letterbox fill).
7. **Timeline wheel:** plain wheel scrolls the tracks (vertical bar); Alt =
   scroll along time; Shift = frame step; Ctrl+Shift = 1 s; Ctrl = zoom.
Tests: tests/test_round7.py (new); round-4 thought checks updated;
test_previewer_trim's fast-trim length now accounts for the keyframe the
cut snaps to.

### Previous session (feedback round 4)
1. **Bubble tail follows the tracked object through grow in/out.** The tail
   tip used to be an absolute canvas position, so with Position keyframes
   the grow-out collapsed into a fixed spot while the object kept moving.
   `TextStyle.tail_x/tail_y` (and their "tail_x"/"tail_y" keyframes) are
   now an OFFSET from the bubble's animated position, in canvas fractions.
   `SCHEMA_VERSION = 2`; `model._migrate_tail_to_offset` converts older
   projects on load (subtracts the static or keyframed bubble position).
   render.py adds the offset after evaluating x/y at the clamped local
   time; preview.py `tail_point` and tail dragging use the same rule.
   Defaults: presets (-0.16, 0.25), Properties > Bubble switch (-0.08, 0.2).
2. **Thought cloud finishes before its text appears.** The cloud grows over
   g in [THOUGHT_CLOUD_START=0.3, THOUGHT_CLOUD_END=THOUGHT_TEXT_IN_AT=0.75];
   the far (top right) bumps start earlier (`u = _window(cg, 0.45*d, 0.55)`),
   and thought text starts at 0.75 of the grow (speech stays at 0.55). The
   shape at g=0.75 equals the finished shape (test_round4 checks this).
3. **Speech/thought bubble default font = Permanent Marker**
   (`controller.comic_font()`), for presets and the Bubble switch.

### Previous session (feedback round 3)
1. **"The app closes and reopens the first time the previewer opens" --
   fixed.** The previewer's player is a QOpenGLWidget; the first GL widget
   to join a window makes Qt destroy and re-create the window's native
   surface (reproduced under X11 and Wayland: Hide + two surface
   re-creations + a new window id). MainWindow now holds a 0x0
   QOpenGLWidget from construction, so the window is GL-capable before it
   is first shown; opening a preview no longer touches the window.
2. **Keys follow the previewer after fullscreen.** While the preview is
   open it installs an application event filter that routes every key
   (and swallows shortcut overrides) to it, except when a text/spin field
   inside it is being typed in or a dialog is active. Focus is also put
   back on the overlay after entering/leaving fullscreen.
3. **Previewer arrows:** Left/Right seek -/+5 s, Up/Down volume +/-5 %.
4. **Fonts:** 21 varied OFL/Apache fonts bundled in
   `gui/resources/fonts/` (README + licenses there; package-data in
   pyproject), registered at startup (`gui/fonts.py`). The font picker is a
   plain (non-editable) dropdown of just those, grouped by style, each
   entry drawn in its own typeface: Comic Neue, Bangers, Luckiest Guy,
   Patrick Hand, Permanent Marker, Tinos (Times New Roman metrics),
   Playfair Display, Cinzel, Roboto, Montserrat, Oswald, Anton, Bebas
   Neue, Courier Prime, Special Elite, Pacifico, Caveat, Lobster, Press
   Start 2P, Creepster, Orbitron. Back Issues can't be bundled (Blambot
   license); when installed it's listed first and is the bubble default,
   otherwise bubbles default to Comic Neue (the previous fallback landed
   on Noto Sans). Title/subtitle/caption presets use Montserrat/Roboto.
5. **Effects fitted to the element** (`render.text_timing`): grow, type
   and delay in/out are scaled down together when they don't fit the
   element, and time is clamped to it. The shrink now has its own easing
   (the grow-in's overshoot easing run backwards stayed near full size
   until the last frames, then popped); it collapses smoothly into the tip
   by the last frame. Interpretation note: the request mentioned the grow
   "going past the end of the segment" -- this is the fix for the
   pop-at-the-end behavior; revisit if something else was meant.
6. **Text effects overlap the grow:** type/delay start at 55 % of a
   bubble's grow-in and finish by 40 % into its grow-out
   (`TEXT_IN_AT`/`TEXT_OUT_AT`); when a text effect covers a phase, the
   plain text fade that goes with the grow is off.
7. **Per-word Delay keys:** `TextStyle.delay_keyed` + `delay_word_times`
   (seconds from the element start per word, None = auto).
   `render.word_start_times()`: keyed words start at their key, unkeyed
   ones spread evenly between the nearest keyed neighbours, or the text's
   start / end. Properties > Text Transitions > "Per-word timing
   (keyframes)" shows each word as a button (flow layout): click = key it
   at the playhead, right-click = clear; "Clear Word Keys". Keyed times
   show as small markers on the timeline segment.
8. **Thought cloud:** no center oval any more. Each bump grows together
   with a wedge from the cloud's center out to it, so the middle fills in
   as the bumps arrive (the wedges together are the middle); bumps nearest
   the trail first.

Tests: `tests/test_round4.py` (43 checks); all earlier suites pass.

### Previous session (Advanced Editor feedback round 2)
1. **Fonts.** Speech/thought bubbles default to Back Issues (Blambot's comic
   lettering font) when it's installed -- matched loosely ("Back Issues BB",
   "BackIssues BB"...), else another comic font, else a plain sans
   (`controller.comic_font()`). The font is NOT bundled (license); it has
   to be installed on the system. Properties > Text > Font is now a
   searchable dropdown of every installed font (type to filter), comic
   fonts listed first.
2. **Hide layers:** `Track.hidden` -- a hidden layer draws nothing and
   plays no sound (renderer + audio + export fast path skip it). H toggles
   the selected layers (click a track header to select; Ctrl adds), or the
   layers holding the selected segments. Eye icon in each track header.
   Hidden lanes are dimmed and hatched.
3. **Timeline wheel (superseded in round 5: plain = tracks, Alt = time):** plain wheel scrolls along the timeline; Shift+wheel
   steps one frame; Ctrl+Shift+wheel one second; Ctrl+wheel zooms;
   Alt+wheel or over the headers scrolls tracks. Uses whichever wheel axis
   moved (Qt/desktops swap axes under Alt/Shift).
4. **Middle-drag pans** the timeline (time and tracks).
5. **Grow in/out for bubbles** (`TextStyle.grow_in`/`grow_out`, seconds;
   presets: speech 0.35/0.3, thought 0.6/0.45; turning a bubble on in
   Properties applies them too). Speech: the body flies out of the tail
   tip to its place while scaling up (slight overshoot), the tail
   stretching between them. Thought: trail circles sprout from the tip
   outward, then the cloud forms -- core first, then the bumps pop in
   starting on the trail's side. Shrinking out is the reverse.
   `render.bubble_shape(kind, body, tip, g)` does both the static and the
   animated shape (g = grow progress).
6. **Delay text transition** (`delay_in`/`delay_out`): words fade in one
   after another, letters left to right within each word; out fades in
   reading order. For bubbles it starts after the grow-in (and ends before
   the grow-out). Bubble presets default to a 0.6 s Delay in. Properties >
   "Text Transitions" (with Type in/out).
7. **Thought trail density:** the number of circles follows the distance
   to the tip (constant spacing relative to circle size), circles shrink
   toward the tip, sized from the bubble (`render._thought_trail`).
8. **Preview click-select:** a single click selects the TOPMOST element
   under the cursor. Before, a selected full-frame video underneath
   claimed the click as a move, so text on top could only be reached by
   double-clicking.
9. **Bubble transparency:** Background transparency and Outline
   transparency (0-100 %) for bubbles.
10. **Drop shadow** on every visual segment (`Segment.shadow*`: color,
    opacity, distance, angle, softness; off by default; Properties > Drop
    Shadow). Rendered by drawing the segment to a layer and compositing a
    tinted, softened, offset copy under it (also inside transitions).
11. **Gap line** at half the previous contrast.
12. **Faster Save.** Profiled a 10 s 1080p60 export (2-core sandbox):
    14.6 s. Most time was the RGB round trip (decode -> RGB -> paint ->
    RGB -> YUV). Now: (a) frames that are one untouched full-frame clip
    go from the decoder straight to the encoder
    (`Renderer.passthrough_frame`) -- plain cuts/trims; (b) pictures are
    rendered on a worker thread while encoding runs; (c) on machines with
    6+ cores the timeline is split into 2-6 pieces rendered + encoded in
    parallel and joined without re-encoding (`export(jobs=...)`,
    `_join_parts`). Same edit: 7.0 s. Verified frame-exact (PSNR >= 43.8 dB
    on all 600 frames vs the source with jobs=3; render tests check colors
    and exact frame counts for jobs=1 and 3). Couldn't measure (c) here --
    2 cores; it should help most on many-core CPUs.

Also: HEADER_W widened to 124 px (room for the eye), MainWindow closes the
previewer's player on exit (its mpv thread outlived it in tests).
Tests: `tests/test_advanced_editor_3.py` (35 checks incl. a stand-in
"Back Issues BB" font built from DejaVu with fontTools), render tests for
parallel export; all earlier suites pass.

### Previous session (Advanced Editor feedback round 1)
1. **Dragging a library clip from Media scrolled the list instead of
   dragging.** QListView's built-in drag in icon mode became a rubber-band
   selection with auto-scroll. `_DragList` (browser.py) now starts the drag
   itself (press on an item + 6 px move -> `QDrag` with the item payload);
   built-in drag/auto-scroll are off.
2. **Browser lists use `CustomScrollBar`** (vertical; horizontal off).
3. **Playhead moves on mouse DOWN** when clicking an empty lane (it used to
   move on release). The ruler already did.
4. **Subtitle preset** sits just above the bottom (Transform.y 0.36;
   Caption 0.42). Presets now set x and y (`TEXT_PRESET_POS`).
5. **Typewriter text:** `TextStyle.type_in` / `type_out` (seconds, 0 = off)
   type the text in at the start and delete it at the end;
   `type_cursor` draws a "|" after the text (solid while typing, blinking
   once typed). Line positions come from the FULL text, so nothing shifts
   while typing. Properties > Typing. `render.typed_state()`.
6. **Speech and thought bubbles:** `TextStyle.bubble` ("" | "speech" |
   "thought"), `bubble_fill`, `bubble_outline`, `bubble_outline_width`,
   and the tail tip `tail_x`/`tail_y` -- (superseded: now an OFFSET from the
   bubble position, see feedback round 4) keyframable ("tail_x"/
   "tail_y"; Keyframes > "Bubble tail tip" sets both). Speech = ellipse +
   curved pointed tail (unioned into one outline); thought = cloud of
   bumps + three shrinking circles toward the tip; no tail when the tip
   is inside the bubble. Presets "Speech bubble" / "Thought bubble" (dark
   text on white, bold/italic, a comic font picked from what's installed
   -- `controller.comic_font()`, since Qt's fallback for a missing Comic
   Sans can be a script font). Preview: orange diamond handle drags the
   tail tip; Properties > Bubble switches type and colors.
   `render.text_layout()/bubble_body()/bubble_path()` are shared by the
   renderer and the preview's hit-testing.
7. **Gaps** are a horizontal line across the gap with a break in the
   middle; the trash can appears in that break only on hover.
8. **Double-click text in the preview** opens an in-place editor over it
   (Enter commits, Shift+Enter new line, Esc cancels, clicking away
   commits; it claims all keys so page shortcuts don't fire while typing).
9. **Save separately:** the Save dialog (library clips) offers "Replace
   this clip" or "Save separately (as a new clip)".
   `nle/save.save_library_separately()` renders to "<title> (edited).mp4"
   beside the clip, adds it to the Library with the original's filters/
   favorite, marks it edited; the original clip is untouched.
10. **The mouse wheel never changes a combo box, app-wide**
    (`gui/wheel_guard.py`, an application event filter installed by
    MainWindow). The wheel is forwarded to the nearest scroll area so the
    page still scrolls.
11. **Position keyframe:** Keyframes > "Position" adds/removes/eases X and Y
    together (X and Y alone are still available). Two-value keyframes are
    `COMPOSITE` in properties.py.
12. **Save dialog sizing:** combo boxes sized from their longest item; the
    dialog's labels are plain text (a "<title>" in the text was being
    parsed as HTML and cut the sentence off).

Also: test teardown segfault fixed (the timeline-visuals worker is joined
on shutdown; the previewer test closes its window before exiting).
Tests: `tests/test_advanced_editor_2.py` (37 checks, real events; covers
all 12 items incl. a real Save Separately render), plus every earlier
suite still passing.

### Previous session (Advanced Editor built, previewer fixes)
1. **Esc from previewer fullscreen still shrank a maximized window --
   fixed for real.** Root cause (reproduced under a Wayland compositor,
   weston headless, not visible under X11/openbox): going fullscreen ->
   maximized in one step (`showMaximized()` or
   `setWindowState(Maximized)`) lands UN-maximized at the default
   restored size on Wayland. Fix: entering fullscreen only ADDS the
   FullScreen bit to the existing state (`windowState() | FullScreen`),
   leaving Maximized set; leaving fullscreen only removes that bit, so
   the compositor hands back the maximized window. Verified on both
   Wayland (weston) and X11 (openbox) for normal/maximized/fullscreen
   startup modes (`tests/test_previewer_fullscreen_filters.py`).
2. **Previewer header:** Favorite and Filters are now circular icon
   buttons side by side on the right of the info line (Filters reuses
   the card quick-action `filters_icon.png`; Favorite uses new
   `favorite_star_icon.png` / `favorite_star_off_icon.png`, gold when
   favorited). A bottom-right "Advanced Editor" button opens the clip in
   the Editor.
3. **The Advanced Editor (phases 2 + 3 of the Editor Update).**
   `afterglow/gui/advanced_editor/`:
   - `page.py` -- Filmora layout (browser | preview | properties over a
     toolbar + timeline, splitters), header (Import, name field =
     rename, unsaved marker, Save), all keyboard shortcuts, Save
     (dialog -> worker-thread render with progress + cancel), Import.
   - `controller.py` -- project, History, selection (click/Ctrl/Shift
     incl. cross-track ranges), playhead, clipboard, snapping, autosave
     (3 s after an edit, to the per-clip project file, with
     `Project.unsaved_changes`), `remap_sources()` (re-points the live
     project AND undo snapshots after a save or a file rename).
   - `timeline.py` -- ruler, red playhead with a bulky head, tracks with
     3-line reorder handles + collapse arrows (header side per the new
     General > Editor setting), segments (film strips centered on each
     tile's time, waveforms, the 0-200% volume line with type-a-percent
     while held, fade handles, split markers, transition badges,
     keyframe diamonds, lock/mute/hidden states), rounded corners except
     where a segment touches a neighbor, drag-move with snapping + kick
     + new tracks from the buffer lanes, edge trims, gap line + hover
     trash can, rubber-band selection, wheel/zoom, context menus,
     drag-and-drop (files from outside, library clips/text/transitions
     from the browser). Static layer cached to a pixmap; playback only
     redraws the playhead.
   - `preview.py` -- plays through the SAME renderer Save uses, audio
     via QAudioSink (stream kept open between plays, fed silence while
     paused, closed after 20 s idle; the clock follows the device's
     processed count so picture and sound stay in sync), transform
     handles (move/scale/rotate, keyframe-aware), Crop mode handles.
   - `properties.py` -- name, time, speed, fades, volume/mute, detach
     audio, transform + opacity, crop, zoom filter, transition in
     (kind/duration/moves/from), text (words, font, size, colors,
     outline, bold/italic), keyframes (per property: add at playhead,
     jump, easing, delete). Spin-box bursts are one undo step.
   - `browser.py` -- Media (library clips with thumbnails, Add File,
     Picture/GIF), Text styles, Audio (add file, detach, project audio
     list), Transitions (with duration/moves/from options), Effects
     presets (zooms, fades, speeds).
   - `visuals.py` -- worker thread for film-strip thumbnails and
     waveform peaks; `icons.py` -- painted icons.
4. **Engine additions:** transitions rendered (crossfade, blur/focus,
   slide and directional fade for destination/original/both x 4 sides;
   the outgoing segment keeps playing past its end from source handles;
   audio crossfades over the same span), GIFs loop, export takes
   width/height/crf, `media.waveform_peaks()` and
   `media.ThumbnailReader`, `Project.unsaved_changes`,
   `store.repoint_video_sources()` (called by `library.rename_video`,
   since renaming a video renames its file and an unrendered project
   still reads from it). New render tests for each transition kind, GIF
   looping and export size.
5. **Removed:** the old basic `EditorPage` implementation
   (`editor_page.py` is now a one-line re-export of the Advanced Editor
   page) and the now-unused `volume_bar.py`. `trim_timeline.py` stays
   (the previewer's trim bar subclasses it).
6. **Tests:** `tests/test_advanced_editor.py` -- 78 checks through real
   mouse/keyboard/wheel/drag-and-drop events on the real widgets, plus a
   real Save render, reopen, rename-safe sources, and Import. Needs Xvfb +
   openbox (window focus for shortcuts; headless weston gives no focus so
   shortcut checks can't run there). Audio playback verified separately
   against a PulseAudio null sink (start latency ~0.1 s after warm-up,
   A/V clock locked to the device).

Known limits / judgment calls:
- `flake.nix`: `qt6.qtmultimedia` added (package buildInputs + dev shell,
  plugin path) for the preview's audio output, plus `av`/`numpy` in the
  dev shell. Not verified with a real Nix build in the sandbox -- run
  `nix build` first. If QtMultimedia is unavailable the preview plays
  silently on the wall clock.
- Exports are H.264/AAC in an MP4 container even when the clip's file
  extension is .mkv (players sniff content, but the extension then
  mismatches). Worth deciding: keep, or remux/rename to .mp4.
- Preview rendering runs on the UI thread (~5-10 ms/frame at 960x540);
  transitions composite extra layers and may drop frames on slow CPUs.
- Crop follows the engine's crop-then-fit rule, so cropping enlarges the
  remaining picture to fit the frame.
- Colors of segment types: video = accent, audio-only = darkened
  highlight/turquoise, text/pictures = translucent accent.

### Previous session (previewer restyle, Editor features, fullscreen fix)
1. **Themed trim bar in the previewer.** New `gui/preview_trim_bar.py`
   (`PreviewTrimBar`) subclasses `TrimTimeline` and overrides only
   painting, so seek/drag/handle behavior and signals are one shared
   implementation. Drawn from `Theme`/appearance: rounded pill track in
   `library_background()`, `accent()` selected range, rounded grip
   handles and a round playhead in the card text/outline colors;
   rounding follows the Rounded Corners setting. `TrimTimeline` itself
   (used by the Editor page, which is being rebuilt) is unchanged.
2. **Editor features in the previewer.** Favorite toggle and filter
   editing added to the header info row (Favorite left, Filters right).
   Filters opens `gui/preview_filters_panel.py`, an in-window child
   panel (categories, tag icons, "+ Add Filter" via `AddFilterDialog`,
   applies the new filter to the clip). It is a child widget rather than
   a popup/menu because those never stayed open reliably while toggling
   (see `filters_popup.py`). Esc closes the panel before closing the
   preview. Already present from earlier: rename, prev/next, speed,
   trim, Frame Perfect, Undo Edits. Not ported: Save & Upload
   (upload unimplemented), Clear Edit Backup.
3. **Fullscreen exit no longer resizes the app.** Exiting previewer
   fullscreen called a bare `showNormal()`, which dropped a maximized
   or fullscreen-at-startup app to the default 1300x820-style restored
   size. The prior state (fullscreen/maximized/geometry) is now saved on
   entry and restored on exit and on overlay teardown. Reproduced on the
   previous code under a window manager (maximized and fullscreen
   startup modes), passes now.
4. **Tests.** `tests/test_previewer_fullscreen_filters.py` (needs Xvfb +
   openbox + libmpv; covers all three window modes, favorite, filters,
   Esc ordering, writes screenshots). `tests/test_previewer_trim.py`
   still passes unchanged. Screenshots come from software GL, so video
   content looks garbled; chrome/layout is representative.

Judgment calls: trim bar handle/selection contrast depends on the
palette (accent vs library background); Favorite/Filters button width is
a fixed 120px.

### Previous session (before restyle)
**Editor Update started; trimming added to the video previewer.**

1. **Previewer trimming.** The previewer's scrubber is replaced by the
   basic Editor's `TrimTimeline`, with identical behavior: left-click/
   drag seeks, right-click drags the nearest trim handle while the
   player follows the handle, dragging pauses playback, playback stops
   at the end handle, and pressing play at/after the end (or before the
   start) restarts from the start handle. A second row in the same
   transport card holds the Start/End/Selected label, Frame Perfect
   Accuracy, Undo Edits and Save Trim (same `library.apply_trim` /
   `undo_edit` as the Editor). Because it's the same card, fullscreen's
   floating/auto-hiding controls carry the trim row too.
   `tests/test_previewer_trim.py` (17 checks) drives it with real mouse
   events and the REAL mpv player: the player itself reports being at
   the dragged handle, a keyframe trim gives 3.04 s for a 3 s range, a
   Frame Perfect trim gives exactly 2.400 s, Undo restores 10.00 s.
2. **Real-player testing is now possible in the sandbox.** mpv needs an
   OpenGL context, which the offscreen Qt platform can't provide -- the
   reason earlier sessions could never test actual playback. Running
   under `Xvfb :99 -screen 0 1920x1080x24 +extension GLX` with
   `QT_QPA_PLATFORM=xcb` (packages: xvfb, libgl1-mesa-dri, libxcb-cursor0
   and the other libxcb-* Qt needs) gives mpv software OpenGL: real
   decode, real seeking, real durations, and real screenshots of the
   app. Software GL draws a faint grid over scaled video in screenshots;
   that's the virtual display, not the app.
3. **Bug found from the first real screenshot: disabled CustomButtons
   looked enabled** (no disabled state was ever painted -- e.g. the
   previewer's arrows at the ends of a list, the Editor's buttons with no
   video). Now painted at 40% opacity; repaint on enable/disable. Same fix
   applied to the standalone UI kit.
4. **Advanced Editor engine built** (see the Editor Update section above
   for decisions, architecture and the phase plan). 121 checks:
   `tests/test_nle_ops.py` (58), `tests/test_nle_render.py` (40, against
   real generated media: pixel colors and audio levels at known times,
   exports decoded back and checked), `tests/test_nle_save.py` (23: first
   save, reopen + re-save from the original, Undo Edits, an
   already-trimmed clip, Import).
   Real bug caught by those tests: mono audio played 3 dB quiet (the
   resampler's default mono->stereo upmix pans to center at x0.707).
   Fixed: mono is decoded as mono and duplicated at full level.
   Test mistakes caught and fixed along the way (worth knowing for
   future tests): ffmpeg's color "green" is #008000, not #00ff00;
   ffmpeg's `sine` source defaults to 1/8 amplitude (use `aevalsrc` for
   exact levels); comparing re-encoded frames needs a tolerance.
5. **Library integration:** a quick trim, Undo Edits and Clear Edit
   Backup now discard the clip's Advanced Editor project (see
   `store.py`); new `library.record_advanced_edit()`.
6. **Known, not fixed (pre-existing):** the first Library load of clips
   with no cached thumbnail runs ffmpeg once per card inside the
   (time-sliced) build -- 80 new clips took 6.1 s and the grid fills in
   with stutters. Once cached it's instant. Fix: generate thumbnails in a
   background thread and show a placeholder until ready.
   Also: plain `QLabel`s (time label, trim range label, "Speed:") take
   their color from the system palette; on a palette with dark text they
   render dark-on-dark (seen in the Xvfb screenshots; the real desktop's
   KDE palette presumably has light text).

### Previous session
**Release bounce looked low-FPS (press and hover were fine) -- fixed.**
Measured, not guessed: recorded every pulse-animation frame during press
vs release on each real button in the full app with an 80-clip library.
The animation itself was fine (plain button: ~12ms/frame on both press
and release). The stutter was only on buttons whose click does work:
sidebar Library/Editor and Refresh stalled the release animation for
~70-80ms at a time. Cause: Qt fires `clicked` on mouse-UP, so whatever
the click does runs on top of the release bounce -- and a sidebar
Library click (or Refresh) was rebuilding all 80 cards every time, even
when nothing had changed (the usual case). The time-slicing from two
sessions ago kept the UI responsive, but each slice plus the final
grid layout/paint still froze the bounce for several frames.

Fix: `_VideoGridTab.refresh()` now computes `_display_signature()` --
every Video field shown on a card (and their order), each video file's
mtime, per-tag icons and outline colors (Settings > Filters, stored
outside video rows), the highlight-unedited option, and the settings
file's stat key -- and skips the rebuild when it matches what's on
screen. ~1-2ms for 80 clips vs ~150ms+ for a rebuild. Font scale is
deliberately NOT in it (resize already updates cards in place; having it
there made the first click after every resize rebuild for nothing --
caught by diffing signatures). Also: `apply_scale()` now updates cards
from an in-progress sliced build too, not just the ones already swapped
in.

Results (release, worst gap between frames, median of runs): sidebar
Library 71ms -> 25ms, Editor 69 -> 25, Refresh 81 -> 23; real page
switches (alternating Settings <-> Library) ~30-35ms. Spam test: 60
clicks on an unchanged library = 0 rebuilds, worst stall 9ms (was 5-6
rebuilds). Correctness suite (10 checks, all pass): unchanged -> no
rebuild for both sidebar and Refresh; rename, re-edited file (mtime),
file dropped into the folder, file deleted, search, clearing search,
settings save, and a per-tag outline color change all still rebuild.

Tried and REVERTED: replacing the page-switch fade
(`crossfade_to_index`, a QGraphicsOpacityEffect over the whole new page
for 200ms) with a one-time snapshot faded in by a cheap overlay. It
halved total event-loop busy time during a switch (70ms -> 30ms) but
concentrated the page render into one block right as the bounce starts,
and a same-session A/B (26 runs each, interleaved) showed a WORSE worst
hitch: 42.7ms vs 35.7ms median. Original fade restored. The remaining
~30ms on real page switches (about one missed frame at 60Hz) is that
fade rendering the new page -- flagged, not fixed.

Also fixed a stale comment in `MainWindow._on_nav_clicked` that claimed
switching to Library no longer refreshes (the refresh call was right
below it).

Pending an all-clear before porting to the UI kit. What carries over:
nothing in the kit code itself (the skip-rebuild is afterglow's Library),
but the guide gets a pitfall: clicks fire on mouse-up, so heavy click
work lands on the release animation -- skip no-op work, slice the rest.
Also make the guide fully impersonal (no names).

### Previous session
**Rounding disappeared on the release bounce -- fixed.** Reported:
that releasing a click made custom buttons bounce past their limits
(fine) but lose their rounded corners at that moment (not fine).
Cause: `PressPulse`'s release overshoot scaled the shape to 1.04x, and a
widget can't paint outside its own rect, so Qt clipped the enlarged
shape at the widget edge and the rounded corners fell outside it.
Measured the visible corner curve on real widgets: a 100x340 sidebar
button went from 24px to ~4px at the peak (reads as square), a 140x36
CustomButton from 18px to 10px, the Local tab from 24px to 15px.

Fix (`press_pulse.py`): `RELEASE_OVERSHOOT_SCALE` 1.04 -> **1.0** (the
widget's own edge). Release still visibly bounces: pressed 0.90 -> 1.0
-> settles at hover size 0.96 (cursor is almost always still over the
button on release). Resting sizes unchanged.

Tried first and REJECTED: keeping the 1.04 overshoot with headroom
(draw everything at scale/1.04 so the peak exactly fills the rect).
Corners were fully intact at the peak, but every button rests ~4%
smaller: ~7px per end on a sidebar button, roughly doubling the gaps
between Library/Editor/Settings. Sidebar sizing has been a sore spot, so
not shipped silently. The headroom mechanism is KEPT in `apply()`
(draws at `scale / overshoot`, a no-op at 1.0) together with a new
`PressPulse.touching_extension(rect)`, used by `LibraryTabButton` and
`_PopoverTabButton` on their touching (square) sides, so raising the
overshoot later automatically keeps corners intact AND joined tabs
flush. If the bigger bounce is wanted back, that's a one-constant
change with the smaller-at-rest tradeoff above -- ask him first.

Verified: frame-by-frame over a real QTest press/release on the sidebar
button and a CustomButton, the drawn scale never exceeds 1.0 on any
frame and the corner curve at the peak equals the resting one (24px /
18px); all 12 pulsing button types still pass hover 0.96 / press 0.90 /
peak 1.00 / settle 0.96; with headroom forced on (overshoot 1.04),
Local's right edge, Uploaded's left edge, and the popover tab's
left/right/bottom edges stay flush at rest. Test pitfall worth
remembering: a "first filled row" corner measurement mid-animation lands
a fraction of a pixel inside the shape and reads a narrower curve --
assert "never crosses the widget edge" instead.

Same fix applied to the standalone UI kit for Conduit/Puppetry
(`qt-ui-kit.zip`, `UI_THEMING_GUIDE.md` section 4 + pitfall 13).

### Previous session
Follow-up to the previous session's five items, after testing them on
a real machine. Two of last session's fixes were WRONG (right symptom,
wrong cause) -- both now re-diagnosed by direct measurement and fixed
properly. Plus an app-wide optimization pass.

1. **Can't resize vertically + Settings slightly off screen -- REAL
   cause found (last session's fix was wrong).** Measured
   `MainWindow.minimumSizeHint()` directly: **1128px tall**. Cause:
   Settings' pages live in a QStackedWidget, whose minimum height is the
   LARGEST minimum of ALL its pages (hidden ones too), and the General
   page's Appearance group alone needs ~1035px with nothing scrollable.
   So the whole window had a hard ~1128px floor -- taller than a 1080p
   screen. The WM can't shrink below that (no vertical resize), and
   maximized/fullscreen clips the bottom (Settings off screen). Fixed by
   wrapping every Settings page in its own SmoothScrollArea
   (`_add_settings_tab` in settings_page.py; autoFillBackground turned
   back off since QScrollArea.setWidget() force-enables it -- verified
   the background pixel still matches app_background). Also lowered
   MpvVideoWidget's hardcoded min height 300 -> 120 (next-largest
   contributor). Min window height now **385px**. REVERTED last
   session's save/restore-geometry hack in
   `VideoPreviewContent._toggle_fullscreen` -- it targeted the wrong
   cause and setGeometry() after showNormal() can fight the WM.
   **Lesson worth keeping:** for any "window won't resize / is too
   big" report, check minimumSizeHint() down the widget tree first.
2. **Spam-refresh freeze -- REAL cause found (last session's guard
   couldn't work).** The rebuild was synchronous, so the event loop was
   blocked while it ran; spam clicks couldn't arrive DURING a rebuild,
   they queued in the OS and were delivered one by one AFTER it
   finished -- each starting another full rebuild. The in-progress flag
   never saw them. Fixed in `_VideoGridTab.refresh()`: card building is
   now time-sliced (`_build_step`, ~12ms per event-loop tick), so the UI
   stays live and clicks arriving mid-load are coalesced into ONE
   pending follow-up. New cards are built off-screen and swapped in all
   at once (`_swap_in_cards`) so the old grid stays visible -- no half-
   built flash. `LibraryPage.refresh()` (sidebar click: filesystem scan
   + both tabs) coalesces the same way while any tab is loading.
   `_do_refresh()` kept as a synchronous path for anything needing it.
   Sidebar Library icon darkens for the whole load. Tested with real
   QTest clicks on 80 real ffmpeg clips: 60 rapid clicks (Refresh +
   sidebar Library) -> 5-6 rebuilds (was 60 blocking ones), worst UI
   stall ~80-95ms, icon darkens then clears, all 80 cards visible after.
3. **Pulse animation on EVERY custom button.** Extracted into a shared
   `gui/press_pulse.py` (`PressPulse(widget)`: an event filter +
   QVariantAnimation; one line in __init__, one `self._pulse.apply(
   painter)` in paintEvent -- no per-class event overrides needed).
   Wired into: CustomButton (refactored onto it; also covers the
   previewer's prev/next arrows), LibraryTabButton (sidebar Library/
   Editor/Settings + Local/Uploaded), CustomCheckBox/FilterCheckBox and
   CustomRadioButton (INDICATOR BOX ONLY, scaled around its own center
   inside save/restore so the label text doesn't slide -- pixel-verified),
   CollapseToggleButton, _PopoverTabButton (Filters/Sort By/Info tabs),
   _SpinArrowButton (spinbox +/-), and the previewer's _PlayPauseButton,
   _VolumeButton, _FullscreenButton. All 12 verified with real QTest
   press/release: hover 0.96, held 0.90, release bounce ~1.04, settle
   back to 0.96 (still hovering), rendered pixels actually change.
   Resets on hide/disable so nothing gets stuck shrunk.
   NOT covered: `_MultiFilterSelectButton` (Settings > Filters > Auto
   Add Filter "Choose filter(s)..."), which is still a NATIVE-painted
   QToolButton -- would need converting to custom paint first.
4. **Optimization pass (measured, 80 real clips):**
   - `config.load()` re-read + re-parsed config.toml on EVERY call --
     401 calls per Library refresh, >half of all refresh time, and it's
     called from paintEvents too. Now cached, re-parsed only when the
     file's mtime/size changes (daemon writes, Settings saves, imports,
     hand edits still picked up immediately; save()/import invalidate
     explicitly). load() returns a deepcopy (safe to mutate + save);
     new `load_readonly()` returns the shared instance with no copy --
     all 48 `config_module.load().<attr>` read-only call sites switched
     to it. Rule: never mutate what load_readonly() returns.
   - Thumbnails were decoded + smooth-scaled + corner-rounded again for
     every card on every refresh. Now an LRU cache in video_card.py
     (`_THUMB_PIXMAP_CACHE`) keyed on path + mtime + radius + size.
   - Button icons were smooth-scaled inside paintEvent -- which the
     pulse animation now triggers every frame -- so added
     `press_pulse.scaled_cached()` and used it in LibraryTabButton,
     CustomButton, CustomCheckBox, CustomRadioButton.
   - Net: full Library rebuild ~0.55s -> ~0.17s (80 clips), and it no
     longer blocks the UI at all since it's time-sliced.

### Previous session
Five direct requests, all in the Editor and the app's overall window/
button behavior rather than Library/appearance this time.

1. **Editor previewer now follows the handle being dragged, not just
   the frame it started on.** `TrimTimeline._on_trim_range_changed`
   in `editor_page.py` only updated the range LABEL on every
   intermediate `range_changed` signal -- it never actually seeked
   `video_widget` until the drag finished, so the preview looked
   frozen on the pre-drag frame the whole time you were dragging.
   Added a `dragging_handle` property to `TrimTimeline` (exposes its
   existing private `_dragging` state: `None | "start" | "end"`) and
   now seek the video to whichever edge is actually moving on every
   `range_changed`. Verified directly: pressing a handle, dragging it,
   and checking both `TrimTimeline.start`/`.end` AND
   `dragging_handle` update correctly through a press/drag/release
   cycle.
2. **Can't vertically resize the app, AND resizing then returning to
   fullscreen messes up the sidebar/settings sizing -- same root
   cause, both fixed.** This is exactly the "one untested hypothesis"
   flagged unresolved several sessions ago in item 5 of that session's
   notes (search this file for "vertical-resize limitation"): the
   video previewer's fullscreen toggle
   (`VideoPreviewContent._toggle_fullscreen` in
   `video_preview_dialog.py`) calls `showFullScreen()`/`showNormal()`
   directly on the actual MainWindow, and on at least some window
   managers that cycle can leave the window's own notion of its
   "normal" geometry/resize state stuck, rather than genuinely
   restoring it. `MainWindow.resizeEvent`'s own sidebar/icon-size math
   was checked and is fine -- it was firing correctly against a
   geometry the WM had never actually finished settling back to.
   Fixed by having `_toggle_fullscreen` record `window.geometry()`
   itself right before calling `showFullScreen()`, and on the way back
   out, explicitly clearing the `Qt.WindowFullScreen` state bit via
   `setWindowState` AND reapplying that saved geometry with
   `setGeometry`, instead of trusting `showNormal()` alone to put
   things back the way they were. Not verified against a real window
   manager (this sandbox's offscreen Qt platform doesn't reproduce WM-
   level state-transition quirks any more than it reproduced the
   stylesheet-cascade bug several sessions back) -- flagged the same
   way that one was; please confirm both the previewer's fullscreen
   toggle and ordinary window resizing (including vertical) still work
   right on your machine.
3. **Library loading debounce reworked from a fixed 750ms cooldown
   timer to an actual "is a refresh in flight" flag**, per direct
   request. The old timer had it backwards in both directions: on a
   large library it could expire before the real rebuild (which is
   synchronous and O(number of cards)) actually finished, letting an
   overlapping second rebuild start and stack up the exact
   "multiplying/messed-up scaling" symptom the debounce exists to
   prevent in the first place; on a small library it kept blocking
   clicks for however much of the 750ms was left over after the
   rebuild had already long since finished. `_VideoGridTab.refresh()`
   now sets a plain `_refresh_in_progress` flag right before calling
   `_do_refresh()` inside a `try`/`finally`, so it's guaranteed to
   clear even if `_do_refresh` raises ("finish or fail," as asked) --
   any `refresh()` call that arrives while the flag is set is
   coalesced into a single `_refresh_pending` flag and run exactly
   once, immediately after the in-flight one finishes, rather than
   being silently dropped as the old debounce did. Also added the
   requested visual feedback: a new `loading_changed` signal on
   `_VideoGridTab`, bubbled up through `LibraryPage` (tracking both
   tabs independently so the icon only un-darkens once BOTH are idle,
   not whichever finishes first), and a `LibraryTabButton.set_loading()`
   method that darkens the sidebar's Library icon while it's true --
   wired together in `MainWindow.__init__`. Verified directly against
   a real `_VideoGridTab` with a real (temp, empty) SQLite DB: a
   `refresh()` call that arrives mid-rebuild is coalesced and runs
   exactly once more afterward (not dropped, not duplicated), and the
   `_refresh_in_progress` flag/the `loading_changed` signal both still
   correctly release/fire `False` when `_do_refresh` is made to raise,
   confirmed by direct test rather than just reading the code.
4. **Custom Buttons now have an eased hover-shrink + click-pulse
   animation**, per the exact spec: slightly smaller on hover,
   pulsing further down while the click is held, pulsing back up the
   instant it's released and smoothly settling back to rest -- all of
   it eased, none of it a hard jump. Added a `visual_scale` Qt
   `Property` to `CustomButton` (`custom_button.py`) driving a
   `QPropertyAnimation` (hover/press transitions, `OutCubic`) or a
   `QSequentialAnimationGroup` of two chained animations for the
   release pulse (a quick `OutCubic` bounce up past resting size, then
   a slower `InOutCubic` ease back down to it) -- applied in
   `paintEvent` as a `QPainter` scale transform around the button's
   own center, so the fill/outline/icon/text all shrink and grow
   together as one unit. Judgment call, flagging it in case it wasn't
   what was meant: on release, "resting" is 1.0 if the mouse has
   already left the button by then, or the hover scale if it's still
   hovering -- so releasing while still over the button settles into
   the hover-shrunk size rather than all the way back up to full size,
   consistent with hovering alone still being supposed to keep it
   smaller. Verified directly (offscreen, real `CustomButton`
   instances): hover/press/release all start the expected animation
   with the expected target scale, and `paintEvent` renders without
   error at both a shrunk and an overshot scale value. This only
   touches `CustomButton` itself -- `LibraryTabButton` (the sidebar's
   Library/Editor/Settings buttons and the Local/Uploaded tab buttons)
   is a separate class and wasn't touched, since the request was
   specifically "for custom buttons."

### Previous session
1. **Context menu reverted to the pre-`FiltersPopup` QMenu-based
   version**, after a `Qt.Popup`-based rebuild (`filters_popup.py`,
   `FiltersPopup`) still didn't reliably stay open while toggling
   checkboxes and looked worse visually than the version before it.
   `_build_filters_menu`, `_open_filters_menu_for_self`, the "Edited"

   `CustomCheckBox`-via-`QWidgetAction`, and the nested
   `_NonClosingMenu` category submenus in `video_card.py` are all back
   to their pre-rebuild form. `filters_popup.py` is left in the tree,
   unused, in case a future session revisits the underlying "menu
   doesn't reliably stay open while toggling several checkboxes in a
   row" problem, which remains unsolved after five separate attempts
   at the QMenu/Qt.Popup level -- see the git history / prior session
   entries below for what was already tried and ruled out.
2. **Scrubber pause-during-drag + drag-to-seek from anywhere on the
   track, with a live preview frame.** `_ClickToSeekSlider`
   (`video_preview_dialog.py`) tracks its own `_track_drag_active`
   state and manually emits sliderPressed/sliderMoved/sliderReleased
   to mirror a real handle drag, rather than a one-shot "jump on
   press" that couldn't support continuing to drag afterward.
   `VideoPreviewContent._on_scrub_start`/`_on_scrub_end` pause
   playback for the duration of any scrub (remembering whether it was
   actually playing beforehand, so scrubbing an already-paused video
   stays paused afterward) and resume only if it was playing when the
   drag started. `_on_scrub_moved` now also issues a live seek on
   every drag step (not just once on release), so the displayed frame
   tracks the current scrub position continuously instead of staying
   frozen on the frame from wherever the drag began.
3. **Previewer fullscreen now removes all remaining chrome.** In
   addition to the content box filling 100% of the window (from an
   earlier pass), entering fullscreen now also hides the Prev/Next
   arrows (previously still occupying horizontal space in `video_row`,
   squeezing the video's own width) and zeroes both the outer 16px
   margin and the video frame's own border margin -- all restored to
   their normal values on exit.
4. **Single-clip card sizing bug fixed.** `grid_layout.addWidget(card,
   row, col, ...)` in `library_page.py` had no horizontal alignment
   constraint (`Qt.AlignTop` only) -- with more than one card sharing
   a row, neighboring cards effectively pinned each column's width,
   but a library with exactly one video (a single cell, nothing else
   to constrain its column) could let that lone card stretch to fill
   the available column width instead of its normal size. Fixed by
   adding `Qt.AlignLeft` alongside `Qt.AlignTop`.
5. **A reported vertical-resize limitation on the main window remains
   unresolved.** The usual causes were checked (fixed/minimum/maximum
   size constraints on the main window or its direct children,
   anything in its resize handler that could compute a height-based
   constraint) with nothing conclusive found. One untested hypothesis:
   the OS-level fullscreen previewer feature added this session --
   fullscreen/normal window-state transitions can sometimes leave a
   window's resize behavior in an altered state depending on the
   window manager -- but this was not verified against a real
   compositor and should not be assumed correct without testing.

### Previous session
Three direct follow-up reports on the previous round, all three
genuine bugs, all three found and fixed.

1. **Context menu STILL closing, and "looks worse than before" -- found
   two concrete, real differences from the proven-working reference
   implementations this was modeled on.** Compared `FiltersPopup`
   directly against `SortPopover`/`SearchBubble` (both confirmed
   working) line-by-line rather than guessing again, and found:
   (a) `FiltersPopup` only set `Qt.Popup` alone, while BOTH working
   references always pair it with `Qt.FramelessWindowHint` -- without
   that, the popup could still pick up a native window-manager frame/
   title bar despite Qt.Popup being set, which would explain "looks
   worse" (a native frame around otherwise-rounded custom content)
   AND is a plausible source of premature closing through an entirely
   different mechanism than any of the four earlier QMenu-level
   attempts were even looking at; (b) the flags were being set via a
   separate `setWindowFlags()` call AFTER construction, not passed
   directly to `super().__init__(parent, flags)` the way both
   references do it -- setting window flags on an already-constructed
   widget requires Qt to re-create the underlying platform window to
   fully take effect, which doesn't reliably happen in every case.
   Fixed both: flags now passed directly to the constructor, exactly
   matching the proven pattern. Re-verified the popup still stays open
   while toggling and still correctly reports closing.
2. **Click-to-seek "flicks to it but immediately comes back" -- a real
   bug in the fix itself, traced precisely.** `_ClickToSeekSlider`
   correctly updated the slider's VISUAL value for a track click, but
   skipping `super().mousePressEvent()` for that case (needed to stop
   QSlider's own page-step reaction from overriding the jump) also
   meant Qt's internal "am I mid-drag" state was never initialized --
   so the mouse release that naturally follows a click was never
   recognized as completing anything, and `sliderReleased` (which is
   what actually triggers the real seek, via `_on_scrub_end`) never
   fired at all. The slider's position visibly jumped, but nothing
   ever actually sought, so the very next routine position update from
   mpv -- still playing from the old, un-sought position -- snapped the
   handle right back, exactly as reported. Fixed by explicitly
   emitting `sliderReleased` immediately after the manual `setValue()`
   for a track click, synthesizing the same "press-and-release
   completed" signal a real drag-then-release would have produced.
   Verified directly against the actual mpv `seek()` call this time
   (not just the slider's own visual value): a click at 60% of a 10s
   video now genuinely issues a seek to ~6.0s, and a subsequent
   position update consistent with that seeked position doesn't snap
   anything back.
3. **"Auto hide/slide is good, but it should be ACTUALLY fullscreen" --
   two remaining sources of padding/rounding found and removed.** The
   WINDOW itself was already genuinely fullscreen (from last round's
   fix), but the CONTENT box inside it was still sized to 97% of that
   (leaving a visible ~3% margin all around) and its own `paintEvent`
   still rounded its corners unconditionally regardless of fullscreen
   state. Fixed both: content now fills 100% of the window with zero
   margin while expanded, and corner rounding is skipped entirely in
   that state (a rounded rect for something that's supposed to BE the
   whole screen just clips its own corners against the scrim behind
   it, which is what was actually being seen). Verified directly: the
   content's geometry exactly matches the full window with no padding,
   and a corner pixel is (within antialiasing noise) identical to a
   pixel just inside it -- no real rounding -- while expanded; exiting
   correctly restores the smaller, rounded, non-fullscreen size.

### Previous session
The context menu issue is finally resolved with a fundamentally
different architecture (after four straight failed QMenu-level fixes),
plus two substantial video previewer features and a real pre-existing
keyboard bug caught along the way.

1. **Context menu closing -- abandoned patching QMenu entirely, after
   four fixes at that level all failed.** Rather than attempt a fifth
   guess at QMenu's exact internal closing mechanism, the Filters
   interaction (both the context menu's entry and the quick-action
   button) now uses a genuine `Qt.Popup`-flagged custom widget
   (`filters_popup.py`, `FiltersPopup`) -- the SAME proven technique
   `SearchBubble`/`SortPopover` already rely on successfully elsewhere
   in this exact codebase. Qt.Popup's own native behavior is precisely
   "a click outside this widget closes it; clicks inside are delivered
   normally and never close it on their own," which is exactly what
   was needed and sidesteps whatever QMenu-specific mechanism was
   actually responsible (still not identified with certainty across
   four attempts). Also simplified "Edited" back to a plain checkable
   QAction -- a single one-shot toggle closing the menu afterward is
   completely normal, expected behavior (same as Favorite); the
   persistent, repeatedly-reported problem was always specifically the
   Filters list, where toggling several tags in one visit genuinely
   needs to keep it open. Caught a real bug of my own while verifying
   this, not left to hit: connecting to the popup's
   `destroyed` signal to track when it closes would likely never have
   fired at all, since a Qt.Popup typically just HIDES (not destroys)
   on an outside click -- fixed by adding an explicit `closed` signal
   emitted from the popup's own `hideEvent` instead. Verified directly:
   toggling a checkbox inside the popup leaves it open and actually
   applies the tag change, and closing it correctly fires the tracking
   signal library_page.py depends on to know when it's safe to run a
   deferred grid refresh again.
2. **Ghost playback line + click-to-seek on both sliders.** The
   scrubber and volume slider both gained an explicit
   `QSlider::add-page` style (previously unstyled, so the "unplayed"
   remainder just showed the plain groove color and read as "the line
   just ends") -- a semi-transparent lighter overlay now makes the
   rest of the timeline clearly visible as its own distinct thing. New
   `_ClickToSeekSlider` (used for both) jumps directly to wherever you
   click on the track, rather than QSlider's own default of moving one
   page-step toward it. Caught a real bug in my own first attempt:
   skipping `super().mousePressEvent()` unconditionally for every left
   click would have broken ORDINARY handle-dragging entirely (that's
   what actually emits sliderPressed/sliderMoved/sliderReleased) --
   fixed by distinguishing a click ON the handle itself (goes through
   completely normal Qt handling) from a click elsewhere on the track
   (jumps directly, bypassing only QSlider's own page-step reaction to
   that specific case). Verified both: clicking the track seeks
   correctly, and clicking-then-checking the handle's own drag
   handling still fires normally.
3. **Actual OS-level fullscreen with auto-hiding, sliding overlay
   controls.** Toggling fullscreen now calls `showFullScreen()` on the
   actual MainWindow (this widget is embedded inside it, not a
   separate top-level window), not just filling most of the existing
   overlay. While fullscreen, the header/transport boxes are
   reparented out of their normal layout slots into floating children
   positioned via manual geometry, overlaying the video directly
   rather than occupying their own separate space above/below it --
   auto-hiding via slide animation (QPropertyAnimation on geometry,
   not opacity or a teleport) after 2 seconds of no mouse movement or
   immediately on the window losing focus, sliding back in on any
   mouse movement. Exiting fullscreen reparents them straight back
   into the normal QVBoxLayout. Verified the full cycle directly:
   hide-after-inactivity, show-on-mouse-movement, and clean layout
   restoration on exit -- and caught a real bug in my OWN test while
   confirming this, not a product bug: an inactivity timer sped up for
   an earlier part of the same test was left running and kept
   re-firing during a later wait, hiding the controls again right
   after they'd just been shown -- not a flaw in the actual show/hide
   logic itself.
4. **Frame-by-frame nudging** -- `,`/`<` steps one frame backward,
   `.`/`>` one frame forward (new `MpvVideoWidget.frame_step()`/
   `frame_back_step()`, thin wrappers around mpv's own commands of the
   same name, which already pause playback as part of what they do).
   "Holding it nudges many frames in quick succession until let go"
   comes entirely for free from the OS/Qt's own key-repeat mechanism
   (a held key re-delivers keyPressEvent repeatedly) -- no separate
   timer needed, just acting on every delivery regardless of whether
   it's a repeat. While wiring this in, found and fixed a genuine
   PRE-EXISTING bug, not something introduced this session:
   `VideoPreviewOverlay` is what actually holds keyboard focus (its
   own `showEvent` calls `self.setFocus()` on itself), but its
   `keyPressEvent` never forwarded anything to
   `VideoPreviewContent.keyPressEvent` for any key other than Escape
   -- meaning Space-bar play/pause (and now frame-step) could never
   actually have been reachable in the running app at all, regardless
   of whatever `VideoPreviewContent` itself implemented. Fixed by
   having the overlay explicitly forward unhandled keys to
   `self.content.keyPressEvent()`. Verified all four key variants
   step correctly, that simulated auto-repeat presses each nudge a
   frame, and that Space now actually reaches the content widget too.

### Previous session
Three persistent bugs (two of which had already survived multiple
fix attempts) finally root-caused for real, plus a new feature.

1. **Context menu closing on a checkbox toggle -- the ACTUAL root
   cause found, after two fixes aimed at the wrong thing entirely.**
   Both prior attempts assumed QMenu itself had some internal closing
   mechanism not routing through the Python overrides -- reasonable,
   but wrong. The real cause: `menu.exec()` runs its own NESTED event
   loop, which still processes timers -- so the debounced grid-refresh
   timer (added a few sessions back to fix toggle lag) could fire
   WHILE a context menu was still open, rebuilding the entire grid
   from scratch and destroying the very VideoCard the open menu was
   parented to. The menu closing was purely a side effect of its own
   parent widget being deleted out from under it -- completely
   unrelated to anything QMenu's own behavior does, which is exactly
   why fixing QMenu's behavior twice never touched it. Fixed properly
   this time: VideoCard gained `context_menu_opened`/
   `context_menu_closed` signals (emitted around both `menu.exec()`
   call sites), and `_VideoGridTab` now tracks how many menus are
   currently open, deferring (rescheduling, not dropping) the debounced
   refresh for as long as any are -- firing it promptly the moment the
   last one closes instead. Verified by directly reproducing the race:
   open a "menu," trigger the debounced refresh, confirm the grid does
   NOT rebuild while the menu is open, then confirm the deferred
   refresh actually runs immediately once the menu closes.
2. **Click-off-to-save only worked on the SAME card -- fixed with an
   app-wide event filter.** The previous fix checked clicks within one
   card's own `mousePressEvent`, which could never see a click landing
   on a genuinely different widget (a different card, the empty grid
   background) -- Qt delivers a mouse press to whichever widget the
   cursor is actually over, not to every other widget in the app.
   `VideoCard._rename()` now installs itself as an
   `QApplication`-wide event filter for as long as it's editing
   (removed the moment the edit commits), watching for a
   `QEvent.MouseButtonPress` ANYWHERE and committing if it lands
   outside the edit box, regardless of what it actually landed on.
   Caught a real flaw in my OWN verification while testing this, not
   after: calling `.mousePressEvent()` directly on a widget bypasses
   Qt's real event dispatch entirely (and with it, every installed
   event filter) -- a test written that way would have falsely
   "confirmed" a fix that only works for real Qt-delivered clicks.
   Fixed by using `QApplication.sendEvent()` instead, which correctly
   exercises the whole dispatch chain. Verified against both a
   different card and a click on empty background.
3. **Filters tab padding -- removed the scroller entirely, per the
   own direct suggestion**, rather than continuing to refine the
   capped/scrolling version through a third attempt.
   `_wrap_scrollable()` is now a documented no-op passthrough (kept,
   not deleted, so a future session could reintroduce a cap if a truly
   huge tag list ever needs one) -- the popover simply grows to fit
   however tall a page's content actually is, no cap, no scrolling.
   Verified against the exact multi-category screenshot scenario from
   the report: full natural height, nothing hidden or capped.
4. **New: "Auto Copy as MP4"** (Settings > Advanced, on by default) --
   `VideoCard._resolve_copy_path()`: when on, Copying a video whose
   file isn't already `.mp4` puts a freshly-made `.mp4`-named COPY on
   the clipboard instead of the original extension -- a pure rename
   via a copy, explicitly NOT a remux or re-encode of any kind (so not
   guaranteed to be genuinely valid MP4 if the underlying container/
   codec really isn't compatible -- accepted on purpose, exactly as
   asked for). The library's own tracked file is never touched;
   repeated copies of the same video overwrite the same temp path
   rather than accumulating new ones. Verified directly: a non-mp4
   source gets copied and renamed with byte-identical content, the
   setting defaults to on, turning it off leaves the original path
   untouched, and an already-.mp4 file is never copied at all.

Caught one more real bug in my OWN edit while wiring the Settings
checkbox for item 4, not left to hit: a `str_replace` meant to
insert the new checkbox's note label accidentally consumed the
following method's own `def _export_settings(self):` line, which
would have crashed Settings outright on construction -- caught
immediately by actually constructing a `SettingsPage` and checking,
not assumed to be fine because the edit "looked right."

### Previous session
Follow-up bug reports on the last batch, plus the remaining custom-
Settings holdouts.

1. **Spinbox arrows -- found the real, exact bug described.** Each
   arrow was a fixed 22px, which needs ~50px+ of total spinbox height
   to stack two without overlapping -- but a real spinbox is typically
   only ~28-32px tall, so the two overlapped almost entirely, with
   whichever was positioned/painted to "win" the shared space
   appearing fully visible and the other barely showing at all,
   exactly as reported ("the bottom increment button being the only
   visible one, the top increment button only filling about 20% of
   the box"). Fixed by sizing each arrow to exactly HALF the spinbox's
   own height, computed fresh in `resizeEvent` rather than a fixed
   constant -- guarantees perfect tiling (no overlap, no gap)
   regardless of how tall or short any given spinbox actually is.
   Verified at both a normal (30px) and a deliberately short (24px)
   height.
2. **"Click off to save the title" -- found the actual gap.** Verified
   directly first that `editingFinished` DOES fire correctly on a
   genuine Qt focus change (a real bug in my OWN test harness looked
   like a product bug at first -- two separate top-level widgets don't
   reliably exchange focus under this sandbox's offscreen platform;
   redone with both widgets in the same window, it worked correctly).
   The actual gap: clicking elsewhere on the SAME card (the thumbnail,
   an action button) is fully consumed by that card's own click
   handling and never naturally shifts Qt's focus away the way
   clicking some unrelated widget would -- "click off doesn't save"
   only failed for exactly that case. Fixed by explicitly committing
   any in-progress title edit at the top of both `VideoCard.
   mousePressEvent` and `VideoPreviewOverlay.mousePressEvent`,
   whenever the click lands anywhere other than the edit box itself
   (mapped via global coordinates, since the edit box's parent isn't
   necessarily the widget whose mousePressEvent this is). Verified
   both: clicking the thumbnail on the same card while editing, and
   clicking the previewer's scrim (which also closes the whole
   overlay right after) both now save correctly.
3. **Context menu still closing on a filter/Edited toggle -- a third,
   different technique after two failed attempts.** Both prior fixes
   (checking `activeAction()`, then also `actionAt()`, in
   `mousePressEvent`/`mouseReleaseEvent`) were reasoned attempts to
   catch WHICH internal call was responsible, but evidently missed
   whatever the actual mechanism is -- most likely another instance of
   the same PySide6 limitation already confirmed for `CustomGroupBox.
   setLayout()`: some internal C++-side call not dispatching to a
   Python subclass's override at all. Rather than keep guessing which
   call, `_NonClosingMenu` now reacts to the OUTCOME instead: a new
   `hideEvent` override checks a flag set the moment a press lands on
   a widget action, and if the menu tries to hide right after that, it
   reverses it by immediately re-showing itself at its own current
   position -- regardless of what actually triggered the hide.
   Verified the mechanism directly (a suppressed hide is reversed; a
   subsequent genuine hide still closes normally) -- flagged honestly
   that two prior fixes for this exact report didn't hold up, so this
   one should be treated as unconfirmed until verified for real.
4. **OBS Test Connection (and every other QMessageBox in Settings) --
   custom-styled.** New `custom_message_dialog.py`
   (`CustomMessageDialog`/`show_message()`) -- same frameless +
   translucent + rounded-fill treatment the video previewer and Add
   Filter dialog already use. All 10 `QMessageBox.information/warning/
   critical` call sites in settings_page.py now go through this one
   shared dialog instead.
5. **Advanced Sound dialog -- rounded, native OK/Cancel replaced.**
   Same frameless/translucent/rounded treatment, and its
   `QDialogButtonBox` swapped for two plain `CustomButton`s wired to
   accept()/reject() directly (its internal sound-path fields were
   already `CustomLineEdit`/`CustomButton` from an earlier session --
   only the dialog's own outer chrome and its OK/Cancel buttons were
   still native). Verified frameless+translucent attributes, zero
   remaining `QDialogButtonBox` instances, and that accept() still
   fires correctly through the new button.
6. **Filter page headers (Filters & Auto Add Filters) -- native
   QTabWidget replaced.** Same `CustomButton` + `QStackedWidget`
   pattern SettingsPage's own top-level tabs already use. Verified
   zero remaining `QTabWidget` instances and that clicking the new
   tab buttons actually switches the stack.
7. **Re-investigated the Filters-tab padding report with the exact
   screenshot scenario reproduced** (a "games" category with 7 tags, a
   "roblox games" category with 1) -- both group boxes size correctly
   for their own content (no cutoff), and the only place content
   isn't fully visible is where the WHOLE page's total content (425px)
   exceeds the popover's existing 320px scroll cap, which is expected,
   by-design scrolling, not a bug. This strongly suggests last
   session's `CustomGroupBox.make_layout()` fix already resolved the
   actual padding bug, and the screenshot may predate that build --
   flagged as verified-by-reproduction rather than claimed fixed with
   full certainty, since it can't be confirmed further without seeing
   it live.

### Previous session
Continuing straight from the same batch (7 items plus one more added
mid-way) -- picking up where the last handoff left off (items 1-4 and
half of 5 were already done; this covers the rest).

1. **Fixed the lag half of item 5.** Root cause: `card.tags_changed`/
   `card.renamed` were connected DIRECTLY to `_VideoGridTab.refresh()`
   -- a full rebuild of every card in the grid, immediately, on every
   single filter toggle or title-edit commit. `_VideoGridTab` gained
   its own `_card_signal_debounce` (150ms, mirroring the existing
   pattern `LibraryPage` already uses for its DB-file-watcher), and
   both signals route through that now instead of calling `refresh()`
   directly -- a burst of several rapid toggles coalesces into ONE
   rebuild shortly after the last one, rather than one rebuild per
   toggle. Verified directly: emitting the signal returns near-
   instantly with no rebuild yet, and a burst of 4 emits in quick
   succession produces exactly 1 refresh.
2. **Root-caused item 6 (unnecessary padding / cut-off filters) for
   real this time.** `CustomGroupBox`'s core technique -- set the
   widget's own `contentsMargins` before a layout gets attached,
   assuming the layout would inherit them -- was flatly wrong,
   confirmed by direct experiment (a layout attached afterward falls
   back to the style's own default margins instead, completely
   ignoring whatever the widget's margins already were). This has been
   silently wrong in EVERY CustomGroupBox in the app since it was
   built. Also confirmed that overriding `setLayout()` to fix this
   doesn't work either -- PySide6 does not route the internal
   `QVBoxLayout(widget)`/`QFormLayout(widget)` attachment call through
   a Python subclass's `setLayout()` override at all (confirmed with a
   print statement that simply never fired). Fixed with a new
   `make_layout()` factory method that constructs the layout AND
   immediately applies the correct margins in one step, updated at
   all 11 call sites across settings_page.py, filters_settings_page.py,
   and library_page.py's Sort-popover category grouping (a `showEvent`
   override remains too, as a defensive fallback for anything that
   might still attach a layout the old way). Verified against the
   literal reported scenario: a category with 3 checkboxes underneath
   it, previously cut off -- now sized tall enough to show all of them.
3. **Made `_NonClosingMenu` more robust and applied it to the ENTIRE
   context menu**, not just the Filters submenu, per "context menus
   still close when toggling filters" -- checks BOTH
   `self.activeAction()` (hover-tracked) and `self.actionAt(pos)`
   (position-based, independent of hover state) as two separate ways
   to reach the same "this was a widget-action click" conclusion, and
   overrides `mousePressEvent` in addition to `mouseReleaseEvent` in
   case the close was reacting to the press half. Verified via the
   class's own logic directly.
4. **New "Extended Dates" setting** (Settings > Appearance) -- when
   on, every place a video's date is shown (the Library card, the
   previewer's header) uses the full timestamp (date + hours/minutes/
   seconds, whatever's in `created_at`) instead of just the date.
   `_format_date()` reads this setting fresh on every call (same
   pattern as other appearance-driven formatting in this codebase)
   rather than threading a parameter through every call site, so both
   places automatically stay in sync with it. Verified both the
   formatting function directly and the Settings checkbox actually
   persisting to disk.
5. **Item 1: rounded the Add Filter dialog** -- frameless + translucent
   + a custom-painted rounded fill, the same technique the video
   previewer already uses for its own corners (a plain
   `setStyleSheet()` background-color on a normal `QDialog` still
   renders with square corners, since that's the native WINDOW frame,
   not something a stylesheet touches).
6. **Item 2: video titles are now inline-editable directly on the
   card**, not via a popup dialog -- clicking the title (via the
   context menu's Rename, which now triggers this instead of opening
   `QInputDialog`) swaps it for a `CustomLineEdit` pre-filled with the
   current title. Autosaves once a second while editing, in addition
   to committing on Enter or clicking away. Caught a real design flaw
   BEFORE it shipped, not after: naively emitting the card's own
   `renamed` signal on every autosave tick would have triggered a full
   grid rebuild every second while the user was still typing --
   destroying the very edit widget they were typing into, mid-edit.
   Fixed so autosave only persists to the DB silently; the refresh-
   triggering signal fires exactly once, on the actual commit.
   Verified the whole cycle directly, including that autosave does
   NOT fire `renamed` and that it fires exactly once on commit.
7. **Item 4: replaced "Mark as Edited" with a single "Edited"
   checkbox** in the context menu (embedded via `QWidgetAction`, same
   as the Filters checkboxes), covering both directions Requested:
   in one control rather than two separate menu items -- checked only
   when EVERY selected video already has `has_edit` set; toggling
   applies the checkbox's new state to the whole selection. New
   `library.mark_as_unedited()` (the reverse of the existing
   `mark_as_edited()`), leaves `backup_path` completely untouched
   either way. Verified toggling flips `has_edit` correctly in both
   directions.
8. **Item 3: a real pass at "make everything in Settings custom."**
   New shared `custom_combo_style.py` (`combo_box_stylesheet()`,
   extracted from what the Add Filter dialog already had, since it's
   now used identically in several more places) -- a QSS reskin
   applied to EVERY `QComboBox` across settings_page.py,
   filters_settings_page.py, and the Add Filter dialog (6 total).
   Every remaining native `QSpinBox`/`QDoubleSpinBox` (22 across
   settings_page.py, 1 in clip_config_row.py) swapped to
   `CustomSpinBox`/`CustomDoubleSpinBox`. A genuinely custom dropdown
   (its own popup-list widget, matching the search bar's own text-
   bubble attachment) remains a separate, bigger undertaking not
   attempted here -- this covers the "custom LOOK" for every combo box
   in Settings via styling, not a full custom widget rebuild. Verified
   directly: all 22+ spinboxes in Settings are the Custom classes, a
   value round-trips correctly through save/reload, and every combo
   box in both Settings pages carries the custom stylesheet.

### Previous session
Six of the seven items from the latest batch -- item 2 ("adjust all
of the settings things to be custom instead of KDE") is still
outstanding, the biggest remaining piece by far, not started this
round.

1. **"Add New" button in Settings > Filters** -- the Filters SETTINGS
   tab could only manage existing tags (icon, category, outline,
   rename), with no way to create a brand new one without leaving to
   the Library's own "+ Add Filter". Added.
2. **Context menu no longer closes when toggling a filter checkbox.**
   Root cause: `CustomCheckBox` is embedded via `QWidgetAction`, and
   the checkbox itself already receives and handles clicks completely
   normally (Qt delivers mouse events directly to whichever real
   widget is under the cursor) -- but `QMenu`'s OWN mouseReleaseEvent
   ALSO reacts to that same click by closing itself, independent of
   whatever the embedded widget did with it. New `_NonClosingMenu`
   subclass skips only that specific reaction (checking
   `self.activeAction()` is a `QWidgetAction`), leaving every other
   kind of click (a plain QAction, clicking outside any item)
   completely unaffected. Used for the Filters submenu and its
   category sub-menus. Verified directly against the class's own
   `mouseReleaseEvent` logic (no `close()` reaction for a widget
   action; normal handling otherwise).
3. **"Mark as Edited" added to the context menu** -- new
   `library.mark_as_edited()`, manually flips `has_edit` without a
   real trim/backup, for a video that's already edited from elsewhere
   or one not considered "raw" even though the app itself never
   touched it. Deliberately leaves `backup_path` alone (stays None) --
   `undo_edit()`/`clear_edit_backup()` already correctly refuse to act
   without a real backup regardless of `has_edit`, so this can't put a
   video into a state where Undo would try to restore from nothing.
   Shown as a bulk action (works across a multi-selection), only
   offered when at least one selected video isn't already marked
   edited. Verified directly, including that Undo still correctly
   refuses afterward.
4. **The wrong-dates bug -- found the missing piece and fixed it
   properly this time.** Last session's fix (using a file's mtime
   instead of "now" during re-ingestion) only prevents the bug from
   corrupting NEW data going forward -- it does nothing for videos
   that were ALREADY corrupted by the mass-false-positive prune bug
   before that fix existed. New `library.repair_incorrect_creation_dates()`,
   called once at every LibraryPage startup (cheap/safe to call
   repeatedly -- already-correct videos are a no-op): for every video,
   compares its stored `created_at` against its file's own mtime, and
   corrects it when the file is impossibly OLDER than its claimed
   creation date by more than an hour (a physical impossibility for a
   genuine original capture, but exactly what a bad re-ingestion looks
   like). Caught a real edge case in this fix while testing it, not
   after: a completely fresh, correctly-dated video can have its file
   finish writing a few MILLISECONDS before its DB row gets created
   (the file write always completes just before `add_video()` runs),
   which an exact `mtime < stored` check with zero tolerance flagged
   as "wrong" too -- added a generous one-hour tolerance, since the
   capture pipeline's real gap is always well under a second, while an
   hour still easily catches the actual multi-day/week corruption
   pattern. Verified both directions: a deliberately backdated file
   gets its date corrected, and a genuinely fresh video's correct date
   is left completely alone on a second repair pass.
5. **A shared, custom "Add Filter" dialog with a category dropdown.**
   Replaced the plain `QInputDialog.getText()` used in THREE places
   (the Library's own "+ Add Filter", the context menu's Filters
   submenu, and this session's new Settings button) with one
   `AddFilterDialog` (new file, add_filter_dialog.py) -- custom-styled
   (theme-colored background, `CustomLineEdit`/`CustomButton`
   throughout) and lets a category be assigned right at creation
   instead of needing a second trip to Settings > Filters afterward.
   The dropdown itself is a QSS-reskinned `QComboBox` (same technique
   as last session's QMenu reskin -- theme colors, rounded corners),
   not yet a fully custom popup-list widget of its own; that remains
   part of item 2's still-outstanding scope. Verified end-to-end: the
   dialog's own result values round-trip correctly, and simulating an
   accepted dialog through `LibraryPage._add_new_filter()` actually
   creates the tag AND assigns it to the chosen category.
6. **Previewer video titles are now editable in place.** Clicking the
   title swaps it for a `CustomLineEdit` pre-filled with the current
   title; committing (Enter, or clicking away -- both go through
   `editingFinished`, which fires for either) calls
   `library.rename_video()` and swaps back to the label showing
   whatever the result actually is. Caught a real construction-order
   bug while testing this, not a design mistake caught after the fact:
   the event filter watching for a click on the title label can fire
   DURING `__init__` (a layout/show event on the label itself, before
   `self.video_widget` exists a few lines later in the same
   constructor) -- the filter's OTHER branch checked `obj is
   self.video_widget` unconditionally, which crashed outright the very
   first time the dialog was ever constructed. Fixed with
   `getattr(self, "video_widget", None)` instead of the bare
   attribute access. Verified the full cycle: clicking swaps to an
   editable field pre-filled correctly, committing writes to the DB
   and swaps back to the label showing the new title, and re-editing
   with no actual change made doesn't error or do anything unexpected.

### Previous session
Finished the item de-prioritized last round (once the data-loss
investigation was done): tag icons now show directly in the dropdowns
themselves, not just on the video card.

**`CustomCheckBox` gained an optional `leading_icon` parameter** --
shown between the checkbox indicator and its text label, distinct from
`_checkmark` (which shows INSIDE the indicator box to mark checked
state) and `FilterCheckBox`'s own block-state x icon. `FilterCheckBox`
passes it straight through to the base class. Wired into every place a
tag appears as a checkbox with an icon available to show:
- `_build_filters_menu` (video_card.py) -- covers BOTH the right-click
  context menu's Filters submenu and the quick-action Filters button's
  menu, since they share this one method.
- `_build_filters_page` (library_page.py) -- the Sort popover's
  Filters page, i.e. the actual search/filtering UI.

Both look up each tag's icon via the same `library.tag_icons()` used
elsewhere (video_card.py's own `_build_icon_row` for the on-card
display), falling back to no icon when a tag doesn't have one set or
its file's gone missing, same leniency as everywhere else in this
codebase that loads a user-chosen icon path.

Caught a real bug in my OWN test while verifying this, not a product
bug: `library.create_tag()` returns `None` (it's fire-and-forget, used
by callers that don't need the id back) -- an early draft of the test
assumed it returned the new tag's id and passed `None` straight into
`set_tag_icon()`, which silently updated zero rows (`WHERE id = NULL`
never matches anything in SQL). Fixed the test to look the id up via
`all_tags_with_ids()` instead, which is what surfaced the real,
correct behavior it needed to check in the first place.

Verified directly: a tag with an icon set shows up with
`_has_leading_icon() == True` on its `CustomCheckBox` in BOTH the
Filters menu and the Sort popover's Filters page.

### Previous session
**A real data-loss incident, root-caused and fixed, plus the durability
feature it directly motivated.** Reported: every video's tags/filters
gone (trims survived), and every video showing "created today" even
though most were captured well before. Also mentioned separately
having moved the entire clips folder out and back in a while back --
that's the actual trigger, confirmed directly.

1. **Root cause: `prune_missing_videos()` had no defense against a
   mass false-positive.** It checks `Path(row["path"]).exists()` for
   every video and unconditionally `DELETE`s any that fail -- which
   CASCADEs to `video_tags` via the schema's `ON DELETE CASCADE`.
   Moving the whole clips folder out (even briefly) makes EVERY video
   fail that check at once; the next prune pass then deletes all of
   them, and `scan_and_ingest_new_videos()` immediately re-discovers
   the same files (now back in place) as brand-new rows -- fresh ids,
   zero tags, and a `created_at` of "now" instead of whenever they
   were actually made. That exactly matches every symptom reported:
   tags gone, trims fine (the file itself was untouched, so the
   trimmed content survived re-ingestion), dates wrong.
   **Fixed with a safety net in `prune_missing_videos()`:** refuses to
   prune anything in a single pass if that would remove more than half
   the library at once (and always allows pruning up to 5 videos
   regardless, so a small library isn't stuck never able to prune a
   single genuinely-deleted video) -- logs an error and does nothing
   that pass instead, since a mass "everything just vanished" result
   is a far stronger signal that the CHECK itself is wrong (a
   transiently unmounted drive, a race at startup, a filesystem-
   visibility difference between the GUI and the newer daemon-offload
   scanning path) than that the user actually deleted most of their
   library between one refresh and the next. A single video going
   missing is completely ordinary and still pruned immediately, same
   as before. Verified directly, both directions: moving files away to
   simulate a mass false-positive is correctly refused (tags stay
   intact), and a single genuinely-deleted file is still pruned
   normally.
2. **The "created today" date bug -- separate but related, also
   fixed.** `add_video()` always stamped `datetime.now()` as
   `created_at`, which is correct for the capture pipeline's own call
   (a video OBS just finished recording) but wrong for
   `scan_and_ingest_new_videos()` re-discovering a file well after it
   was actually made. Added an optional `created_at` parameter
   (defaulting to `None` -> "now", so the capture pipeline's own
   behavior is unchanged) and had the scan function pass the file's
   own `st_mtime` instead -- the best available answer given only
   filesystem info to go on (survives an ordinary same-filesystem
   `mv`, which is exactly what happened here; would NOT survive a
   copy-then-delete or a cross-filesystem move, where the OS itself
   has no better answer either). Verified both the ingestion path
   (uses the file's real mtime, confirmed against a deliberately
   backdated file) and that the capture pipeline's own direct
   `add_video()` calls still default to "now" as before.
3. **New: a durable, human-readable manifest file outside the SQLite
   DB**, per the direct follow-up request once the root cause
   was found -- `library.write_library_manifest()` writes a plain
   JSON snapshot (title, tags, favorite, has_edit, dates, for every
   video) to `~/.config/afterglow/library_manifest.json`, called after
   every mutation that changes what it describes (tag add/remove,
   rename, favorite, delete, and both the scan and prune passes -- only
   when something in it actually changed, not on every no-op call).
   Deliberately NOT a live source of truth the app reads back from
   automatically, and deliberately not baked into the video files
   themselves (renaming already happens today when a title changes --
   see `rename_video`'s existing file-rename logic -- but going further
   and encoding tags/edited-status into filenames or container
   metadata would fight the app's own filename-matching logic
   throughout scan/prune/backup handling, for comparatively little
   benefit over a plain external backup file): this is a recovery
   reference a person can open and manually cross-check or restore
   from by hand if the database itself is ever lost or corrupted again,
   which is a substantially simpler and more robust thing to get right
   than an automatic two-way sync between two sources of truth would
   be. Verified directly: the manifest is created and correctly
   reflects a tag addition, and updates correctly on both rename and
   delete.

**Not done this round:** item 3's follow-up (showing filter icons
directly IN the dropdown/checkbox rows themselves -- context menu,
quick-action Filters menu, and Sort popover filtering) was
de-prioritized this session in favor of the data-loss investigation
and fix, which took priority once reported.

### Previous session
Four more items, all found/fixed/verified.

1. **Defensive fix to `resource_qpixmap`'s new cache** -- reported
   directly that filter icons had gone invisible after last round's
   caching change. Couldn't find a code path where the caching itself
   would break a per-tag custom icon (those load via a completely
   separate `QPixmap(path)` call, not `resource_qpixmap`), but the
   caching COULD plausibly permanently lock in a transient failure for
   any BUNDLED icon that DOES go through it (e.g. if something
   requested an icon before a QApplication/QGuiApplication fully
   existed, which can make Qt hand back a null pixmap without raising)
   -- something the old always-reload behavior would have naturally
   "healed" from on the very next call. Fixed regardless of whether
   that's the exact mechanism at play here: `resource_qpixmap` now only
   caches a SUCCESSFUL (non-null) load, so a failed one just retries
   (and possibly fails again) on the next call instead of being
   silently broken forever. Verified directly that requesting a
   nonexistent file returns a null pixmap without polluting the cache.
2. **Context menu and Filters submenu -- both now custom-styled.** Both
   already used `CustomCheckBox`/`CustomButton` internally (from an
   earlier session), but the surrounding `QMenu` chrome itself
   (background, hover highlighting, borders, submenu appearance) was
   still native/KDE styling -- what "not custom" actually meant here.
   Added a shared `_menu_stylesheet()` (QSS: card-background fill,
   accent-colored border and hover highlight, rounded corners) applied
   to the right-click context menu, the Filters submenu, AND each
   category sub-menu nested inside it -- Qt does NOT cascade a parent
   QMenu's stylesheet down into its child QMenus automatically, so
   each one needs it applied individually. QSS is the standard,
   supported way to reskin QMenu without losing its own built-in
   submenu/keyboard-navigation/hover machinery, which would have been
   substantial to rebuild from scratch for comparatively little gain
   over reskinning the existing one.
3. **Icon preview added in Filters settings**, next to the filename --
   `_TagIconRow` used to show only the filename text; now a real 24x24
   scaled preview of the actual icon file sits next to it, updated on
   every browse/clear, falling back to no preview if the path is empty
   or the file's gone missing. Verified directly against a real icon
   file.
4. **Previewer's video now has rounded corners matching its frame's
   border**, which was already rounded while the video itself stayed
   sharp-cornered inside it. `MpvVideoWidget` is a `QOpenGLWidget`, so
   a normal paintEvent-based rounded clip doesn't apply to its own
   GL-rendered content -- used `setMask()` with a `QRegion` instead,
   which clips at the native surface level regardless of how the
   content was drawn. Installed as an event filter watching for
   `QEvent.Resize` on the previewer's own video widget instance,
   rather than touching `MpvVideoWidget` itself (shared with the
   Editor, which was never asked to round its own video corners).
   Verified the resulting mask is non-empty and genuinely excludes the
   corners (not equal to the widget's full rectangular bounds).

### Previous session
The big one this round: the actual cause of the ~5 second startup
time and the returned "click Library multiple times" bug, confirmed
directly and fixed -- plus the quick-action-button shape, the
previewer's fade in/out, and its size reverted back to fixed pixels.

**Startup performance -- confirmed root cause, fixed at both layers.**
the initial diagnosis was exactly right: `resource_qpixmap()`/
`resource_qicon()` (afterglow/gui/resources/__init__.py) had NO
caching at all -- every single call re-read the file from disk AND
re-decoded the full-resolution PNG from scratch, even for the exact
same file requested by many different widgets (a filter icon or
`CustomCheckBox`'s checkmark, for example, loaded fresh for every
single VideoCard/checkbox instance). On top of that, several of the
actual PNG files provided across recent sessions were
enormously oversized for how they're ever displayed on screen --
`checkmark_icon.png` was 1920x1920 (577KB), the four new action icons
were all 2048x2048 (up to 421KB each), `volume_speaker_icon.png` was
2048x2048 at 1.26MB, several sidebar/tab icons were 2048x2048 too --
all rendered at roughly 20-140px on screen. Both problems compounded:
no caching meant these huge images got decoded over and over, and
each individual decode was itself far more expensive than it needed
to be. Fixed both layers:
1. Added a module-level cache (`_pixmap_cache`/`_icon_cache`, keyed by
   filename) to both functions -- safe since nothing downstream
   mutates a pixmap it gets back from these (every effect --
   `hue_shift_pixmap_cached`, `tint_pixmap_cached`,
   `set_icon_pixmap`, etc. -- already treats these as read-only source
   images and returns a NEW pixmap for anything that needs to look
   different).
2. Resized the actual on-disk files, per the suggested target
   sizes: small UI icons (checkmark, x, search/refresh/sort,
   edit/copy/filters/delete, volume speaker) down to 128x128; sidebar/
   tab full-art icons (library/editor/settings/local/uploaded videos)
   and the editor's `bar_marker.png` texture down to 256x256. Left the
   already-appropriately-sized 512x512 gradient textures (stretched
   across full card-sized backgrounds) and `app_icon_source.png`
   (never loaded by the running app at all -- only used at build time
   to generate the desktop icon set) untouched. Total resources folder
   dropped from several megabytes to about 2.2MB.
   Verified concretely, not just "should be faster now": confirmed
   `resource_qpixmap()` now returns the literal same object on repeat
   calls (not a fresh decode); confirmed every runtime-loaded PNG is
   now under 512px in its largest dimension; and measured actual
   construction time directly -- `SettingsPage()` (the initial suspicion
   for why Settings specifically was slow, given how many
   `CustomCheckBox`es it has) now constructs in ~51ms. `LibraryPage()`
   with 20 videos still takes real time (~850ms) -- that remaining
   cost is VideoCard construction itself (thumbnails, gradient
   rendering, etc.), a separate, already-flagged-once concern
   ("lazy-load the grid") rather than anything to do with icon
   loading, and NOT something this fix was trying to solve -- flagged
   here rather than silently left unmentioned.
2. **Quick-action buttons -- now real circles, not tall rectangles.**
   They were sized via `setMinimumHeight(48)` alone, with no width
   constraint -- a `QHBoxLayout` stretches each button to fill
   available row width, so they rendered as wide rectangles with a
   small icon centered in the middle of mostly-empty space ("barely
   visible"). Switched to `set_circular(56)` -- the same mechanism the
   Search/Refresh/Sort header buttons already use -- which fixes both
   the shape AND the size in one call (slightly larger than the old
   48px, per the "slightly increase the size"). Also reduced
   `CustomButton`'s own icon margin fraction (0.2 -> 0.14, applies to
   every icon-bearing CustomButton, not just these four) so icons fill
   more of whatever shape they're drawn in generally. Verified pixel-
   level that these are genuinely circular (corner color differs from
   fill), not just a big rounded-corner rectangle.
3. **Preview overlay now fades in and out**, instead of appearing/
   disappearing instantly. Fade-in is fast (90ms, "very quickly as to
   be responsive"); fade-out is slower (220ms, "normal speed") --
   both via one `QGraphicsOpacityEffect` on the overlay itself
   (covers the scrim and the content box together, so they fade as
   one unit, not separately). `close_overlay()` now takes an
   `immediate` flag: a normal user-initiated close (scrim click,
   Escape) fades out first, THEN actually hides/cleans up; MainWindow
   replacing an already-open overlay with a fresh one (a second
   preview request arriving before the first closed) uses
   `immediate=True` to skip the fade entirely, since fading the old
   one out while a new one fades in on top would just look like two
   overlapping scrims rather than a clean swap. Verified directly:
   opacity starts near 0 and animates to 1 on show; closing (non-
   immediate) stays visible and holds full opacity briefly before
   actually animating down, and the overlay only truly closes (its
   `closed` signal fires, triggering MainWindow's own cleanup) once
   that fade-out completes.
4. **Preview content size reverted to a fixed pixel size**, per the
   direct request -- back to 1581x1035 (the same value from before the
   proportional-sizing overlay rewrite), rather than a percentage of
   whatever window it's embedded in. Still clamped to fit the overlay's
   own bounds (`min(CONTENT_WIDTH, 97% of overlay width)`, same for
   height) so it can't overflow a genuinely smaller window.

### Previous session
Two pieces landed and packaged so far -- a critical regression fix
(the previewer redesign from last session broke in exactly the ways a
separate top-level window would be expected to), and the action-button
icons provided. Four more items (page-load lag, the still-invisible
outlines, the Filters tab's own remaining issues, and custom Settings
widgets) are queued but not started yet this round -- see "Next up".

**Video previewer -- rebuilt as an embedded overlay, not a top-level
window.** Reported after the previous fix: fully detached from the
main window, clicking the background did nothing, and it was even
possible to open two at once. All three are exactly the failure modes
of a genuinely separate OS window (a modal dialog can make the window
manager swallow clicks on whatever's behind it before the app ever
sees them; nothing prevented a second `.show()` from creating a second
one). Per the initial suggestion ("put the window in the main window and
size it proportionally"): `VideoPreviewDialog` (a `QDialog`) is now
`VideoPreviewContent` (a plain `QWidget`) wrapped by a new
`VideoPreviewOverlay`, which is a direct CHILD of MainWindow's central
widget -- not a top-level window at all. The overlay paints a semi-
transparent scrim over the current page and sizes the content box to
85% of whatever its own size is (kept matched to the central widget's
full size via `MainWindow.resizeEvent`), so "click outside" is now a
completely ordinary `mousePressEvent` within the SAME window, and
MainWindow tracks the one active overlay itself (`_show_preview_overlay`
replaces rather than stacks). The open request now bubbles up through
a `preview_requested` signal (VideoCard -> `_VideoGridTab` ->
`LibraryPage` -> MainWindow), the same pattern `edit_requested` already
used, rather than VideoCard constructing anything directly (it has no
reference to MainWindow to embed into). Caught two real bugs while
verifying this, not just trusting it worked: a dropped `QTimer` import
from the refactor that would have crashed the very first time a
preview was opened (autoplay's delay logic depends on it), and a
version of a bug already documented once before in this codebase --
`deleteLater()`'s deletion is deferred to the next event-loop pass, so
replacing an already-open overlay needs an explicit `hide()` too, or
the old one stays visibly on screen for that brief window. Verified
directly: the overlay is a real child of the central widget (not a
top-level window), sized to match it, with the content box
proportionally smaller and centered; clicking the scrim closes it;
opening a second preview replaces the first with the old one hidden
immediately, not left visible until Qt gets around to deleting it.

**Video-card action-button icons.** The 4 icons provided (pencil/
copy/funnel/trash) replace the Edit/Copy/Filters/Delete text labels on
the action-buttons row -- shown at their own original colors (not
retinted, unlike the Search/Refresh/Sort toolbar icons, since these
are individually-branded action icons rather than icons meant to
blend into the text color system). Verified all 4 buttons carry a
real, non-null icon pixmap and that the underlying handlers (checked
directly via Edit) are unchanged.

### Previous session
Continuing straight from last round's four bug reports -- three more
came back with follow-ups (one fully explained, one root-caused
further, one flagged as "still not visible"), plus four new items.

1. **Autoplay doubled the audio -- root cause was the SAME reload
   workaround from last session's fix, one layer deeper.** Last
   round's fix made `_reload_first_video` preserve whatever pause
   state was already in effect instead of forcing paused -- correct,
   and it did stop the "plays then stops" symptom. But that reload
   still re-issues `self._mpv.play(path)` (a genuine restart of the
   file) 150ms after the original load, and if OUR OWN explicit
   `play()` had already started real audio output by then, the reload
   briefly overlapped a second copy of it starting on top -- "doubles
   up on the audio... jarring." Per the initial suggested fix (delay the
   autoplay a little): `VideoPreviewDialog._load_video()` now checks
   `MpvVideoWidget._first_load_done` and, ONLY on the very first video
   ever loaded into a fresh widget (Prev/Next never re-trigger the
   workaround), delays its own `play()` call via
   `QTimer.singleShot(200, ...)` -- comfortably past the 150ms reload
   -- so there's only ever one play command in flight by the time
   audio actually starts. Subsequent loads (arrows) still play
   immediately, unchanged.
2. **Click-off-close STILL wasn't working -- found the actual reason.**
   The event-filter logic itself was correct, but the dialog was
   opened MODALLY (`.exec()`) -- a modal window can make the OS/window
   manager swallow clicks on whatever's behind it entirely (a shake or
   a beep, no real `QMouseEvent` ever delivered to the app), so the
   filter's own geometry check never even got a chance to run for
   those clicks. Switched to non-modal (`.show()`), with a live
   reference kept on the originating `VideoCard`
   (`self._active_preview_dialog`) so the Python wrapper isn't
   garbage-collected the moment the opening method returns.
3. **Selection replacing the unedited-highlight border -- confirmed
   and fixed.** `_render_background`'s video-thumbnail-border branch
   was gated on `if not selected and show_highlight`, treating
   selection and the highlight as mutually exclusive -- so selecting
   an unedited video silently swapped its gradient border for the
   plain accent() one. Changed to gate on `show_highlight` alone;
   selection's own outer ring (built separately, unchanged) is layered
   on top of whichever thumbnail border is already showing, exactly as
   it always should have been. Verified directly: an unedited video's
   highlight border is now confirmed present both before AND after
   selecting it.
4. **Selection snapping instead of fading -- implemented a real
   cross-fade.** `VideoCard` now keeps the pre-change cached
   background (`_fade_from`) alongside the newly-rendered one, and
   blends between them over a 200ms `QVariantAnimation` (draw the old
   pixmap, then the new one on top at partial opacity) rather than an
   instant swap -- both are already-cached bitmaps, so each frame of
   the fade is just two cheap blits, not a re-render. Caught a real
   bug in this fix while verifying it, not just trusting the animation
   "ran": the fade's progress value needs to be reset to 0.0
   SYNCHRONOUSLY the moment the fade starts, not left for the
   animation's own first tick, since `QVariantAnimation` doesn't
   guarantee emitting its first `valueChanged` synchronously within
   `start()` -- without the explicit reset, a repaint landing before
   that first tick would still show the OLD, fully-settled progress
   value from whatever fade last completed, silently skipping the
   blend for that frame.
5. **Page switch lag, still present -- removed the automatic Library
   refresh entirely, per the suggestion.** Both of last round's
   fixes (a faster crossfade, deferring the refresh call) only changed
   WHEN or HOW FAST the rebuild happened, never WHETHER it happened --
   rebuilding potentially hundreds of VideoCards is real, unavoidable
   work no amount of scheduling trickery removes. Per "maybe just
   leave the pages loaded after switching off of them": `_on_nav_clicked`
   no longer calls `library_page.refresh()` at all. Switching to
   Library is now a plain, instant page swap; the page stays exactly
   as it was until something ACTUALLY changes it -- the existing
   DB-file-watcher (already there, catching daemon/Editor writes) or
   the Library's own manual Refresh button.
6. **Sidebar and page outlines still not visible -- likely cause
   found: paint ORDER, not the margin fix from last round.** Every
   affected `paintEvent` was drawing the border FIRST, then calling
   `super().paintEvent(event)` SECOND. On a real KDE/Plasma-integrated
   Qt style (unlike this sandbox's offscreen platform), the base
   `QWidget.paintEvent()` can genuinely paint an OPAQUE background of
   its own -- if that ran after the border, it would silently erase
   it, matching a bug CLASS already seen once before in this exact
   codebase (a sandbox-vs-real-compositor rendering difference).
   Reordered all FIVE affected `paintEvent`s (the four pages from last
   round, plus the sidebar itself -- new `_Sidebar(QWidget)` subclass
   in main_window.py, since "sidebar" was explicitly listed as still
   missing one this round) to call `super().paintEvent()` FIRST and
   draw the border SECOND, guaranteeing the border is always the last
   thing painted regardless of what the base class does on any given
   platform. Verified this doesn't regress anything in this sandbox
   (all prior pixel checks still pass) and added a matching check for
   the sidebar itself -- genuinely can't confirm this is the real
   fix without the real machine, flagged as such.
7. **Sort popover's Filters-tab padding -- actually fixed this time,
   found the real cause.** Last round's fix (cap each page's own
   wrapping QScrollArea to `min(sizeHint, 320)`) was necessary but not
   sufficient: `_RoundedContentArea` (the `QStackedWidget` holding all
   three pages) had no `sizeHint()`/`minimumSizeHint()` override, so
   it fell back to `QStackedWidget`'s own default -- which sizes to
   the LARGEST of ALL its pages, not just the one showing. A short
   Filters page (few tags) was still being stretched to whatever
   height the tallest of the three pages (often Sort By, with 8 fixed
   radio options) needed, leaving real unfilled space below its own
   content -- exactly the reported symptom, still present because the
   actual bottleneck was one level up from where last round's fix
   landed. Fixed by overriding both to return the CURRENT page's own
   size instead, and by having `SortPopover.set_current_index()`
   re-run `adjustSize()` so switching tabs while the popover is
   already open resizes it immediately rather than waiting for the
   next time it's reopened. Verified directly: with a single tag, the
   popover now sizes to ~159px (matching the Filters page's own
   ~127px sizeHint plus margins) instead of being forced toward the
   old ~320px regardless of content, and switching to the Sort By tab
   resizes it again to fit that page's own (taller) content.

**New items, all done:**
8. Previewer resized again, 1.15x on top of last round's 1.25x
   (1581x1035, was 1375x900).

### Previous session
Four direct bug reports on last session's work, all four were real
bugs, all four found and fixed -- plus a codebase-wide audit that
caught two MORE instances of one of them before they got reported
separately.

1. **Autoplay "plays for a moment then stops" -- real race condition,
   found and fixed.** `MpvVideoWidget` has a workaround for a known
   black-screen bug on the very first video played after the app
   opens: it reloads that first video a second time, 150ms later, via
   `_reload_first_video()`. That method unconditionally forced
   `pause = True` regardless of what the caller actually wanted --
   so the previewer's explicit `.play()` call (issued right after
   `load()`) would genuinely start playback, only for this delayed
   callback to silently undo it 150ms later. This only ever hit the
   FIRST video shown in a freshly-opened previewer (a fresh
   `MpvVideoWidget` each time means `_first_load_done` is always
   False on open) -- exactly matching "it DOES autoplay when you go
   to a new clip with the arrows" (no fresh reload-workaround firing
   on those) "but not the first time." Fixed by having
   `_reload_first_video` capture and re-apply whatever pause state was
   ACTUALLY in effect right before it fires, instead of hardcoding
   `True` -- this method exists purely to work around a rendering bug,
   it was never supposed to have its own opinion about play state.
   Verified both directions: the previewer's load-then-play sequence
   now survives the delayed reload, AND Editor's own default
   paused-on-load behavior (which never calls `.play()` after `load()`)
   is confirmed unchanged.
2. **Page switch lag -- a SECOND blocking call found sitting right next
   to the one fixed last session.** Last round's fix addressed
   `crossfade_to_index()` itself (no longer grabbing a snapshot before
   switching). Still reported as laggy, because `_on_nav_clicked` also
   calls `library_page.refresh()` (a full filesystem scan + every
   VideoCard rebuilt from scratch) or `settings_page.
   refresh_dynamic_lists()` SYNCHRONOUSLY, right alongside the page
   switch -- so even with the switch itself now fast, nothing could
   actually get painted on screen until that heavier call finished
   too. Fixed by deferring both with `QTimer.singleShot(0, ...)`, so
   Qt gets a chance to paint the already-switched page before the
   heavier refresh work runs. Verified with a simulated slow
   `refresh()` (artificially sleeping 300ms): clicking Library now
   returns in ~7ms instead of blocking for the full 300ms, and the
   deferred refresh is confirmed to still run afterward.
3. **Sort By tab missing its outline -- a real, and apparently
   recurring, Qt gotcha.** `Qt.NoPen` is a PEN STYLE, not merely "no
   color set yet" -- calling `.setColor()`/`.setWidthF()` on a pen
   that's still styled `Qt.NoPen` does nothing; the stroke still won't
   render regardless of what gets set on it afterward. `CustomRadioButton`
   drew its fill via `setPen(Qt.NoPen)` then tried to reuse that exact
   pen object (via `painter.pen()`) for the outline stroke -- which
   silently never drew anything. Audited the ENTIRE codebase for this
   same pattern (a small script scanning every `paintEvent` for
   `setPen(Qt.NoPen)` followed by a later `painter.pen()` call in the
   same method) and found it in two more places written this same
   recent stretch: `_FullscreenButton` (the corner brackets) and
   `_VolumeButton` (the speaker arcs/mute-X) in the video previewer --
   neither had ever actually been rendering their own icon detail,
   just an accent-colored circle. All three fixed by constructing a
   brand-new `QPen(...)` for the stroke pass instead of mutating the
   dead one. Verified pixel-level that the radio button's ring now
   actually appears (a broad "does the accent color show up anywhere
   on this widget" scan, not a single fragile coordinate guess).
4. **Page outlines not appearing -- likely root cause found (can't
   fully confirm without the real display, but fixed regardless).**
   The border-drawing code itself was correct, but it relied on
   whatever the ACTIVE QSTYLE's own default QLayout margin happens to
   be to leave room for the border -- which this sandbox's default
   offscreen-platform style happens to leave nonzero, but a real
   KDE/Qt style easily might not. If that margin were ever zero, child
   content (the scroll area, the mpv widget, Settings' own group
   boxes) would sit flush against each page's outer edge and
   completely paint over the border drawn beneath it in `paintEvent`
   -- matching a bug CLASS already documented once before in this
   exact codebase (a stylesheet-cascade difference between this
   sandbox's offscreen platform and a real compositor). Fixed by
   making the margin EXPLICIT (3px, matching `page_outline.BORDER_
   WIDTH`) on all four pages' own outer layouts, rather than hoping
   the ambient default happens to leave enough room. Caught a real
   bug of my OWN while wiring this in and testing it properly rather
   than assuming it worked: `settings_page.py` was missing the actual
   `BORDER_WIDTH` import (only `paint_page_outline` had been
   imported), which would have crashed SettingsPage outright on
   construction -- caught immediately by actually constructing one
   and checking, not left to hit.

### Previous session
A huge combined batch on top of the previewer's first version --
previewer polish, two real bugs found and fixed, three new custom
widget classes, and page-level outlines. Grouped by area.

**Video previewer polish (all the items from this round):**
- Title now centered and larger, with tags/length/size/date collapsed
  onto ONE line directly beneath it, both inside a new `_CardBox`
  (rounded, `card_background()`-colored) -- matches a Library card's
  own info-box treatment rather than being plain dialog chrome.
- Speed control is now `CustomDoubleSpinBox` (see below) instead of a
  plain `QDoubleSpinBox`.
- Close button removed entirely.
- Resized to 1.25x (1375x900, was 1100x720).
- Real rounded window corners: `Qt.FramelessWindowHint` +
  `WA_TranslucentBackground`, painted directly in the dialog's own
  `paintEvent` (falls back to square when fullscreen, since a
  fullscreen window covering the whole screen has no corners to round
  against anyway).
- The video itself sits inside a new `_VideoFrame`, bordered in
  `accent()` at `unedited_selected_border_width` thickness -- the same
  border a Library thumbnail gets by default (see video_card.py's
  plain, non-highlighted video-box fill).
- Transport controls (play/pause, scrubber, volume, speed, fullscreen)
  now sit inside their own `_CardBox` "protrusion," matching the
  header's treatment.
- Click-anywhere-outside now closes the dialog -- an app-wide
  `eventFilter` installed in `showEvent`/removed in `hideEvent`
  (checking whether a `QEvent.MouseButtonPress`'s global position
  falls outside `self.geometry()`), not a `Qt.Popup` window flag: a
  Popup's own mouse-grab behavior is meant for lightweight, momentary
  content like SearchBubble/SortPopover, and would have been fragile
  for a window this complex (mpv embedding, sliders, a spinbox).
- Prev/Next arrows added, cycling through whichever Library tab/sort
  the video was opened from -- reuses the EXACT same
  `neighbor_provider` shape MainWindow already passes to the Editor
  (`_VideoGridTab.neighbors`), just plumbed through a new
  `VideoCard.__init__(..., neighbor_provider=...)` parameter instead,
  since the previewer opens directly from a card rather than through
  MainWindow's own nav. `_VideoGridTab.refresh()` now passes
  `neighbor_provider=self.neighbors` when constructing each card.
  Navigating calls the dialog's own `_load_video()` (resets title,
  info, mpv source, speed, and re-queries neighbors) rather than
  opening a second dialog instance.
- **Real bug found and fixed: autoplay wasn't actually autoplaying.**
  `MpvVideoWidget.load()` always loads paused by design ("Editor
  decides whether/when to auto-play") -- the previous version only set
  the Play/Pause BUTTON's visual checked state to true without ever
  calling `.play()` on the underlying widget, so the video sat there
  paused despite the button showing "playing." Fixed by calling
  `self.video_widget.play()` explicitly right after `load()`.
- **Real perf bug found and fixed: switching to Library or Settings
  took a perceptible delay to even BEGIN, reported directly ("Editor
  is fine").** Traced to last session's `crossfade_to_index()`: it
  called `old_widget.grab()` -- a full synchronous re-render of the
  ENTIRE outgoing widget subtree -- BEFORE switching pages. For a big
  Library grid (many VideoCards) or a Settings page full of custom-
  painted controls, that grab could take long enough to be a
  perceptible blocking delay before any visual change happened at all,
  even though each widget's own paint is individually cheap (cached).
  Editor's simple layout never had enough content for the cost to be
  noticeable. Rewrote `crossfade_to_index()` to switch FIRST (instant,
  as it should be) and fade the already-current new page in via
  `QGraphicsOpacityEffect` instead of grabbing-then-fading-out a
  snapshot of the old one -- no pre-switch render cost at all.
  Verified directly: the function call itself now completes in ~1ms
  for a 500-widget page, and the page has already switched by the time
  it returns.

**New custom widgets:**
- `custom_spinbox.py` -- `CustomSpinBox`/`CustomDoubleSpinBox`. Native
  up/down arrows hidden (`setButtonSymbols(NoButtons)`); two small
  custom-painted triangle buttons positioned over the box do the same
  job via `stepBy()`, which is what both spinbox variants already
  implement to apply one step correctly regardless of range/wrapping,
  so the buttons don't need separate int/float logic. The box itself
  reuses the same rounded/accent-outlined/card-background-filled look
  as `CustomLineEdit`. Used so far by the previewer's speed control;
  ready to drop into Settings' own numeric fields next.
- `custom_radio_button.py` -- `CustomRadioButton`. Same
  card-background-box + checkmark-icon look as `CustomCheckBox`, just
  circular instead of rounded-rect, matching the conventional
  round-vs-square distinction between radio buttons and checkboxes.
  Replaces the Sort By tab's `QRadioButton`s -- exclusivity still
  enforced by the same `QButtonGroup` as before.
- `page_outline.py` -- `paint_page_outline()`. A plain 3px,
  15%-darker-than-itself outline for a "page" widget, with
  `skip_top`/`skip_bottom`/`skip_left`/`skip_right` for whichever edge
  touches something else with no gap. Applied to `_VideoGridTab`
  (Local/Uploaded -- `library_background()`, skipping the top edge
  since it's flush against the Library header right above it, per
  "make sure this doesn't bleed into the middle where they combine"),
  `LibraryPage` itself (full outline, same color), `EditorPage` and
  `SettingsPage` (both `app_background()`, full outline -- neither
  page sets an explicit background of its own, so that's the
  "itself" being darkened for each). Verified pixel-level: Local's
  left/bottom edges show the exact darkened color, its top edge does
  NOT, and LibraryPage's own top edge DOES (the "outer" page, not
  skipping anything).

**Sort popover fixes:**
- **Filters tab padding bug -- root cause found.** Every popover page
  was forced to a fixed 320px height regardless of actual content
  (`_wrap_scrollable`'s old `scroll.setFixedHeight(320)`). Combined
  with `setWidgetResizable(True)`, a SHORT page (few tags) got
  stretched to fill that full 320px anyway, and its own trailing
  `addStretch(1)` was then filling real, visible dead space at the
  bottom -- reported as "unnecessary padding... isn't even filled."
  A LONG page (many tags) genuinely exceeding 320px was a separate,
  correctly-scrolling case that just happened to look like the same
  complaint from the outside. Fixed by computing each page's actual
  `sizeHint().height()` and using `min(that, 320)` instead of always
  320 -- short pages now size to their own content (no wasted
  padding), long pages still cap at 320 and scroll as before (that
  part was never actually broken).
- Sort By tab's radio buttons are now `CustomRadioButton` (see above).

**Not done this round:** applying `CustomSpinBox`/`CustomComboBox` to
Settings' own numeric fields and dropdowns -- the spinbox class is
built and proven (used in the previewer) but not yet swapped into
Settings itself; a custom combo box (its own popup list, text-bubble
attachment style like search) still isn't started at all.

### Previous session
Continuing straight from the last handoff -- same batch, picking up
where it left off after packaging what was already done.

**Video previewer -- first working version.** New
`video_preview_dialog.py`, `VideoPreviewDialog(QDialog)`. Opens on a
plain (unmodified) left-click directly on a card's thumbnail; shows
title (with the favorite star if set), tags, and length/file-size/
date, alongside a real embedded player reusing `MpvVideoWidget` (the
exact same embedding editor_page.py uses) at a much larger size than
a Library card -- play/pause, a seek scrubber, volume + mute, a
fullscreen toggle, and a Watch Speed spinbox mirroring the Editor's
own (same range/step/decimals/suffix, same "resets to 1x, preview-
only" behavior). No trimming -- this is read-only playback, distinct
from the Editor.

No answer was ever given to the two clarifying questions asked before
starting (click behavior vs. the existing multi-select; Local-only vs.
Uploaded scope), so both were decided as reasonable defaults rather
than continuing to block:
- **Scope: Local videos only for now.** Uploaded already has its own
  separate, previously-speced double-click -> embed/fallback-to-
  YouTube behavior; this new dialog doesn't touch that.
- **Click behavior, and a real conflict found while implementing it:**
  a plain left-click on a card ALREADY drives multi-select (unchanged
  here), and double-click was ALREADY wired to open the Editor
  (`VideoCard.mouseDoubleClickEvent`) -- discovered while
  investigating exactly where "left click" could safely hook in
  without breaking either. Landed on: an UNMODIFIED (no Ctrl/Shift)
  left-click, only when it lands on the thumbnail specifically (not
  the info box or action buttons), opens the preview -- but only
  after a 250ms delay, cancelled if a genuine double-click arrives in
  that window. Ctrl/Shift-clicks (multi-select) never trigger it.
  This is the standard Qt technique for disambiguating a single click
  from the first half of a double-click, since Qt has no built-in
  event that already tells you which one you're in until the second
  press either does or doesn't arrive. Verified all three cases
  directly with synthesized mouse events: a plain click opens the
  preview after the delay and does NOT fire edit_requested; a
  double-click fires edit_requested (opens the Editor) and does NOT
  also flash the preview open first; a Ctrl-click never opens the
  preview at all.

Play/pause, fullscreen, and the volume/mute button are all custom-
painted (a triangle/two bars, four corner brackets, a speaker cone +
arcs) rather than needing a provided icon asset for any of them --
per the "see which of these you can do yourself." The scrubber
and volume level are real `QSlider`s (dragging, click-to-seek, and
keyboard stepping all come for free from that) recolored via
stylesheet to match the theme, rather than built from scratch.

Caught one real bug immediately via the test suite, not by luck:
`MpvVideoWidget.is_paused` is a `@property` (confirmed directly from
editor_page.py's own usage, which never calls it with parentheses) --
an early draft of this dialog called it as `is_paused()`, which would
have crashed on the very first Play/Pause click. Fixed both call
sites before this ever reached a real run.

Verified end-to-end against a real ffmpeg-generated clip (with a
stubbed-out `mpv` module tracking every property write, since this
sandbox has no libmpv): play/pause actually flips the underlying
`pause` property both ways; the volume slider drives `set_volume`
exactly; mute/unmute round-trips through the last non-zero volume
correctly; the speed spinbox drives `set_speed`; dragging the
scrubber and releasing it calls `seek()` at the mathematically
correct position for the fraction dragged to.

**Not done this round, still deferred:** custom spinboxes/dropdowns
in Settings (unchanged from last note -- still the biggest remaining
piece, a real dropdown needs its own popup list styled like the
search bar's text-bubble attachment).

### Previous session
Five more items from the latest feedback round, all implemented and
verified:

1. **Smooth scrolling everywhere**, not just the Library grid. Every
   `QScrollArea` instantiation across the app (filters_settings_page.py
   x2, settings_page.py, stats_settings_page.py, and the Sort
   popover's own `_wrap_scrollable` in library_page.py) now uses
   `SmoothScrollArea` instead of plain `QScrollArea` -- a one-line swap
   at each call site since `SmoothScrollArea` only overrides
   `wheelEvent`, nothing else about `QScrollArea`'s API changed.
2. **Found and fixed the actual cause of the action buttons' "inconsistent
   outline."** `CustomButton`'s outline was being stroked on a
   SEPARATELY inset copy of the button's rect, with the corner radius
   ALSO independently reduced by the same inset amount
   (`max(0, radius - inset)`). Insetting a rect and shrinking its
   corner radius by the same linear amount does NOT produce a
   concentric rounded shape -- straight edges scale one way, corner
   arcs scale differently -- so the gap between the fill's rounded
   corner and the outline's own (separately-computed) rounded corner
   visibly widened or narrowed right at each corner, while staying
   constant along the straight edges. That inconsistency IS what
   looked "off." Fixed by tracing the outline stroke on the EXACT SAME
   rect + radius the fill already uses -- Qt centers a stroke on its
   own path by default, so the pen's outer half simply has no widget
   area left to draw into (invisible, not distorted) rather than
   needing a manual inset at all. Verified at the code level that the
   fill's clip path and the outline's stroke path are now built from
   one identical `rounded_rect_path(rect, radius)` call, not two
   separately-computed ones -- a more reliable check than pixel
   measurements here, since "is this shape geometrically concentric"
   is exactly what the bug was about.
3. **Custom group box headers** -- new `custom_group_box.py`,
   `CustomGroupBox`. A drop-in replacement for the exact
   `QGroupBox("Title")` + `QVBoxLayout(group)` construction pattern
   already used everywhere (Qt seeds a newly-attached layout's margins
   from the widget's own `contentsMargins`, which `CustomGroupBox`
   sets in `__init__` to reserve room for its own painted title, so
   no call site needed to change beyond the class name itself).
   Replaces every `QGroupBox` in settings_page.py and
   filters_settings_page.py, AND the Sort popover's tag-category
   grouping in library_page.py's `_build_filters_page` -- the "sort
   tab" headers that were meant. Rounded, accent-colored border with a
   punched-out gap behind the title text (same idea as a native
   groupbox's own title notch), filled with the actual palette window
   color so the border doesn't visibly run behind the text.
4. **Clip Options now glide open/closed**, synced to the SAME
   `_ANIM_DURATION_MS` (180ms) and easing curve the `>` arrow's own
   rotation already uses (imported directly from
   `collapse_toggle_button.py` rather than a second hardcoded 180,
   so the two can't silently drift out of sync later) -- animates the
   body's `maximumHeight` from 0 up to its natural `sizeHint()` height
   (or the reverse), rather than the old instant
   `setVisible(expanded)` teleport. The surrounding `QVBoxLayout`
   reflows everything below the row smoothly frame-by-frame as a
   direct result, since animating maximumHeight (not a one-shot
   resize) is what gives the layout something to keep re-measuring
   against on every frame. Releases the height cap entirely once an
   expand finishes (so a later content change, e.g. picking a longer
   sound file path, isn't stuck capped at that one snapshot), and
   hides the body entirely once a collapse finishes (matching the old
   behavior's end state). Verified both directions actually reach
   their correct end state, not just that an animation started.
5. **Fade between page changes** -- new `crossfade_to_index()` in
   scale_reveal.py, used for both Local<->Uploaded
   (`LibraryPage._switch_page`) and the main sidebar's Library/Editor/
   Settings (`MainWindow._on_nav_clicked` and `_open_in_editor`). Same
   "don't fight the real widget's layout, animate a disposable
   snapshot on top of it instead" principle `ScaleRevealOverlay`
   already established: the actual page switch
   (`QStackedWidget.setCurrentIndex`) happens immediately and
   normally, and only a grabbed snapshot of whatever USED to be
   showing gets overlaid on top and animated to transparent, revealing
   the already-fully-correct new page underneath as it fades --
   rather than attempting a true two-layer cross-blend, which would
   need both pages' geometry animated simultaneously and would fight
   the stack's own layout the same way a direct scale animation would
   have. Verified the overlay appears immediately after a page switch
   and cleans itself up once the fade finishes.

Hit one real bug while writing item 5 (not a product bug, a mistake in
my own edit): a `str_replace` meant to insert `crossfade_to_index`
before `animate_popup_from_point` accidentally consumed that
function's own `def` line, leaving its body orphaned under the wrong
function -- caught immediately by the very next compile check (a
plain `ImportError`, not a subtle runtime issue), fixed by restoring
the missing `def` line.

**Not done this round, explicitly deferred:** custom spinboxes/
dropdowns in Settings (spinboxes need custom-painted increment/
decrement controls; dropdowns need a real popup list styled like the
search bar's own text-bubble attachment -- both meaningfully bigger
builds than anything else in this batch), and the video previewer
(queued from last session, no answer yet on the two open questions
about click behavior vs. the existing multi-select and Local-only vs.
Uploaded scope).

### Previous session
provided a real app icon (replacing the `library.png`-derived
placeholder from last session) and gave three more animation notes:

1. **New app icon applied.** Regenerated the full 9-size icon set
   (16 through 512) from the provided image via the same Pillow resize
   pipeline as before -- same `data/icons/hicolor/<size>x<size>/apps/
   afterglow.png` paths flake.nix's `postInstall` expects, just sourced
   from the real icon now instead of the placeholder. Verified each
   generated file is actually its claimed size.
2. **Settings animation removed.** The "grow out of the clicked tab
   button" overlay effect added last session (`reveal_from_point`,
   called from `_on_settings_tab_clicked`) is gone -- tab switching is
   back to a plain, instant `QStackedWidget.setCurrentIndex()`, per
   the direct "get rid of the animations in the settings."
3. **Search bubble now grows out of the Search icon**, matching what
   Sort already had. `SearchBubble.show_below()` now calls the same
   `animate_popup_from_point()` SortPopover uses, computing its usual
   final position/size and animating geometry + opacity from a small
   point at the Search button's own center up to it, instead of
   jumping straight there.
4. **Found and fixed a real bug while verifying #3 actually looked
   right, not just that SOME animation was running.** Both
   `SearchBubble` (`setFixedSize`) and `SortPopover` (`setFixedWidth`)
   had hard size constraints that silently clamped
   `animate_popup_from_point()`'s small starting geometry straight back
   up to the FINAL size the instant `setGeometry()` was called --
   meaning the animation was technically executing (the position
   component moved correctly) but the size never actually appeared to
   shrink, defeating the entire point of a "grow" effect. A SECOND,
   independent source of the same clamping was also found and fixed:
   even after removing those explicit constraints, each widget's own
   `QLayout` (a `QHBoxLayout`/`QVBoxLayout` with real child widgets --
   a line edit + checkbox for the bubble, tab buttons + a stack for the
   popover) was AUTOMATICALLY computing and enforcing a minimum size
   from those children's own size hints, which caused the exact same
   clamping via a completely different mechanism. Fixed both: replaced
   `setFixedSize`/`setFixedWidth` with plain `resize()` calls (a
   one-time hint, not a hard floor/ceiling), and set
   `QLayout.setSizeConstraint(QLayout.SetNoConstraint)` on both
   widgets' own top-level layouts so Qt stops trying to auto-derive a
   minimum size from their children at all -- safe in both cases
   specifically because `show_below()` always asserts an exact final
   geometry itself, so nothing actually depends on the layout's own
   size negotiation to pick these widgets' size. Verified by checking
   each popup's ACTUAL size in the very first frame after the
   triggering click (not after letting the animation run) -- both now
   genuinely measure roughly 24x24px at that moment, growing to their
   real final size (260x64 for the bubble, 320xcontent-height for the
   popover) only as the animation progresses. Two of my own smoke-test
   assertions had to be updated alongside this fix -- they'd been
   written checking geometry/pixel state immediately after a
   `show_below()` call, back when that was still instantaneous;
   they now wait for the animation to settle first, which is exactly
   what exposed this bug's fix needed verifying properly rather than
   just trusting the animation "ran."

### Previous session
**Build-breaking bug, reported directly from a real `nixos-rebuild-flaked`
failure log:** `flake.nix`'s `postInstall` has always expected
`data/applications/afterglow.desktop` and a full `data/icons/hicolor/
<size>x<size>/apps/afterglow.png` icon set (9 sizes: 16/22/24/32/48/64/
128/256/512) to exist in the repo -- but neither ever actually existed;
these were apparently expected by whichever earlier session wrote that
part of flake.nix but never followed through on creating the files
themselves, so the build has presumably been broken this way for a
while, just not hit/reported until now. Fixed: added
`data/applications/afterglow.desktop` (standard freedesktop entry --
Name, Comment, Exec=afterglow, Icon=afterglow, Categories=AudioVideo;
Video;Recorder;) and the full 9-size icon set, generated from
`library.png` (the sidebar's own Library icon) resized down via
Pillow, since no dedicated app logo/icon has ever been provided --
flagged here as a placeholder specifically so a future session (or
directly) knows to swap in a real one if/when one exists, rather
than assuming this was a deliberate icon choice. Verified: the
`.desktop` file parses correctly as valid INI/desktop-entry syntax,
and every one of the 9 generated PNGs is confirmed to actually be
its claimed size (not just resized-then-forgot-to-check). Not
verified against an actual `nix build` (no Nix in this sandbox) --
verified as far as this environment allows; the path structure exactly
matches what flake.nix's `postInstall` install commands reference.

### Previous session
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
   toggle in Settings > Advanced > Performance). Per the
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
   layer underneath the old border system" was explicitly NOT what was
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
   the EXACT same value the Library grid uses between cards, per the
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
Confirmed is the SAME setting that gets replaced by the
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
real saved config could never follow. Every one of the past "please
double it" requests was reasonably based on what he actually SAW
rendered (a small, never-budging border), not the increasingly
disconnected code default -- which is exactly why the gap kept
growing instead of closing. Fixed: default reset to 6 (double the
confirmed 3px), and 3 (plus the now-understood-mistaken 144) added to
the stale-value migration list. Verified by simulating the exact
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
Video-card-background box + accent outline + the checkmark icon
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
  the "bump"/arc shape described, replacing the old smoother
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
(icons for these come later). Toggle lives on the Info popover page
alongside the other three card-info checkboxes.

**Sidebar -- turquoise added, NOT a full rewrite.** Requested: the
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
expected. Flagged as a scoped-down delivery, not deferred: if the
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
`set_circular()` for when per-tab icons are provided and requested as
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
A copy-paste mistake was caught in the provided colors from two sessions ago: the
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
   thickness in the past doesn't seem to have worked."** Reported:
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

**Investigated, NOT changed, needs the input:** "everything in the
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
what is wanted now that it's visible rendered -- flagged rather than
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

1. **Search/Refresh/Sort moved into the header row.** Per the direct
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
   had nothing to hang a normal `setIcon()` off of. The four icons
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
6. **New color palette given directly **: accent, card_background,
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
   was expensive. Diagnosis matched the initial suggestion exactly
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
   the direct answer, not the original pre-trim capture length.
   Percentages deliberately NOT shown on the three length rows --
   there's no sensible "percent of what" for a duration, only for a
   count-of-videos subset -- flagged as a judgment call in case the
   actually wants a percent shown there anyway (e.g. against some
   fixed reference length), since the ask's own wording technically
   covered "all except total." Read-only tab, no save() hook (unlike
   every other Settings tab) -- deliberately only recomputes when its
   own Refresh button is clicked, per the explicit instruction to
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
   stacked vertically -- the "vertical tiling" requested gone) with an
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
   background color), per the explicit color pairing for this one --
   note this is the OPPOSITE pairing convention from the Sort popover's
   content panel above only in which theme color plays which role, not
   a new color source. Auto-closes on an outside click for free via
   `Qt.Popup`'s own mouse-grab behavior -- no separate "clicked
   elsewhere" handling needed, confirmed this is what was wanted when
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
   persisting across videos (a judgment call made with no feedback available from
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

   **Bug found afterward (caught from a screenshot, not this
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
    the border customization suite, completed after all -- requested
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
    gradient image**, per an image provided directly (now bundled
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
      per the "smooth, not immediately circular" clarification)
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
    Exact repro steps were given (spam-click the sidebar Library button,
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
      on every side, everywhere, per the ask. Neither inner box's own
      internal children reach far enough into ITS corners to need any
      per-pixel child masking for the rounding to look right -- the
      padding itself keeps everything clear of the curved areas, which
      is what let this be done with plain clip-path-then-fill/draw
      calls in `paintEvent`, no `QWidget.setMask()` or render-to-pixmap
      tricks needed.
    - **Unedited highlight redefined**, per the clarification: now
      BOTH a background wash across the whole outer card (behind BOTH
      the video box and info box -- was previously the only place the
      highlight rendered at all) AND a separate border drawn
      specifically into `video_box`'s own margin. Selection was
      deliberately NOT redefined the same way (only this was requested
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
      smaller-preview-player feature (confirmed: distinct from both
      the Editor and the future hover-autoplay), and what clicking the
      thumbnail/video-box area itself should do now that it's visually
      separated from the info box (current selection/double-click-to-
      edit behavior was left exactly as-is, still bound to the whole
      card via `mousePressEvent`/`mouseDoubleClickEvent`, since neither
      was explicitly asked to change -- worth confirming rather than
      assuming this is right once the preview player actually gets
      built). Video padding setting also not done yet.

17. **Real-screenshot feedback round (first time this was actually seen
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
      for renamed, i.e. edited, clips) -- so the correlation
      actually saw ("unedited next to edited") was real, just not
      caused by edit-state itself. Fixed by reserving the title row's
      height from the TARGET (un-shrunk) font size in both `__init__`
      and `set_font_scale()`, decoupled from whatever size shrinking
      actually landed on. Verified directly: two videos, one 1-char
      title and one title long enough to shrink drastically under
      Resize Text to Fit, end up with literally identical title-row
      pixel heights and identical overall `sizeHint()`, despite very
      different actual font sizes.
    - **New color palette**, given directly , replacing the
      placeholders from two sessions ago: `#2161bb` (accent -- buttons,
      card info box), `#274162` (card background), `#1d2c3d` (library
      page background -- reassigned from turquoise), and turquoise
      itself (`#12b5c8`) reassigned to a NEW, narrower role: the Local/
      Uploaded tab icons' own background specifically (request: "for now
      leave it as just local and uploaded" -- filters mentioned as a
      possible future use, not done). App background
      (`afterglow_color_app_background`) is no longer an independently
      hand-picked color -- its default is now COMPUTED from the library
      background (10% brighter, 15% more saturated in HSV, via
      `colorsys`) so it's reliably a bit lighter than the library page
      per the earlier ask, while staying a normal editable field
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
      rendering), wired into all four text elements per the answer
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
      this is a real font-size limitation, not a setting that can be tuned
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
      the unedited-highlight border -- per the answer to "keep both,
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
    - **Still not done**, per the "if you get to those now"
      framing (explicitly optional this round): turquoise applied to
      filters as well as the Local/Uploaded tabs.

18. **Follow-up from a second screenshot** (report: turquoise showing up
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
        what is visible there is a config file still holding
        color values saved under an OLDER assignment -- worth checking
        Settings > Advanced directly to see what's actually saved
        there now, since editing/re-saving those fields (or deleting
        the relevant lines from the config file to fall back to the
        current code defaults) would resolve it either way. Flagged as
        a real open question below rather than guessed at further,
        since there's no way to inspect the actual local config file
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
        cross-checked against the actual on-screen KDE/Wayland
        rendering, this is flagged as unverified-on-real-hardware
        rather than "definitely fine" -- but there's no bug found in
        the actual rounding code itself.
    - **The redesign Requested: outright (not a bug -- a deliberate
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

19. **The turquoise-color bug -- found for real this time.** The
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
    back correct in this environment despite the real machine showing the wrong
    colors on his real machine -- a good concrete example of "verified
    in the sandbox" and "verified on the real machine" not being the same claim,
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
      the app background, which was confirmed already correct)
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
    - **Direct color/sizing adjustments, all on the explicit
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
      values/defaults question to weigh in on, not a bug in
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
      `_VideoGridTab.refresh()`, per the suggested fix -- the
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
      exactly why the initial framing ("match the way the library pages
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
    - **Filters, Sort By, and Info merged into one button** (request: "all
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
**Verify the clip indicator (the clapper) on a real KDE Plasma Wayland desktop** -- it is built and
tested offscreen (see "Clip indicator -- BUILT" near the top, including its "Unverified on real
hardware" list). First build the flake (the layer-shell shim has never been compiled against a real
LayerShellQt), then walk through plan step 6. Expect fixes to the shim and to anything that only
shows on a real compositor.
Custom QComboBox styling (queued last time) is DONE (this session, along
with every other native/KDE dialog). Editor feedback rounds continue
alongside. Still untested on real hardware: real Puppetry/OBS timing
(`offset_ms`) and controller ranges; the new themed dialogs/popups on a
real Wayland session (popup placement, startSystemMove dragging).

**Nothing else explicitly re-requested is still outstanding** -- the
previous round's two open items (custom text fields app-wide, and the
sidebar's full custom-button treatment) are both done; see the
"This session" entry above that one.

**Still queued from earlier sessions:**
1. **Lazy-load the grid**: load the first ~36 videos so the app opens
   immediately, then load more as the user scrolls further down,
   rather than building every card synchronously up front. Explicitly
   OK with videos still loading in the background per request ("its okay
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
  These will be provided later, at which point both should also
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
6. Turquoise applied to filters too (optional, "if you get to
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
- **Themed replacements for native Qt/KDE UI** (newest): every dialog goes
  through `gui/themed_dialogs.py` (ThemedDialog, ask_text/get_text,
  get_color, get_open_file_name(s)/get_save_file_name/get_existing_directory)
  or `gui/custom_message_dialog.py` (show_message, ask_confirm); combo boxes
  are `gui/custom_combo_box.CustomComboBox`; lists in popups/pickers are
  `gui/themed_list.ThemedListWidget`; panels are `gui/themed_frame.ThemedFrame`;
  tooltips / unstyled menus / native scroll bars are caught app-wide by
  `gui/app_chrome.py`. Don't add a QMessageBox/QFileDialog/QColorDialog/
  QInputDialog/raw QComboBox -- tests/test_themed_dialogs.py fails on them.
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
  gold/white image provided directly, now the selection border's
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
- **Watch Speed persistence** -- CONFIRMED: keep resetting to 1.00x. Currently resets to 1.00x on every
  `load_video()` (a judgment call made with no feedback available from his PC,
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
  afterward regardless of hue. Worth actually looking at once
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
