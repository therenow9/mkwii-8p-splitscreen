#!/usr/bin/env bash
# The Linux half of a release, into build/linux/: WiiCompiled's AppImage built from external/Wiicompiled at its HEAD,
# and tools/ss8_setup.py frozen by PyInstaller. release.py --linux build/linux packs them with the payload.
#
#   tools/build_linux.sh [--wiicompiled DIR] [--only appimage|setup]
#
# It runs itself in Docker's ubuntu:22.04 (glibc 2.35, WiiCompiled's own Linux CI base), so a player's distro needs
# that glibc or newer. The volume ss8-linux-build keeps the toolchain, aurora's prebuilt package and the Python between
# runs. Inside the container it is called with --in-container.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
wc="$root/external/Wiicompiled"
only=""
in_container=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --wiicompiled) wc=$(cd "$2" && pwd); shift 2 ;;
        --only) only=$2; shift 2 ;;
        --in-container) in_container=1; shift ;;
        *) echo "build_linux.sh: unknown argument $1" >&2; exit 2 ;;
    esac
done

# uv's Python build carries Tk and python.org's LICENSE.txt, which the frozen setup ships.
UV_VERSION=0.9.5
PYTHON_VERSION=3.12.11
PYINSTALLER_VERSION=6.22.3

if [[ $in_container == 0 ]]; then
    out="$root/build/linux"
    mkdir -p "$out/src"
    if [[ $only != setup ]]; then
        [[ -z "$(git -C "$wc" status --porcelain)" ]] || { echo "build_linux.sh: $wc has uncommitted files" >&2; exit 1; }
        [[ -f "$wc/aurora-main/CMakeLists.txt" ]] || { echo "build_linux.sh: $wc/aurora-main is not checked out" >&2; exit 1; }
        git -c core.autocrlf=false -c core.eol=lf -C "$wc" archive -o "$out/src/wiicompiled.tar" HEAD
        git -c core.autocrlf=false -c core.eol=lf -C "$wc/aurora-main" archive --prefix=aurora-main/ -o "$out/src/aurora-main.tar" HEAD
        git -C "$wc" rev-parse HEAD > "$out/src/wiicompiled.commit"
    fi
    if [[ $only != appimage ]]; then
        # The setup freezes from the working tree; release.py --linux holds it to the root's HEAD.
        dirty=$(git -C "$root" status --porcelain -- tools external/wuj5)
        echo "$(git -C "$root" rev-parse HEAD)${dirty:+-dirty}" > "$out/src/setup.commit"
    fi
    # Git Bash rewrites /paths in arguments; Docker Desktop takes C:/ forms.
    host() { if command -v cygpath >/dev/null; then cygpath -m "$1"; else echo "$1"; fi; }
    MSYS_NO_PATHCONV=1 docker run --rm -v ss8-linux-build:/work -v "$(host "$root"):/tree:ro" -v "$(host "$out"):/out" \
        -e HOST_UID="$(id -u)" ubuntu:22.04 bash /tree/tools/build_linux.sh --in-container ${only:+--only "$only"}
    exit 0
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# WiiCompiled's package.yml list (SDL3's build), plus what GitHub's runners carry and this script and PyInstaller use.
apt-get install -y -qq curl ca-certificates git python3 binutils file unzip xz-utils build-essential make pkg-config \
    cmake ninja-build libasound2-dev libpulse-dev libaudio-dev libfribidi-dev libjack-dev libsndio-dev libx11-dev \
    libxext-dev libxrandr-dev libxcursor-dev libxfixes-dev libxi-dev libxss-dev libxtst-dev libxkbcommon-dev \
    libdrm-dev libgbm-dev libgl1-mesa-dev libgles2-mesa-dev libegl1-mesa-dev libdbus-1-dev libibus-1.0-dev \
    libudev-dev libthai-dev libusb-1.0-0-dev libpipewire-0.3-dev libwayland-dev libdecor-0-dev liburing-dev \
    libvulkan-dev libicu-dev >/dev/null

if [[ $only != setup ]]; then
    if [[ ! -x /work/dotnet/dotnet ]]; then
        curl -fsSL https://dot.net/v1/dotnet-install.sh -o /work/dotnet-install.sh
        bash /work/dotnet-install.sh --channel 8.0 --install-dir /work/dotnet
    fi
    export PATH="/work/dotnet:$PATH" DOTNET_CLI_TELEMETRY_OPTOUT=1
    # The checkout's tracked files only; Launcher/artifacts (the toolchain, aurora's prebuilt) survive between runs.
    rm -rf /work/wc-src && mkdir -p /work/wc-src
    tar -xf /out/src/wiicompiled.tar -C /work/wc-src
    tar -xf /out/src/aurora-main.tar -C /work/wc-src
    mkdir -p /work/wc/Launcher/artifacts
    find /work/wc -mindepth 1 -maxdepth 1 ! -name Launcher -exec rm -rf {} +
    find /work/wc/Launcher -mindepth 1 -maxdepth 1 ! -name artifacts -exec rm -rf {} +
    cp -a /work/wc-src/. /work/wc/
    # appimagetool is an AppImage too, and a container has no FUSE.
    APPIMAGE_EXTRACT_AND_RUN=1 bash /work/wc/Launcher/build-appimage.sh --output-dir /work/dist
    cp /work/dist/WiiCompiled-Setup-x86_64.AppImage /out/WiiCompiled-Setup.AppImage
    cp /out/src/wiicompiled.commit /out/WiiCompiled-Setup.AppImage.commit
    cp /work/wc/Launcher/artifacts/appimage-build/AppDir/usr/toolchain/LICENSE.txt /out/WiiCompiled-Setup.AppImage.toolchain.txt
fi

if [[ $only != appimage ]]; then
    if [[ ! -x /work/uv/uv ]]; then
        mkdir -p /work/uv
        curl -fsSL "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-unknown-linux-gnu.tar.gz" |
            tar -xz -C /work/uv --strip-components=1
    fi
    export UV_PYTHON_INSTALL_DIR=/work/python UV_CACHE_DIR=/work/uv-cache
    /work/uv/uv python install "$PYTHON_VERSION"
    [[ -x /work/venv/bin/python ]] || /work/uv/uv venv --python "$PYTHON_VERSION" /work/venv
    /work/uv/uv pip install --python /work/venv/bin/python "pyinstaller==$PYINSTALLER_VERSION"
    rm -rf /out/setup
    # release.py writes to the tree's build/; the tree is read-only here, so it freezes from a copy of what it needs.
    rm -rf /work/tree && mkdir -p /work/tree/external
    cp -a /tree/tools /work/tree/tools
    cp -a /tree/external/wuj5 /work/tree/external/wuj5
    cp /tree/LICENSE /work/tree/LICENSE
    /work/venv/bin/python /work/tree/tools/release.py --freeze /out/setup
    cp /out/src/setup.commit /out/setup/commit.txt
fi
chown -R "${HOST_UID:-0}" /out 2>/dev/null || true
