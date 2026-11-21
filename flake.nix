{
  description = "Local video transcription, timestamp alignment, acoustic prosody and audited review";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
    }:
    let
      inherit (nixpkgs) lib;
      # nixpkgs 26.11 dropped x86_64-darwin.
      systems = [
        "aarch64-darwin"
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
      overlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };

      # MLX transcription needs Apple Silicon; Linux gets every other stage.
      extrasFor =
        pkgs:
        if pkgs.stdenv.hostPlatform.isDarwin then
          [
            "asr"
            "prosody"
            "japanese"
          ]
        else
          [
            "prosody"
            "japanese"
          ];

      # pyopenjtalk downloads this dictionary into its own package directory on first
      # use, which the read-only store forbids; supply it and point the library at it.
      openJTalkDict =
        pkgs:
        pkgs.fetchzip {
          url = "https://github.com/r9y9/open_jtalk/releases/download/v1.11.1/open_jtalk_dic_utf_8-1.11.tar.gz";
          hash = "sha256-+6cHKujNEzmJbpN9Uan6kZKsPdwxRRzT3ZazDnCNi3s=";
        };

      # Each fix applies only when the set contains the package, so a dependent set
      # without some extras can still compose these.
      packageOverrides =
        pkgs: final: prev:
        lib.filterAttrs (name: _: prev ? ${name}) {
          # libmlx.dylib ships in the separate mlx-metal wheel; the extension modules
          # only look beside themselves, which differs from it in the store.
          mlx = prev.mlx.overrideAttrs (old: {
            postFixup = (old.postFixup or "") + ''
              find $out -name '*.so' -exec install_name_tool -add_rpath \
                ${final.mlx-metal}/${final.python.sitePackages}/mlx/lib {} \;
            '';
          });
          pyopenjtalk = prev.pyopenjtalk.overrideAttrs (old: {
            nativeBuildInputs =
              (old.nativeBuildInputs or [ ])
              ++ [ pkgs.cmake ]
              ++ final.resolveBuildSystem {
                setuptools = [ ];
                setuptools-scm = [ ];
                cython = [ ];
                numpy = [ ];
              };
            # setup.py runs CMake itself to generate config.h.
            dontUseCmakeConfigure = true;
          });
          # The Linux wheel's optional TBB threading layer links libtbb.so.12.
          numba = prev.numba.overrideAttrs (old: {
            buildInputs =
              (old.buildInputs or [ ]) ++ lib.optionals pkgs.stdenv.hostPlatform.isLinux [ pkgs.onetbb ];
          });
          unidic-lite = prev.unidic-lite.overrideAttrs (old: {
            nativeBuildInputs =
              (old.nativeBuildInputs or [ ]) ++ final.resolveBuildSystem { setuptools = [ ]; };
          });
          # Dependents lock this project as a git source, for which uv.lock records
          # no build system.
          video-prosody = prev.video-prosody.overrideAttrs (old: {
            nativeBuildInputs =
              (old.nativeBuildInputs or [ ])
              ++ final.resolveBuildSystem {
                setuptools = [ ];
                wheel = [ ];
              };
          });
        };

      pythonSet =
        pkgs:
        (pkgs.callPackage pyproject-nix.build.packages { python = pkgs.python312; }).overrideScope (
          lib.composeManyExtensions [
            pyproject-build-systems.overlays.wheel
            overlay
            (packageOverrides pkgs)
          ]
        );

      runtimeEnv = pkgs: {
        IMAGEIO_FFMPEG_EXE = "${pkgs.ffmpeg-headless}/bin/ffmpeg";
        OPEN_JTALK_DICT_DIR = "${openJTalkDict pkgs}";
      };
      runtimeInputs = pkgs: [ pkgs.ffmpeg-headless ];
    in
    {
      # For dependent flakes, such as the private YouTube adapter. Compose
      # pyprojectOverrides into another uv2nix package set that locks video-prosody
      # with its extras, or extend pythonSet; export runtimeEnv and put runtimeInputs
      # on PATH in the wrapper.
      lib = {
        inherit
          extrasFor
          pythonSet
          runtimeEnv
          runtimeInputs
          ;
        pyprojectOverrides = packageOverrides;
      };

      packages = forAllSystems (
        pkgs:
        let
          env = (pythonSet pkgs).mkVirtualEnv "video-prosody-env" { video-prosody = extrasFor pkgs; };
        in
        {
          # Interpreter with core and the platform's inference extras; set runtimeEnv.
          python-env = env;
          default =
            pkgs.runCommand "video-prosody-${(lib.importTOML ./pyproject.toml).project.version}"
              {
                nativeBuildInputs = [ pkgs.makeWrapper ];
                meta.mainProgram = "video-prosody";
              }
              ''
                mkdir -p $out/bin
                makeWrapper ${env}/bin/video-prosody $out/bin/video-prosody \
                  --prefix PATH : ${lib.makeBinPath (runtimeInputs pkgs)} \
                  ${lib.concatStringsSep " " (
                    lib.mapAttrsToList (k: v: "--set-default ${k} ${v}") (runtimeEnv pkgs)
                  )}
              '';
        }
      );

      apps = forAllSystems (pkgs: {
        default = {
          type = "app";
          program = lib.getExe self.packages.${pkgs.stdenv.hostPlatform.system}.default;
        };
      });

      devShells = forAllSystems (
        pkgs:
        let
          editableSet = (pythonSet pkgs).overrideScope (
            workspace.mkEditablePyprojectOverlay { root = "$REPO_ROOT"; }
          );
          env = editableSet.mkVirtualEnv "video-prosody-dev-env" { video-prosody = extrasFor pkgs; };
        in
        {
          default = pkgs.mkShell {
            packages = [
              env
              pkgs.uv
              pkgs.nodejs
              pkgs.ffmpeg-headless
            ];
            env = runtimeEnv pkgs // {
              UV_NO_SYNC = "1";
              UV_PYTHON = "${env}/bin/python";
              UV_PYTHON_DOWNLOADS = "never";
            };
            shellHook = ''
              unset PYTHONPATH
              export REPO_ROOT=$(git rev-parse --show-toplevel)
            '';
          };
        }
      );

      checks = forAllSystems (pkgs: {
        tests =
          pkgs.runCommand "video-prosody-tests"
            {
              nativeBuildInputs = [
                ((pythonSet pkgs).mkVirtualEnv "video-prosody-test-env" { video-prosody = [ ]; })
              ];
            }
            ''
              cp -r ${./tests} tests
              python -m unittest discover -s tests
              touch $out
            '';
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt);
    };
}
