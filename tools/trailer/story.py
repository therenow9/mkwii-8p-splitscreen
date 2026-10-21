#!/usr/bin/env python3
"""Cut a 16:9 video with sound into vertical 1080x1920 parts for an Instagram story: the whole picture across the
middle, a blurred and darkened copy of it filling above and below.

Usage:
  story.py VIDEO [--parts N] [--out-dir DIR]

The parts are equal (a 2:00 video in two parts is two 1:00 clips, a story's limit). Each part's sound fades over
its first and last 0.15 s so the cut does not click.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from edit import ffmpeg, run

W, H, FPS = 1080, 1920, 30


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path)
    ap.add_argument("--parts", type=int, default=2)
    ap.add_argument("--out-dir", type=Path)
    args = ap.parse_args()
    ff = ffmpeg()
    ffprobe = Path(ff).with_name(Path(ff).name.replace("ffmpeg", "ffprobe"))
    length = float(subprocess.run([str(ffprobe), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                   str(args.video)], capture_output=True, text=True, check=True).stdout)
    out_dir = args.out_dir or args.video.parent
    part = length / args.parts
    graph = (f"[0:v]fps={FPS},split[v1][v2];"
             f"[v1]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=40,"
             f"colorchannelmixer=rr=0.45:gg=0.45:bb=0.45[bg];"
             f"[v2]scale={W}:-2:flags=lanczos[fg];[bg][fg]overlay=0:(H-h)/2,format=yuv420p[vout];"
             f"[0:a]afade=t=in:d=0.15,afade=t=out:st={part - 0.15:.3f}:d=0.15[aout]")
    for n in range(args.parts):
        out = out_dir / f"{args.video.stem}-story-{n + 1}of{args.parts}.mp4"
        run([ff, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{n * part:.3f}", "-t", f"{part:.3f}",
             "-i", str(args.video), "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
             "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-maxrate", "12M", "-bufsize", "24M",
             "-profile:v", "high", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])
        print(f"{out}: {part:.2f} s")


if __name__ == "__main__":
    main()
