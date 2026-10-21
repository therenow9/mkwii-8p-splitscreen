# MKWii 8P Splitscreen: 5 to 8 player local splitscreen for Mario Kart Wii

![Eight players racing on Baby Park](https://github.com/therenow9/mkwii-8p-splitscreen/releases/download/v1.0.0/01-8p-babypark.jpg)

## Intro
Hi, I am a Software Engineer and video game hobbyist, I grew up on Mario Kart Wii and Nintendo games, I made this mod so my brother
and I could play Retro Rewind with more than 4 players locally, the project quickly grew in scale to much larger than I thought it would.
Alot of love and care went into this project catching all the little bugs and adding polish.  This is my gift back to the Mario Kart Wii Community,
I hope you all enjoy playing Mario Kart Wii with 7 of your closest friends! :)

## AI disclosure

This project was built with [Claude Code](https://claude.com/claude-code), Anthropic's AI coding assistant. Claude Code
wrote most of the code, build tools, tests and documentation, and did much of the reverse engineering, working from
my direction and guardrails.  Despite being AI assisted the project was still well over 100 hours of hard work from planning, testing, bug fixes,
packaging, and release

## What it is

MKWii 8P Splitscreen lets 5, 6, 7 or 8 people race on one screen, on
[Retro Rewind](https://github.com/Retro-Rewind-Team/rr-pulsar) and on Mario Kart Wii without it, in the spirit of
Mario Kart 8 Deluxe's 8-player update. It plays in Dolphin, in
[WiiCompiled](https://github.com/patchzyy/Wiicompiled), and on a real Wii through USB Loader GX.

**To play, download the zip from the [Releases](../../releases) page** and follow its `README.txt`. You need your own
Mario Kart Wii disc image, and Retro Rewind 6.13.3 for the Retro Rewind build, which the setup downloads if yours is
missing or another version; Wheel Wizard is optional. The setup
makes everything else from your files. The rest of this page is
for building from source.

> **PAL only.** MKWii 8P Splitscreen needs the PAL (European) Mario Kart Wii, game ID **RMCP01**. The USA (RMCE01), Japanese
> (RMCJ01) and Korean (RMCK01) versions do not work, even though Retro Rewind runs on them: the code is built for the
> PAL game only. The setup checks the ID first and stops without changing anything if it is not RMCP01.

| Players | Screen |
|---|---|
| 1–4 | as the game |
| 5–6 | 3×2 tiles |
| 7–8 | 4×2 tiles |

- VS, Team VS and Battle with up to 8 local players, plus Retro Rewind's KO, Lap KO, Battle Royale and Elimination.
- Two players can pick the same character.
- Two builds from one tree: **Retro Rewind** with MKWii 8P Splitscreen added (offline only), and **Mario Kart Wii**
  with MKWii 8P Splitscreen and nothing else, everything unlocked.
- With 1 to 4 players, the Retro Rewind build plays as Retro Rewind does, offline, apart from a few deliberate
  differences: the main menu's 2 to 8 Players buttons, no Retro WFC, and the same-character OK markers among them.

<p>
<img src="https://github.com/therenow9/mkwii-8p-splitscreen/releases/download/v1.0.0/07-join-8.jpg" width="49%" alt="The join page with eight controllers">
<img src="https://github.com/therenow9/mkwii-8p-splitscreen/releases/download/v1.0.0/08-character-8.jpg" width="49%" alt="The character page with eight picks">
<img src="https://github.com/therenow9/mkwii-8p-splitscreen/releases/download/v1.0.0/11-8p-team.jpg" width="49%" alt="Team VS with eight players on Rainbow Road">
<img src="https://github.com/therenow9/mkwii-8p-splitscreen/releases/download/v1.0.0/04-v-8p-coconutmall.jpg" width="49%" alt="The Mario Kart Wii build, eight players on Coconut Mall">
</p>

## How it fits together

- The game code is a module in a fork of Retro Rewind's engine, `external/rr-pulsar/PulsarEngine/SplitScreen8/`,
  built with Kamek into a `Code.pul` (PAL addresses). WiiCompiled takes code only as a `Code.pul`, which it translates
  at setup.
- The Mario Kart Wii build is the same tree built with `--vanilla` (`SS8_VANILLA`): SplitScreen8 without Retro Rewind,
  loaded by Pulsar's own `Loader.pul`.
- Files derived from the game (the character pages' per-player OK-marker controls) are generated at install time from
  the player's own files (`tools/assets/`). **No Nintendo or Retro Rewind data is in this repository**, nor anything
  derived from it, apart from the Wii Wheel icon (`tools/release_docs/wii-wheel.ico` and `.rgba`).
- Each build installs as its own local copy. A player's own Retro Rewind, its saves and online play are only read.

## Adopting SplitScreen8 in another pack

SplitScreen8 is a Pulsar module, so a pack built on Pulsar or Retro Rewind can take it in.

**The module** is `PulsarEngine/SplitScreen8/` in the fork: 21 source files, about 8,600 lines. A Pulsar build
compiles every source under `PulsarEngine/` (Pulsar's Makefile and `build_code.py` alike), so the folder needs no build
list.

| Area | Files |
|---|---|
| Race screens: the screen count, cameras, culling, sound | `Screens.cpp`, `ScreenGuards.cpp`, `Culling.cpp`, `Lakitu.cpp`, `Audio.cpp` |
| Race HUD, wipes and pause | `Hud.cpp`, `Wipe.cpp`, `Colours.cpp`, `Pause.cpp` |
| Players 5 to 8: controllers, slots, items | `Input.cpp`, `Locals.cpp`, `Items.cpp` |
| Menus: the 2 to 8 Players buttons, the join, character and kart pages, previews | `Entry.cpp`, `Join.cpp`, `CharSelect.cpp`, `Select.cpp`, `Previews.cpp` |
| The Mario Kart Wii build's stand-ins for Retro Rewind | `Vanilla.cpp` |
| Testing only: scripted menus and debug boots | `MenuScript.cpp`, `DebugBoot.cpp` |

`SplitScreen8.hpp` holds the shared state and the game addresses two files use.

**Outside the module**, the fork changes or adds 43 files (+757/−94 against upstream `f1c9208a`;
`git -C external/rr-pulsar diff --stat f1c9208a HEAD -- . ':!PulsarEngine/SplitScreen8'` lists them): Retro Rewind's
per-player arrays widened from 4 to 8, the same-character page, fixes to Retro Rewind's own modes for 5 to 8 players,
and the symbols the module calls in `GameSource/symbols.txt`.

**Without Retro Rewind**, `build_code.py --vanilla` (`SS8_VANILLA`) compiles the module alone, plus the
same-character page, and builds Pulsar's `Loader.pul` to load it. `Vanilla.cpp` stands in for the Pulsar and Retro
Rewind hooks the module relies on, and the link fails on any it misses. That build is the smaller starting point for a
pack that is not Retro Rewind.

**The character pages' per-player OK-marker controls** (2 to 8 players) are generated at install time by
`tools/assets/gen_charselect_multi.py`, from the player's own `MenuMulti.szs` overlaid by the pack's
`ReplacedAssets.szs` where it has one: Retro Rewind's copies of those controls replace the disc's at runtime. A pack
ships the generator, not the file.

**What a port must keep.** Each of these fails silently: it builds, and the symptom shows up somewhere else.
- With 1 to 4 local players every hook but the deliberate differences above returns early, so 4-player play stays the
  pack's own. A race widens only for VS and Battle: 5 to 6 locals race on 6 screens, 7 to 8 on 8 (`WideScreenCount`
  in `Screens.cpp`).
- Players 5 to 8 live in side tables sized by `kMaxLocal`, never at indices 4 to 7 of the game's 4-wide arrays, which
  would overwrite the next field. The pack's own per-player statics widen in place to `kMaxLocal`.
- Search `PulsarEngine` for an address before hooking it: two hooks on one site both apply, and the later one wins.
- PAL addresses only, from `GameSource/symbols.txt`, and only Kamek command forms Pulsar already uses (this module
  uses `kmCall`, `kmBranch`, `kmPatchExitPoint` and `kmWrite32`): WiiCompiled translates `Code.pul` at setup and fails
  there on a form it does not know.

## Layout

```
external/rr-pulsar    Retro Rewind's engine (GPLv3) with SplitScreen8, branch splitscreen8 of the fork
external/Wiicompiled  WiiCompiled (GPLv3), the fork whose installer the release ships
external/wuj5         Wii UI file formats (MIT), used by the asset generator
tools/                build, install, test and release scripts (Python)
tools/menu_scripts/   scripted controller input for unattended test runs in Dolphin
```

## Building

Windows only. Clone near the root of a drive: Kamek's link line names every object file and sits close to Windows'
32,767-character limit, and rr-pulsar's deepest header passes 260 characters under a long path. A clone path of
about 60 characters or less works (`C:\src\mkwii-8p-splitscreen`, for example).

```
git clone --recurse-submodules https://github.com/therenow9/mkwii-8p-splitscreen.git C:\src\mkwii-8p-splitscreen
```

### Toolchain

None of these are in the repository; each goes in a gitignored folder under `tools/`.

| Tool | Where it goes | Where it comes from |
|---|---|---|
| CodeWarrior `mwcceppc` / `mwasmeppc`, Wii 1.7 (4.3 build 213) | `tools/cw-wii17/` | The `Wii/1.7` folder of `https://files.decomp.dev/compilers_latest.zip`, which needs no licence: `unzip -j compilers_latest.zip "Wii/1.7/*" -d tools/cw-wii17` |
| Kamek 2.0 | `tools/kamek/` | Treeki/Kamek's release `2026-02-24`, `win-x64.zip` |
| Python 3.12 | `tools/.venv/` | python.org's 3.12: `py -3.12 -m venv tools/.venv`, then `tools/.venv/Scripts/python -m pip install capstone json5`. Or uv: `uv venv --python 3.12 tools/.venv`, then `uv pip install --python tools/.venv capstone json5` |

Other paths go in `.env` (copy `.env.example`; relative paths are taken from the repository's root).

### Code.pul

```
tools/.venv/Scripts/python.exe tools/build_code.py              # Retro Rewind build -> build/code/Code.pul
tools/.venv/Scripts/python.exe tools/build_code.py --vanilla    # Mario Kart Wii build -> build/code-vanilla/Code.pul, Loader.pul
tools/.venv/Scripts/python.exe tools/build_code.py --help       # debug builds, debug boots, menu scripts
```

The Retro Rewind build's title shows its commit, `<rr-pulsar commit>-ss8`.

### A development copy

`tools/install_8p.py` makes a separate copy to play and test in, under `Documents/Modding/` by default
(`--root` moves it). It needs Wheel Wizard with Retro Rewind 6.13.3 (the fork's base) and a PAL
disc image, and Dolphin for `dolphin`. It only reads those.

```
tools/.venv/Scripts/python.exe tools/install_8p.py isolation snapshot   # first: records your own files, to verify later
tools/.venv/Scripts/python.exe tools/install_8p.py prepare              # the copy: RR, a Dolphin user folder, the launch file
tools/.venv/Scripts/python.exe tools/install_8p.py code ss8             # puts build/code/Code.pul in it
tools/.venv/Scripts/python.exe tools/install_8p.py dolphin              # plays it
tools/.venv/Scripts/python.exe tools/install_8p.py isolation verify     # your own files unchanged
```

`--vanilla` before the command does the same for the Mario Kart Wii build, in its own copy. `wiicompiled` and `wii`
make a portable WiiCompiled install and USB Loader GX images in the copy; `wii` needs wit, fetched once with
`release.py --fetch-wit`. `tools/lane.py` and `tools/smoke_8p.py` run
scripted games in Dolphin unattended, with screenshots and logs.

### The release zip

`tools/release.py` builds `build/release/mkwii-8p-splitscreen-<version>.zip`: both builds from a clean tree, the player's
setup (`tools/ss8_setup.py`) frozen by PyInstaller, WiiCompiled's installer, wit, `LICENSES/` and `SOURCE.txt`. It
needs:
- a second venv on a python.org-style CPython 3.12 (not Anaconda's) with `pip install pyinstaller`;
- Wheel Wizard with Retro Rewind at the fork's base (6.13.3), and rr-pulsar's upstream as a remote, which it checks
  against: `git -C external/rr-pulsar remote add upstream https://github.com/Retro-Rewind-Team/rr-pulsar.git`, then
  `git -C external/rr-pulsar fetch upstream`;
- WiiCompiled's installer built in `external/Wiicompiled` at the commit this repository records: the .NET 8 SDK, then
  `Launcher/Prepare-PortableTools.ps1`, `Prepare-Dependencies.ps1` and `Build-Installer.ps1` from that checkout, with
  Visual Studio's VC++ redistributable installed or `-VcRuntimeDirectory` pointing at a copy of it.

It fetches wit and its sources itself, and refuses a dirty tree, a submodule off its recorded commit, and a
repository a player cannot read.

## Contributing

Pull requests are welcome and encouraged. Every one is reviewed before it is merged.

Report a bug or suggest a feature with the [issue forms](../../issues/new/choose). Changes to the game code go to [therenow9/rr-pulsar](https://github.com/therenow9/rr-pulsar)'s `splitscreen8`
branch, and this repository records its commit. Keep 1 to 4 players behaving as Retro Rewind does (the few differences above are deliberate), use PAL
addresses from `GameSource/symbols.txt`, and never commit game files or anything derived from them.

## Credit

Reusing this code in your own project or pack is encouraged, under the GPLv3 (see License). Please credit
**therenow9** with a link to [github.com/therenow9](https://github.com/therenow9), preferably on your repository, for
example in its README's credits. A credit in your release notes is acceptable too.

## License

GPLv3 (`LICENSE`), matching Retro Rewind and WiiCompiled. The release zip's `LICENSES/` holds the licence of every
part it ships.
