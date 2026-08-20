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

## This delivery: "Could not load the Qt platform plugin"

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

## Previous delivery: hotkeys not working at all

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

