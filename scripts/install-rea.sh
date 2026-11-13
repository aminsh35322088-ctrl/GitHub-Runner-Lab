#!/usr/bin/env bash
set -Eeuo pipefail

# REA tracks npm's latest stable dist-tag, but an install never floats: the
# dist-tag is resolved exactly once per run, the resolved version's
# engines.node range is checked against the pinned Node runtime, the registry
# tarball's SHA-512 integrity (dist.integrity) is verified before npm executes
# it, and only that exact version is installed. The resolved version and
# integrity are recorded in providers.env (REA_VERSION / REA_INTEGRITY).
# The companion providers below are a tested, fixed compatibility set for the
# current REA 6.8.x contract. Update these pins only after a provider smoke test.
readonly REA_PACKAGE_NAME='rea-agents'
readonly REA_DIST_TAG='latest'
readonly NODE_VERSION_PIN='26.12.1'
readonly PYTHON_VERSION_LINE='3.13'
readonly CHROME_VERSION_PREFIX='156.0.8078.'
readonly NODE_X64_ARCHIVE='node-v26.12.1-linux-x64.tar.xz'
readonly NODE_X64_URL='https://nodejs.org/dist/v26.12.1/node-v26.12.1-linux-x64.tar.xz'
readonly NODE_X64_SHA256='6170b2d474e1c626f43cff3adc118df9fe3b573258a4fc3d850c8dbc6b079bbd'
readonly NODE_ARM64_ARCHIVE='node-v26.12.1-linux-arm64.tar.xz'
readonly NODE_ARM64_URL='https://nodejs.org/dist/v26.12.1/node-v26.12.1-linux-arm64.tar.xz'
readonly NODE_ARM64_SHA256='42aa04922591ee5d4dbc7740d452621acf28508239daa512f5ee8c47ee224fdb'
readonly GHIDRA_VERSION='12.1.4'
readonly GHIDRA_ARCHIVE='ghidra_12.1.4_PUBLIC_20260921.zip'
readonly GHIDRA_URL='https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.1.4_build/ghidra_12.1.4_PUBLIC_20260921.zip'
readonly GHIDRA_SHA256='ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db'
readonly JDK_VERSION='21.0.13+9'
readonly JDK_X64_ARCHIVE='OpenJDK21U-jdk_x64_linux_hotspot_21.0.13_9.tar.gz'
readonly JDK_X64_URL='https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9/OpenJDK21U-jdk_x64_linux_hotspot_21.0.13_9.tar.gz'
readonly JDK_X64_SHA256='d7f5d5cf865a2902b70ff549b97586a7d3f234e016f041ab447ea740c7d66d1c'
readonly JDK_ARM64_ARCHIVE='OpenJDK21U-jdk_aarch64_linux_hotspot_21.0.13_9.tar.gz'
readonly JDK_ARM64_URL='https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9/OpenJDK21U-jdk_aarch64_linux_hotspot_21.0.13_9.tar.gz'
readonly JDK_ARM64_SHA256='e3522e707fc7dbe2a72c30746b1e7068301a0213ecd213d4491f35a14eb55578'
readonly WABT_VERSION='1.0.42'
readonly WABT_X64_ARCHIVE='wabt-1.0.42-linux-x64.tar.gz'
readonly WABT_X64_URL='https://github.com/WebAssembly/wabt/releases/download/1.0.42/wabt-1.0.42-linux-x64.tar.gz'
readonly WABT_X64_SHA256='84895407a6bbb80e918f33b16b2fb2206021c150b6bc9ff6f761263a745ab131'
readonly WABT_ARM64_ARCHIVE='wabt-1.0.42-linux-arm64.tar.gz'
readonly WABT_ARM64_URL='https://github.com/WebAssembly/wabt/releases/download/1.0.42/wabt-1.0.42-linux-arm64.tar.gz'
readonly WABT_ARM64_SHA256='a7632c1cc66f70886441842ecf5a2ffa91a98c79e026059458451234ad013ea8'
readonly JADX_VERSION='0.7.1'
readonly JADX_ARCHIVE='jadx-headless-mcp-0.7.1-all.jar'
readonly JADX_URL='https://github.com/1013503897/jadx-headless-mcp/releases/download/v0.7.1/jadx-headless-mcp-0.7.1-all.jar'
readonly JADX_SHA256='6e5eacf500b64292bfb73c49797c1958f6ee44646e43e868039ae7feb573ff75'
readonly APKTOOL_VERSION='2.12.1'
readonly APKTOOL_ARCHIVE='apktool_2.12.1.jar'
readonly APKTOOL_URL='https://github.com/iBotPeaches/Apktool/releases/download/v2.12.1/apktool_2.12.1.jar'
readonly APKTOOL_SHA256='66cf4524a4a45a7f56567d08b2c9b6ec237bcdd78cee69fd4a59c8a0243aeafa'
readonly MITMPROXY_VERSION='12.2.3'
readonly FRIDA_TOOLS_VERSION='14.11.0'
readonly FRIDA_RUNTIME_VERSION='17.25.2'
readonly PWNTOOLS_VERSION='4.15.0'
readonly PYELFTOOLS_VERSION='0.33'
readonly CAPSTONE_VERSION='5.0.9'
readonly UNICORN_VERSION='2.1.2'
readonly UNBLOB_VERSION='26.6.4'
readonly BINWALK3_PY_VERSION='3.2.0'
readonly BINWALK_VERSION='3.1.0'
readonly BINWALK_GIT_REVISION='4fdab3d464d97b68e0af9088df3f9e2e1545b21c'

INSTALL_ROOT="${REA_INSTALL_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/rea}"
SKIP_OS=false SKIP_GHIDRA=false SKIP_ANDROID=false SKIP_NETWORK=false SKIP_BROWSER=false SKIP_PYTHON_TOOLS=false SKIP_BINWALK=false DRY_RUN=false

usage() {
  cat <<'USAGE'
Usage: install-rea.sh [options]

Install the REA CLI plus a fixed, verified provider set. REA tracks npm's
latest stable dist-tag, resolved exactly once per run to an exact version:
that version's engines.node range is checked, its registry tarball SHA-512
integrity (dist.integrity) is verified before installation, and the resolved
version is recorded in providers.env. Provider versions and checksums are
intentionally pinned to a tested REA-compatible baseline.

Pinned runtime/providers: Node 26.12.1, Python 3.13.x Runner toolcache patch, Ghidra 12.1.4,
Temurin JDK 21.0.13+9, WABT 1.0.42, JADX bridge 0.7.1, Apktool 2.12.1,
TShark from the distribution's current Wireshark 4.x package, mitmproxy 12.2.3, Frida tools 14.11.0,
Frida runtime 17.25.2, pwntools 4.15.0, capstone 5.0.9, pyelftools 0.33,
Unicorn 2.1.2, Unblob 26.6.4, Binwalk 3.1.0, Binwalk3 Python API 3.2.0,
and system Google Chrome 156.0.8078.x from the Runner image. REA itself carries
no version pin in this script; each run resolves and verifies the current
latest release as described above.

Options:
  --skip-os-packages   Do not use apt for host-level prerequisites.
  --skip-ghidra        Skip Ghidra and its pinned JDK.
  --skip-android       Skip JADX bridge and Apktool.
  --skip-network       Skip mitmproxy and TShark.
  --skip-browser       Skip the preinstalled Google Chrome smoke check.
  --skip-python-tools  Skip Frida tools, pwntools, Unblob and the Binwalk Python API.
  --skip-binwalk       Skip building the pinned native Binwalk executable.
  --dry-run            Print the plan without changing the host.
  -h, --help           Show this help.

The installer does not run `rea setup`, register MCP clients, or edit OpenCode
or Codex configuration. Commercial providers (IDA/Hopper/JEB) are not installed.
USAGE
}

while (($#)); do
  case "$1" in
    --skip-os-packages) SKIP_OS=true ;;
    --skip-ghidra) SKIP_GHIDRA=true ;;
    --skip-android) SKIP_ANDROID=true ;;
    --skip-network) SKIP_NETWORK=true ;;
    --skip-browser) SKIP_BROWSER=true ;;
    --skip-python-tools) SKIP_PYTHON_TOOLS=true ;;
    --skip-binwalk) SKIP_BINWALK=true ;;
    --dry-run) DRY_RUN=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[install-rea] Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

log() { printf '[install-rea] %s\n' "$*"; }
warn() { printf '[install-rea] WARNING: %s\n' "$*" >&2; }
die() { printf '[install-rea] ERROR: %s\n' "$*" >&2; exit 1; }
# Expose the pinned native Binwalk CLI through the shared command directory.
# The provider lives in its own root, while Shell and Tool Center resolve
# commands through /opt/runner-tools/bin.
link_binwalk_cli() {
  local binary="$1" shared_bin="$2" version="$3"
  [[ -x "$binary" ]] || die "Pinned Binwalk executable is missing: $binary"
  mkdir -p "$shared_bin"
  ln -sfn "$binary" "$shared_bin/binwalk"
  [[ -x "$shared_bin/binwalk" ]] || die "Could not expose Binwalk at $shared_bin/binwalk"
  "$shared_bin/binwalk" --version | grep -q "binwalk $version" || die 'Shared Binwalk command failed its version check.'
}

# Make providers.env visible to login and interactive Bash startup paths.
# The stock ~/.bashrc returns early for non-interactive shells, so this block
# must precede that guard. ~/.bash_profile takes precedence over ~/.profile.
install_provider_shell_environment() {
  local env_file="$1" startup_file clean_file staged_file
  local start_marker='# >>> GitHub Runner Lab REA environment >>>'
  local end_marker='# <<< GitHub Runner Lab REA environment <<<'
  for startup_file in "$HOME/.profile" "$HOME/.bashrc" "$HOME/.bash_profile"; do
    touch "$startup_file"
    clean_file="$(mktemp)"
    staged_file="$(mktemp "${startup_file}.XXXXXX")"
    awk -v start="$start_marker" -v end="$end_marker" '
      $0 == start { skip=1; next }
      $0 == end { skip=0; next }
      !skip { print }
    ' "$startup_file" > "$clean_file"
    {
      printf '%s\n' "$start_marker"
      # shellcheck disable=SC2016 # Expand this guard only in the future shell.
      printf 'if [[ "${REA_PROVIDERS_ENV_LOADED:-}" != "1" && -r %q ]]; then\n' "$env_file"
      printf '  source %q\n' "$env_file"
      printf '  export REA_PROVIDERS_ENV_LOADED=1\n'
      printf 'fi\n'
      printf '%s\n' "$end_marker"
      cat "$clean_file"
    } > "$staged_file"
    chmod --reference="$startup_file" "$staged_file"
    mv -f "$staged_file" "$startup_file"
    rm -f "$clean_file"
  done
}

[[ "$(uname -s)" == Linux ]] || die 'This installer supports Linux only.'
case "$(uname -m)" in x86_64|aarch64) HOST_ARCH="$(uname -m)" ;; *) die "Unsupported architecture: $(uname -m)" ;; esac

# Preserve provider paths when an operator uses skip flags for a partial update.
# Restore the caller PATH after sourcing so repeated installs do not duplicate it.
# A dry run must never execute this file: it is operator-controlled content and
# --dry-run is documented as side-effect-free, so it is skipped (and reported).
BASE_PATH="$PATH"
if [[ "$DRY_RUN" == true ]]; then
  [[ -r "$INSTALL_ROOT/providers.env" ]] && log "DRY-RUN: not sourcing existing providers.env; a dry run never executes it."
elif [[ -r "$INSTALL_ROOT/providers.env" ]]; then
  # shellcheck disable=SC1090,SC1091
  source "$INSTALL_ROOT/providers.env"
  PATH="$BASE_PATH"
fi

if [[ "$SKIP_OS" != true ]]; then
  OS_PACKAGES=(ca-certificates curl file jq procps binutils gdb strace ltrace unzip zip xz-utils python3 python3-venv python3-pip python3-dev build-essential libmagic1 libssl-dev libffi-dev pkg-config zlib1g-dev liblzma-dev libbz2-dev libsqlite3-dev)
  [[ "$SKIP_NETWORK" == true ]] || OS_PACKAGES+=(tshark)
  if [[ "$DRY_RUN" == true ]]; then
    log "DRY-RUN: apt-get update + install base analysis prerequisites: ${OS_PACKAGES[*]}"
  else
    if [[ "$EUID" -eq 0 ]]; then APT=(apt-get)
    elif command -v sudo >/dev/null 2>&1; then APT=(sudo -n apt-get)
    else die 'OS prerequisites require root or passwordless sudo; use --skip-os-packages to skip them.'
    fi
    "${APT[@]}" -qq update
    DEBIAN_FRONTEND=noninteractive "${APT[@]}" install --yes --no-install-recommends "${OS_PACKAGES[@]}"
  fi
fi

log "Pinned runtime prerequisite: Node.js $NODE_VERSION_PIN. REA resolves once from $REA_PACKAGE_NAME@$REA_DIST_TAG to an exact, integrity-verified version."
INSTALL_ROOT="${REA_INSTALL_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/rea}"
if [[ "$DRY_RUN" == true ]]; then
  log "DRY-RUN: install Node.js $NODE_VERSION_PIN, resolve $REA_PACKAGE_NAME@$REA_DIST_TAG once to an exact version, verify its engines.node range and tarball SHA-512 integrity, and install only that version."
  NODE_HOME="$INSTALL_ROOT/node/node-v$NODE_VERSION_PIN"
  NPM_GLOBAL_PREFIX="$INSTALL_ROOT/npm-global"
  NPM_GLOBAL_BIN="$NPM_GLOBAL_PREFIX/bin"
  NPM_GLOBAL_ROOT="$NPM_GLOBAL_PREFIX/lib/node_modules"
  REA_ROOT="$NPM_GLOBAL_ROOT/rea-agents"
else
  for command_name in python3 curl sha256sum tar xz readlink; do
    command -v "$command_name" >/dev/null 2>&1 || die "Missing Node bootstrap command: $command_name"
  done
  mkdir -p "$INSTALL_ROOT/node" "$INSTALL_ROOT/downloads"
  case "$HOST_ARCH" in
    x86_64) NODE_ARCHIVE="$NODE_X64_ARCHIVE"; NODE_URL="$NODE_X64_URL"; NODE_SHA256="$NODE_X64_SHA256" ;;
    aarch64) NODE_ARCHIVE="$NODE_ARM64_ARCHIVE"; NODE_URL="$NODE_ARM64_URL"; NODE_SHA256="$NODE_ARM64_SHA256" ;;
  esac
  NODE_HOME="$INSTALL_ROOT/node/node-v$NODE_VERSION_PIN"
  if [[ ! -x "$NODE_HOME/bin/node" || ! -x "$NODE_HOME/bin/npm" ]]; then
    NODE_PART="$INSTALL_ROOT/downloads/$NODE_ARCHIVE.part"
    curl --fail --location --retry 3 --retry-all-errors --connect-timeout 30 "$NODE_URL" --output "$NODE_PART"
    printf '%s  %s\n' "$NODE_SHA256" "$NODE_PART" | sha256sum --check --status || die 'Pinned Node archive checksum mismatch.'
    mv -f "$NODE_PART" "$INSTALL_ROOT/downloads/$NODE_ARCHIVE"
    NODE_STAGE="$(mktemp -d "$INSTALL_ROOT/node/.stage.XXXXXX")"
    tar -xJf "$INSTALL_ROOT/downloads/$NODE_ARCHIVE" -C "$NODE_STAGE"
    NODE_SUFFIX=x64; [[ "$HOST_ARCH" == aarch64 ]] && NODE_SUFFIX=arm64
    NODE_EXTRACTED="$NODE_STAGE/node-v$NODE_VERSION_PIN-linux-$NODE_SUFFIX"
    [[ -x "$NODE_EXTRACTED/bin/node" && -x "$NODE_EXTRACTED/bin/npm" ]] || die 'Pinned Node archive layout was unexpected.'
    rm -rf "$NODE_HOME"
    mv "$NODE_EXTRACTED" "$NODE_HOME"
    rm -rf "$NODE_STAGE"
  fi
  export NODE_HOME
  export PATH="$NODE_HOME/bin:$PATH"
  export NPM_CONFIG_PREFIX="$INSTALL_ROOT/npm-global"
  NODE_VERSION="$(node --version)"
  [[ "$NODE_VERSION" == "v$NODE_VERSION_PIN" ]] || die "Expected Node v$NODE_VERSION_PIN, found $NODE_VERSION."
  # Resolve the floating dist-tag exactly once. Every later registry query and
  # the install itself use the immutable exact version, so the engine check
  # cannot validate a different release than the one installed (no TOCTOU).
  REA_VERSION="$(npm view "$REA_PACKAGE_NAME@$REA_DIST_TAG" version 2>/dev/null | tail -n 1 | tr -d '\r')"
  [[ "$REA_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] || die "Could not resolve $REA_PACKAGE_NAME@$REA_DIST_TAG to an exact version (got: $REA_VERSION)."
  REA_SPEC="$REA_PACKAGE_NAME@$REA_VERSION"
  REA_ENGINE_RANGE="$(npm view "$REA_SPEC" engines.node 2>/dev/null | tail -n 1 | tr -d '\r')"
  [[ -n "$REA_ENGINE_RANGE" ]] || die "Could not read $REA_SPEC engines.node from npm."
  REA_TARBALL_URL="$(npm view "$REA_SPEC" dist.tarball 2>/dev/null | tail -n 1 | tr -d '\r')"
  REA_INTEGRITY="$(npm view "$REA_SPEC" dist.integrity 2>/dev/null | tail -n 1 | tr -d '\r')"
  [[ "$REA_TARBALL_URL" == https://* && "$REA_INTEGRITY" == sha512-* ]] || die "Could not read $REA_SPEC tarball metadata from npm."
  NPM_CLI_PATH="$(readlink -f "$(command -v npm)")"
  NPM_PACKAGE_ROOT="$(cd -- "$(dirname -- "$NPM_CLI_PATH")/.." && pwd -P)"
  SEMVER_MODULE="$NPM_PACKAGE_ROOT/node_modules/semver"
  [[ -f "$SEMVER_MODULE/package.json" ]] || die 'npm bundled semver is missing; refusing global installation.'
  node - "$SEMVER_MODULE" "$NODE_VERSION" "$REA_ENGINE_RANGE" <<'NODE'
const semver = require(process.argv[2]);
const version = semver.clean(process.argv[3]);
const range = process.argv[4];
if (!version || semver.prerelease(version) || !semver.satisfies(version, range)) {
  console.error(`Pinned Node.js ${process.argv[3]} does not satisfy the resolved REA engines.node range (${range}).`);
  process.exit(1);
}
NODE
  NPM_GLOBAL_PREFIX="$(npm prefix --global)"
  NPM_GLOBAL_BIN="$NPM_GLOBAL_PREFIX/bin"
  NPM_GLOBAL_ROOT="$(npm root --global)"
  export PATH="$NPM_GLOBAL_BIN:$PATH"
  # Download the exact version's tarball and verify the registry-published
  # SHA-512 integrity before npm executes any of it.
  REA_TARBALL="$INSTALL_ROOT/downloads/rea-agents-$REA_VERSION.tgz"
  curl --fail --location --retry 3 --retry-all-errors --connect-timeout 30 "$REA_TARBALL_URL" --output "$REA_TARBALL.part"
  REA_TARBALL_INTEGRITY="$(node -e 'const c=require("crypto"),f=require("fs");console.log("sha512-"+c.createHash("sha512").update(f.readFileSync(process.argv[1])).digest("base64"));' "$REA_TARBALL.part")"
  [[ "$REA_TARBALL_INTEGRITY" == "$REA_INTEGRITY" ]] || die "REA $REA_VERSION tarball integrity mismatch (expected $REA_INTEGRITY, got $REA_TARBALL_INTEGRITY)."
  mv -f "$REA_TARBALL.part" "$REA_TARBALL"
  npm_config_engine_strict=true npm install --global --allow-scripts=frida "$REA_TARBALL"
  REA_ROOT="$NPM_GLOBAL_ROOT/rea-agents"
  command -v rea >/dev/null 2>&1 || die 'REA installed but its CLI is not on PATH.'
  [[ -f "$REA_ROOT/package.json" ]] || die "Cannot resolve REA under $NPM_GLOBAL_ROOT."
  log "Installed REA CLI $(rea --version) (exact version $REA_VERSION resolved from $REA_DIST_TAG, tarball integrity verified) on pinned Node $NODE_VERSION."
  for command_name in curl jq sha256sum tar unzip awk find grep; do
    command -v "$command_name" >/dev/null 2>&1 || die "Missing host prerequisite: $command_name"
  done
  mkdir -p "$INSTALL_ROOT"/{bin,ghidra,jdks,android,browsers,downloads,python-tools}
  INSTALL_ROOT="$(cd -- "$INSTALL_ROOT" && pwd -P)"
  export PATH="$INSTALL_ROOT/bin:$PATH"
fi

download_pinned() {
  local url="$1" expected_sha="$2" destination="$3" temporary="${3}.part"
  mkdir -p "$(dirname -- "$destination")"
  if [[ -f "$destination" ]] && printf '%s  %s\n' "$expected_sha" "$destination" | sha256sum --check --status; then
    log "Using verified cached archive $(basename -- "$destination")."
    return 0
  fi
  rm -f "$temporary"
  log "Downloading pinned provider $(basename -- "$destination")."
  curl --fail --location --retry 3 --retry-all-errors --connect-timeout 30 "$url" --output "$temporary"
  printf '%s  %s\n' "$expected_sha" "$temporary" | sha256sum --check --status || die "SHA-256 mismatch for $(basename -- "$destination")."
  mv -f "$temporary" "$destination"
}

install_jdk21() {
  local archive url digest jdk_home stage extracted
  case "$HOST_ARCH" in
    x86_64) archive="$JDK_X64_ARCHIVE"; url="$JDK_X64_URL"; digest="$JDK_X64_SHA256" ;;
    aarch64) archive="$JDK_ARM64_ARCHIVE"; url="$JDK_ARM64_URL"; digest="$JDK_ARM64_SHA256" ;;
  esac
  jdk_home="$INSTALL_ROOT/jdks/temurin-$JDK_VERSION"
  if [[ ! -x "$jdk_home/bin/javac" ]]; then
    download_pinned "$url" "$digest" "$INSTALL_ROOT/downloads/$archive"
    stage="$(mktemp -d "$INSTALL_ROOT/jdks/.stage.XXXXXX")"
    tar -xzf "$INSTALL_ROOT/downloads/$archive" -C "$stage"
    extracted="$(find "$stage" -mindepth 1 -maxdepth 1 -type d -print -quit)"
    [[ -x "$extracted/bin/javac" ]] || die 'Pinned Temurin archive does not contain a full JDK.'
    rm -rf "$jdk_home"
    mv "$extracted" "$jdk_home"
    rm -rf "$stage"
  fi
  ln -sfn "$jdk_home" "$INSTALL_ROOT/jdk-current"
  export JAVA_HOME="$INSTALL_ROOT/jdk-current"
  export PATH="$JAVA_HOME/bin:$PATH"
  [[ "$("$JAVA_HOME/bin/java" -version 2>&1 | head -n 1)" == *'21.0.13'* ]] || die 'Installed JDK does not report the pinned 21.0.13 runtime.'
  "$JAVA_HOME/bin/javac" --version | grep -q '21.0.13' || die 'Pinned JDK javac version check failed.'
  log "Temurin JDK $JDK_VERSION verified."
}

if [[ "$SKIP_GHIDRA" != true ]]; then
  if [[ "$DRY_RUN" == true ]]; then
    log "DRY-RUN: Ghidra $GHIDRA_VERSION + Temurin $JDK_VERSION (fixed REA-verified pair)."
  else
    GHIDRA_HOME="$INSTALL_ROOT/ghidra/ghidra_${GHIDRA_VERSION}_PUBLIC"
    if [[ ! -x "$GHIDRA_HOME/support/analyzeHeadless" ]]; then
      download_pinned "$GHIDRA_URL" "$GHIDRA_SHA256" "$INSTALL_ROOT/downloads/$GHIDRA_ARCHIVE"
      GHIDRA_STAGE="$(mktemp -d "$INSTALL_ROOT/ghidra/.stage.XXXXXX")"
      trap 'rm -rf "${GHIDRA_STAGE:-}"' EXIT
      unzip -q "$INSTALL_ROOT/downloads/$GHIDRA_ARCHIVE" -d "$GHIDRA_STAGE"
      GHIDRA_EXTRACTED="$GHIDRA_STAGE/ghidra_${GHIDRA_VERSION}_PUBLIC"
      [[ -x "$GHIDRA_EXTRACTED/support/analyzeHeadless" ]] || die 'Pinned Ghidra archive layout differs from the expected release.'
      rm -rf "$GHIDRA_HOME"
      mv "$GHIDRA_EXTRACTED" "$GHIDRA_HOME"
      rm -rf "$GHIDRA_STAGE"
      trap - EXIT
    fi
    [[ "$(grep -E '^application.version=' "$GHIDRA_HOME/Ghidra/application.properties" | cut -d= -f2)" == "$GHIDRA_VERSION" ]] || die 'Installed Ghidra version does not match the pinned release.'
    if [[ "$HOST_ARCH" == x86_64 ]]; then
      [[ -x "$GHIDRA_HOME/Ghidra/Features/Decompiler/os/linux_x86_64/decompile" ]] || die 'Pinned Ghidra package lacks its Linux x64 native decompiler.'
    fi
    ln -sfn "$GHIDRA_HOME" "$INSTALL_ROOT/ghidra-current"
    install_jdk21
    export GHIDRA_INSTALL_DIR="$INSTALL_ROOT/ghidra-current"
    log "Ghidra $GHIDRA_VERSION and JDK $JDK_VERSION installed."
  fi
elif [[ "$SKIP_ANDROID" != true && "$DRY_RUN" != true ]]; then
  install_jdk21
fi

if [[ "$DRY_RUN" != true ]]; then
  case "$HOST_ARCH" in
    x86_64) WABT_ARCHIVE="$WABT_X64_ARCHIVE"; WABT_URL="$WABT_X64_URL"; WABT_SHA256="$WABT_X64_SHA256" ;;
    aarch64) WABT_ARCHIVE="$WABT_ARM64_ARCHIVE"; WABT_URL="$WABT_ARM64_URL"; WABT_SHA256="$WABT_ARM64_SHA256" ;;
  esac
  WABT_MARKER="$INSTALL_ROOT/.wabt-$WABT_VERSION"
  if [[ ! -x "$INSTALL_ROOT/bin/wasm2wat" || ! -f "$WABT_MARKER" ]]; then
    download_pinned "$WABT_URL" "$WABT_SHA256" "$INSTALL_ROOT/downloads/$WABT_ARCHIVE"
    WABT_STAGE="$(mktemp -d "$INSTALL_ROOT/downloads/wabt.XXXXXX")"
    tar -xzf "$INSTALL_ROOT/downloads/$WABT_ARCHIVE" -C "$WABT_STAGE"
    WABT_BIN="$(find "$WABT_STAGE" -type f -name wasm2wat -printf '%h\n' -quit)"
    [[ -n "$WABT_BIN" ]] || die 'Pinned WABT archive does not contain wasm2wat.'
    cp -a "$WABT_BIN"/. "$INSTALL_ROOT/bin/"
    rm -rf "$WABT_STAGE"
    touch "$WABT_MARKER"
  fi
  "$INSTALL_ROOT/bin/wasm2wat" --version | grep -q "$WABT_VERSION" || die 'WABT version verification failed.'
  log "WABT $WABT_VERSION verified."
else
  log "DRY-RUN: WABT $WABT_VERSION."
fi

if [[ "$SKIP_ANDROID" != true ]]; then
  if [[ "$DRY_RUN" == true ]]; then
    log "DRY-RUN: JADX headless bridge $JADX_VERSION + Apktool $APKTOOL_VERSION."
  else
    [[ -n "${JAVA_HOME:-}" && -x "$JAVA_HOME/bin/java" ]] || install_jdk21
    JADX_JAR="$INSTALL_ROOT/android/$JADX_ARCHIVE"
    APKTOOL_JAR="$INSTALL_ROOT/android/$APKTOOL_ARCHIVE"
    # Always go through download_pinned: it reuses a cached jar only after its
    # SHA-256 verifies, so a pre-existing corrupt or tampered jar is replaced
    # instead of silently trusted.
    download_pinned "$JADX_URL" "$JADX_SHA256" "$JADX_JAR"
    download_pinned "$APKTOOL_URL" "$APKTOOL_SHA256" "$APKTOOL_JAR"
    export REA_JADX_MCP_JAR="$JADX_JAR"
    cat > "$INSTALL_ROOT/bin/apktool" <<WRAPPER
#!/usr/bin/env bash
set -Eeuo pipefail
exec "\${JAVA_HOME:?source the generated REA environment first}/bin/java" -jar "$APKTOOL_JAR" "\$@"
WRAPPER
    chmod 0755 "$INSTALL_ROOT/bin/apktool"
    "$JAVA_HOME/bin/java" -jar "$APKTOOL_JAR" --version | grep -q "$APKTOOL_VERSION" || die 'Apktool version verification failed.'
    log "JADX $JADX_VERSION and Apktool $APKTOOL_VERSION installed."
  fi
fi

resolve_python_toolchain() {
  local cache_root="${RUNNER_TOOL_CACHE:-/opt/hostedtoolcache}" cache_arch python_path candidate actual_version
  case "$HOST_ARCH" in x86_64) cache_arch=x64 ;; aarch64) cache_arch=arm64 ;; esac
  python_path=''
  # Runner images update patch releases in-place (for example 3.13.17 ->
  # 3.13.18). Select the newest installed patch within the tested 3.13 line.
  if [[ -d "$cache_root/Python" ]]; then
    while IFS= read -r candidate; do
      [[ -x "$cache_root/Python/$candidate/$cache_arch/bin/python" ]] && python_path="$cache_root/Python/$candidate/$cache_arch/bin/python"
    done < <(find "$cache_root/Python" -mindepth 1 -maxdepth 1 -type d -name "$PYTHON_VERSION_LINE.*" -printf '%f\n' | sort -V)
  fi
  if [[ ! -x "$python_path" ]]; then python_path="$(command -v python3.13 || true)"; fi
  [[ -x "$python_path" ]] || die "Python $PYTHON_VERSION_LINE.x is required for the pinned native provider wheels. Install it in the Runner tool cache; refusing to compile large native engines under a different Python."
  actual_version="$("$python_path" -c 'import platform; print(platform.python_version())')"
  [[ "$actual_version" == "$PYTHON_VERSION_LINE".* ]] || die "Expected Python $PYTHON_VERSION_LINE.x at $python_path; found $actual_version."
  PYTHON_TOOLCHAIN="$python_path"
  log "Python toolchain: $actual_version ($PYTHON_TOOLCHAIN)"
}

if [[ "$SKIP_NETWORK" != true && "$DRY_RUN" != true ]]; then
  resolve_python_toolchain
  MITMPROXY_VENV="$INSTALL_ROOT/python-tools/mitmproxy"
  [[ -x "$MITMPROXY_VENV/bin/python" ]] || "$PYTHON_TOOLCHAIN" -m venv "$MITMPROXY_VENV"
  "$MITMPROXY_VENV/bin/python" -m pip install --disable-pip-version-check "mitmproxy==$MITMPROXY_VERSION"
  "$MITMPROXY_VENV/bin/mitmdump" --version | grep -q "$MITMPROXY_VERSION" || die 'mitmproxy pinned version verification failed.'
  REA_MITMDUMP_COMMAND="$MITMPROXY_VENV/bin/mitmdump"
  log "mitmproxy $MITMPROXY_VERSION installed in an isolated virtual environment."
elif [[ "$SKIP_NETWORK" != true ]]; then
  log "DRY-RUN: mitmproxy $MITMPROXY_VERSION and TShark from apt."
fi

if [[ "$SKIP_PYTHON_TOOLS" != true && "$DRY_RUN" != true ]]; then
  resolve_python_toolchain
  install_pinned_python_tool() {
    local package="$1" version="$2" venv="$INSTALL_ROOT/python-tools/$3"
    [[ -x "$venv/bin/python" ]] || "$PYTHON_TOOLCHAIN" -m venv "$venv"
    "$venv/bin/python" -m pip install --disable-pip-version-check "$package==$version"
  }
  FRIDA_VENV="$INSTALL_ROOT/python-tools/frida-tools"
  [[ -x "$FRIDA_VENV/bin/python" ]] || "$PYTHON_TOOLCHAIN" -m venv "$FRIDA_VENV"
  "$FRIDA_VENV/bin/python" -m pip install --disable-pip-version-check "frida-tools==$FRIDA_TOOLS_VERSION" "frida==$FRIDA_RUNTIME_VERSION"
  "$FRIDA_VENV/bin/frida" --version | grep -qx "$FRIDA_RUNTIME_VERSION" || die 'Pinned Frida runtime version verification failed.'
  "$FRIDA_VENV/bin/python" -c "import importlib.metadata as m; assert m.version('frida-tools') == '$FRIDA_TOOLS_VERSION'"
  install_pinned_python_tool pwntools "$PWNTOOLS_VERSION" pwntools
  "$INSTALL_ROOT/python-tools/pwntools/bin/python" -m pip install --disable-pip-version-check "capstone==$CAPSTONE_VERSION" "pyelftools==$PYELFTOOLS_VERSION" "unicorn==$UNICORN_VERSION"
  REA_EXPECTED_PWNTOOLS="$PWNTOOLS_VERSION" REA_EXPECTED_CAPSTONE="$CAPSTONE_VERSION" REA_EXPECTED_PYELFTOOLS="$PYELFTOOLS_VERSION" REA_EXPECTED_UNICORN="$UNICORN_VERSION" "$INSTALL_ROOT/python-tools/pwntools/bin/python" - <<'PYTHON_CHECK'
import importlib.metadata as metadata
import os
from capstone import Cs, CS_ARCH_X86, CS_MODE_64
expected = {
    "pwntools": "4.15.0",
    "capstone": "5.0.9",
    "pyelftools": "0.33",
    "unicorn": "2.1.2",
}
for package, key in (("pwntools", "REA_EXPECTED_PWNTOOLS"), ("capstone", "REA_EXPECTED_CAPSTONE"), ("pyelftools", "REA_EXPECTED_PYELFTOOLS"), ("unicorn", "REA_EXPECTED_UNICORN")):
    wanted = os.environ[key]
    actual = metadata.version(package)
    if actual != wanted:
        raise SystemExit(f"{package}: expected {wanted}, found {actual}")
instructions = list(Cs(CS_ARCH_X86, CS_MODE_64).disasm(bytes([0x90]), 0x1000))
if len(instructions) != 1 or instructions[0].mnemonic != "nop":
    raise SystemExit("Capstone functional disassembly smoke failed")
PYTHON_CHECK
  REA_PWNTOOLS_PYTHON="$INSTALL_ROOT/python-tools/pwntools/bin/python"
  install_pinned_python_tool unblob "$UNBLOB_VERSION" unblob
  install_pinned_python_tool binwalk3 "$BINWALK3_PY_VERSION" binwalk3
  log "Frida tools $FRIDA_TOOLS_VERSION/$FRIDA_RUNTIME_VERSION, pwntools $PWNTOOLS_VERSION, capstone $CAPSTONE_VERSION, pyelftools $PYELFTOOLS_VERSION, Unicorn $UNICORN_VERSION, Unblob $UNBLOB_VERSION and Binwalk3 Python API $BINWALK3_PY_VERSION installed in isolated environments."
  REA_UNBLOB_COMMAND="$INSTALL_ROOT/python-tools/unblob/bin/unblob"
  "$REA_UNBLOB_COMMAND" --version | grep -q "$UNBLOB_VERSION" || die 'Pinned Unblob version verification failed.'
elif [[ "$SKIP_PYTHON_TOOLS" != true ]]; then
  log "DRY-RUN: Python $PYTHON_VERSION_LINE.x with Frida tools/runtime $FRIDA_TOOLS_VERSION/$FRIDA_RUNTIME_VERSION, pwntools $PWNTOOLS_VERSION, capstone $CAPSTONE_VERSION, pyelftools $PYELFTOOLS_VERSION, Unicorn $UNICORN_VERSION, Unblob $UNBLOB_VERSION and Binwalk3 Python API $BINWALK3_PY_VERSION."
fi

if [[ "$SKIP_BINWALK" != true && "$DRY_RUN" != true ]]; then
  command -v cargo >/dev/null 2>&1 || die 'Cargo is required for Binwalk 3.1.0; use --skip-binwalk to skip this provider.'
  BINWALK_ROOT="$INSTALL_ROOT/binwalk"
  BINWALK_COMMAND="$BINWALK_ROOT/bin/binwalk"
  if [[ ! -x "$BINWALK_COMMAND" ]] || ! "$BINWALK_COMMAND" --version 2>&1 | grep -q "binwalk $BINWALK_VERSION"; then
    RUST_FONTCONFIG_DLOPEN=1 cargo install --git https://github.com/ReFirmLabs/binwalk --rev "$BINWALK_GIT_REVISION" --locked --root "$BINWALK_ROOT" binwalk
  fi
  "$BINWALK_COMMAND" --version | grep -q "binwalk $BINWALK_VERSION" || die 'Pinned Binwalk binary version check failed.'
  link_binwalk_cli "$BINWALK_COMMAND" "$INSTALL_ROOT/bin" "$BINWALK_VERSION"
  REA_BINWALK_COMMAND="$BINWALK_COMMAND"
  BINWALK3_BINARY="$BINWALK_COMMAND"
  log "Binwalk $BINWALK_VERSION installed and exposed at $INSTALL_ROOT/bin/binwalk."
elif [[ "$DRY_RUN" == true && "$SKIP_BINWALK" != true ]]; then
  log "DRY-RUN: build pinned Binwalk $BINWALK_VERSION (revision $BINWALK_GIT_REVISION) with Cargo."
fi

if [[ "$SKIP_BROWSER" != true && "$DRY_RUN" != true ]]; then
  # Reuse the fixed Google Chrome build supplied by the Runner image. The
  # Ubuntu 26.04 image is not yet in Playwright's Chromium download platform map.
  REA_BROWSER_EXECUTABLE="$(command -v google-chrome-stable || command -v google-chrome || true)"
  [[ -n "$REA_BROWSER_EXECUTABLE" && -x "$REA_BROWSER_EXECUTABLE" ]] || die 'Google Chrome is missing; use --skip-browser to omit browser-dependent REA tools.'
  CHROME_BANNER="$("$REA_BROWSER_EXECUTABLE" --version 2>&1)"
  [[ "$CHROME_BANNER" == *"$CHROME_VERSION_PREFIX"* ]] || die "Expected Google Chrome ${CHROME_VERSION_PREFIX%?}.x, found: $CHROME_BANNER"
  PLAYWRIGHT_CLI="$REA_ROOT/node_modules/playwright-core/cli.js"
  [[ -f "$PLAYWRIGHT_CLI" ]] || die 'Installed REA package does not contain its expected Playwright Core CLI.'
  node --input-type=module - "$REA_ROOT" "$REA_BROWSER_EXECUTABLE" <<'NODE'
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
const root = process.argv[2];
const executable = process.argv[3];
const require = createRequire(pathToFileURL(`${root}/package.json`));
const { chromium } = require('playwright-core');
const browser = await chromium.launch({ headless: true, executablePath: executable });
const page = await browser.newPage();
await page.setContent('<title>REA browser smoke</title><h1>ready</h1>');
if ((await page.title()) !== 'REA browser smoke') throw new Error('Chrome smoke test failed');
console.log(`Chrome ${browser.version()} Playwright smoke passed.`);
await browser.close();
NODE
  log "Google Chrome ${CHROME_VERSION_PREFIX%?}.x verified through bundled Playwright Core."
elif [[ "$DRY_RUN" == true && "$SKIP_BROWSER" != true ]]; then
  log "DRY-RUN: verify host Google Chrome ${CHROME_VERSION_PREFIX%?}.x using bundled Playwright Core."
fi

if [[ "$DRY_RUN" != true ]]; then
  REA_WABT_BIN_DIRECTORY="$INSTALL_ROOT/bin"
  REA_PYTHON_TOOLCHAIN="${PYTHON_TOOLCHAIN:-${REA_PYTHON_TOOLCHAIN:-}}"
  ENV_FILE="$INSTALL_ROOT/providers.env"
  {
    printf 'export REA_INSTALL_ROOT=%q\n' "$INSTALL_ROOT"
    printf 'export NODE_HOME=%q\n' "$NODE_HOME"
    if [[ -n "${REA_VERSION:-}" ]]; then printf 'export REA_VERSION=%q\n' "$REA_VERSION"; fi
    if [[ -n "${REA_INTEGRITY:-}" ]]; then printf 'export REA_INTEGRITY=%q\n' "$REA_INTEGRITY"; fi
    if [[ -n "${PYTHON_TOOLCHAIN:-}" ]]; then printf 'export REA_PYTHON_TOOLCHAIN=%q\n' "$PYTHON_TOOLCHAIN"; fi
    # shellcheck disable=SC2016
    printf 'export PATH=%q:"$PATH"\n' "$NODE_HOME/bin"
    # shellcheck disable=SC2016
    printf 'export PATH=%q:"$PATH"\n' "$NPM_GLOBAL_BIN"
    # shellcheck disable=SC2016
    printf 'export PATH=%q:"$PATH"\n' "$INSTALL_ROOT/bin"
    for venv_bin in "$INSTALL_ROOT"/python-tools/*/bin; do
      if [[ -d "$venv_bin" ]]; then
        # shellcheck disable=SC2016
        printf 'export PATH=%q:"$PATH"\n' "$venv_bin"
      fi
    done
    if [[ -n "${JAVA_HOME:-}" ]]; then
      # shellcheck disable=SC2016
      printf 'export JAVA_HOME=%q\nexport PATH=%q:"$PATH"\n' "$JAVA_HOME" "$JAVA_HOME/bin"
    fi
    if [[ -n "${GHIDRA_INSTALL_DIR:-}" ]]; then printf 'export GHIDRA_INSTALL_DIR=%q\n' "$GHIDRA_INSTALL_DIR"; fi
    if [[ -n "${REA_JADX_MCP_JAR:-}" ]]; then printf 'export REA_JADX_MCP_JAR=%q\n' "$REA_JADX_MCP_JAR"; fi
    if [[ -n "${REA_PWNTOOLS_PYTHON:-}" ]]; then printf 'export REA_PWNTOOLS_PYTHON=%q\n' "$REA_PWNTOOLS_PYTHON"; fi
    if [[ -n "${REA_MITMDUMP_COMMAND:-}" ]]; then printf 'export REA_MITMDUMP_COMMAND=%q\n' "$REA_MITMDUMP_COMMAND"; fi
    if [[ -n "${REA_WABT_BIN_DIRECTORY:-}" ]]; then printf 'export REA_WABT_BIN_DIRECTORY=%q\n' "$REA_WABT_BIN_DIRECTORY"; fi
    if [[ -n "${REA_BROWSER_EXECUTABLE:-}" ]]; then printf 'export REA_BROWSER_EXECUTABLE=%q\n' "$REA_BROWSER_EXECUTABLE"; fi
    if [[ -n "${REA_BINWALK_COMMAND:-}" ]]; then printf 'export REA_BINWALK_COMMAND=%q\n' "$REA_BINWALK_COMMAND"; fi
    if [[ -n "${REA_UNBLOB_COMMAND:-}" ]]; then printf 'export REA_UNBLOB_COMMAND=%q\n' "$REA_UNBLOB_COMMAND"; fi
    if [[ -n "${BINWALK3_BINARY:-}" ]]; then printf 'export BINWALK3_BINARY=%q\n' "$BINWALK3_BINARY"; fi
    if [[ -n "${PLAYWRIGHT_BROWSERS_PATH:-}" ]]; then printf 'export PLAYWRIGHT_BROWSERS_PATH=%q\n' "$PLAYWRIGHT_BROWSERS_PATH"; fi
  } > "$ENV_FILE"
  chmod 0600 "$ENV_FILE"
  install_provider_shell_environment "$ENV_FILE"
  for optional_env in JAVA_HOME GHIDRA_INSTALL_DIR REA_JADX_MCP_JAR REA_PYTHON_TOOLCHAIN REA_PWNTOOLS_PYTHON REA_MITMDUMP_COMMAND REA_WABT_BIN_DIRECTORY REA_BROWSER_EXECUTABLE REA_BINWALK_COMMAND REA_UNBLOB_COMMAND BINWALK3_BINARY HOPPER_LAUNCHER_PATH REA_IDA_MCP_CONFIG PLAYWRIGHT_BROWSERS_PATH; do
    if [[ -z "${!optional_env:-}" ]]; then unset "$optional_env"; fi
  done
  # shellcheck disable=SC1090,SC1091
  source "$ENV_FILE"

  rea capabilities --format json > "$INSTALL_ROOT/capabilities.json"
  jq -e '.capabilities | type == "array"' "$INSTALL_ROOT/capabilities.json" >/dev/null || die 'REA capabilities command did not return a valid document.'

  # Exercise the native PTY/process-supervision path with a harmless owned child.
  PROCESS_SCENARIO="$INSTALL_ROOT/process-capture-smoke-scenario.json"
  PROCESS_EVIDENCE="$INSTALL_ROOT/process-capture-smoke.json"
  jq -n --arg executable "$NODE_HOME/bin/node" --arg cwd "$INSTALL_ROOT" \
    '{executable:$executable,arguments:["-e","console.log(\"rea-process-capture-smoke-ok\")"],working_directory:$cwd,timeout_ms:5000,idle_timeout_ms:5000,settle_ms:100}' \
    > "$PROCESS_SCENARIO"
  rea capture-process "$PROCESS_SCENARIO" --json > "$PROCESS_EVIDENCE" || die 'REA process capture failed its native PTY smoke test.'
  jq -e --arg marker 'rea-process-capture-smoke-ok' \
    '.operation == "capture_process_scenario" and .normalized_result.exit.reason == "exited" and .normalized_result.exit.code == 0 and any(.normalized_result.frames[]?; (.data | contains($marker))) and .normalized_result.cleanup.temporary_root == "removed"' \
    "$PROCESS_EVIDENCE" >/dev/null || die 'REA process capture Evidence failed its output/exit/cleanup checks.'
  log 'REA process capture and owned-process cleanup smoke passed.'
  if [[ "$SKIP_GHIDRA" != true ]]; then
    rea doctor --provider ghidra --format json > "$INSTALL_ROOT/ghidra-doctor.json"
    jq -e '.scope_checks | type == "array" and all(.[]; .ok == true)' "$INSTALL_ROOT/ghidra-doctor.json" >/dev/null || die "Ghidra doctor checks failed; inspect $INSTALL_ROOT/ghidra-doctor.json"
  fi
  if [[ "$SKIP_ANDROID" != true ]]; then
    rea doctor --provider jadx --format json > "$INSTALL_ROOT/jadx-doctor.json"
    jq -e '.scope_checks | type == "array" and all(.[]; .ok == true)' "$INSTALL_ROOT/jadx-doctor.json" >/dev/null || die "JADX doctor checks failed; inspect $INSTALL_ROOT/jadx-doctor.json"
    rea inspect-apktool-client --format json > "$INSTALL_ROOT/apktool-client-smoke.json" || die 'REA could not inspect the installed Apktool client.'
    jq -e --arg version "$APKTOOL_VERSION" '.operation == "inspect_apktool_client" and .normalized_result.client.apktool_version == $version' "$INSTALL_ROOT/apktool-client-smoke.json" >/dev/null || die 'Apktool client Evidence did not report the installed pinned version.'
  fi
  if [[ -n "${REA_BINWALK_COMMAND:-}" ]]; then
    "$REA_BINWALK_COMMAND" --version | grep -q "binwalk $BINWALK_VERSION" || die 'Configured REA Binwalk command failed its version check.'
    rea inspect-firmware-regions /bin/ls --format json > "$INSTALL_ROOT/rea-binwalk-smoke.json" || die 'REA Binwalk integration failed the ELF smoke test.'
    jq -e --arg version "$BINWALK_VERSION" '.provider.id == "binwalk" and .provider.version == $version and (.normalized_result.regions | any(.signature == "elf"))' "$INSTALL_ROOT/rea-binwalk-smoke.json" >/dev/null || die 'REA Binwalk evidence did not contain the expected ELF signature and pinned provider identity.'
  fi
  if [[ -n "${REA_UNBLOB_COMMAND:-}" ]]; then
    "$REA_UNBLOB_COMMAND" --version | grep -q "$UNBLOB_VERSION" || die 'Configured REA Unblob command failed its version check.'
  fi
  if [[ "$SKIP_PYTHON_TOOLS" != true ]]; then
    rea list-frida-devices --format json > "$INSTALL_ROOT/frida-devices-smoke.json" || die 'REA Frida device discovery failed.'
    jq -e 'any(.devices[]?; .device_id == "local")' "$INSTALL_ROOT/frida-devices-smoke.json" >/dev/null || die 'REA Frida did not expose its local device.'
    log 'REA Frida runtime/device discovery smoke passed.'
    if [[ "$HOST_ARCH" == x86_64 ]]; then
      ELF_FIXTURE="$INSTALL_ROOT/rea-elf-smoke"
      cp -L /usr/bin/true "$ELF_FIXTURE"
      rea inspect-binary-layout "$ELF_FIXTURE" --json > "$INSTALL_ROOT/rea-pwntools-smoke.json" || die 'REA pwntools ELF layout analysis failed.'
      jq -e '.operation == "inspect_binary_layout" and .provider.id == "pwntools-elf" and (.normalized_result.sections | length) > 0' "$INSTALL_ROOT/rea-pwntools-smoke.json" >/dev/null || die 'REA pwntools Evidence failed its ELF layout checks.'
      log 'REA pwntools ELF layout smoke passed.'
    fi
  fi
  if [[ "$SKIP_PYTHON_TOOLS" != true && -n "${BINWALK3_BINARY:-}" ]]; then
    BINWALK3_BINARY="$BINWALK3_BINARY" "$INSTALL_ROOT/python-tools/binwalk3/bin/python" -c 'import binwalk; backend = binwalk.get_backend(); assert backend.available; print(backend.binary_path)' > "$INSTALL_ROOT/binwalk3-api-smoke.txt" || die 'Binwalk3 Python API cannot bind to the pinned native Binwalk executable.'
  fi
  command -v objdump >/dev/null 2>&1 || die 'GNU objdump is missing.'
  command -v gdb >/dev/null 2>&1 || die 'GDB is missing.'
  command -v readelf >/dev/null 2>&1 || die 'GNU readelf is missing.'
  command -v strings >/dev/null 2>&1 || die 'GNU strings is missing.'
  command -v ps >/dev/null 2>&1 || die 'procps-compatible ps is missing.'
  command -v wasm2wat >/dev/null 2>&1 || die 'WABT wasm2wat is missing.'
  printf '(module (func (export "answer") (result i32) i32.const 42))\n' > "$INSTALL_ROOT/rea-wasm-smoke.wat"
  "$INSTALL_ROOT/bin/wat2wasm" "$INSTALL_ROOT/rea-wasm-smoke.wat" -o "$INSTALL_ROOT/rea-wasm-smoke.wasm"
  rea inspect-wasm-artifact "$INSTALL_ROOT/rea-wasm-smoke.wasm" --json > "$INSTALL_ROOT/rea-wasm-smoke.json" || die 'REA WABT integration failed its WASM fixture.'
  jq -e '.operation == "inspect_wasm_artifact" and .normalized_result != null' "$INSTALL_ROOT/rea-wasm-smoke.json" >/dev/null || die 'REA WASM Evidence failed its schema/content check.'
  log 'REA WABT/WASM inspection smoke passed.'
  if [[ "$SKIP_NETWORK" != true && "$SKIP_OS" != true ]]; then
    command -v tshark >/dev/null 2>&1 || die 'TShark is missing.'
    command -v capinfos >/dev/null 2>&1 || die 'capinfos is missing.'
    tshark --version | grep -Eq '^TShark \(Wireshark\) 4\.' || die 'REA requires a Wireshark/TShark 4.x installation.'
  fi
  if [[ -n "${GITHUB_ENV:-}" ]]; then
    {
      printf 'REA_INSTALL_ROOT=%s\n' "$INSTALL_ROOT"
      printf 'NODE_HOME=%s\n' "$NODE_HOME"
      if [[ -n "${REA_VERSION:-}" ]]; then printf 'REA_VERSION=%s\n' "$REA_VERSION"; fi
      if [[ -n "${PYTHON_TOOLCHAIN:-}" ]]; then printf 'REA_PYTHON_TOOLCHAIN=%s\n' "$PYTHON_TOOLCHAIN"; fi
      if [[ -n "${JAVA_HOME:-}" ]]; then printf 'JAVA_HOME=%s\n' "$JAVA_HOME"; fi
      if [[ -n "${GHIDRA_INSTALL_DIR:-}" ]]; then printf 'GHIDRA_INSTALL_DIR=%s\n' "$GHIDRA_INSTALL_DIR"; fi
      if [[ -n "${REA_JADX_MCP_JAR:-}" ]]; then printf 'REA_JADX_MCP_JAR=%s\n' "$REA_JADX_MCP_JAR"; fi
      if [[ -n "${REA_PWNTOOLS_PYTHON:-}" ]]; then printf 'REA_PWNTOOLS_PYTHON=%s\n' "$REA_PWNTOOLS_PYTHON"; fi
      if [[ -n "${REA_MITMDUMP_COMMAND:-}" ]]; then printf 'REA_MITMDUMP_COMMAND=%s\n' "$REA_MITMDUMP_COMMAND"; fi
      if [[ -n "${REA_WABT_BIN_DIRECTORY:-}" ]]; then printf 'REA_WABT_BIN_DIRECTORY=%s\n' "$REA_WABT_BIN_DIRECTORY"; fi
      if [[ -n "${REA_BROWSER_EXECUTABLE:-}" ]]; then printf 'REA_BROWSER_EXECUTABLE=%s\n' "$REA_BROWSER_EXECUTABLE"; fi
      if [[ -n "${REA_BINWALK_COMMAND:-}" ]]; then printf 'REA_BINWALK_COMMAND=%s\n' "$REA_BINWALK_COMMAND"; fi
      if [[ -n "${REA_UNBLOB_COMMAND:-}" ]]; then printf 'REA_UNBLOB_COMMAND=%s\n' "$REA_UNBLOB_COMMAND"; fi
      if [[ -n "${BINWALK3_BINARY:-}" ]]; then printf 'BINWALK3_BINARY=%s\n' "$BINWALK3_BINARY"; fi
      if [[ -n "${PLAYWRIGHT_BROWSERS_PATH:-}" ]]; then printf 'PLAYWRIGHT_BROWSERS_PATH=%s\n' "$PLAYWRIGHT_BROWSERS_PATH"; fi
    } >> "$GITHUB_ENV"
    if [[ -n "${GITHUB_PATH:-}" ]]; then
      printf '%s\n' "$NODE_HOME/bin" "$NPM_GLOBAL_BIN" "$INSTALL_ROOT/bin" >> "$GITHUB_PATH"
      for venv_bin in "$INSTALL_ROOT"/python-tools/*/bin; do
        if [[ -d "$venv_bin" ]]; then printf '%s\n' "$venv_bin" >> "$GITHUB_PATH"; fi
      done
    fi
  fi
  log "Pinned provider environment saved at $ENV_FILE"
  log "REA $(rea --version) and provider checks completed. MCP/agent config was not modified."
else
  log 'DRY-RUN: persist environment and validate REA/provider doctor checks.'
fi
