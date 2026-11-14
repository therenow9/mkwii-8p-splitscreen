#!/usr/bin/env python3
"""Build a SplitScreen8 release zip: both Code.pul builds from a clean tree, the frozen player setup, and the payload.

  release.py [--version V] [--dirty-root] [--linux build/linux]
  release.py --fetch-wit      only fetch wit into build/wit/, where install_8p.py wii takes it
  release.py --freeze DIR     only freeze the setup into DIR (tools/build_linux.sh runs it on Linux)

Into build/release/mkwii-8p-splitscreen-<V>.zip (V defaults to dev-<root commit>):
  MKWii 8P Splitscreen Setup/  tools/ss8_setup.py frozen by PyInstaller (onedir, no UPX), with the menu generator and wuj5
  payload/rr/Code.pul        the RR build, for the Retro Rewind release on Wheel Wizard (rr_version, rr_commit)
  payload/mkw/Code.pul, Loader.pul   the --vanilla build and its loader
  payload/WiiCompiled-Setup.exe      WiiCompiled's installer, built in external/Wiicompiled at the root's gitlink
  payload/wit/               wit (Wiimms ISO Tools, GPLv2), which makes the Wii images: its exe, DLLs, licence, and
                             the sources of wit and of cygwin1.dll (LGPLv3+)
  payload/release.json       the version, the commits built, rr_version, rr_commit and each payload file's sha256
  README.txt                 tools/release_docs/README.txt
  LICENSES/                  every shipped component's licence, copied from where it lives
  SOURCE.txt                 each component's public repository at the commit built
With --linux, also mkwii-8p-splitscreen-<V>-linux-x86_64.tar.gz: the same Code.pul and Loader.pul, and in place of the
Windows files tools/build_linux.sh's WiiCompiled-Setup.AppImage and frozen setup, and wit's own Linux build.

Run it with a Python that has PyInstaller (README.md, The release zip). It refuses a dirty rr-pulsar, wuj5 or Wiicompiled, a
submodule not at the commit the root records, a repository that is not public or lacks the commit, a missing README.txt,
an installer older than its sources, EXTRA_CFLAGS, a build with any define but the release's, and an RR build whose
commit string is not exactly <commit>-ss8;
--dirty-root lets uncommitted tools/ through for a candidate and skips the public check; the version says so. No game data goes in.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from importlib.metadata import distribution
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import build_code  # noqa: E402
import install_8p  # noqa: E402

PULSAR = ROOT / "external" / "rr-pulsar"
WUJ5 = ROOT / "external" / "wuj5"
WC = ROOT / "external" / "Wiicompiled"
INSTALLER = WC / "Launcher" / "dist" / "WiiCompiled-Setup.exe"
OUT = ROOT / "build" / "release"
# Short output folders: Kamek's link line names every object and sits near Windows' 32,767-character limit.
# Each build's defines, as its cflags.txt records them, and its commit string's suffix (--vanilla has no Version.cpp).
BUILDS = {"rr": (ROOT / "build" / "rel", [], {"-DPULSAR_RANDOM_KEY=0xADD2BFAF"}, "-ss8"),
          "mkw": (ROOT / "build" / "relv", ["--vanilla"], {"-DPULSAR_RANDOM_KEY=0xADD2BFAF", "-DSS8_VANILLA"}, None)}
WUJ5_MODULES = ["common", "brctr", "brlyt", "u8", "yaz"]  # what the generator imports
# wit 3.05a's Windows build and the source of the same release, and what of the build wit.exe needs to run.
WIT_DIR = ROOT / "build" / "wit"
WIT_ZIP = ("https://wit.wiimm.de/download/wit-v3.05a-r8638-cygwin64.zip",
           "049670558970f0cea2796d68e0ba1e48491474b5708bf12a95ab8a185f4e59c1")
WIT_SRC = ("https://github.com/Wiimm/wiimms-iso-tools/archive/fc1c0b840cb3ac41ca6e4f1d5e16da12b47eab58.tar.gz",
           "ebcff01c6d674f21a8c63ef0530d0516616d082e1d5041863ebc744549f937ea")
WIT_FILES = ["bin/wit.exe", "bin/cygwin1.dll", "bin/cygncursesw-10.dll", "bin/cygz.dll", "bin/cygcrypto-1.1.dll", "gpl-2.0.txt"]
WIT_SOURCE = "wiimms-iso-tools-v3.05a-source.tar.gz"
# The zip's cygwin1.dll is byte-identical to Cygwin's 3.1.7-1 package; this is that release's own source package.
# Cygwin's mirrors keep only current releases and the Time Machine serves plain http, so the sha256 is the check.
CYGWIN_SRC = ("http://ctm.crouchingtigerhiddenfruitbat.org/pub/cygwin/circa/64bit/2021/03/28/092633/x86_64/release/cygwin/cygwin-3.1.7-1-src.tar.xz",
              "17bf431941b454d6c338cd1673c9c7d90964f84552c11bb9ddc86afadc50ea18")
CYGWIN_SOURCE = CYGWIN_SRC[0].rsplit("/", 1)[1]
# wit 3.05a's own Linux build of the same release: one static binary, its licence beside it.
WIT_LINUX = ("https://wit.wiimm.de/download/wit-v3.05a-r8638-x86_64.tar.gz",
             "df95a14f1b4b2bd95d3111b70c6304e91d4ed3e4c7f72b1ad5306c3616d8933f")
SETUP_NAME = "MKWii 8P Splitscreen Setup"
DOCS = ROOT / "tools" / "release_docs"
# rr-pulsar files under their own licence, each stated in the file's leading comment block.
NOTICES = ["KamekInclude/kamek.hpp", "PulsarEngine/Extensions/Cosmos/WUP028.cpp", "PulsarEngine/IO/Patches.cpp",
           "PulsarEngine/Sound/BRSARPatches.cpp", "PulsarEngine/Network/GPReport.cpp", "PulsarEngine/Network/WiiLink.cpp",
           "PulsarEngine/Network/RSA.cpp", "PulsarEngine/Network/SHA256.cpp", "PulsarEngine/Network/Ranking.hpp",
           "PulsarEngine/UI/CtrlRaceBase/InputViewer.cpp"]


def git(repo: Path, *argv: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *argv], capture_output=True, text=True, check=True).stdout.strip()


def fetch(url: str, digest: str) -> Path:
    dst = WIT_DIR / "download" / url.rsplit("/", 1)[1]
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists() or install_8p.sha256(dst) != digest:
        urllib.request.urlretrieve(url, dst)
    if install_8p.sha256(dst) != digest:
        install_8p.fail(f"{url} is not the file pinned ({digest}); a new wit means new pins")
    return dst


def fetch_wit() -> None:
    got = [fetch(*pin) for pin in (WIT_ZIP, WIT_SRC, CYGWIN_SRC)]
    with zipfile.ZipFile(got[0]) as z:
        top = z.namelist()[0].split("/")[0]
        for name in WIT_FILES:
            (WIT_DIR / Path(name).name).write_bytes(z.read(f"{top}/{name}"))
    shutil.copy2(got[1], WIT_DIR / WIT_SOURCE)
    shutil.copy2(got[2], WIT_DIR / CYGWIN_SOURCE)
    print(f"[release] wit in {WIT_DIR}")


def freeze(dist: Path, work: Path) -> None:
    """tools/ss8_setup.py frozen by PyInstaller into dist/SETUP_NAME, with what it reads at run time; a Linux freeze
    also writes its Python's and PyInstaller's licences and versions into dist/, where release.py --linux takes them."""
    shutil.rmtree(work, ignore_errors=True)
    data = [install_8p.GENERATOR, install_8p.SAME_GENERATOR, WUJ5 / "LICENSE", DOCS / "wii-wheel.ico", *sorted(WUJ5.glob("*.py"))]
    windows = ["--hide-console", "hide-early", "--icon", str(DOCS / "wii-wheel.ico")] if install_8p.WINDOWS else []
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--noupx", "--console",
                    *windows, "--name", SETUP_NAME, "--distpath", str(dist), "--workpath", str(work / "work"),
                    "--specpath", str(work), "--paths", str(ROOT / "tools"), "--paths", str(WUJ5),
                    *(a for m in WUJ5_MODULES for a in ("--hidden-import", m)),
                    *(a for f in data for a in ("--add-data", f"{f}{os.pathsep}{f.parent.relative_to(ROOT).as_posix()}")),
                    str(ROOT / "tools" / "ss8_setup.py")], check=True)
    if not install_8p.WINDOWS:
        (dist / "licenses").mkdir()
        shutil.copy2(python_licence(), dist / "licenses" / "Python LICENSE.txt")
        shutil.copy2(pyinstaller_licence(), dist / "licenses" / "PyInstaller COPYING.txt")
        (dist / "versions.json").write_text(json.dumps({"python": platform.python_version(),
                                                        "pyinstaller": distribution("pyinstaller").version}), encoding="utf-8")


def python_licence() -> Path:
    # python.org's Windows layout keeps it at the top; a Linux one under lib/.
    return next(p for p in (Path(sys.base_prefix) / "LICENSE.txt",
                            Path(sys.base_prefix) / "lib" / f"python{sys.version_info[0]}.{sys.version_info[1]}" / "LICENSE.txt")
                if p.exists())


def pyinstaller_licence() -> Path:
    pyinstaller = distribution("pyinstaller")
    return Path(pyinstaller.locate_file(next(f for f in pyinstaller.files if f.name == "COPYING.txt")))


def write_docs(stage: Path, release: dict, urls: dict, linux: Path | None = None) -> None:
    shutil.copy2(DOCS / "README.txt", stage / "README.txt")
    rr, wit, py = Path("Retro Rewind (rr-pulsar)"), Path("wit" if linux else "wit and Cygwin"), Path("Python and PyInstaller")
    versions = json.loads((linux / "setup" / "versions.json").read_text(encoding="utf-8")) if linux else \
        {"python": platform.python_version(), "pyinstaller": distribution("pyinstaller").version}
    copies = {Path("SplitScreen8/LICENSE.txt"): ROOT / "LICENSE",
              rr / "LICENSE.txt": PULSAR / "LICENSE", rr / "LICENSE_mkw-sp.txt": PULSAR / "LICENSE_mkw-sp",
              rr / "LICENSE_pulsar.txt": PULSAR / "LICENSE_pulsar", rr / "Kamek LICENSE.txt": DOCS / "licenses" / "Kamek LICENSE.txt",
              Path("wuj5/LICENSE.txt"): WUJ5 / "LICENSE",
              Path("WiiCompiled/LICENSE.txt"): WC / "LICENSE", Path("WiiCompiled/THIRD-PARTY-NOTICES.md"): WC / "THIRD-PARTY-NOTICES.md",
              wit / "wit gpl-2.0.txt": WIT_DIR / ("linux" if linux else "") / "gpl-2.0.txt"}
    if linux:
        copies |= {wit / "glibc COPYING.LIB.txt": DOCS / "licenses" / "glibc COPYING.LIB.txt",
                   Path("WiiCompiled/AppImage toolchain.txt"): linux / "WiiCompiled-Setup.AppImage.toolchain.txt",
                   py / "Python LICENSE.txt": linux / "setup" / "licenses" / "Python LICENSE.txt",
                   py / "PyInstaller COPYING.txt": linux / "setup" / "licenses" / "PyInstaller COPYING.txt"}
    else:
        copies |= {**{wit / f: DOCS / "licenses" / f for f in ("OpenSSL 1.1.1f LICENSE.txt", "ncurses 6.1 COPYING.txt", "zlib 1.2.11 LICENSE.txt")},
                   py / "Python LICENSE.txt": python_licence(), py / "PyInstaller COPYING.txt": pyinstaller_licence()}
    lic = stage / "LICENSES"
    for dst, src in copies.items():
        (lic / dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, lic / dst)
    notices = []
    for f in NOTICES:
        lines = (PULSAR / f).read_text(encoding="utf-8").splitlines()
        end = (next(i for i, line in enumerate(lines) if "*/" in line) + 1 if lines[0].startswith("/*")
               else len(list(itertools.takewhile(lambda line: line.startswith("//"), lines))))
        notices.append(f"== {f}\n\n" + "\n".join(lines[:end]) + "\n\n")
    (lic / rr / "File notices.txt").write_text("".join(notices), encoding="utf-8")
    # cygwin1.dll's terms are in the newlib-cygwin tarball inside Cygwin's source package: winsup/'s, and newlib's.
    if not linux:
        with tarfile.open(WIT_DIR / CYGWIN_SOURCE) as outer:
            inner = next(m for m in outer.getmembers() if m.name.endswith(".tar.bz2"))
            with tarfile.open(fileobj=outer.extractfile(inner), mode="r|bz2") as t:
                for m in t:
                    if m.name.partition("/")[2] in ("winsup/CYGWIN_LICENSE", "winsup/COPYING.LIB", "winsup/COPYING", "COPYING.NEWLIB"):
                        (lic / wit / f"Cygwin {Path(m.name).name}.txt").write_bytes(t.extractfile(m).read())

    if linux:
        wc_text = """WiiCompiled (payload/WiiCompiled-Setup.AppImage):
  {url}  commit {commit}
  Its third-party components and their sources: LICENSES/WiiCompiled/THIRD-PARTY-NOTICES.md. The AppImage also
  carries the compiler that builds the game on your PC, LLVM's clang and lld with CMake and Ninja (their versions,
  licences and sources: LICENSES/WiiCompiled/AppImage toolchain.txt); the .NET runtime of its setup and translator
  (MIT, https://github.com/dotnet/runtime); nodtool (MIT or Apache-2.0, https://github.com/encounter/nod); and
  AppImage's type-2 runtime (MIT, https://github.com/AppImage/type2-runtime), statically linked with musl, squashfuse
  and libfuse (LGPL-2.1), whose sources that repository's build scripts name.

wit 3.05a (payload/wit/wit, Wiimm's own Linux build): payload/wit/{src},
  from https://github.com/Wiimm/wiimms-iso-tools commit {wit}
  It is statically linked with the GNU C Library (LGPL-2.1+, LICENSES/wit/), whose source is at
  https://sourceware.org/glibc/. For three years from this release, the source of the GNU C Library as linked
  into payload/wit/wit is available on request through this project's GitHub issues.
"""
    else:
        wc_text = """WiiCompiled (payload/WiiCompiled-Setup.exe):
  {url}  commit {commit}
  Its third-party components and their sources: LICENSES/WiiCompiled/THIRD-PARTY-NOTICES.md. The GNU utilities in its
  llvm-mingw toolkit are GPL: their source is at https://github.com/mstorsjo/llvm-mingw, at the version that
  WiiCompiled's Launcher/Prepare-PortableTools.ps1 pins, and WiiCompiled's offer to supply it is passed on here.

wit 3.05a (payload/wit/wit.exe): payload/wit/{src},
  from https://github.com/Wiimm/wiimms-iso-tools commit {wit}

Cygwin 3.1.7-1 (payload/wit/cygwin1.dll): payload/wit/{cygwin}, Cygwin's own source package.
  Its terms, newlib's notices among them, are in LICENSES/wit and Cygwin/. The other DLLs beside it are Cygwin's
  builds of OpenSSL 1.1.1f, ncurses 6.1 and zlib 1.2.11, under the permissive licences there.
"""
    wit_commit = WIT_SRC[0].rsplit("/", 1)[1].split(".")[0]
    (stage / "SOURCE.txt").write_text(f"""SplitScreen8 {release['version']}: where the source of everything in this {"archive" if linux else "zip"} is

Each repository below holds the exact commit this release was built from.

SplitScreen8, the setup (MKWii 8P Splitscreen Setup/) and the tools it runs:
  {urls['root']}  commit {release['root']}
  Its README.md says how the release is built (tools/release.py).

Retro Rewind's engine with SplitScreen8 (payload/rr/Code.pul; payload/mkw/Code.pul and Loader.pul, the same commit built
with SS8_VANILLA):
  {urls['rr-pulsar']}  commit {release['rr-pulsar']}

wuj5, the menu file formats inside the setup:
  {urls['wuj5']}  commit {release['wuj5']}

{wc_text.format(url=urls['Wiicompiled'], commit=release['Wiicompiled'], src=WIT_SOURCE, wit=wit_commit, cygwin=CYGWIN_SOURCE)}
Python {versions['python']} and PyInstaller {versions['pyinstaller']}, the setup's runtime:
  https://www.python.org/ and https://github.com/pyinstaller/pyinstaller
""", encoding="utf-8")


def require_public(repo: Path, url: str) -> None:
    head = git(repo, "rev-parse", "HEAD")
    # No credential helper, askpass or prompt (an empty GIT_ASKPASS also overrides core.askPass and SSH_ASKPASS),
    # so a private repository fails here as it would for a player.
    heads = subprocess.run(["git", "-c", "credential.helper=", "ls-remote", "--heads", url], capture_output=True, text=True,
                           env={**os.environ, "GIT_ASKPASS": "", "GIT_TERMINAL_PROMPT": "0"})
    if heads.returncode:
        install_8p.fail(f"{url} is not publicly readable, and SOURCE.txt points there:\n{heads.stderr.strip()}")
    for branch in (line.split()[0] for line in heads.stdout.splitlines()):
        if subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", head, branch], capture_output=True).returncode == 0:
            return
    install_8p.fail(f"{repo} is at {head[:8]}, which no branch of {url} holds that this checkout has fetched: "
                    "push it (SOURCE.txt points there)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", help="the release's name (default dev-<root commit>)")
    ap.add_argument("--dirty-root", action="store_true", help="allow uncommitted root files (a candidate); marks the version")
    ap.add_argument("--fetch-wit", action="store_true", help="only fetch wit into build/wit/")
    ap.add_argument("--freeze", type=Path, help="only freeze the setup into this folder (tools/build_linux.sh)")
    ap.add_argument("--linux", type=Path, help="also the Linux archive, from tools/build_linux.sh's output (build/linux)")
    args = ap.parse_args()
    if args.fetch_wit:
        fetch_wit()
        return 0
    if args.freeze:
        freeze(args.freeze, args.freeze.parent / "pyinstaller")
        return 0

    for repo in (PULSAR, WUJ5, WC, *(() if args.dirty_root else (ROOT,))):
        if status := git(repo, "status", "--porcelain"):
            install_8p.fail(f"{repo} has uncommitted files:\n{status}")
    for sub in (PULSAR, WUJ5, WC):
        recorded = git(ROOT, "ls-tree", "HEAD", sub.relative_to(ROOT).as_posix()).split()[2]
        if recorded != git(sub, "rev-parse", "HEAD"):
            install_8p.fail(f"{sub} is not at {recorded}, the commit the root records")
    urls = {"root": git(ROOT, "remote", "get-url", "origin"),
            **{sub.name: git(ROOT, "config", "-f", ".gitmodules", "--get", f"submodule.external/{sub.name}.url") for sub in (PULSAR, WUJ5, WC)}}
    if not args.dirty_root:
        for repo, url in zip((ROOT, PULSAR, WUJ5, WC), urls.values()):
            require_public(repo, url)
    if not (DOCS / "README.txt").is_file():
        install_8p.fail(f"no {DOCS / 'README.txt'}: the zip's README is written by hand")
    if not INSTALLER.is_file():
        install_8p.fail(f"no {INSTALLER}: build WiiCompiled's installer there first (README.md, The release zip)")
    # The installer carries the runtime, Aurora and the project as source, so a dist/ older than the checkout must not ship.
    sources = [WC / f for f in git(WC, "ls-files", "-z").split("\0") if f]
    if INSTALLER.stat().st_mtime < max(f.stat().st_mtime for f in sources):
        install_8p.fail(f"{INSTALLER} is older than its sources in {WC}: build it again")
    if args.linux:
        # Built in a container, which has no git: build_linux.sh records the commit it archived.
        built = (args.linux / "WiiCompiled-Setup.AppImage.commit").read_text(encoding="utf-8").strip()
        if built != git(WC, "rev-parse", "HEAD"):
            install_8p.fail(f"{args.linux / 'WiiCompiled-Setup.AppImage'} was built from {built[:9]}, not {WC}'s HEAD: "
                            "run tools/build_linux.sh again")
        frozen = args.linux / "setup" / "commit.txt"
        if not frozen.is_file():
            install_8p.fail(f"no frozen Linux setup in {args.linux / 'setup'}: run tools/build_linux.sh")
        # The Windows setup is frozen here from the tree released; the Linux one earlier, from what tools/ held then.
        head, (commit, _, dirty) = git(ROOT, "rev-parse", "HEAD"), frozen.read_text(encoding="utf-8").strip().partition("-")
        if commit != head or (dirty and not args.dirty_root):
            install_8p.fail(f"the Linux setup was frozen at {commit[:9]}{'-' + dirty if dirty else ''}, not {head[:9]}: "
                            "run tools/build_linux.sh --only setup again")
    if build_code.load_env().get("EXTRA_CFLAGS"):
        install_8p.fail("EXTRA_CFLAGS is set (.env or the environment); a release builds with none")
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        install_8p.fail(f"{sys.executable} has no PyInstaller")

    if args.version and not re.fullmatch(r"[\w.+-]+", args.version):
        install_8p.fail(f"--version {args.version!r}: letters, digits and . + - _ only")
    dirty = args.dirty_root and git(ROOT, "status", "--porcelain")
    version = args.version or f"dev-{git(ROOT, 'rev-parse', '--short=8', 'HEAD')}"
    version += "-dirty" if dirty else ""
    rr_version = (install_8p.RR_SRC / "version.txt").read_text(encoding="utf-8").strip()
    rr_commit = install_8p.code_pul_commit(install_8p.RR_SRC / "Binaries" / "Code.pul")
    base = git(PULSAR, "merge-base", "HEAD", "upstream/main")
    if not base.startswith(rr_commit):
        install_8p.fail(f"Wheel Wizard's Retro Rewind {rr_version} is {rr_commit}, not the fork's base {base[:8]}")

    desc = git(PULSAR, "describe", "--always", "--dirty", "--match", "NOT A TAG")
    stage = OUT / f"mkwii-8p-splitscreen-{version}"
    shutil.rmtree(stage, ignore_errors=True)
    files = {}
    for name, (out, flags, defines, suffix) in BUILDS.items():
        subprocess.run([sys.executable, str(ROOT / "tools" / "build_code.py"), "--clean", *flags, "--out", str(out)], check=True)
        if (built := {f for f in (out / "cflags.txt").read_text().split() if f.startswith("-D")}) != defines:
            install_8p.fail(f"{out} was built with {sorted(built)}, not {sorted(defines)}")
        if suffix and (commit := install_8p.code_pul_commit(out / "Code.pul")) != desc + suffix:
            install_8p.fail(f"{out / 'Code.pul'} names {commit}, not {desc + suffix}")
        for pul in ["Code.pul"] + (["Loader.pul"] if flags else []):
            dst = stage / "payload" / name / pul
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out / pul, dst)
            files[f"{name}/{pul}"] = install_8p.sha256(dst)

    shutil.copy2(INSTALLER, stage / "payload" / INSTALLER.name)
    files[INSTALLER.name] = install_8p.sha256(stage / "payload" / INSTALLER.name)
    fetch_wit()
    (stage / "payload" / "wit").mkdir()
    for name in [Path(f).name for f in WIT_FILES] + [WIT_SOURCE, CYGWIN_SOURCE]:
        shutil.copy2(WIT_DIR / name, stage / "payload" / "wit" / name)
        files[f"wit/{name}"] = install_8p.sha256(stage / "payload" / "wit" / name)

    freeze(stage, OUT / "pyinstaller")

    release = {"version": version, "root": git(ROOT, "rev-parse", "HEAD"), "rr-pulsar": git(PULSAR, "rev-parse", "HEAD"),
               "wuj5": git(WUJ5, "rev-parse", "HEAD"), "Wiicompiled": git(WC, "rev-parse", "HEAD"),
               "rr_version": rr_version, "rr_commit": rr_commit, "files": files}
    (stage / "payload" / "release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    write_docs(stage, release, urls)
    archive = OUT / f"{stage.name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(stage.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(OUT))
    print(f"[release] {archive}: {archive.stat().st_size >> 20} MB, sha256 {install_8p.sha256(archive)}")
    print(json.dumps(release, indent=2))
    if args.linux:
        linux_archive(args.linux, stage, release, urls)
    return 0


def linux_archive(built: Path, windows: Path, release: dict, urls: dict) -> None:
    """The Linux archive beside the zip: the zip's Code.pul and Loader.pul, tools/build_linux.sh's AppImage and setup,
    wit's Linux build. A tar keeps what the zip cannot: the executables' mode bits."""
    stage = OUT / f"{windows.name}-linux-x86_64"
    shutil.rmtree(stage, ignore_errors=True)
    with tarfile.open(fetch(*WIT_LINUX)) as t:
        for name in ("bin/wit", "gpl-2.0.txt"):
            member = next(m for m in t.getmembers() if m.name.endswith(f"/{name}"))
            (WIT_DIR / "linux").mkdir(exist_ok=True)
            (WIT_DIR / "linux" / Path(name).name).write_bytes(t.extractfile(member).read())
    files = {name: digest for name, digest in release["files"].items() if name.endswith(".pul")}
    for build in BUILDS:
        shutil.copytree(windows / "payload" / build, stage / "payload" / build)
    shutil.copy2(built / "WiiCompiled-Setup.AppImage", stage / "payload" / "WiiCompiled-Setup.AppImage")
    (stage / "payload" / "wit").mkdir()
    for src, name in ((WIT_DIR / "linux" / "wit", "wit"), (WIT_DIR / "linux" / "gpl-2.0.txt", "gpl-2.0.txt"), (WIT_DIR / WIT_SOURCE, WIT_SOURCE)):
        shutil.copy2(src, stage / "payload" / "wit" / name)
    for f in ("WiiCompiled-Setup.AppImage", "wit/wit", "wit/gpl-2.0.txt", f"wit/{WIT_SOURCE}"):
        files[f] = install_8p.sha256(stage / "payload" / f)
    shutil.copytree(built / "setup" / SETUP_NAME, stage / SETUP_NAME)
    linux = {**release, "files": files}
    (stage / "payload" / "release.json").write_text(json.dumps(linux, indent=2) + "\n", encoding="utf-8")
    write_docs(stage, linux, urls, built)
    archive = OUT / f"{stage.name}.tar.gz"
    executables = {stage / SETUP_NAME / SETUP_NAME, stage / "payload" / "WiiCompiled-Setup.AppImage", stage / "payload" / "wit" / "wit"}

    def mode(info: tarfile.TarInfo) -> tarfile.TarInfo:
        path = OUT / info.name
        info.mode = 0o755 if info.isdir() or path in executables or path.suffix == ".so" or ".so." in path.name else 0o644
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info

    with tarfile.open(archive, "w:gz") as t:
        t.add(stage, stage.relative_to(OUT).as_posix(), filter=mode)
    print(f"[release] {archive}: {archive.stat().st_size >> 20} MB, sha256 {install_8p.sha256(archive)}")


if __name__ == "__main__":
    sys.exit(main())
