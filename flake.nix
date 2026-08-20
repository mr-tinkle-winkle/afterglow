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
      obswsPythonOverlay = pyFinal: pyPrev: {
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
      };

      mkPython = pkgs: pkgs.python3.override {
        packageOverrides = obswsPythonOverlay;
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
          ];

          propagatedBuildInputs = with python.pkgs; [
            obsws-python
            tomli-w
            pyside6
            evdev
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

          postFixup = ''
            for prog in afterglow afterglow-daemon afterglow-cli; do
              wrapProgram $out/bin/$prog \
                --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.ffmpeg pkgs.pipewire pkgs.pulseaudio ]}
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
                ps.setuptools
              ]))
              pkgs.ffmpeg
              pkgs.pipewire
            ];
            shellHook = ''
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
