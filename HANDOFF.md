# afterglow — handoff

Handed off per Max's handoff protocol (conversation getting long). This
document is for picking the project back up cold in a new conversation;
`README.md` in this same repo is the running project changelog written
across many prior sessions and is worth reading too, but this file is
the faster orientation.

## What the app does

OBS-triggered clip capture (via hotkeys), a local clip library, an
in-app video editor, and (planned, not started) YouTube upload. Runs on
NixOS — Max's machine and his friends' machines — packaged as a Nix
flake with a systemd user service (the capture daemon) plus a PySide6
GUI. Repo: `github:mr-tinkle-winkle/afterglow`.

## Current status, by page

- **Settings** — done, confirmed working end-to-end (OBS connection,
  clip configs with hotkeys/sounds/lengths).
- **Library** — done. Local/Uploaded tabs, search, tag filters,
  thumbnail grid, right-click Edit/Upload/Delete/Add Filter/Rename,
  auto-prunes entries whose files no longer exist.
- **Editor** — video playback + trim UI (drag handles, live seek,
  Frame Perfect Accuracy checkbox, Local Save wired to the real trim
  pipeline, Save & Upload stubbed). **A crash here was just fixed this
  session and is UNCONFIRMED — see "Most urgent thing to verify" below.**
- **YouTube upload** — not started. Both "Save & Upload" and the
  Library's "Upload" context action show a "not implemented yet" message.

## Most urgent thing to verify

Max reported the video player completely broken: audio played, but the
video area didn't render, the time display was stuck at `0:00 / 0:00`,
and the scrubber never moved. He included a traceback:

```
RuntimeError: Error calling Python override of QOpenGLWidget::initializeGL():
(MpvRenderParam) TypeError: expected CFunctionType instance, got function
```

Root-caused by reading python-mpv 1.0.8's actual installed source
directly: `MpvOpenGLInitParams.get_proc_address` must be an actual
ctypes `CFUNCTYPE` instance (`mpv.MpvGlGetProcAddressFn`), not a plain
Python function — passing a bare function silently fails inside
python-mpv's own kwargs-to-struct conversion. Fixed in
`afterglow/gui/mpv_widget.py` by wrapping the callback explicitly with
`mpv.MpvGlGetProcAddressFn(...)`, and storing the wrapped callback as a
persistent instance attribute rather than a throwaway inline expression
— a bare ctypes callback with no remaining Python reference can be
garbage-collected while native code still holds the raw function
pointer, which is a classic, intermittent, hard-to-diagnose ctypes bug
class worth avoiding proactively.

**This fix has NOT been confirmed by Max yet** — the handoff happened
right after applying it. The `0:00/0:00`-stuck symptom is very likely a
downstream consequence of the same root cause (mpv's `vo=libmpv` output
stalls without a working render context, which appears to also stall
position/duration reporting), but that's an inference, not something
directly confirmed — check both the crash and the playback/scrubber
behavior when Max reports back, don't assume the second one is fixed
just because the first one is.

## Requested next (not yet started)

Max asked for these in the same message that reported the crash, so none
of this has been built yet:

1. **A persistent volume control for the video player itself** —
   explicitly *only* affects the in-app player's playback volume, not
   the underlying clip's actual audio. Requirements as stated:
   - A volume bar, "probably on the left of the video player" (exact
     orientation/placement not pinned down — a vertical slider to the
     left of `MpvVideoWidget` is a reasonable default, but worth
     confirming with Max rather than assuming)
   - Must persist across sessions (belongs in `config.py`'s
     `AppSettings`, alongside a new settings-page field)
   - Also editable in Settings, labeled "Video Player Volume"
2. **Hover-to-reveal play button on Library video cards** — a play
   button should appear on hover over a thumbnail; clicking it OR
   double-clicking the thumbnail itself should open the video. Currently
   `VideoCard` only responds to a double-click on the whole card
   (`mouseDoubleClickEvent` → `edit_requested`).
3. **A separate small "quick view" popup**, triggered by clicking a
   video's title (Max flagged that the title currently sits "significantly
   below" the thumbnail, which reads as a layout complaint worth
   revisiting too). Requirements as stated:
   - Small-ish popup window, lets you watch the clip
   - A fullscreen toggle button on its video player
   - The title shown above the clip, editable inline
   - A small down-arrow next to the title that expands/reveals an
     editable description box below

   **Open question, worth confirming with Max before building this:**
   is this popup meant to *replace* opening the main Editor page for a
   simple watch/rename, or exist *alongside* it as a lighter-weight
   companion flow? The request describes watch + fullscreen + rename +
   description, with no mention of trim controls — reads like a
   lightweight companion to the full Editor rather than a replacement,
   but that's a guess, not a confirmed answer.

## Planned, later (not started, lower priority than the above)

- The audio graph editor (per-segment volume/mute/trim/reposition) —
  flagged as "next" for a long stretch of prior sessions, still not
  started.
- YouTube OAuth + upload.

## Architecture / file map

```
afterglow/                  installable Python package
  config.py                 settings (TOML, ~/.config/afterglow/config.toml)
  db.py                     SQLite schema (~/.config/afterglow/library.db)
  editor.py                 trim engine: fast/keyframe vs frame-perfect
                             (fast-seek + residual decode, NOT naive
                             decode-from-0 -- that was a real, fixed perf bug)
  library.py                video CRUD, tags, filtering, rename,
                             prune_missing_videos()
  clips.py                  clip config CRUD + trigger_clip() -- the full
                             OBS-save -> sound -> settle -> trim -> verify
                             -> move -> register pipeline
  obs_client.py             obs-websocket wrapper. Does NOT trust
                             GetLastReplayBufferReplay's reported path
                             (confirmed unreliable/stale) -- watches OBS's
                             output directory for new files instead
  hotkeys.py                combo state machine (pure, tested) + evdev I/O.
                             Has a regression test for evdev's tuple-valued
                             keycodes (mute key) that once permanently
                             killed the reader thread
  daemon.py                 background daemon: serializes captures through
                             one worker, bounded by a watchdog timeout
  thumbnails.py             first-frame extraction via ffmpeg, cached by
                             video id + file mtime
  cli.py                    full CLI, including `debug-listen` (raw key
                             event dump bypassing everything -- the
                             fastest diagnostic tool when input isn't
                             behaving as expected)
  gui/
    main.py                 entry point; forces LC_NUMERIC=C, sets window icon
    main_window.py           sidebar nav; each page built ONCE and kept
                             alive in the QStackedWidget -- this is why
                             the Editor "remembers last video" for free
    settings_page.py, clip_config_row.py, hotkey_record_dialog.py
    library_page.py, video_card.py
    editor_page.py, mpv_widget.py, trim_timeline.py   <- mpv_widget.py
                                                          just patched, unconfirmed
flake.nix                   packages afterglow; inline-packages
                             obsws-python and python-mpv from PyPI (both
                             patched -- python-mpv needed find_library
                             hardcoded to an absolute nix store path,
                             matching nixpkgs' own approach, since
                             ctypes.util.find_library doesn't reliably
                             work on NixOS at actual runtime). Also
                             defines the NixOS module (services.afterglow)
                             and the daemon's systemd user service.
data/                       .desktop file + icon set
```

## Build/run, and real environment constraints

- Max's workflow: edit → `full-git-update afterglow "<message>"` (commits
  + pushes) → `flake-update afterglow` (bumps his system flake.lock) →
  rebuilds his NixOS system. He reports back build errors or runtime
  behavior; there is no faster feedback loop than that.
- **No `nix` binary in this sandbox.** Every `flake.nix` change is
  unverified against a real `nix build` until Max reports back. This has
  caught real, confirmed bugs multiple times already (wrong assumed
  nixpkgs attribute names, `pythonImportsCheck` failing inside an
  isolated build sandbox that doesn't have runtime deps yet, an
  unverified `pyFinal.pkgs` backlink assumption). Don't treat a
  `flake.nix` edit as correct just because it looks right — say plainly
  that it's unverified, the same way prior sessions have.
- **No real display or GPU in this sandbox either.** `QOpenGLWidget` is
  confirmed *not* supported under Qt's `offscreen` platform here (tested
  directly, not assumed) — so `mpv_widget.py`'s actual GL rendering can
  never be visually verified in this environment. What *can* be tested:
  mpv's playback control logic via `vo=null` (headless, no GL needed),
  and all the surrounding Qt UI logic (button states, slider mapping,
  signal wiring) via a mocked video widget standing in for mpv. Keep
  being explicit about which category a given verification falls into —
  that distinction is what caught the CFUNCTYPE bug even though the fix
  itself couldn't be visually confirmed.
- Reinstalling `libmpv2` in this sandbox (`apt-get install -y libmpv2
  libmpv-dev mpv`) is necessary after any container reset — it doesn't
  persist, and its absence silently makes `import mpv` fail in ways that
  look unrelated to whatever you're actually working on.

## Decisions worth knowing (so they don't get accidentally re-litigated)

- mpv is embedded via its **OpenGL render API**, deliberately not
  window-ID ("wid") embedding — wid is X11-only, and this app can run as
  a native Wayland client (relevant: an earlier, separate bug was Qt's
  platform plugin itself failing to load under Wayland due to missing
  library wiring in the Nix package, since fixed).
- Trigger-time trims always use `frame_perfect=True` with a fast-seek +
  residual-decode optimization — NOT naive fast/keyframe mode, and NOT
  naive decode-from-frame-0 frame-perfect mode either. Both were real,
  confirmed bugs: fast mode can silently return the *entire untrimmed*
  buffer if OBS's keyframe interval is sparse; naive frame-perfect mode
  measurably took 27+ seconds on a realistic clip by decoding the whole
  file from the start every time.
- `trigger_clip()` skips trimming entirely (just renames/moves) when the
  raw capture is already at or under the requested length.
- OBS's `GetLastReplayBufferReplay` reported path is **not trusted** —
  confirmed via Max's own logs to sometimes return a stale filename from
  a previous save. Directory-watching (snapshot before triggering the
  save, diff after, with a short grace period to catch a
  near-simultaneous remux sibling like OBS's "Automatically Remux to
  mp4") replaced it entirely.
- Clip filenames are timestamp-only, not prefixed with the clip config's
  name — the config's name still appears in the video's *title*, per
  explicit request.
- `LC_NUMERIC` is forced to `"C"` in two places (right after
  `QApplication` construction, and again right before mpv itself is
  created) — libmpv segfaults under a non-C numeric locale; confirmed via
  Max's own crash log, which included mpv's own explicit diagnostic
  message naming the exact fix.
- All captures serialize through one daemon worker (prevents two hotkeys
  racing to talk to OBS's replay buffer at once), bounded by a watchdog
  timeout so a single hung capture can't permanently block every future
  one.
- `library.rename_video()` had a real bug where it read back its own
  write through a fresh DB connection before the writing connection had
  committed, silently returning the pre-rename title. Fixed by reading
  the updated row back through the *same* connection/transaction. Worth
  knowing as a pattern to watch for elsewhere: any function that writes
  via one `db.get_conn()` block and then calls a separate getter
  (opening its own connection) to build its return value has this same
  latent bug.

## Open questions for Max, next session

1. Did the CFUNCTYPE fix actually resolve the crash *and* the stuck
   scrubber/timestamp? (Top priority — confirm before building anything
   else on top of the Editor page.)
2. Is the "click title → small popup" feature meant to replace opening
   the main Editor for a simple watch/rename, or exist alongside it?
3. Exact volume-bar placement/orientation, and its value range/default —
   reasonable to default to a vertical slider left of the video widget,
   0–100 range (matches mpv's own `volume` property convention), but
   worth a quick confirmation rather than assuming.
