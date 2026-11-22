"""
preview_all.py — 生成猫头鹰版的所有预览素材（不需要窗口）

输出到 preview/：
  owl_light.gif   轻敲全流程
  owl_heavy.gif   重击全流程
  owl_grid.gif    9 宫格扫一遍
  owl_hype.gif    上滑彩蛋
  owl_overview.png  四种交互的关键帧对照图

用法：
    python3 preview_all.py
    python3 preview_all.py --size 128
"""

from __future__ import annotations

import argparse
import os
import sys

from PIL import Image, ImageDraw, ImageFont

import eyes_core as ec
from duetto_owl import build_owl_frame
from duetto_render import OwlPillowRenderer
from duetto_states import state_to_owl
from eyes_core import EyeStateMachine

FPS = 30
OUT = "preview"


def _frames(segments, renderer, warm=20):
    sm = EyeStateMachine()
    for _ in range(warm):
        sm.update(1 / FPS)

    out = []
    for dur, events in segments:
        for ev in events:
            if ev[0] == "tap":
                sm.tap(ev[1])
            elif ev[0] == "grid":
                sm.press_grid(ev[1])
            elif ev[0] == "release":
                sm.release_grid()
        for _ in range(max(1, int(dur * FPS))):
            sm.update(1 / FPS)
            out.append(renderer.render(build_owl_frame(state_to_owl(sm))))
    return out


SCRIPTS = {
    "owl_light": [
        (0.4, []), (0.05, [("tap", "light")]), (0.8, []),
        (0.05, [("tap", "light")]), (0.8, []),
        (0.05, [("tap", "light")]), (0.9, []),
    ],
    "owl_heavy": [
        (0.4, []), (0.05, [("tap", "heavy")]), (1.2, []),
        (0.05, [("tap", "heavy")]), (1.3, []),
    ],
    "owl_grid": sum(([
        (0.04, [("grid", k)]),
        (0.55, [("release",)]),
    ] for k in ("b", "m", "t", "u", "h")), []),
    "owl_hype": [
        (0.4, []),
        (0.04, [("grid", "n")]), (0.10, []),
        (0.04, [("grid", "h")]), (0.10, []),
        (0.04, [("grid", "y")]), (0.10, [("release",)]),
        (3.0, []),
    ],
}

TITLES = {
    "owl_light": "轻敲 ×3",
    "owl_heavy": "重击 ×2",
    "owl_grid": "9 宫格扫一遍",
    "owl_hype": "上滑彩蛋",
}


def save_gif(frames, path):
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   duration=1000 // FPS, loop=0, optimize=True, disposal=2)


def overview(collected, out_path):
    font = _font(15)
    thumb, pad, lh = 170, 8, 24
    cols = 7
    W = cols * (thumb + pad) + pad
    H = sum(thumb + lh + pad * 2 for _ in collected) + pad
    sheet = Image.new("RGB", (W, H), (14, 12, 10))
    d = ImageDraw.Draw(sheet)
    y = pad
    for name, frames in collected:
        d.text((pad, y + 5), TITLES.get(name, name), fill=(214, 196, 170), font=font)
        n = len(frames)
        idxs = [0, n // 6, n // 4, n // 3, n // 2, (2 * n) // 3, n - 1]
        for i, ix in enumerate(idxs[:cols]):
            im = frames[min(ix, n - 1)].resize((thumb, thumb), Image.LANCZOS)
            sheet.paste(im, (pad + i * (thumb + pad), y + lh))
        y += thumb + lh + pad * 2
    sheet.save(out_path)


def _font(size):
    for p in ("/System/Library/Fonts/PingFang.ttc",
              "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=240)
    ap.add_argument("--only", action="append", choices=list(SCRIPTS))
    args = ap.parse_args(argv)

    os.makedirs(OUT, exist_ok=True)
    renderer = OwlPillowRenderer(size=args.size, super_sample=3)

    collected = []
    for name in (args.only or list(SCRIPTS)):
        frames = _frames(SCRIPTS[name], renderer)
        path = os.path.join(OUT, f"{name}.gif")
        save_gif(frames, path)
        print(f"{path}  {len(frames)} frames")
        collected.append((name, frames))

    overview(collected, os.path.join(OUT, "owl_overview.png"))
    print(os.path.join(OUT, "owl_overview.png"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
