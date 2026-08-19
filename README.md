# clipping-app — backend + Settings GUI + hotkey daemon

Status: **Settings page (page 1 of 3) is implemented and working**, along
with the OBS-triggered clip capture pipeline behind it. Build order per
plan:

1. **Settings page + OBS-backed clip capture — done, this delivery.**
2. Local Library page (video player, tags, Medal-style trim editor +
   audio graph editor) — next.
3. Uploaded Library page + YouTube OAuth/upload — after that.

## What's implemented and tested

Everything from the previous backend delivery, plus:

- **`hotkeys.py`** — hotkey combo handling, split into a pure-logic layer
  and a thin evdev I/O layer on top:
  - `ComboStateMachine` — tracks held keys, fires a callback when a
    registered combo is completed, ignores key-repeat (no refiring while
    held), collapses left/right modifier variants (`ctrl` matches either
    physical ctrl key). **Fully unit tested** (no hardware needed) —
    covers combo firing, repeat-suppression, partial-combo non-firing,
    and left/right modifier collapsing.
  - `ComboRecorder` — used by the Settings GUI's "Record" button: captures
    a chord as it's pressed, finalizes on first key-up. **Tested.**
  - `EvdevHotkeyListener` — reads real keyboard devices via `python-evdev`
    and feeds either of the above (`RecorderAdapter` lets the same
    listener feed a recorder instead of the daemon's state machine, so
    there's one I/O implementation, not two). **Not testable in this
    sandbox — no `/dev/input` available.** This is the one piece that
    genuinely needs a real run on your machine to confirm.
  - Passive listening only (no `grab()`) — a clip hotkey doesn't eat the
    keypress from whatever's focused, it just observes.
- **`daemon.py`** — background process: loads clip configs, registers
  their hotkeys, and runs `clips.trigger_clip()` when one fires. Polls
  the DB every 2s and live-reloads hotkey registrations if clip configs
  changed in the GUI, without needing a restart. Clip triggers are
  serialized through a single worker queue (not one thread per press) so
  two hotkeys fired close together can't race each other's OBS
  replay-buffer save/read cycle. **Tested end-to-end** with OBS and evdev
  I/O stubbed out — confirmed hotkey-fire → capture → library registration,
  and live pickup of a newly-added clip config without restarting.
- **`gui/`** — PySide6 Settings page:
  - OBS host/port/password + a "Test Connection" button that also warns
    if any clip option's length exceeds OBS's configured replay buffer
    length.
  - Clips folder + default sound pickers.
  - Clip Options list: collapsible rows, each with name / length (seconds)
    / sound (optional, falls back to default) / hotkey (record via the
    same evdev listener the daemon uses) / delete.
  - Explicit "Save Settings" button. Saving diffs rows against the DB
    (insert new / update changed / delete removed) rather than wiping and
    reinserting everything, so a clip config's id — and therefore the
    daemon's registration and any future video↔clip-config links — stays
    stable across edits.
  - **Tested headless** (`QT_QPA_PLATFORM=offscreen`): window construction,
    nav, loading existing clip configs into rows, adding rows, saving
    (insert), editing + re-saving (update, not duplicate), duplicate-name
    rejection, delete, and settings persistence all verified. **Not
    tested on a real display** — layout/visual polish may need a pass
    once you can actually look at it.

## Running it

```
nix-shell
python gui/main.py       # Settings GUI
python daemon.py         # background hotkey listener + capture daemon
python cli.py ...        # still available, see previous README section
```

The GUI and daemon share the same DB/config, so clip options created in
the GUI show up in the daemon (within ~2s) and vice versa.

**Permissions:** the daemon and the GUI's hotkey-record dialog both read
`/dev/input/event*` via evdev. This needs the running user to be in the
`input` group on NixOS — the same requirement your macro daemon already
has, so if that works today this should too, but it hasn't been confirmed
against your actual system yet.

## Known gaps / next steps

1. **Real evdev test.** Run the daemon and try recording + firing a hotkey
   for real. This is the biggest unknown since it can't be tested in this
   sandbox at all.
2. **Real display test of the GUI.** Only headless-tested so far — worth
   checking the collapsible rows, spacing, and OBS test-connection flow
   actually look right on your monitor.
3. **Daemon lifecycle.** Right now `daemon.py` is a plain foreground
   script (`Ctrl+C` to stop). Given you chose "separate background
   daemon" over a system-tray app, next step here is probably a systemd
   user service (or NixOS module, matching how the macro daemon is
   likely packaged) so it starts automatically and survives logout/login
   — let me know if you want that as part of the Settings page work or
   as a separate packaging task.
4. Local Library page (next build phase per your ordering).

## Files changed or added since the last delivery

New: `hotkeys.py`, `daemon.py`, `gui/__init__.py`, `gui/main.py`,
`gui/main_window.py`, `gui/settings_page.py`, `gui/clip_config_row.py`,
`gui/hotkey_record_dialog.py`.
Changed: `requirements.txt` (added PySide6, evdev), `shell.nix` (added
pyside6/evdev via nixpkgs, venv now uses `--system-site-packages` so those
compiled packages don't need to be pip-built inside the venv).
Unchanged from before: `config.py`, `db.py`, `editor.py`, `library.py`,
`clips.py`, `obs_client.py`, `cli.py`.

