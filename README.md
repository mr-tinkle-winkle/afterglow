# afterglow

OBS-triggered clip capture, local clip library, Medal-style editor, and
YouTube upload. This is the `mr-tinkle-winkle/afterglow` repo, structured
to match the `puppetry`/`conduit` flake pattern for inclusion in a NixOS
system flake.

## Status

Build order, in progress:

1. **Settings page + OBS-backed clip capture — done.**
2. Local Library page (video player, tags, Medal-style trim editor +
   audio graph editor) — next.
3. Uploaded Library page + YouTube OAuth/upload — after that.

This delivery is the packaging pass: the existing Python code (previously
delivered as loose scripts) is now a proper installable package
(`afterglow/`) with a `flake.nix` exposing `packages.default`,
`devShells.default`, and `nixosModules.default`, matching how puppetry and
conduit are wired into the system flake.

## Layout

```
afterglow/            the installable Python package
  config.py, db.py, editor.py, library.py, clips.py, obs_client.py,
  hotkeys.py, daemon.py, cli.py
  gui/                 PySide6 Settings page (Clips/Editor pages pending)
pyproject.toml         package metadata + entry points
flake.nix              packages.default, devShells.default, nixosModules.default
shell.nix              legacy pip-venv dev shell (nix develop is preferred now)
```

Entry points (from `pyproject.toml`):
- `afterglow` — GUI
- `afterglow-daemon` — background hotkey/capture daemon
- `afterglow-cli` — CLI (see `afterglow/cli.py`'s docstring for commands)

## Using this as a flake input

This is already set up to match your existing pattern:

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
  `afterglow-cli` are on PATH, and the GUI is launchable)
- adds `cfg.user` to the `input` group (needed for evdev hotkey capture —
  same requirement puppetry already has for that user)
- runs `afterglow-daemon` as a **systemd user service** (`systemd.user`,
  not a system-wide service) under `default.target`, with
  `Restart = "on-failure"`. It's a user service rather than system-wide
  because it needs to reach that user's own OBS instance and PipeWire
  session.

## obsws-python packaging note

`obsws-python` isn't in nixpkgs, so `flake.nix` packages it inline from
PyPI (pinned to 1.8.0, hash computed directly from the downloaded sdist).
If you ever need to bump it: download the new sdist from PyPI, and either
run `nix hash convert --hash-algo sha256 --to sri <hex-digest>` or ask me
to recompute it the way I did this one (downloaded the file directly and
hashed it, since I don't have a live `nix` binary to prefetch with).

## ⚠️ Not verified against a real Nix evaluation

I don't have `nix` available in my working environment, so **nothing in
`flake.nix` has actually been built or `nix flake check`-ed.** Everything
I could test without Nix, I did test directly and confirmed working:

- The Python package refactor (flat scripts → `afterglow/` package with
  relative imports) — installed via `pip install -e .` into a clean venv
  and re-ran the full backend + GUI regression suite against the
  installed package, not just the source tree. All passed.
- The `obsws-python` version/hash — downloaded the real sdist from PyPI
  and hashed it directly, not guessed.
- `pyproject.toml`'s entry points — confirmed `afterglow-cli`,
  and the `afterglow`/`afterglow-daemon` modules, all import and run
  correctly as installed console scripts.

What I could **not** test here (no `nix` binary in this environment):
- Whether `flake.nix` evaluates at all (syntax is correct by manual
  review, but Nix has no equivalent of "looks right" — it either
  evaluates or it doesn't)
- Whether `python3Packages.pyside6` / `python3Packages.evdev` exist under
  those exact names in whatever nixpkgs revision your flake pins to
- Whether the `buildPythonApplication` + `pyproject` format build
  actually succeeds (setuptools picking up the entry points correctly
  under Nix's build sandbox, rather than a normal pip environment)
- The systemd user service definition, or the `extraGroups` merge with
  whatever puppetry's module already sets for the same user

**Next step on your end:** push this to the repo, run
`flake-update afterglow` (should now succeed since `flake.nix` exists),
then try an actual rebuild. If it fails, paste me the error — Nix build
errors are usually precise about exactly what's wrong, and I can fix it
from that rather than guessing blind.
