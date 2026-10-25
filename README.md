# MKWii 8P Splitscreen: 5 to 8 player local splitscreen for Mario Kart Wii

![Eight players racing on Baby Park](https://github.com/user-attachments/assets/cb7c8f56-158c-4823-a301-fb3543c8c36e)

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

## A note from the developer about future updates

I'm very happy with how this project has turned out. But this is the last major feature update for some time,
because I can't keep pace on my own with Retro Rewind and WiiCompiled, both made by extraordinary teams and both
moving fast.

Retro Rewind is already a memory-hungry pack, and with 8 players on top the game runs very close to its limit: some
tracks are already greyed out at 5-8 players. Retro Rewind is built mostly for one player and some local play, and
each of its updates changes how it hooks into the game and manages memory, so bringing this mod along takes many
hours of rework. WiiCompiled is the same: each update can bring new glitches and breaks, and needs hours of testing
and troubleshooting to rule them out. This last WiiCompiled update was meant to take 2 to 3 hours to bring in and
lightly test; it took more than 12.

Unless one of those projects adopts the mod officially, which I doubt given how niche it is, I can't keep up with
them alone. So the 8 player mod stays on Retro Rewind 6.13.4 and WiiCompiled v0.9.2. I'll still gladly put out a
hotfix for any game-breaking bug or crash.

And I believe it is complete as it is. Retro Rewind 6.13.4 is feature rich and mature: nearly every track from
every Mario Kart game, custom tracks, custom characters with their own voices, and many extra features and modes.
The Mario Kart Wii build now runs at 60 fps in Dolphin too, and WiiCompiled runs both builds at a smooth 60 fps.
Chasing every new version would risk more breakage than it would add.

## What it is

MKWii 8P Splitscreen lets 5, 6, 7 or 8 people race on one screen, on
[Retro Rewind](https://github.com/Retro-Rewind-Team/rr-pulsar) and on Mario Kart Wii without it, in the spirit of
Mario Kart 8 Deluxe's 8-player update. It plays in Dolphin, in
[WiiCompiled](https://github.com/patchzyy/Wiicompiled), and on a real Wii through USB Loader GX.

**To play, download the zip from the [Releases](../../releases) page** and follow its `README.txt`. You need your own
Mario Kart Wii disc image, and Retro Rewind 6.13.4 for the Retro Rewind build, which the setup downloads if yours is
missing or another version; Wheel Wizard is optional. The setup
makes everything else from your files. The rest of this page is
for building from source.

> **Which disc.** MKWii 8P Splitscreen runs on the PAL (**RMCP01**), USA (**RMCE01**) and Japanese (**RMCJ01**) Mario Kart
> Wii. The Korean version (RMCK01) does not work: neither Retro Rewind nor WiiCompiled has a Korean build. The setup
> checks the ID first and stops without changing anything if it is not one of the three.

| Players | Screen |
|---|---|
| 1–4 | as the game |
| 5–6 | 3×2 tiles |
| 7–8 | 4×2 tiles |

- VS, Team VS and Battle with up to 8 local players, plus Retro Rewind's KO, Lap KO, Battle Royale and Elimination.
- Two players can pick the same character.
- With 5 to 8 players the Retro Rewind build greys out the tracks that need more memory than it has left; the
  release's `README.txt` names them.
- Two builds from one tree: **Retro Rewind** with MKWii 8P Splitscreen added (offline only), and **Mario Kart Wii**
  with MKWii 8P Splitscreen and nothing else, everything unlocked.
- With 1 to 4 players, the Retro Rewind build plays as Retro Rewind does, offline, apart from a few deliberate
  differences: the main menu's 2 to 8 Players buttons, no Retro WFC, and the same-character OK markers among them.

<p>
<img src="https://github.com/user-attachments/assets/28b1d826-2c75-481e-a6b4-e3fbab6222e1" width="49%" alt="The join page with eight controllers">
<img src="https://github.com/user-attachments/assets/5de06b55-4953-413e-8712-cd23ead09b77" width="49%" alt="The character page with eight picks">
<img src="https://github.com/user-attachments/assets/8829bf0b-4657-4178-b47b-8bb704c1a69a" width="49%" alt="Team VS with eight players on Rainbow Road">
<img src="https://github.com/user-attachments/assets/ac8f40fa-0fdd-4287-9f5a-7fe383e9418a" width="49%" alt="The Mario Kart Wii build, eight players on Coconut Mall">
</p>

## How it fits together

- The game code is a module in a fork of Retro Rewind's engine, `external/rr-pulsar/PulsarEngine/SplitScreen8/`,
  built with Kamek into a `Code.pul`: written with PAL addresses, linked once per region through
  `GameSource/versions.txt`. WiiCompiled takes code only as a `Code.pul`, which it translates at setup.
- The Mario Kart Wii build is the same tree built with `--vanilla` (`SS8_VANILLA`): SplitScreen8 without Retro Rewind,
  loaded by Pulsar's own `Loader.pul`.
- Files derived from the game (the character pages' per-player OK-marker controls) are generated at install time from
  the player's own files (`tools/assets/`). **No Nintendo or Retro Rewind data is in this repository**, nor anything
  derived from it, apart from the setup's Wii Wheel icon (`tools/release_docs/wii-wheel.ico`).
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

**Outside the module**, the fork changes or adds 43 files (+759/−94 against upstream `52f23582`;
`git -C external/rr-pulsar diff --stat 52f23582 HEAD -- . ':!PulsarEngine/SplitScreen8'` lists them): Retro Rewind's
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
- PAL addresses only, and every one through `GameSource/symbols.txt` or a Kamek hook, never as a number in the code:
  Kamek ports those to the USA and Japanese builds, and nothing else. `tools/region_check.py` checks a build's three
  binaries against the discs (its `--derive` gives a `versions.txt` line for an address the map lacks). Only Kamek
  command forms Pulsar already uses (this module
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
(`--root` moves it). It needs Wheel Wizard with Retro Rewind 6.13.4 (the fork's base) and a PAL, USA or
Japanese disc image (Wheel Wizard's, or `--disc`), and Dolphin for `dolphin`. It only reads those.

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
- Wheel Wizard with Retro Rewind at the fork's base (6.13.4), and rr-pulsar's upstream as a remote, which it checks
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
addresses through `GameSource/symbols.txt` (`tools/region_check.py` should report nothing new), and never commit game files or anything derived from them.

## Credit

Reusing this code in your own project or pack is encouraged, under the GPLv3 (see License). Please credit
**therenow9** with a link to [github.com/therenow9](https://github.com/therenow9), preferably on your repository, for
example in its README's credits. A credit in your release notes is acceptable too.

## License

GPLv3 (`LICENSE`), matching Retro Rewind and WiiCompiled. The release zip's `LICENSES/` holds the licence of every
part it ships.
