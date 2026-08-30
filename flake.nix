{
  description = "afterglow - OBS-triggered clip capture, library, editor, and YouTube upload";

  inputs = {
    # Overridden by the parent flake's `inputs.nixpkgs.follows = "nixpkgs"`
    # (same pattern as puppetry/conduit) -- this default is only used if
    # afterglow is ever built standalone.
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      forAllSystems = nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ];

      # obsws-python isn't in nixpkgs, so it's packaged inline here from
      # PyPI. Version/hash pinned against 1.8.0 -- bump both together if
      # you ever update it (get the new hash with:
      #   nix hash convert --hash-algo sha256 --to sri $(sha256sum FILE | cut -d' ' -f1)
      # after downloading the new sdist).
      #
      # Takes `pkgs` as an explicit parameter (applied per-system in
      # mkPython below) rather than reaching for a `pyFinal.pkgs` backlink
      # -- that backlink exists on some nixpkgs python package sets but
      # isn't something worth depending on without being able to verify
      # it directly against the exact nixpkgs revision this ends up
      # building against.
      pythonOverlay = pkgs: pyFinal: pyPrev: {
        obsws-python = pyFinal.buildPythonPackage rec {
          pname = "obsws-python";
          version = "1.8.0";
          format = "pyproject";
          src = pyFinal.fetchPypi {
            pname = "obsws_python";
            inherit version;
            hash = "sha256-4IKJT4DesINoYf3DwiLklzCMj2YyjaYHW6ultFaiCXE=";
          };
          nativeBuildInputs = [ pyFinal.hatchling ];
          propagatedBuildInputs = [ pyFinal.websocket-client ];
          # Upstream has no test suite bundled in the sdist.
          doCheck = false;
          pythonImportsCheck = [ "obsws_python" ];
        };

        # Also not in nixpkgs as far as I could confirm -- same treatment.
        # Pinned against 1.0.8; single-module package (mpv.py), no deps of
        # its own beyond libmpv itself at runtime.
        #
        # mpv.py's module-level code resolves libmpv via
        # `ctypes.util.find_library("mpv")` the moment it's imported. On
        # NixOS this can only ever succeed by accident: find_library's
        # primary path shells out to `ldconfig -p`, which NixOS never
        # populates for store paths, and its only fallback
        # (_findLib_gcc/_findLib_ld) needs `gcc`/`ld` on PATH -- present in
        # the Nix build sandbox (via stdenv), which is why the
        # LD_LIBRARY_PATH attribute below made `pythonImportsCheck` pass
        # during `nix build`, but absent from the final wrapped binary's
        # runtime PATH (postFixup only adds ffmpeg/pipewire/pulseaudio),
        # which is why the packaged app fails with "Cannot find libmpv in
        # the usual places" even though the build itself succeeds.
        #
        # Fix: patch mpv.py to skip find_library entirely and hardcode the
        # resolved store path to libmpv.so, same as nixpkgs' own
        # python3Packages.python-mpv derivation does for exactly this
        # reason. This makes the LD_LIBRARY_PATH attribute unnecessary
        # (CDLL() is now given an absolute path, no linker search
        # involved) but it's left in place since it's harmless and still
        # documents the runtime dependency.
        python-mpv = pyFinal.buildPythonPackage rec {
          pname = "python-mpv";
          version = "1.0.8";
          format = "pyproject";
          src = pyFinal.fetchPypi {
            pname = "python_mpv";
            inherit version;
            hash = "sha256-AX+jWdoFnIMalMQZCDSRkD5tL3yBuYQcM8GWyr9LP+M=";
          };
          nativeBuildInputs = [ pyFinal.setuptools ];
          buildInputs = [ pkgs.mpv-unwrapped ];
          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.mpv-unwrapped ];
          postPatch = ''
            substituteInPlace mpv.py \
              --replace-fail "sofile = ctypes.util.find_library('mpv')" \
                             'sofile = "${pkgs.mpv-unwrapped}/lib/libmpv${pkgs.stdenv.hostPlatform.extensions.sharedLibrary}"'
          '';
          doCheck = false;
          pythonImportsCheck = [ "mpv" ];
        };
      };

      mkPython = pkgs: pkgs.python3.override {
        packageOverrides = pythonOverlay pkgs;
      };

      mkAfterglowPackage = pkgs:
        let
          python = mkPython pkgs;
        in
        python.pkgs.buildPythonApplication {
          pname = "afterglow";
          version = "0.1.0";
          format = "pyproject";
          src = ./.;

          nativeBuildInputs = [
            python.pkgs.setuptools
            pkgs.makeWrapper
            pkgs.qt6.wrapQtAppsHook
          ];

          # qtbase + qtwayland so both the xcb and wayland platform plugins'
          # own runtime library dependencies (including libxcb-cursor,
          # explicitly called out in the error you hit -- Qt >=6.5 requires
          # it for the xcb plugin) are actually present in the closure, not
          # just the plugin .so files themselves being "found" by path.
          # mpv-unwrapped provides libmpv.so for python-mpv's ctypes-based
          # FFI (the render-API video widget dlopens it at runtime, not at
          # Python import time, so this needs to be reachable via
          # LD_LIBRARY_PATH -- handled in postFixup below, not just listed
          # as a build dependency).
          buildInputs = [
            pkgs.qt6.qtbase
            pkgs.qt6.qtwayland
            pkgs.xcb-util-cursor
            pkgs.mpv-unwrapped
          ];

          propagatedBuildInputs = with python.pkgs; [
            obsws-python
            tomli-w
            pyside6
            evdev
            python-mpv
            pyopengl
          ];

          # ffmpeg and paplay (pipewire) are invoked as subprocesses, not
          # Python imports -- they need to be on PATH at runtime, not just
          # build time, hence wrapProgram rather than a build input.
          # Also installs the .desktop entry + icon set from data/ so the
          # app actually shows up in a DE's application launcher/search --
          # buildPythonApplication only installs the Python package by
          # default, not arbitrary XDG data.
          postInstall = ''
            install -Dm644 data/applications/afterglow.desktop \
              $out/share/applications/afterglow.desktop

            for size in 16 22 24 32 48 64 128 256 512; do
              install -Dm644 data/icons/hicolor/''${size}x''${size}/apps/afterglow.png \
                $out/share/icons/hicolor/''${size}x''${size}/apps/afterglow.png
            done
          '';

          # wrapQtAppsHook (from nativeBuildInputs above) provides the
          # `wrapQtApp` shell function used below -- it sets QT_PLUGIN_PATH,
          # QML2_IMPORT_PATH, and LD_LIBRARY_PATH correctly so Qt can
          # actually *load* whichever platform plugin it picks (xcb or
          # wayland), rather than finding the .so but failing to resolve
          # its own dependencies -- which is exactly what "Could not load
          # the Qt platform plugin ... even though it was found" means.
          #
          # This does NOT force a specific platform/backend -- Qt still
          # auto-detects xcb vs wayland at runtime from the session's
          # XDG_SESSION_TYPE / WAYLAND_DISPLAY, same as any other Qt app.
          # That auto-detection is what makes this version/DE-agnostic:
          # it works whether the user is on Wayland or X11, and on
          # whatever Plasma/Qt version they're running, because it's fixing
          # *library loading*, not *platform selection*.
          #
          # Important: wrapQtAppsHook's automatic detection only reliably
          # wraps ELF binaries, not the text/shebang entry-point scripts
          # buildPythonApplication generates for console_scripts -- so
          # `wrapQtApp` is called explicitly here rather than relying on
          # auto-wrap. wrapProgram is layered on top afterward for the
          # ffmpeg/audio PATH additions; wrapProgram is safe to call on an
          # already-wrapped script, it just extends the existing wrapper.
          postFixup = ''
            for prog in afterglow afterglow-daemon afterglow-cli; do
              wrapQtApp "$out/bin/$prog"
              wrapProgram "$out/bin/$prog" \
                --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.ffmpeg pkgs.pipewire pkgs.pulseaudio ]} \
                --prefix LD_LIBRARY_PATH : ${pkgs.lib.makeLibraryPath [ pkgs.mpv-unwrapped ]}
            done
          '';

          # No test suite wired up for nix's own check phase yet (the
          # project's tests are the __main__ smoke-tests in each module,
          # run manually during development, not a pytest suite).
          doCheck = false;

          meta = {
            description = "OBS-triggered clip capture, local clip library, editor, and YouTube upload";
            homepage = "https://github.com/mr-tinkle-winkle/afterglow";
            platforms = pkgs.lib.platforms.linux;
          };
        };
    in
    {
      packages = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        {
          default = mkAfterglowPackage pkgs;
        });

      devShells = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          python = mkPython pkgs;
        in
        {
          default = pkgs.mkShell {
            packages = [
              (python.withPackages (ps: [
                ps.obsws-python
                ps.tomli-w
                ps.pyside6
                ps.evdev
                ps.python-mpv
                ps.pyopengl
                ps.setuptools
              ]))
              pkgs.ffmpeg
              pkgs.pipewire
              pkgs.qt6.qtbase
              pkgs.qt6.qtwayland
              pkgs.mpv-unwrapped
            ];
            # Same runtime-linking issue as the packaged app -- a plain
            # `nix develop` shell doesn't get automatic Qt wrapping the
            # way wrapQtAppsHook gives the built package, so the plugin
            # paths need setting by hand here too. Same for libmpv.
            shellHook = ''
              export QT_PLUGIN_PATH="${pkgs.qt6.qtbase}/lib/qt-6/plugins:${pkgs.qt6.qtwayland}/lib/qt-6/plugins''${QT_PLUGIN_PATH:+:$QT_PLUGIN_PATH}"
              export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath [ pkgs.mpv-unwrapped ]}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
              echo "afterglow dev shell (via flake). Try: python -m afterglow.cli settings show"
            '';
          };
        });

      nixosModules.default = { config, lib, pkgs, ... }:
        let
          cfg = config.services.afterglow;
          afterglowPkg = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
        in
        {
          options.services.afterglow = {
            enable = lib.mkEnableOption "the afterglow clip-capture daemon";

            user = lib.mkOption {
              type = lib.types.str;
              description = ''
                User to run the afterglow background daemon as, and to grant
                /dev/input read access to (needed for hotkey capture).
              '';
            };
          };

          config = lib.mkIf cfg.enable {
            environment.systemPackages = [ afterglowPkg ];

            # Hotkey capture reads /dev/input/event* via evdev -- same
            # requirement as puppetry.
            users.users.${cfg.user}.extraGroups = [ "input" ];

            # Runs as a per-user systemd service (not system-wide) since it
            # needs to reach the user's own OBS instance, PipeWire session,
            # and eventually their linked YouTube account -- all
            # per-session/per-user resources.
            systemd.user.services.afterglow-daemon = {
              description = "afterglow background clip-capture daemon";
              wantedBy = [ "default.target" ];
              serviceConfig = {
                ExecStart = "${afterglowPkg}/bin/afterglow-daemon";
                Restart = "on-failure";
                RestartSec = 2;
              };
            };
          };
        };
    };
}
