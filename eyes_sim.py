"""
eyes_sim.py — 1.24 寸圆屏「眼睛」原型模拟器（Tkinter，纯 Python 图形绘制，不 import 任何图片）

运行：
    python3 eyes_sim.py                  # 1.24 寸圆屏，按 240×240 渲染，窗口放大 2 倍
    python3 eyes_sim.py --size 128       # 模拟 128×128 的小圆屏
    python3 eyes_sim.py --scale 3        # 窗口再放大一点
    python3 eyes_sim.py --grid-keys tgb  # 换一组 9 宫格键

按键
----
  轻敲：  F           （也可以用 ← 方向键 / 空格）
  重击：  J           （也可以用 → 方向键 / 回车）
  9 宫格：T Y U / G H J / B N M   （左边一列=左，右边一列=右，上中下三行对应高低）
  上滑彩蛋：1 秒内先按 B/N/M，再按 G/H/J，再按 T/Y/U
  退出：  Esc 或关窗口

设计说明
--------
本文件只负责「输入 + 窗口 + 每帧重绘」。
所有动画状态在 eyes_core.py（纯浮点逻辑，可原样搬到 Arduino）。
所有像素绘制在 eyes_render.py（TkRenderer 就是 Arduino GFX 的对照表）。
"""

from __future__ import annotations

import argparse
import sys
import time
import tkinter as tk

import eyes_core as ec
from eyes_core import EyeStateMachine
from eyes_render import TkRenderer

# ---------------------------------------------------------------------------
# 默认键位
# ---------------------------------------------------------------------------

LIGHT_KEYS = {"f", "space", "Left"}
HEAVY_KEYS = {"j", "Return", "Right"}
GRID_KEYS = set("tyughjbnm")


class EyeSimApp:
    def __init__(self, root: tk.Tk, screen: int, scale: int):
        self.root = root
        self.screen = screen
        self.px_size = screen * scale
        self.sm = EyeStateMachine()

        # 状态栏文字
        self.hint_text = tk.StringVar(value="")
        # 键盘左右下三个格子键一直按住的「松开检测」
        self.pressed_grid: set[str] = set()

        root.title("圆屏眼睛原型 · 1.24\" (240×240 逻辑分辨率)")
        root.configure(bg="#0a0a0f")
        root.resizable(False, False)

        self.canvas = tk.Canvas(root, width=self.px_size, height=self.px_size,
                                bg="#0a0a0f", highlightthickness=0, bd=0)
        self.canvas.pack(padx=18, pady=(18, 6))

        bar = tk.Frame(root, bg="#0a0a0f")
        bar.pack(fill="x", padx=18, pady=(0, 14))

        self.status = tk.Label(bar, textvariable=self.hint_text, anchor="w",
                               justify="left", font=("Helvetica", 11),
                               fg="#8f9bb8", bg="#0a0a0f")
        self.status.pack(fill="x")

        self.renderer = TkRenderer(self.canvas, self.px_size)
        self._draw_ring()

        # 键盘绑定
        root.bind("<KeyPress>", self.on_press)
        root.bind("<KeyRelease>", self.on_release)
        root.bind("<Escape>", lambda e: root.destroy())
        root.focus_force()

        self.last_t = time.perf_counter()
        self.fps = 0.0
        self._fps_acc = 0.0
        self._fps_n = 0
        self.tick()

    # ------------------------------------------------------------------
    # 圆屏外框（Tkinter 里画一个物理屏的壳，方便看 1.24 寸的比例）
    # ------------------------------------------------------------------

    def _draw_ring(self) -> None:
        s = self.px_size
        pad = 10
        # 外框（屏幕排线/边框）
        self.canvas.create_oval(pad, pad, s - pad, s - pad,
                                outline="#22283a", width=3)
        # 圆外遮罩（四角切掉，呈现圆屏效果）
        self.canvas.create_oval(pad + 3, pad + 3, s - pad - 3, s - pad - 3,
                                outline="#0a0a0f", width=1)

    # ------------------------------------------------------------------
    # 输入
    # ------------------------------------------------------------------

    def on_press(self, event) -> None:
        key = _norm_key(event)
        if key in LIGHT_KEYS and key not in GRID_KEYS:
            self.sm.tap(ec.LIGHT)
            self._flash("轻敲 LIGHT")
        elif key in HEAVY_KEYS:
            self.sm.tap(ec.HEAVY)
            self._flash("重击 HEAVY")
        elif key in GRID_KEYS:
            if key in self.pressed_grid:
                return  # 屏蔽系统按键重复
            self.pressed_grid.add(key)
            res = self.sm.press_grid(key)
            col, row = ec.KEY_GRID[key]
            label = f"{ec.KEY_COL_NAME[col]}-{ec.KEY_ROW_NAME[row]}  ({key.upper()})"
            if res == "hype":
                self._flash(f"{label}  →  ★ 上滑彩蛋！")
            else:
                self._flash(label)

    def on_release(self, event) -> None:
        key = _norm_key(event)
        if key in self.pressed_grid:
            self.pressed_grid.discard(key)
        if not self.pressed_grid:
            self.sm.release_grid()

    def _flash(self, text: str) -> None:
        self.hint_text.set("  " + text)

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def tick(self) -> None:
        now = time.perf_counter()
        dt = now - self.last_t
        self.last_t = now

        # 按键全松开后延迟回中，避免连打时左右抖动
        self.sm.update(dt)
        frame = self.sm.build_frame()
        self.renderer.render(frame)

        # FPS
        self._fps_acc += dt
        self._fps_n += 1
        if self._fps_acc >= 0.5:
            self.fps = self._fps_n / self._fps_acc
            self._fps_acc = 0.0
            self._fps_n = 0
        self.root.after(8, self.tick)


def _norm_key(event) -> str:
    """把 Tk 的 keysym 归一化成小写 / 方向键名。"""
    k = event.keysym
    if k in ("Left", "Right", "Up", "Down", "Return", "Escape", "space"):
        return k
    if len(k) == 1:
        return k.lower()
    return k.lower()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="圆屏眼睛原型模拟器")
    ap.add_argument("--size", type=int, default=ec.SCREEN,
                    help="圆屏逻辑分辨率，默认 240")
    ap.add_argument("--scale", type=int, default=2,
                    help="窗口放大倍数，默认 2")
    args = ap.parse_args(argv)

    ec.SCREEN = args.size

    root = tk.Tk()
    EyeSimApp(root, args.size, max(1, args.scale))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
