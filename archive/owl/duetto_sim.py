"""
duetto_sim.py — Duetto 猫头鹰形象模拟器（Tkinter）

和 eyes_sim.py 的区别：
  - 渲染的是猫头鹰形象（duetto_owl / duetto_render）
  - 屏幕外多画一圈"灯带"，模拟 Duetto 的双人视界（左=我 / 右=对方）
  - 输入逻辑完全复用 eyes_core，一字没改

运行：
    python3 duetto_sim.py
    python3 duetto_sim.py --size 128 --scale 3
"""

from __future__ import annotations

import _bootstrap  # noqa: F401  (把仓库根目录加进 sys.path)

import argparse
import sys
import time
import tkinter as tk

import eyes_core as ec
from duetto_owl import build_owl_frame
from duetto_render import OwlTkRenderer, hexc
from duetto_states import light_for, state_to_owl
from eyes_core import EyeStateMachine

LIGHT_KEYS = {"f", "space", "Left"}
HEAVY_KEYS = {"j", "Return", "Right"}
GRID_KEYS = set("tyughjbnm")


class DuettoApp:
    def __init__(self, root: tk.Tk, screen: int, scale: int):
        self.root = root
        self.screen = screen
        self.px_size = screen * scale
        self.sm = EyeStateMachine()
        self.pressed: set[str] = set()
        self.hint = tk.StringVar(value="")

        root.title('Duetto · 小猫头鹰原型')
        root.configure(bg="#0a0908")
        root.resizable(False, False)

        # 画布 = 圆屏 + 外圈灯带
        self.ring_pad = int(self.px_size * 0.075)
        total = self.px_size + self.ring_pad * 2
        self.canvas = tk.Canvas(root, width=total, height=total,
                                bg="#0a0908", highlightthickness=0, bd=0)
        self.canvas.pack(padx=20, pady=(20, 6))

        bar = tk.Frame(root, bg="#0a0908")
        bar.pack(fill="x", padx=20, pady=(0, 16))
        tk.Label(bar, textvariable=self.hint, anchor="w", justify="left",
                 font=("Helvetica", 11), fg="#8d8175", bg="#0a0908").pack(fill="x")

        # 屏幕画布嵌在中间
        self.screen_canvas = tk.Canvas(self.canvas, width=self.px_size,
                                       height=self.px_size, bg="#14100c",
                                       highlightthickness=0, bd=0)
        self.canvas.create_window(total // 2, total // 2,
                                  window=self.screen_canvas)

        self.renderer = OwlTkRenderer(self.screen_canvas, self.px_size)
        self._ring_ids = []

        root.bind("<KeyPress>", self.on_press)
        root.bind("<KeyRelease>", self.on_release)
        root.bind("<Escape>", lambda e: root.destroy())
        root.focus_force()

        self.last_t = time.perf_counter()
        self.tick()

    # ------------------------------------------------------------------

    def _norm(self, event) -> str:
        k = event.keysym
        if k in ("Left", "Right", "Return", "Escape", "space"):
            return k
        return k.lower()

    def on_press(self, event) -> None:
        key = self._norm(event)
        if key in LIGHT_KEYS:
            self.sm.tap(ec.LIGHT)
            self.hint.set("  轻敲")
        elif key in HEAVY_KEYS:
            self.sm.tap(ec.HEAVY)
            self.hint.set("  重击")
        elif key in GRID_KEYS:
            if key in self.pressed:
                return
            self.pressed.add(key)
            res = self.sm.press_grid(key)
            col, row = ec.KEY_GRID[key]
            txt = f"{ec.KEY_COL_NAME[col]}-{ec.KEY_ROW_NAME[row]}  ({key.upper()})"
            self.hint.set("  " + (txt + "  ★ 上滑彩蛋！" if res == "hype" else txt))

    def on_release(self, event) -> None:
        key = self._norm(event)
        self.pressed.discard(key)
        if not self.pressed:
            self.sm.release_grid()

    # ------------------------------------------------------------------

    def _draw_ring(self, left_col, right_col) -> None:
        """外圈灯带：左半=我，右半=对方。"""
        for i in self._ring_ids:
            self.canvas.delete(i)
        self._ring_ids.clear()

        total = self.px_size + self.ring_pad * 2
        inset = self.ring_pad * 0.30
        n = 48
        # 每颗灯画成一小段圆弧
        for i in range(n):
            a0 = 360.0 * i / n
            a1 = 360.0 * (i + 1) / n - 1.5
            # 顶部/底部划分左右：用角度判断
            mid = (a0 + a1) / 2.0
            col = left_col if 90.0 < mid < 270.0 else right_col
            self._ring_ids.append(
                self.canvas.create_arc(
                    inset, inset, total - inset, total - inset,
                    start=270.0 - a1, extent=(a1 - a0),
                    style="arc", outline=hexc(col),
                    width=max(2, int(self.ring_pad * 0.55))))

    def tick(self) -> None:
        now = time.perf_counter()
        dt = now - self.last_t
        self.last_t = now

        self.sm.update(dt)
        self.renderer.render(build_owl_frame(state_to_owl(self.sm)))
        l, r = light_for(self.sm)
        self._draw_ring(l, r)

        self.root.after(8, self.tick)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=ec.SCREEN)
    ap.add_argument("--scale", type=int, default=2)
    args = ap.parse_args(argv)
    ec.SCREEN = args.size

    root = tk.Tk()
    DuettoApp(root, args.size, max(1, args.scale))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
