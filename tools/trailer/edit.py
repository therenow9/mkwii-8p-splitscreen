#!/usr/bin/env python3
"""Cut the trailer from an edit list: each clip trimmed, cropped and scaled to 3840x2160 at 60 fps, its cards
overlaid, the clips joined with crossfades, the music laid under them, one file encoded for YouTube.

Usage:
  edit.py EDL.json [--out FILE] [--preview]     --preview: 1280x720, fast, for a review artifact

The edit list (paths relative to its own folder; times in seconds):
  {"music": [{"src": "music/title.wav", "in": 0, "at": 0, "fade_in": 0, "fade_out": 2},
             {"src": "music/n_Rainbow32_n.wav", "in": 0, "at": 82, "fade_in": 2, "gain": -4}],   # gain in dB, optional
   "end_fade": 2,
   "clips": [{"src": "takes/8p-vs/dump/x.avi", "in": 12.0, "out": 19.0,
              "crop": [x, y, w, h],          # optional, fractions of the frame
              "xfade": 0.5,                  # optional, a crossfade into this clip
              "dim": 0.55,                   # optional, darkens and softens the whole clip, for a card over it (0-1)
              "cards": [{"png": "cards/now.png", "from": 0.5, "to": 5.0,
                         "anim": "fade", "dur": 0.3}]}]}   # or "slide-left", with "slide": its travel in px at 4K

A source is a Dolphin dump at the internal resolution, aspect-corrected (3336x1824 at 4x): a clip with no crop of its
own is cut to the centre 16:9, a crop's fractions should make a 16:9 box in pixels.
The music is the only sound: the takes' own sound is not used.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

FPS = 60


def ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    winget = sorted(Path.home().glob("AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg*/*/bin/ffmpeg.exe"))
    if not winget:
        sys.exit("ffmpeg not found (winget install Gyan.FFmpeg)")
    return str(winget[-1])


def run(cmd: list[str]) -> None:
    print(" ".join(cmd[:6]), "...", cmd[-1], flush=True)
    subprocess.run(cmd, check=True)


def render_clip(ff: str, base: Path, i: int, clip: dict, size: tuple[int, int], work: Path) -> Path:
    w, h = size
    out = work / f"clip{i:02d}.mkv"
    dur = clip["out"] - clip["in"]
    chain = []
    if "crop" in clip:
        x, y, cw, ch = clip["crop"]
        chain.append(f"crop=iw*{cw}:ih*{ch}:iw*{x}:ih*{y}")
    else:
        chain.append("crop='min(iw,ih*16/9)':'min(ih,iw*9/16)'")
    chain += [f"scale={w}:{h}:flags=lanczos", "setsar=1", f"fps={FPS}"]
    if clip.get("dim"):
        # The editor's preview draws blur(6 px at 4K) and brightness(1 - dim): the same here.
        k = 1 - clip["dim"]
        chain += [f"gblur=sigma={6 * w / 3840:.2f}", f"colorchannelmixer=rr={k}:gg={k}:bb={k}"]
    chain.append("format=yuv420p")
    inputs = ["-ss", str(clip["in"]), "-t", str(dur), "-i", str(base / clip["src"])]
    graph = f"[0:v]{','.join(chain)}[v0]"
    last = "v0"
    for n, card in enumerate(clip.get("cards", []), 1):
        inputs += ["-loop", "1", "-framerate", str(FPS), "-t", str(dur), "-i", str(base / card["png"])]
        start, end, anim, d = card["from"], card["to"], card.get("anim", "fade"), card.get("dur", 0.3)
        if anim == "fade":
            # Linear here; the editor's preview eases it, with the same start and length.
            fades, x = f"fade=t=in:st={start}:d={d}:alpha=1,fade=t=out:st={end - 0.3}:d=0.3:alpha=1", "x=0"
        elif anim == "slide-left":
            # As the editor's preview: in from the left on a cubic ease over d, back out over 0.3 s, opaque by a third.
            p = f"clip(min((t-{start})/{d},({end}-t)/0.3),0,1)"
            fades = f"fade=t=in:st={start}:d={d / 3:.3f}:alpha=1,fade=t=out:st={end - 0.1:.3f}:d=0.1:alpha=1"
            x = f"x='-pow(1-{p},3)*{card['slide'] * w / 3840:.1f}'"
        else:
            sys.exit(f"card entrance {anim!r} is not rendered here yet")
        graph += (f";[{n}:v]scale={w}:{h},format=rgba,{fades}[c{n}];"
                  f"[{last}][c{n}]overlay={x}:y=0:format=auto[v{n}]")
        last = f"v{n}"
    run([ff, "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", f"[{last}]",
         "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "12", "-pix_fmt", "yuv420p", str(out)])
    return out


def join(ff: str, clips: list[dict], parts: list[Path], out: Path) -> float:
    """Crossfade the rendered clips in order; returns the joined length."""
    inputs, graph, last, length = [], "", "0:v", clips[0]["out"] - clips[0]["in"]
    for p in parts:
        inputs += ["-i", str(p)]
    for i in range(1, len(parts)):
        fade = clips[i].get("xfade", 0) or 1 / FPS
        length -= fade
        graph += f"[{last}][{i}:v]xfade=transition=fade:duration={fade}:offset={length:.4f}[j{i}];"
        last = f"j{i}"
        length += clips[i]["out"] - clips[i]["in"]
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", *inputs]
    cmd += (["-filter_complex", graph.rstrip(";"), "-map", f"[{last}]"] if graph else ["-map", "0:v"])
    run(cmd + ["-c:v", "libx264", "-preset", "medium", "-crf", "12", "-pix_fmt", "yuv420p", str(out)])
    return length


def music(ff: str, base: Path, edl: dict, length: float, out: Path) -> None:
    inputs, graph, labels = [], "", []
    for n, m in enumerate(edl["music"]):
        inputs += ["-ss", str(m.get("in", 0)), "-i", str(base / m["src"])]
        chain = [f"aresample=48000", f"volume={m['gain']}dB" if m.get("gain") else None,
                 f"afade=t=in:d={m['fade_in']}" if m.get("fade_in") else None]
        # A track playing out runs on through the next one's fade-in, fading out over its own last seconds.
        if n + 1 < len(edl["music"]):
            nxt = edl["music"][n + 1]
            stop = nxt["at"] + nxt.get("fade_in", 0) - m["at"]
            if m.get("fade_out"):
                chain.append(f"afade=t=out:st={stop - m['fade_out']:.3f}:d={m['fade_out']}")
            chain.append(f"atrim=0:{stop:.3f}")
        chain.append(f"adelay={int(m['at'] * 1000)}:all=1")
        graph += f"[{n}:a]{','.join(c for c in chain if c)}[m{n}];"
        labels.append(f"[m{n}]")
    end = edl.get("end_fade", 2)
    graph += (f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,atrim=0:{length:.3f},"
              f"afade=t=out:st={length - end:.3f}:d={end},alimiter=limit=-1dB:level=disabled[a]")
    run([ff, "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "[a]",
         "-c:a", "pcm_s16le", str(out)])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("edl", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    ff = ffmpeg()
    base = args.edl.resolve().parent
    edl = json.loads(args.edl.read_text(encoding="utf-8"))
    size = (1280, 720) if args.preview else (3840, 2160)
    work = base / ("work-preview" if args.preview else "work")
    work.mkdir(exist_ok=True)
    parts = [render_clip(ff, base, i, c, size, work) for i, c in enumerate(edl["clips"])]
    length = join(ff, edl["clips"], parts, work / "video.mkv")
    music(ff, base, edl, length, work / "music.wav")
    out = args.out or base / ("trailer-preview.mp4" if args.preview else "trailer.mp4")
    video = (["-c:v", "libx264", "-preset", "slow", "-crf", "26", "-maxrate", "6M", "-bufsize", "12M"] if args.preview
             else ["-c:v", "libx264", "-preset", "slow", "-crf", "14", "-profile:v", "high", "-level", "5.2"])
    run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(work / "video.mkv"), "-i", str(work / "music.wav"),
         "-map", "0:v", "-map", "1:a", *video, "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "320k",
         "-movflags", "+faststart", "-shortest", str(out)])
    print(f"{out}: {length:.2f} s")


if __name__ == "__main__":
    main()
