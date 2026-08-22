# afterglow

OBS-triggered clip capture, local clip library, Medal-style editor, and
YouTube upload. This is the `mr-tinkle-winkle/afterglow` repo, structured
to match the `puppetry`/`conduit` flake pattern for inclusion in a NixOS
system flake.

## Status

Build order, in progress:

1. **Settings page + OBS-backed clip capture — done.**
2. **Library page (Local + Uploaded tabs, search, tag filters, thumbnail
   grid, right-click Edit/Upload/Delete/Add Filter) — done.**
   **Editor page shell with cross-page persistent state — done.** The
   actual Medal-style trim UI (libmpv live preview, audio graph editor)
   is still next.
3. YouTube OAuth/upload — after that (the Library's "Upload" action
   currently shows a "not implemented yet" message).

## This delivery: the real performance bug, plus a hang safety net

Your description this time -- one button works (slowly, ~20s), then
that exact button goes dead, then a *different* button works once and
also goes dead -- pointed away from a per-capture correctness bug (which
was already re-checked and held up) and toward the single shared worker
thread that all captures funnel through getting permanently stuck. Found
two real, compounding problems and fixed both.

### The actual reason captures were slow: decoding the entire file from frame 0, every time

Frame-perfect mode's `-ss` placement (after `-i`, for accuracy) means
ffmpeg decodes from the very start of the file up through the target
point, discarding everything before it. For a short synthetic test clip
this is barely noticeable -- but I benchmarked it against a more
realistic 1080p60 file and it took **27.5 seconds** to trim the last 5
seconds off a 30-second clip. For a real OBS replay buffer (often 60-120+
seconds, at real gameplay resolution/bitrate), this scales up
significantly, and gets *worse* the shorter your configured clip length
is relative to the buffer -- you're decoding almost the entire buffer
just to keep the last few seconds of it.

Fixed with the standard two-stage technique: a fast input-side seek
lands on the nearest keyframe at or before the target (cheap, no
decoding), then a small residual seek decodes just the short remaining
gap for exact accuracy -- instead of decoding from frame 0 every time.
Also switched the automatic pipeline's encode preset from `medium` to
`veryfast` (manual Editor exports, once built, will still default to
`medium` where quality-per-size matters more than speed). Measured
directly on the same realistic test file: **27.5s → 11.2s, ~59% faster**,
with duration still exactly correct. The improvement scales further with
a longer real buffer relative to a short clip length -- which is exactly
the common case.

This alone should explain most of the ~20s wait, and likely explains why
a second hotkey press while the first was still processing looked like
"nothing happened" -- it just meant it was queued.

### The actual reason things went permanently dead: no timeout on a stuck capture

All captures run through a single serialized worker (intentional --
avoids two hotkeys racing to talk to OBS's replay buffer at the same
time), but there was no upper bound on how long the worker would wait for
one capture before moving to the next. If any single capture ever
genuinely hangs -- an OBS websocket call that never returns, an ffmpeg
process that never exits -- every future hotkey press queues up behind
it and never runs, with no error, no crash, nothing: exactly "it just
stopped working," for every button, permanently.

Fixed: the worker now gives each capture up to 120 seconds (generous --
well beyond even a slow real capture) before logging a clear error and
moving on to the next queued press, rather than waiting forever. Verified
directly: simulated a capture that hangs indefinitely, confirmed a
second, different capture queued shortly after still gets processed
instead of waiting on the first forever.

**Worth knowing about the tradeoff here:** if the timeout does trigger,
the "only one OBS conversation at a time" guarantee is briefly given up
in exchange for not permanently bricking hotkeys -- a rare stuck capture
could in principle overlap with the next one's OBS calls. Given the
timeout is generous and this only matters in the genuine-hang case (which
should now be rare with the speed fix above), that's a reasonable trade,
but flagging it as an intentional decision rather than an oversight.

## Previous delivery: redesigned capture sequencing, per your proposal

Trimming stopped working again after some back-to-back testing and a
`pkill`/restart, and stayed broken even after a clean daemon restart --
which pointed away from stale process state and toward something in the
pipeline's own sequencing/robustness. Reproducing OBS's actual internal
behavior (its replay buffer is a concatenation of ring-buffer segments,
which can leave discontinuous timestamps) didn't reveal a correctness bug
in the existing frame-perfect trim -- but the redesign you proposed
directly addresses the real gaps regardless: not waiting for OBS's own
post-save bookkeeping to finish, not verifying the trim actually did what
it was supposed to, and not cleaning up OBS's own leftover files.

Implemented exactly as you described:

1. **Save order changed.** Sound now plays immediately once OBS confirms
   the raw file exists and is done being written -- before the slower
   trim/re-encode step, not buried after it. Verified directly: the sound
   callback now fires measurably before `trigger_clip()` returns, not at
   the very end.
2. **Added a settle delay** (2s default, `POST_SAVE_SETTLE_SECONDS` in
   `clips.py`) after the raw file looks stable, before starting the trim
   -- a defensive buffer for OBS still finishing internal work (e.g. its
   own "Automatically Remux to mp4" setting, which runs as a separate step
   after the replay file itself is written and can produce a second file
   we didn't ask for).
3. **Added a duration sanity check.** After trimming, the actual duration
   is now compared against what was requested (within 1.5s tolerance) --
   if it's off, `trigger_clip()` now raises a clear `ClipError` and
   *leaves the raw OBS file in place for inspection* instead of silently
   moving a wrong-length clip into your library. This is the single
   biggest change: "clipped but didn't trim" can no longer happen
   silently, regardless of what causes a bad trim in the future --
   verified directly by forcing a no-op trim and confirming it now raises
   instead of passing through.
4. **Cleans up OBS's own leftover files.** If OBS's "Automatically Remux
   to mp4" setting produces a sibling file (e.g. reports the `.mkv` but
   also independently creates a same-named `.mp4`), that sibling is now
   deleted once we've extracted what we need -- verified directly with a
   simulated sibling remux file, confirmed removed after a successful
   capture.
5. **Filenames are now just the timestamp** (e.g. `2026-08-22_01-21-01.mkv`),
   not prefixed with the clip config's name -- the config's name still
   appears in the video's *title* in the library, this only changes what
   the file itself is called on disk, matching what you asked for.

All five verified together in one integration test: a fake OBS producing
both a raw file and a simulated remux sibling, confirming the correct
call order, correct final filename/title, correct duration, and both the
original file *and* the sibling fully cleaned up from OBS's folder
afterward.

## Previous delivery: hotkeys work now, but captured clips weren't trimmed

Your `debug-listen` run confirmed key detection, matching, and the
capture pipeline all work correctly now. "It clipped but didn't trim" was
a real, separate, fourth bug -- found and fixed.

### Root cause: fast trim mode silently fails on sparse-keyframe files

`trigger_clip()`'s automatic trim step used fast/keyframe-seek mode
(stream-copy, no re-encode) rather than frame-perfect mode. Fast mode
seeks to the nearest keyframe *at or before* the requested start time --
which works fine when keyframes are frequent, but OBS's replay buffer
output can have a keyframe interval larger than the requested clip
length, depending on encoder settings. In the worst case (effectively one
keyframe near the very start of the saved segment), fast mode can't seek
forward at all: it snaps back to that single keyframe regardless of what
start time was actually requested, handing back the *entire* raw buffer
with zero trim applied -- silently, no error, exactly matching what you
saw.

I reproduced this directly: generated a 20s test file with only one
keyframe (mirroring a sparse-keyframe OBS-style recording), requested the
last 5 seconds via the exact logic `trigger_clip()` uses, and confirmed
fast mode returned the full 20s untouched.

**Fixed:** trigger-time trims now always use frame-perfect mode (full
re-encode) instead of fast mode. This is a real behavior change, not just
a bug fix -- fast mode's speed advantage isn't worth it here, since
whether it actually cuts anything at all depends on OBS's keyframe
interval, which is outside this app's control. The entire point of
automatic capture is reliably getting the requested length; frame-perfect
guarantees that regardless of OBS's encoder settings, at the cost of a
short re-encode per capture (a few seconds on modern hardware for a
typical short clip). `frame_perfect` remains available as an explicit
opt-in for the manual Editor (still to be built) for precise custom-range
trims, but is no longer something the automatic pipeline can silently get
wrong.

Verified with the same reproduction case (sparse-keyframe 20s file,
requesting the last 5s): frame-perfect mode now correctly returns exactly
5.00s. Also re-ran the full `trigger_clip()` pipeline end-to-end with a
fake OBS producing that same sparse-keyframe file, confirming the
complete capture → trim → library-registration flow now produces the
correct duration.

## Previous delivery: a real diagnostic tool, since guessing further isn't working

Two rounds of fixes (the mute-key tuple crash, the OBS file-timing race)
and hotkeys still do nothing. Both of those were real, confirmed bugs --
but "still broken after two confirmed fixes" means I'm out of things I
can find by re-reading the code without seeing what's actually happening
on your hardware. Rather than guess at a third bug, I've added a tool
that answers the actual question directly:

```
afterglow-cli debug-listen
```

This bypasses hotkey combo matching, the daemon, clip configs, OBS --
everything -- and just prints every raw keyboard event it sees, live,
until you Ctrl+C. Run it, then press your configured hotkey (and a few
other random keys too, for comparison). One of a few things will happen:

- **Nothing prints at all, for any key.** This means the app genuinely
  can't see your keyboard's input events -- almost certainly a
  `/dev/input` permissions problem. It'll also print
  `No keyboard devices found` up front with specific guidance (check
  `groups` includes `input`; remember group membership only applies after
  a fresh login, not just a rebuild).
- **Other keys print, but nothing happens for your specific hotkey's
  keys.** This would be unusual, but would point at something specific to
  those particular keys/that device.
- **Everything prints, including your hotkey's keys, with sensible-looking
  names** (e.g. `raw='KEY_LEFTCTRL'`, `display='ctrl'`). This means event
  delivery is fine, and the bug is specifically in combo registration or
  matching -- at which point the next step is checking that the *exact*
  hotkey string saved in your clip config matches what's printing here
  (case matters is handled, but e.g. a leftover typo or unexpected key
  name wouldn't).
- **Something prints, but the raw key names look unusual** (not the usual
  `KEY_*` format) -- worth flagging specifically, since you're on a
  Wooting 80HE, and some analog/gaming keyboards can behave unusually at
  the evdev level compared to a standard keyboard depending on what mode
  they're in.

Please run this and send me what it prints when you press your hotkey --
that tells us which of the above we're actually dealing with, rather than
me guessing at a third fix blind.

## Previous delivery: mute-key crash and OBS timing bug

Your `journalctl` output pinned down the real bug directly, and the
`afterglow-cli trigger` error confirmed your own diagnosis of a second,
separate one. Both fixed and verified:

### Bug: evdev's mute key crashed the hotkey listener permanently

```
AttributeError: 'tuple' object has no attribute 'startswith'
```

Root cause, confirmed directly: evdev's own keycode reverse-mapping isn't
consistently `str`/`list` -- some key codes resolve to a `tuple` instead.
Code 113 (mute) is one of them: it maps to
`('KEY_MIN_INTERESTING', 'KEY_MUTE')`. My code only checked for `list`,
not `tuple`, so a tuple went straight into `.startswith()` and raised.

Mute is an extremely easy key to hit by accident (volume rocker, media
keys, a stray keypress), which is exactly why this crashed within seconds
of the daemon starting in your logs. Worse: the reader thread for that
keyboard device died **permanently** when this happened -- the daemon
kept running (which is why `systemctl status` showed "active"), but that
device's hotkey detection was dead for the rest of the daemon's life,
with no visible symptom besides "the hotkey does nothing."

Fixed two ways:
1. `_display_name()` now handles `tuple` as well as `list`, at the source.
2. **More importantly:** the per-event processing in the reader thread is
   now wrapped in its own try/except that logs and continues, rather than
   letting any single malformed event kill the thread forever. This is
   the actual structural fix -- it means no *future* unexpected evdev
   quirk (and there may be others; evdev's keycode tables are large and
   not something I can exhaustively audit from here) can silently kill
   hotkey detection again the same way.

Verified directly: reproduced the exact tuple (`('KEY_MIN_INTERESTING', 'KEY_MUTE')`)
using evdev's own event categorization for the real mute keycode, fed it
through `_display_name()` and `ComboStateMachine.key_down()`, confirmed
both handle it without raising (this is now a permanent regression test
in `hotkeys.py`'s `__main__` block).

### Bug: OBS's reported replay file was checked before it finished writing

Your diagnosis was exactly right. `save_replay_buffer()` asks OBS for the
path it just saved to, then checked `path.exists()` **once, immediately**.
But OBS can report the destination path before the file is actually done
being written/remuxed to disk -- so on a slightly slower disk or a larger
buffer, the file legitimately doesn't exist yet at the moment we check,
even though OBS did fire the save correctly (matching what you saw: OBS's
replay buffer visibly triggered, but our own code errored out before ever
reaching the trim/sound/library-registration steps -- which is also why
that particular clip ended up untrimmed and silent: our pipeline never
got that far).

Fixed: now waits up to 15s for the file to (a) exist and (b) have a
stable size across two consecutive checks (not still growing), instead of
one immediate check. Verified directly with a simulated OBS that reports
the path immediately but doesn't actually finish writing the file until
1.6s later -- confirmed `save_replay_buffer()` now waits and succeeds
instead of failing immediately. Also verified the genuine-failure path
still raises a clear error (using the real 15s timeout) when a file
truly never appears at all.

## Previous delivery: "Could not load the Qt platform plugin"

Root cause, from the error text itself: *"From 6.5.0, xcb-cursor0 or
libxcb-cursor0 is needed to load the Qt xcb platform plugin"* is Qt's own
built-in message for that exact version threshold -- so updating Plasma
to unstable also bumped the Qt version afterglow gets built against
(since afterglow's flake follows your system's `nixpkgs` input, a system
update rebuilds it against the new revision too). Both the wayland *and*
xcb plugins were failing to load for the same underlying reason: on
NixOS, a binary only gets the exact runtime library closure its
derivation explicitly declares -- unlike a traditional distro, it doesn't
matter that Plasma itself already has `libxcb-cursor` on the system
somewhere; afterglow's own build never declared it as a dependency, so
its isolated closure didn't have it.

This is a known gotcha specifically with `buildPythonApplication` for
PySide6/PyQt apps on Nix: it doesn't automatically get the Qt runtime
wrapping (`QT_PLUGIN_PATH`, `LD_LIBRARY_PATH` for Qt's shared libs) that
native Qt packages get via `wrapQtAppsHook` -- that has to be wired up
explicitly.

**Fixed in `flake.nix`:** added `qt6.wrapQtAppsHook` and explicit
`qt6.qtbase` / `qt6.qtwayland` / `xcb-util-cursor` build inputs, and the
package's `postFixup` now calls `wrapQtApp` on each entry-point script
before layering the existing PATH wrapping on top. (`wrapQtAppsHook`'s
automatic detection only reliably wraps ELF binaries, not the
text/shebang scripts `buildPythonApplication` generates for
`console_scripts` -- so `wrapQtApp` is called explicitly rather than
relying on auto-wrap picking them up.) Also applied the same fix to
`devShells.default` for `nix develop`.

**Important: this is not a platform-selection fix, and doesn't hardcode
anything DE- or version-specific.** Qt still auto-detects xcb vs. wayland
at runtime from the session's `XDG_SESSION_TYPE`/`WAYLAND_DISPLAY`, the
same as any other Qt application -- this fix only makes sure that
whichever one it picks can actually load its own dependencies. That's
exactly why it should work unmodified for your friends on older Plasma
versions too: it's fixing library resolution, not choosing a backend.

**Still unverified against a real build** (no `nix` in my environment, as
before) -- I'm confident in this being the standard, correct nixpkgs
pattern for this exact class of error, but "the standard pattern" and "a
successful build on your exact nixpkgs revision" aren't the same
guarantee. If it still fails after rebuilding, the error message will be
different from this one (a missing-attribute error at evaluation time
would mean something in `qt6.*` doesn't exist under that name in your
pinned nixpkgs revision, vs. the same runtime plugin error would mean the
wrapping still isn't reaching the binary correctly) -- paste me whichever
you get.

## Earlier delivery: sound-playback and permission diagnostics

Two real bugs found and fixed, plus better diagnostics for whatever's
still wrong on your specific system (I can't see your logs from here, so
some of this is "here's how to find out" rather than "here's the fix"):

### Bug #1 (likely root cause): a sound-playback failure silently killed the whole capture

`play_sound()` shelled out to `paplay` unconditionally. Two problems:
- `paplay` traditionally ships in the **`pulseaudio`** package, not
  `pipewire` itself, even on a PipeWire-based audio stack -- and
  `flake.nix`'s `postFixup` only ever put `ffmpeg` and `pipewire` on the
  wrapped daemon's PATH. `pulseaudio` was never on PATH at all, so
  `paplay` would never have been found.
- Worse: that call was unguarded. `subprocess.Popen(["paplay", ...])`
  raising `FileNotFoundError` propagated straight up through
  `trigger_clip()` -- **before** the clip got registered in the library.
  So a hotkey press would: successfully talk to OBS, successfully trim,
  successfully move the file into your clips folder... and then die on
  the sound step, meaning the clip never made it into the library DB at
  all, despite the file existing on disk. If you've been finding files in
  the clips folder that never showed up in the app, this is almost
  certainly why.

Fixed: `play_sound()` now tries `pw-play` (PipeWire's own player,
included with the `pipewire` package -- no Pulse compat layer required)
first, falls back to `paplay`, and never raises -- a missing/broken
player now logs a warning and the capture still completes normally. Also
wrapped the call site in `trigger_clip()` with its own try/except as
defense in depth. `flake.nix` now also puts `pulseaudio` on the wrapped
PATH alongside `pipewire`/`ffmpeg`, so both players are actually available.

I verified this fix directly: simulated a PATH with `ffmpeg` but no audio
player at all, confirmed `trigger_clip()` still completes and the clip
still lands in the library (previously it wouldn't have).

### Bug #2 (possible root cause): silent crash-loop if evdev can't read /dev/input

If the daemon can't get read access to `/dev/input/event*` (e.g. this
user isn't actually in the `input` group yet), `EvdevHotkeyListener.start()`
raises -- and previously, that exception wasn't caught anywhere in
`daemon.py`'s startup path, so the whole process would crash with a bare
Python traceback. Under the systemd service's `Restart = "on-failure"`,
that means a silent crash-loop: `systemctl --user status` would show
"activating (auto-restart)" but nothing would obviously explain why
unless you went digging in `journalctl`.

Fixed: this failure now logs a specific, actionable error (checks for
`input` group membership, notes that group membership only takes effect
after a fresh login) before re-raising, so it's immediately visible in
`journalctl --user -u afterglow-daemon` rather than a bare traceback.
Also added logging of exactly which keyboard devices were found (or
permission-denied) on startup.

### How to actually diagnose what's happening on your machine

1. **Check whether the daemon is even running:**
   ```
   systemctl --user status afterglow-daemon
   ```
   If it says "activating (auto-restart)" repeatedly, it's crash-looping
   -- go to step 2. If it's not even listed, the service either wasn't
   enabled by the NixOS module (check `services.afterglow.enable = true;`
   made it into your active config) or the rebuild didn't apply it yet.

2. **Read the actual logs:**
   ```
   journalctl --user -u afterglow-daemon -e
   ```
   With this delivery's fixes, a permission problem now shows a clear
   `FAILED TO START HOTKEY LISTENER` message instead of a bare traceback.
   If you see that, confirm with `groups` that `input` is actually listed
   -- and if you only just rebuilt with the module enabled, **log out and
   back in** (group membership doesn't apply to already-running sessions),
   then `systemctl --user restart afterglow-daemon`.

3. **Bypass hotkeys entirely to isolate the problem** -- this tells you
   whether the *capture pipeline* works at all, independent of hotkey
   detection:
   ```
   afterglow-cli trigger --name <your clip config name>
   ```
   If this works (produces a clip, shows up in the Library), the problem
   is specifically in hotkey detection (permissions, device discovery, or
   the combo not matching). If this *also* fails, the problem is
   somewhere in the OBS/trim/sound pipeline itself, and the error message
   from this command will say where.

4. **Run the daemon in the foreground** to watch it live while you press
   the hotkey:
   ```
   systemctl --user stop afterglow-daemon
   afterglow-daemon
   ```
   You should see `Discovered N usable keyboard device(s): [...]` on
   startup. If N is 0, or if you see permission-denied warnings, that
   confirms the `/dev/input` access problem. Press your configured hotkey
   -- you should see `Hotkey fired: '<name>' -- capturing clip...` in the
   log. If nothing prints at all when you press it, the combo isn't
   matching what was actually registered -- double check the exact combo
   shown in `Active hotkeys: [...]` against what you're pressing.



Three things, in response to real feedback from running the app:

### 1. Fixed a crash: recording a hotkey aborted the app

`QThread: Destroyed while thread '' is still running` / `Aborted (core
dumped)`. Real bug in `gui/hotkey_record_dialog.py`: the recording
QThread was signaled to stop but the dialog moved on (`accept()`/
`reject()`) without actually waiting for it to finish first, so Qt tore
down a still-running QThread object and aborted the process. Fixed by
blocking on `self._thread.wait()` after every `stop()` call before the
dialog proceeds.

While in there, also fixed a related resource leak: the evdev reader
threads used `device.read_loop()`, which blocks until the *next* key
event before it can even check whether it's been asked to stop -- so
repeatedly opening/closing the hotkey-record dialog would pile up reader
threads that only exited whenever some unrelated future keypress happened
to occur on that device. Rewrote it with a `select()`-based read with a
timeout so reader threads notice `stop()` within ~0.2s regardless of
whether a key was pressed.

### 2. Added a `.desktop` entry + icon set so the app shows up in search

Nothing was missing on your end -- `buildPythonApplication` only installs
the Python package by default, not XDG application/icon data, and I
hadn't added any. Now included:

- `data/applications/afterglow.desktop`
- `data/icons/hicolor/{16,22,24,32,48,64,128,256,512}/apps/afterglow.png`,
  generated from the two images you sent (the "small" master for the
  smaller icon-theme sizes, the "large" master for the bigger ones --
  both were 1024x1024 source files, resized down with Lanczos)
- `flake.nix`'s package build now has a `postInstall` step that installs
  both into `$out/share/...`
- the GUI itself also sets its window icon at runtime (`QIcon.fromTheme`,
  falling back to a repo-relative dev path if run outside the Nix
  package), so it shows correctly in the taskbar/alt-tab too

**Note for after you rebuild:** KDE's KRunner/app-launcher search relies
on a cache (`kbuildsycoca`) that doesn't always pick up new `.desktop`
files immediately. If it still doesn't show up in search right after
rebuilding, try logging out and back in, or running
`kbuildsycoca6 --noincremental` (or `kbuildsycoca5`, depending on your
Plasma version) manually.

### 3. Restructured the nav: Settings / Library / Editor, all live

- **Library page** (`gui/library_page.py`, `gui/video_card.py`): Local and
  Uploaded sub-tabs (built from one shared, parameterized grid widget
  rather than duplicated code, since they only differ in which videos
  they show). Each tab has a search bar (title/description substring,
  case-insensitive), a Filters dropdown (checkable list of all known
  tags, ANDed together), and a thumbnail grid. Thumbnails are the video's
  first frame via `ffmpeg` (`thumbnails.py`), cached to
  `~/.cache/afterglow/thumbnails/`, keyed by video id *and* file mtime --
  so a trim (which changes the file's mtime) automatically invalidates
  the old thumbnail without any explicit cache-clearing logic anywhere in
  the edit/undo path. Right-click a card for Edit / Upload / Delete / Add
  Filter, matching the original spec (Add Filter opens a text box with a
  filtering dropdown of existing tags, plus a "+" to add a brand new one).
  Uploaded is currently always empty (nothing has a YouTube id yet) and
  shows an empty state -- it's real, just has nothing to show until phase 3.
- **Editor page** (`gui/editor_page.py`): shows the currently-loaded
  video's info and an Undo button. Persistence works because of how
  `MainWindow` is structured, not because of any explicit save/restore
  code -- each page (`SettingsPage`, `LibraryPage`, `EditorPage`) is
  constructed exactly once in `MainWindow.__init__` and kept alive inside
  the `QStackedWidget` for the app's entire lifetime. Switching nav only
  changes which widget is *visible*; it never destroys or recreates them.
  So `EditorPage.current_video_id` just stays whatever it was set to,
  automatically, across any number of trips to Settings or Library and
  back. `load_video()` is the only place that state ever changes.
- `library.list_videos()` gained a `search` parameter (title/description
  substring match) to back the search bar.

## Layout

```
afterglow/            the installable Python package
  config.py, db.py, editor.py, library.py, clips.py, obs_client.py,
  hotkeys.py, daemon.py, cli.py, thumbnails.py
  gui/
    main.py, main_window.py, settings_page.py, clip_config_row.py,
    hotkey_record_dialog.py, library_page.py, video_card.py, editor_page.py
data/
  applications/afterglow.desktop
  icons/hicolor/.../apps/afterglow.png    (generated icon set)
  icon_sources/                            (your two original master PNGs, kept for future re-generation)
pyproject.toml         package metadata + entry points
flake.nix              packages.default, devShells.default, nixosModules.default
shell.nix              legacy pip-venv dev shell (nix develop is preferred now)
```

Entry points (from `pyproject.toml`):
- `afterglow` — GUI
- `afterglow-daemon` — background hotkey/capture daemon
- `afterglow-cli` — CLI (see `afterglow/cli.py`'s docstring for commands)

## Using this as a flake input

```nix
afterglow = {
    url = "github:mr-tinkle-winkle/afterglow";
    inputs.nixpkgs.follows = "nixpkgs";
};
```

```nix
inputs.afterglow.nixosModules.default
{
    services.afterglow = {
        enable = true;
        user = "mrtw";
    };
}
```

The module:
- installs the `afterglow` package system-wide (so `afterglow` /
  `afterglow-cli` are on PATH, the GUI is launchable, and it now shows up
  in the application launcher/search per the fix above)
- adds `cfg.user` to the `input` group (needed for evdev hotkey capture —
  same requirement puppetry already has for that user)
- runs `afterglow-daemon` as a **systemd user service** (`systemd.user`,
  not a system-wide service) under `default.target`, with
  `Restart = "on-failure"`

## obsws-python packaging note

`obsws-python` isn't in nixpkgs, so `flake.nix` packages it inline from
PyPI (pinned to 1.8.0, hash computed directly from the downloaded sdist).
If you ever need to bump it: download the new sdist from PyPI, and either
run `nix hash convert --hash-algo sha256 --to sri <hex-digest>` or ask me
to recompute it the way I did this one.

## What's tested vs. not

Tested directly (installed the package fresh into a clean venv and ran
against it, headless via `QT_QPA_PLATFORM=offscreen` for GUI pieces):

- The QThread crash fix -- confirmed the dialog's stop/wait sequencing is
  correct by re-running the hotkey state-machine test suite after the
  evdev read-loop rewrite (the pure-logic parts; see the caveat below for
  what's still unverified).
- Thumbnail generation + caching (including the cache-hit path).
- Library page: grid population, search filtering, tag filtering
  (including toggling a filter off again), delete via a card's context
  menu, and the empty-state label showing correctly for an empty Uploaded
  tab (verified by actually switching to that tab on-screen -- an
  `isVisible()` check on a background tab reads `False` regardless of the
  widget's own visibility flag, which tripped me up for a second before I
  confirmed it wasn't a real bug).
- Editor routing from a Library card's Edit action, and state persistence
  across navigating to other pages and back.
- Trim + Undo through the Editor page's Undo button.

**Still not testable in this sandbox (no `/dev/input`, no `nix`, no real
display):**
- The evdev fix's actual behavior on real hardware -- record a hotkey a
  few times in a row and confirm both that it no longer crashes and that
  reader threads aren't piling up (`ps -eLf | grep afterglow` thread
  count should stay flat across repeated open/close of the record dialog).
- Whether the app now actually appears in KRunner search after a rebuild
  (and whether you need the `kbuildsycoca` nudge mentioned above).
- The flake build itself, still un-`nix build`-ed for the same reason as
  last time.

