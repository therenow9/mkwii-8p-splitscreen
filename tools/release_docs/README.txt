SplitScreen8
5 to 8 player local splitscreen for Mario Kart Wii
====================================================

SplitScreen8 lets 5, 6, 7 or 8 people race on one screen, on Retro Rewind and on
Mario Kart Wii without it. It plays in Dolphin, in WiiCompiled, and on a real Wii.

Two builds come in this zip:

  Retro Rewind     Retro Rewind with SplitScreen8 added. Offline only.
  Mario Kart Wii   The original game with SplitScreen8 and nothing else,
                   everything unlocked.

With 1 to 4 players, the Retro Rewind build plays as Retro Rewind does, offline.
The Mario Kart Wii build plays as the original game, offline, with everything
unlocked, and with two players allowed to pick the same character.


WHICH DISC
----------

SplitScreen8 works with three versions of Mario Kart Wii:

  PAL (Europe, Australia)   game ID RMCP01
  USA                       game ID RMCE01
  Japan                     game ID RMCJ01

The Korean version (RMCK01) does not work: neither Retro Rewind nor WiiCompiled
has a Korean build.

To see your disc image's game ID, look at the ID column in Dolphin's game list, or
right-click the game and open Properties, then Info.

The setup checks the ID before it does anything else. If it is not one of the
three, the setup stops and says so, and nothing is installed or changed.


WHAT YOU NEED
-------------

- Windows.
- Your own Mario Kart Wii disc image: PAL, USA or Japan (see WHICH DISC above).
  No game files come in this zip; the setup makes everything from yours.
- For the Retro Rewind build: Retro Rewind 6.13.4, its RetroRewind6 folder with the
  riivolution folder beside it, as Wheel Wizard or Retro Rewind's own download
  puts them. If you have none, or yours is another version (6.13.5, say), leave
  "Download Retro Rewind 6.13.4 for me" ticked: the setup downloads it (about 2 GB)
  into the install folder, and your own Retro Rewind stays as it is.
- To play in Dolphin: Dolphin.
- Wheel Wizard is optional. With it, the setup fills in your disc, Retro Rewind and
  Dolphin by itself; without it, you pick each with Browse.
- Disk space: when done, the Retro Rewind copy takes about 2.2 GB, each WiiCompiled
  copy about 5 GB, each Wii image 2.5 to 5 GB, and a downloaded Retro Rewind 2.2 GB.
  While it installs it needs more: WiiCompiled builds each copy with about 23 GB
  free, and a Wii image needs about 10 GB while it is made. The setup's window
  shows how much your picks need and how much the drive has, under "Install to",
  and the setup checks the space before it starts.

Your own Retro Rewind, its saves and online play are never changed. The setup only
reads them. Each build installs as its own separate copy.


INSTALL
-------

1. Unzip this zip close to the root of a drive, for example C:\SS8.
   A long folder path makes the setup fail to start.

2. Open the mkwii-8p-splitscreen-<version> folder it makes, then its
   "MKWii 8P Splitscreen Setup" folder, and run "MKWii 8P Splitscreen Setup.exe".

   If Windows says "Windows protected your PC", click More info, then Run anyway.
   The setup is not signed, so Windows does not know it yet.

3. The setup's window shows what it found on this PC: your disc, Retro Rewind
   and Dolphin. Anything missing or wrong is marked with a cross and says why, and
   a build or a place to play it cannot use is greyed out.

   Tick the builds and where to play: Dolphin, WiiCompiled, or A real Wii
   (images for USB Loader GX). The folder both copies go in is Documents\MKWii 8 Player
   unless you pick another. Then click Install.

4. Wait. A WiiCompiled copy took under 2 minutes to set up on a fast PC, and can
   take much longer on a slower one; a Wii image takes a few minutes more. The
   window shows how far WiiCompiled has got. If the setup stops, it says
   why: fix that and click Install again.

   If it stops moving for more than 15 minutes, close it and run it again: it
   carries on where it was. A "Cygwin" warning in its details is harmless.

You get one folder per build, "Retro Rewind" and "Mario Kart Wii", inside the
folder you picked.


PLAY ON PC
----------

Open a build's folder and run:

  "Play (Dolphin).cmd"       to play in Dolphin
  "Play (WiiCompiled).cmd"   to play in WiiCompiled

From the main menu, pick Multiplayer, then 2 to 8 Players.

Above 60 fps in WiiCompiled: press F10, and under Graphics set "Race frame
interpolation" to 120 or 180 FPS and tick VSync. The game still runs at 60; the
extra frames make races smoother. Use a monitor at least as fast as the setting
(WiiCompiled follows the refresh rate of the monitor its window is on), and with
8 players in the Retro Rewind build, use 120 or Off: 180 is uneven there.


CONTROLLERS IN WIICOMPILED
--------------------------

Press F10 to open the controller settings, and assign each controller once to one
of the eight rows by picking it from the row's list. The row is what the game sees:
"GameCube 1" to "GameCube 4" are its GameCube controller ports, and "Wii Remote 1"
to "Wii Remote 4" its Wii Remotes, so up to 4 of each. Any kind of controller works
in any row. Which player each one plays is the order you join in the game's menus,
as on a Wii, so any player can use either kind.

A GameCube adapter shows up as four controllers with the same name. To find yours,
hold a button on it while the row's list is open: its entry says "pressing".

A pad on a Wii Remote row plays as a Wii Remote with a Classic Controller, with
buttons by position unless you change them: on a GameCube controller, that makes X
the A button.

Each row has its own buttons: pick the row, then "Customize buttons", a GameCube
layout on the GameCube rows and a Classic Controller layout on the Wii Remote rows.
A Wii U Pro Controller keeps its own fixed layout.

A real Wii Remote works too, held sideways or with a Nunchuk or a Classic
Controller. To pair one, press F10, open "Wii Remote setup (Bluetooth)",
click "Pair a Wii Remote" and press the red SYNC button under the remote's battery
cover (1 and 2 together only connects it until you close the game). After that,
any button connects it. A real Wii Remote plays instead of a pad on the same row.
F10 also shows a Wii Remote under GameCube 1 to 4 when that row has no pad: with 1
to 4 players it can be any player, as on a Wii.

Holding the Wii Remote sideways, and the Nunchuk, need a real Wii Remote. Any other
controller on a Wii Remote row always plays as a Classic Controller.

Windows gives Xbox-style controllers (XInput) only 4 places, so a 5th and 6th Xbox
controller may not show up in WiiCompiled at all. Past 4 players, use a GameCube
adapter (it uses none of those places) or PlayStation or Switch controllers, or mix
them with up to 4 Xbox controllers. If you only have Xbox controllers, you can try
an environment variable (Settings > System > About > Advanced system settings >
Environment Variables, under your user): add SDL_JOYSTICK_RAWINPUT with the value
1 and start the game again. It lets WiiCompiled's controller library, SDL, see
more than 4, though the triggers of the 5th and later may not work separately.
This is SDL's own setting, not tested with this release.

To use a Wii U Pro Controller or a Mayflash DolphinBar as an ordinary pad, untick
"Use Wii Remotes" in F10 and start the game again.

If a GameCube adapter shows nothing, or the game crashes as it starts:

- Look for an environment variable named SDL_JOYSTICK_HIDAPI_GAMECUBE
  (Settings > System > About > Advanced system settings > Environment Variables).
  If it is there, delete it and start the game again.
- With a Mayflash adapter in PC mode, uninstall Mayflash's vibration driver.
- An adapter in Wii U mode needs the WinUSB driver (Zadig, "WUP-028"), as for
  Dolphin. Plug it in before starting the game. For rumble, plug in both USB plugs.

If a controller stops working after you replug it, plug your controllers in in the
same order each time, or assign the empty slot again in F10.

Close every other copy of the game before you start one: two copies fight over the
controllers.


PLAY ON A WII
-------------

This needs a Wii with USB Loader GX and a USB drive. Tick "A real Wii" when you
install (see INSTALL).

1. Copy the folders inside each build's Wii\wbfs\ folder into the wbfs folder on
   your USB Loader GX drive.
2. Start USB Loader GX from its channel on the Wii Menu, not from the Homebrew
   Channel, which leaves the game too little memory.
3. In USB Loader GX, set each game's Aspect Ratio to Force 16:9, and put your TV
   on its Full, Wide or Stretch mode. At 4:3 the 8 player pages are too crowded.

Each image has its own save, apart from your own Retro Rewind's.

On a Wii, connect up to 4 GameCube controllers and 4 Wii Remotes. Any player, 1 to 8,
can use either kind.


GOOD TO KNOW
------------

- This release is built on Retro Rewind 6.13.4, and later Retro Rewind versions may
  not get a release of their own. The setup makes its copy from 6.13.4, downloading
  it when yours is another version, so your own Retro Rewind can keep updating.
- The Retro Rewind build is offline only. Play online from your own Retro Rewind.
- In the Retro Rewind build with 5 to 8 players, some tracks need more memory than
  the game has left, so they show greyed out and random picks skip them:
  - Mystic Tangle, always.
  - With Battle Royale on, at 5 to 8 players: 3DS Rosalina's Ice World, Call of the
    Glassheart, Citro's Wedding Altar, Cubing Conundrum, Darkest Night, Dreamer,
    GP Bowser's Castle, GP Diamond City, Luigi's Island, Promise of Annalise, SW Peach
    Gardens (DS Peach Gardens' second version), SW2 Toad's Factory (Wii Toad's
    Factory's second version), The Frozen Lakes of Steleah, Tour Vancouver Velocity,
    Wii U Ribbon Road (GBA Ribbon Road's second version), Wii U Sunshine Airport,
    Wii U Super Bell Subway and Wii U Twisted Mansion.
  - With Battle Royale on, at 7 or 8 players, also: Botania, Good Egg Drive,
    Honeybee Hideout, Tour Athens Dash and Wii U Electrodrome.
- On a window wider than 16:9, a splitscreen race fills the whole window. If you
  move or resize the window during a race, the HUDs and the lines between the
  screens stay where they were until the next race. Size the window before a
  race starts.
- Custom characters and other mods are not supported. With 5 to 8 players the
  menus have little memory to spare, and mods can leave players 5 to 8 with no
  character shown on the character page, and skins that will not change. So the
  setup leaves your Retro Rewind's mod folders, Patches (where Wheel Wizard puts
  mods) and MyStuff, out of the 8-player copy, and an update removes them from a
  copy made by 1.0.2 or earlier. Your own Retro Rewind keeps them.
- WiiCompiled's graphics thread is off in both builds: with it on, race audio
  crackles with 4 players and more, and on our PC the frame rate was the same
  without it. To try it anyway, add gx_worker = true under [video] in the copy's
  WiiCompiled\UserData\Config.toml.


A NOTE FROM THE DEVELOPER ABOUT FUTURE UPDATES
----------------------------------------------

I'm very happy with how this project has turned out. But this is the last major
feature update for some time, because I can't keep pace on my own with Retro
Rewind and WiiCompiled, both made by extraordinary teams and both moving fast.

Retro Rewind is already a memory-hungry pack, and with 8 players on top the game
runs very close to its limit: some tracks are already greyed out at 5-8 players.
Retro Rewind is built mostly for one player and some local play, and each of its
updates changes how it hooks into the game and manages memory, so bringing this
mod along takes many hours of rework. WiiCompiled is the same: each update can
bring new glitches and breaks, and needs hours of testing and troubleshooting to
rule them out. This last WiiCompiled update was meant to take 2 to 3 hours to
bring in and lightly test; it took more than 12.

Unless one of those projects adopts the mod officially, which I doubt given how
niche it is, I can't keep up with them alone. So the 8 player mod stays on Retro
Rewind 6.13.4 and WiiCompiled v0.9.2. I'll still gladly put out a hotfix for any
game-breaking bug or crash.

And I believe it is complete as it is. Retro Rewind 6.13.4 is feature rich and
mature: nearly every track from every Mario Kart game, custom tracks, custom
characters with their own voices, and many extra features and modes. The Mario
Kart Wii build now runs at 60 fps in Dolphin too, and WiiCompiled runs both builds
at a smooth 60 fps. Chasing every new version would risk more breakage than it
would add.


THANKS
------

Thanks to Duke Inferno's Biggest Fan, on Discord, for reporting the character
page's memory problem that 1.0.2 and 1.0.3 fix, and for finding that custom
characters caused it.


LICENCE AND SOURCE
------------------

SplitScreen8 is free software under the GNU General Public License, version 3.
LICENSES\ holds the licence of every part shipped in this zip. SOURCE.txt lists
where the source of each part is, at the exact version built.
