{ pkgs ? import <nixpkgs> {} }:

# Legacy pip-venv dev shell, kept for convenience. Prefer `nix develop`
# (via flake.nix's devShells.default) going forward -- it uses properly
# packaged Nix derivations for every dependency including obsws-python,
# rather than pip-installing into a venv.

pkgs.mkShell {
  name = "afterglow-dev";

  buildInputs = with pkgs; [
    python3
    python3Packages.pip
    python3Packages.virtualenv
    python3Packages.pyside6
    python3Packages.evdev
    ffmpeg
    pipewire   # provides `paplay` for clip-capture sound feedback
    sqlite
  ];

  shellHook = ''
    if [ ! -d .venv ]; then
      echo "Creating venv and installing afterglow (editable) + obsws-python..."
      python3 -m venv .venv --system-site-packages
      .venv/bin/pip install --quiet -e .
    fi
    source .venv/bin/activate
    echo "afterglow dev shell ready."
    echo "  GUI:    afterglow"
    echo "  Daemon: afterglow-daemon"
    echo "  CLI:    afterglow-cli settings show"
    echo ""
    echo "Note: reading /dev/input for hotkeys requires this user to be in"
    echo "the 'input' group (same requirement as puppetry)."
  '';
}
