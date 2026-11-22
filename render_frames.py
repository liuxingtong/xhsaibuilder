"""
render_frames.py — 无头预览：把 eyes_core 的动画渲染成 PNG 序列 / GIF / 拼图

用途：
  1. 在没有窗口的环境（比如让我截图给你看）也能验证动画观感
  2. 快速对比调参前后的差别
  3. 导出 GIF 发群里看

用法：
    python3 render_frames.py                     # 生成全部演示 GIF 到 preview/
    python3 render_frames.py --only tap          # 只生成敲击演示
    python3 render_frames.py --sheet             # 额外输出一张关键时刻拼图
"""

from __future__ import annotations

import argparse
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFont

import eyes_core as ec
from eyes_core import EyeStateMachine
from eyes_render import PillowRenderer

OUT_DIR = "preview"
FPS = 30


# ---------------------------------------------------------------------------
# 脚本化输入：每一段是一个 (时长秒, [事件...]) 
# 事件 = ("tap", "light") / ("tap", "heavy") / ("grid", "t") / ("release",)
# ---------------------------------------------------------------------------


def script_idle():
    return [(2.0, [])]


def script_light():
    return [
        (0.55, []),
        (0.05, [("tap", "light")]),
        (0.75, []),
        (0.05, [("tap", "light")]),
        (0.75, []),
        (0.05, [("tap", "light")]),
        (0.85, []),
    ]


def script_heavy():
    return [
        (0.45, []),
        (0.05, [("tap", "heavy")]),
        (1.15, []),
        (0.05, [("tap", "heavy")]),
        (1.25, []),
    ]


def script_grid():
    seq = ["b", "h", "t", "j", "y", "u", "n", "m", "g"]
    out = []
    for k in seq:
        out.append((0.05, [("grid", k)]))
        out.append((0.62, [("release",)]))
    return out


def script_swipe():
    """1 秒内 B->H->T 上滑，触发彩蛋。"""
    return [
        (0.35, []),
        (0.05, [("grid", "n")]),
        (0.12, []),
        (0.05, [("grid", "h")]),
        (0.12, []),
        (0.05, [("grid", "y")]),
        (0.10, [("release",)]),
        (3.0, []),
    ]


SCRIPTS = {
    "idle": script_idle,
    "light": script_light,
    "heavy": script_heavy,
    "grid": script_grid,
    "swipe": script_swipe,
}

LABEL = {
    "idle": "待机呼吸 + 眨眼 + 微瞟",
    "light": "轻敲 ×3",
    "heavy": "重击 ×2",
    "grid": "9宫格滑动（左→中→右 / 上→下）",
    "swipe": "上滑彩蛋 B→H→T",
}


def run_script(sm: EyeStateMachine, segments, renderer: PillowRenderer):
    frames = []
    dt = 1.0 / FPS
    for dur, events in segments:
        # 事件在段首触发
        for ev in events:
            if ev[0] == "tap":
                sm.tap(ev[1])
            elif ev[0] == "grid":
                sm.press_grid(ev[1])
            elif ev[0] == "release":
                sm.release_grid()
        n = max(1, int(round(dur * FPS)))
        for _ in range(n):
            sm.update(dt)
            frames.append(renderer.render(sm.build_frame()))
    return frames


def save_gif(frames, path: str, frame_ms: int = 1000 // FPS) -> None:
    # GIF 调色板只能 256 色，量化一下
    first, rest = frames[0], frames[1:]
    first.save(path, save_all=True, append_images=rest,
               duration=frame_ms, loop=0, optimize=True, disposal=2)


def contact_sheet(paths: list[tuple[str, list[Image.Image]]], out: str,
                  cols: int = 4) -> None:
    """把各动画的关键帧拼成一张对照图。"""
    thumb = 200
    pad = 12
    label_h = 22
    rows = len(paths)
    sheet = Image.new("RGB", (cols * (thumb + pad) + pad,
                             rows * (thumb + label_h + pad) + pad), (18, 18, 24))
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 14)
    except Exception:
        font = ImageFont.load_default()

    for r, (name, frames) in enumerate(paths):
        n = len(frames)
        picks = ["min", "max", "end"]
        idxs = [
            0,
            max(range(n), key=lambda i: _busy(frames[i])) if name not in ("grid", "swipe") else n // 3,
            n - 1,
        ]
        d.text((pad, pad + r * (thumb + label_h + pad)), LABEL.get(name, name),
               fill=(200, 210, 235), font=font)
        for c, i in enumerate(idxs[:cols]):
            im = frames[i].resize((thumb, thumb), Image.LANCZOS)
            x = pad + c * (thumb + pad)
            y = pad + label_h + r * (thumb + label_h + pad)
            sheet.paste(im, (x, y))
    sheet.save(out)


def _busy(img) -> int:
    """粗略估计"画面最热闹"的一帧（非背景像素数量）。"""
    g = img.convert("L")
    px = list(g.getdata())
    return sum(1 for v in px if v > 40)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=list(SCRIPTS), action="append")
    ap.add_argument("--outdir", default=OUT_DIR)
    ap.add_argument("--size", type=int, default=240)
    ap.add_argument("--sheet", action="store_true")
    args = ap.parse_args(argv)

    os.makedirs(args.outdir, exist_ok=True)
    names = args.only or list(SCRIPTS)
    renderer = PillowRenderer(size=args.size, super_sample=3)

    collected = []
    for name in names:
        sm = EyeStateMachine()
        # 让待机状态先跑 0.5s，避免从"全闭眼"开始
        for _ in range(15):
            sm.update(1 / FPS)
        frames = run_script(sm, SCRIPTS[name](), renderer)
        path = os.path.join(args.outdir, f"{name}.gif")
        save_gif(frames, path)
        # 存几帧 PNG 方便看
        for i in (len(frames) // 3, len(frames) // 2, len(frames) - 1):
            frames[i].save(os.path.join(args.outdir, f"{name}_{i:03d}.png"))
        print(f"{path}  {len(frames)} frames")
        collected.append((name, frames))

    if args.sheet:
        sheet_path = os.path.join(args.outdir, "contact_sheet.png")
        contact_sheet(collected, sheet_path)
        print(sheet_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
