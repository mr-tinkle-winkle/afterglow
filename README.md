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

## This delivery

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

