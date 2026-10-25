#!/usr/bin/env python3
"""Build a SplitScreen8 release zip: both Code.pul builds from a clean tree, the frozen player setup, and the payload.

  release.py [--version V] [--dirty-root]
  release.py --fetch-wit      only fetch wit into build/wit/, where install_8p.py wii takes it

Into build/release/mkwii-8p-splitscreen-<V>.zip (V defaults to dev-<root commit>):
  MKWii 8P Splitscreen Setup/  tools/ss8_setup.py frozen by PyInstaller (onedir, no UPX), with the menu generator and wuj5
  payload/rr/Code.pul        the RR build, for the Retro Rewind release on the owner's Wheel Wizard (rr_version, rr_commit)
  payload/mkw/Code.pul, Loader.pul   the --vanilla build and its loader
  payload/WiiCompiled-Setup.exe      WiiCompiled's installer, built in external/Wiicompiled at the root's gitlink
  payload/wit/               wit (Wiimms ISO Tools, GPLv2), which makes the Wii images: its exe, DLLs, licence, and
                             the sources of wit and of cygwin1.dll (LGPLv3+)
  payload/release.json       the version, the commits built, rr_version, rr_commit and each payload file's sha256
  README.txt                 tools/release_docs/README.txt
  LICENSES/                  every shipped component's licence, copied from where it lives
  SOURCE.txt                 each component's public repository at the commit built

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
DOCS = ROOT / "tools" / "release_docs"
# rr-pulsar files under their own licence, each stated in the file's leading comment block.
NOTICES = ["KamekInclude/kamek.hpp", "PulsarEngine/Extensions/Cosmos/WUP028.cpp", "PulsarEngine/IO/LooseArchiveOverrides.cpp",
           "PulsarEngine/Sound/LooseBRSAROverrides.cpp", "PulsarEngine/Network/GPReport.cpp", "PulsarEngine/Network/WiiLink.cpp",
           "PulsarEngine/Network/RSA.cpp", "PulsarEngine/Network/SHA256.cpp", "PulsarEngine/Network/Ranking.hpp",
           "PulsarEngine/UI/CtrlRaceBase/InputViewer.cpp"]


def git(repo: Path, *argv: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *argv], capture_output=True, text=True, check=True).stdout.strip()


def fetch_wit() -> None:
    download = WIT_DIR / "download"
    download.mkdir(parents=True, exist_ok=True)
    got = []
    for url, digest in (WIT_ZIP, WIT_SRC, CYGWIN_SRC):
        dst = download / url.rsplit("/", 1)[1]
        if not dst.exists() or install_8p.sha256(dst) != digest:
            urllib.request.urlretrieve(url, dst)
        if install_8p.sha256(dst) != digest:
            install_8p.fail(f"{url} is not the file pinned ({digest}); a new wit means new pins")
        got.append(dst)
    with zipfile.ZipFile(got[0]) as z:
        top = z.namelist()[0].split("/")[0]
        for name in WIT_FILES:
            (WIT_DIR / Path(name).name).write_bytes(z.read(f"{top}/{name}"))
    shutil.copy2(got[1], WIT_DIR / WIT_SOURCE)
    shutil.copy2(got[2], WIT_DIR / CYGWIN_SOURCE)
    print(f"[release] wit in {WIT_DIR}")


def write_docs(stage: Path, release: dict, urls: dict) -> None:
    shutil.copy2(DOCS / "README.txt", stage / "README.txt")
    pyinstaller = distribution("pyinstaller")
    rr, wit, py = Path("Retro Rewind (rr-pulsar)"), Path("wit and Cygwin"), Path("Python and PyInstaller")
    copies = {Path("SplitScreen8/LICENSE.txt"): ROOT / "LICENSE",
              rr / "LICENSE.txt": PULSAR / "LICENSE", rr / "LICENSE_mkw-sp.txt": PULSAR / "LICENSE_mkw-sp",
              rr / "LICENSE_pulsar.txt": PULSAR / "LICENSE_pulsar", rr / "Kamek LICENSE.txt": DOCS / "licenses" / "Kamek LICENSE.txt",
              Path("wuj5/LICENSE.txt"): WUJ5 / "LICENSE",
              Path("WiiCompiled/LICENSE.txt"): WC / "LICENSE", Path("WiiCompiled/THIRD-PARTY-NOTICES.md"): WC / "THIRD-PARTY-NOTICES.md",
              wit / "wit gpl-2.0.txt": WIT_DIR / "gpl-2.0.txt",
              **{wit / f: DOCS / "licenses" / f for f in ("OpenSSL 1.1.1f LICENSE.txt", "ncurses 6.1 COPYING.txt", "zlib 1.2.11 LICENSE.txt")},
              py / "Python LICENSE.txt": Path(sys.base_prefix) / "LICENSE.txt",
              py / "PyInstaller COPYING.txt": Path(pyinstaller.locate_file(next(f for f in pyinstaller.files if f.name == "COPYING.txt")))}
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
    with tarfile.open(WIT_DIR / CYGWIN_SOURCE) as outer:
        inner = next(m for m in outer.getmembers() if m.name.endswith(".tar.bz2"))
        with tarfile.open(fileobj=outer.extractfile(inner), mode="r|bz2") as t:
            for m in t:
                if m.name.partition("/")[2] in ("winsup/CYGWIN_LICENSE", "winsup/COPYING.LIB", "winsup/COPYING", "COPYING.NEWLIB"):
                    (lic / wit / f"Cygwin {Path(m.name).name}.txt").write_bytes(t.extractfile(m).read())

    wit_commit = WIT_SRC[0].rsplit("/", 1)[1].split(".")[0]
    (stage / "SOURCE.txt").write_text(f"""SplitScreen8 {release['version']}: where the source of everything in this zip is

Each repository below holds the exact commit this release was built from.

SplitScreen8, the setup (MKWii 8P Splitscreen Setup/) and the tools it runs:
  {urls['root']}  commit {release['root']}
  Its README.md says how the release is built (tools/release.py).

Retro Rewind's engine with SplitScreen8 (payload/rr/Code.pul; payload/mkw/Code.pul and Loader.pul, the same commit built
with SS8_VANILLA):
  {urls['rr-pulsar']}  commit {release['rr-pulsar']}

wuj5, the menu file formats inside the setup:
  {urls['wuj5']}  commit {release['wuj5']}

WiiCompiled (payload/WiiCompiled-Setup.exe):
  {urls['Wiicompiled']}  commit {release['Wiicompiled']}
  Its third-party components and their sources: LICENSES/WiiCompiled/THIRD-PARTY-NOTICES.md. The GNU utilities in its
  llvm-mingw toolkit are GPL: their source is at https://github.com/mstorsjo/llvm-mingw, at the version that
  WiiCompiled's Launcher/Prepare-PortableTools.ps1 pins, and WiiCompiled's offer to supply it is passed on here.

wit 3.05a (payload/wit/wit.exe): payload/wit/{WIT_SOURCE},
  from https://github.com/Wiimm/wiimms-iso-tools commit {wit_commit}

Cygwin 3.1.7-1 (payload/wit/cygwin1.dll): payload/wit/{CYGWIN_SOURCE}, Cygwin's own source package.
  Its terms, newlib's notices among them, are in LICENSES/wit and Cygwin/. The other DLLs beside it are Cygwin's
  builds of OpenSSL 1.1.1f, ncurses 6.1 and zlib 1.2.11, under the permissive licences there.

Python {platform.python_version()} and PyInstaller {pyinstaller.version}, the setup's runtime:
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
    args = ap.parse_args()
    if args.fetch_wit:
        fetch_wit()
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

    work = OUT / "pyinstaller"
    shutil.rmtree(work, ignore_errors=True)
    data = [f"{install_8p.GENERATOR};tools/assets", f"{WUJ5 / 'LICENSE'};external/wuj5",
            f"{DOCS / 'wii-wheel.ico'};tools/release_docs",
            *(f"{p};external/wuj5" for p in sorted(WUJ5.glob("*.py")))]
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--noupx", "--console",
                    "--hide-console", "hide-early", "--icon", str(DOCS / "wii-wheel.ico"), "--name", "MKWii 8P Splitscreen Setup",
                    "--distpath", str(stage), "--workpath", str(work / "work"),
                    "--specpath", str(work), "--paths", str(ROOT / "tools"), "--paths", str(WUJ5),
                    *(a for m in WUJ5_MODULES for a in ("--hidden-import", m)),
                    *(a for d in data for a in ("--add-data", d)), str(ROOT / "tools" / "ss8_setup.py")], check=True)

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
