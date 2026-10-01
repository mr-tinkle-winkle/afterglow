# Input visualizer prep: Puppetry → afterglow input overlay (v4)

Prepared on the Puppetry side for the afterglow project: what Puppetry
provides, the design for the afterglow side, a drop-in module, and a
passing integration test.

**Changed since v3:**
- Puppetry's visualizer is now a layout the user arranges (*Edit layout*):
  any number of keyboards (full, 80%, 60%, left half), mice (classic,
  gaming, minimal, buttons only), controllers and mouse-movement views.
  `full` renders that whole layout; `el:<id>` renders one element of it
  (`aio.elements()` lists them); `keyboard` / `mouse` / `controller` /
  `comet` / `mousepad` / `joystick` render the first element of that type,
  or a default one.
- The movement arrow is replaced by three square movement views: Comet
  (a trail behind the pointer), Mousepad and Joystick. New pieces `comet`,
  `mousepad`, `joystick` with their own default placements.
- `resolve_placements` accepts `el:<id>` pieces in the global / clip-type
  layers and takes `element_types` (`{id: type}`) to give them their type's
  built-in default. Their files are `el-<id>.mov` (`aio.piece_file`).

**Changed in v3:** placements default per clip type over global; the
previewer only toggles.

**Changed in v2:**
- The overlay is no longer burned into the saved clip. It is stored beside
  the clip as transparent video pieces plus the raw input. afterglow shows
  it live in the previewer and editor, where it can be toggled, moved and
  resized, and burns it in only when exporting.
- The overlay comes as separate pieces: keyboard, mouse and controller can
  each be placed on their own.
- Your input is drawn in the input color (orange) and a macro's output in
  the output color (blue).
- Controller support: buttons, sticks, triggers and d-pad.

## What exists (Puppetry side, done)

- **OBS pages** (Input Visualizer page → *OBS & replay overlay*). Each is a
  local page for an OBS Browser Source, or is created by **Add to OBS**.
  - the whole layout is one page, and optionally every element is also its
    own page.
  - *simple input list* is one line of text of what's held, with an
    optional *mouse movement* page (one movement view).
  - Held keys show a millisecond hold timer. Movement views draw the
    pointer's recent path with clicks and scrolls.
- **Layered Replay Buffer.** A rolling record of all keyboard, mouse and
  controller input in RAM (`$XDG_RUNTIME_DIR/puppetry/input_buffer.jsonl`).
  It is as long as OBS's replay buffer, which Puppetry reads over
  obs-websocket, plus a margin.
- **`puppetry-overlay`**, a command that renders any piece of that input as
  a transparent video (`render`), cuts it to a clip (`align`), and burns
  pieces onto a clip at chosen places (`layer`). The full reference is in
  FORMAT.md.

## How it's meant to work in afterglow

### 1. Settings, per clip type

| Setting | Values | Default |
|---|---|---|
| Input overlay | on / off (whether to capture it for this clip type) | off |
| Pieces | any of: full (the whole layout), keyboard, mouse, controller, comet, mousepad, joystick, simple, movement, or a layout element `el:<id>` | full |
| Show by default | overlay starts visible in the previewer/editor | on |
| Timing offset | ms (calibration, see FORMAT.md) | 0 |
| Piece placements | where each piece starts on new clips of this type; "use global" per piece | use global |

Plus one **global** set of piece placements (Settings), which every clip
type uses unless it overrides a piece. A new clip starts at
`aio.resolve_placements(global_defaults, clip_type_defaults)`: per piece,
the clip type's placement if it set one, else the global one, else the
built-in default. From then on the clip keeps its own placements, which
only the editor changes.

Everything about how the overlay *looks* (colors, key size, timers, labels)
is set in Puppetry and applies to OBS and to clips alike. afterglow only
decides which pieces, where they go, and whether they're shown.

### 2. Capture (pipeline hook)

```python
import afterglow_input_overlay as aio

# right where SaveReplayBuffer is sent to OBS, if the clip type has "Input overlay" on:
t_save = time.time()
job = aio.start_clip(t_save, aio.OverlaySettings(
    pieces=clip_type.overlay_pieces, offset_ms=clip_type.overlay_offset_ms,
    placements=aio.resolve_placements(settings.overlay_placements, clip_type.overlay_placements)))
# ... OBS writes the clip (existing "replay buffer completed" step) ...
manifest = aio.finish_clip(job, clip_path)     # -> clip_path + ".input/" (the clip itself untouched)
job.cleanup()
```

- `start_clip` must run **immediately** at save time. It freezes the input
  and starts rendering every piece in the background, while OBS writes.
- `finish_clip` cuts each piece to the clip frame for frame (a stream copy,
  seconds) and writes the sidecar: pieces, `inputs.jsonl` and
  `manifest.json` (FORMAT.md, "Sidecar folder").
- The sidecar must move, copy or delete with its clip. Afterglow's existing
  move and cleanup steps need to include `<clip>.input/`.
- On `OverlayError`, keep the clip and play the error noise. A missing
  overlay never costs a clip.
- Trimming: the sidecar pieces are aligned to the *untrimmed* capture. If
  afterglow trims to a new file, trim the pieces the same way (same `-ss` /
  `-t`; they're all-intra, so a stream copy is exact). Alternatively keep
  the trim as in/out points and apply it at export.

### 3. Video previewer (toggle only)

- Show an **Input overlay** toggle only when the clip has one:
  `aio.load_sidecar(clip)` is not `None`.
- Its initial state comes from `manifest["visible_by_default"]`.
- The previewer only turns the whole overlay on and off. Pieces appear
  where the clip's placements put them; moving and resizing happen in the
  editor.
- Drawing: `files, graph = aio.preview_overlay_args(sc, video_w, video_h,
  enabled)`. Set mpv's `external-files` to `files` when the clip loads, and
  `lavfi-complex` to `graph`; set `graph` again on every toggle. The pieces
  are transparent, so only the keys, buttons and arrow cover the video.

### 4. Editor

- The same toggle, plus per-piece **show/hide**, **move** and **resize**:
  - `placements = aio.placements_of(sc)` gives
    `{piece: {x, y, w, visible}}` as fractions of the video.
  - `aio.piece_rect(sc, piece, placement, video_w, video_h)` gives the
    pixel rectangle for drawing drag and resize handles over the video
    widget. Height follows each piece's aspect ratio, so resizing only
    needs one corner handle.
  - While dragging, draw only the outline (a Qt overlay on the mpv widget).
    On release, recompute `mpv_overlay_args` and set `lavfi-complex`,
    because rebuilding mpv's graph on every mouse move would stutter.
  - `aio.save_placements(sc, placements)` stores them in the manifest, so
    the clip remembers.
- **Export:** `aio.export(clip, sc, placements, out)` burns in the visible
  pieces at their placements (H.264, CRF 18, audio copied). With nothing
  visible, it just copies the clip.
- **Re-render:** `aio.rerender(clip, sc, piece)` rebuilds a piece from the
  stored `inputs.jsonl`. Use it after changing Puppetry's look, or to add a
  piece that wasn't captured, e.g. the controller on a clip saved with
  keyboard only.

### 5. Availability

`aio.available()` returns `(ok, reason)`, e.g. `(False, '"Layered Replay
Buffer" is off in Puppetry (Input Visualizer page)')`. Show the reason
next to the clip type's toggle.

## Files in this folder

| File | What it is |
|---|---|
| `afterglow_input_overlay.py` | The drop-in module (stdlib only): capture, sidecar, previewer/editor helpers, export, re-render |
| `FORMAT.md` | Interface reference: buffer file, status file, sidecar manifest, every CLI command, transparency formats, mpv graph, timing model and calibration |
| `test_afterglow_input_overlay.py` | Integration test of the whole flow against the real `puppetry-overlay` + ffmpeg; the mpv graph is checked by running it through ffmpeg. Skips if Puppetry isn't installed |
| `sample/` | `input_buffer.jsonl`, `overlay_status.json`, `manifest.json` examples |

## Measured (offscreen sandbox, not the target machine)

- Capture to sidecar for a 4 s clip with four pieces (keyboard, mouse,
  controller, a comet element) at 30 fps: about 3.3 s, mostly while OBS would still be
  writing.
- Transparent qtrle pieces: about 1 MB per second of heavy keyboard
  activity; controller and mouse pieces are much smaller. Rendering runs at
  roughly real time per piece (pieces render in parallel).
- VP9 `.webm` loses its transparency with ffmpeg's default decoder. Use
  qtrle `.mov` (the module's default); see FORMAT.md.

## Not verified

- mpv itself: the `lavfi-complex` graphs were validated with ffmpeg, which
  mpv uses underneath, but not in a running mpv.
- A real OBS and real input devices, including real controllers; stick
  ranges and trigger axes vary by model.
- The exact alignment offset on real hardware. Expect a few frames at most;
  `offset_ms` corrects it.

## Decided

- Placements: global defaults, optionally overridden per clip type (per
  piece); each clip then keeps its own, changed only in the editor.
- The previewer only toggles the overlay; moving and resizing are editor
  features.
