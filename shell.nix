{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  name = "clipping-app-dev";

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
      echo "Creating venv and installing deps (obsws-python, tomli_w)..."
      python3 -m venv .venv --system-site-packages
      .venv/bin/pip install --quiet obsws-python tomli_w
    fi
    source .venv/bin/activate
    echo "clipping-app dev shell ready."
    echo "  GUI:    python gui/main.py"
    echo "  Daemon: python daemon.py"
    echo "  CLI:    python cli.py settings show"
    echo ""
    echo "Note: reading /dev/input for hotkeys requires this user to be in"
    echo "the 'input' group (same requirement as the macro daemon)."
  '';
}
