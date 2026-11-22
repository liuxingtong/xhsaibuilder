"""
eyes_sim.py — 1.24 寸圆屏「眼睛」原型模拟器（Tkinter，纯 Python 图形绘制，不 import 任何图片）

运行：
    python3 eyes_sim.py                  # 1.24 寸圆屏，按 240×240 渲染，窗口放大 2 倍
    python3 eyes_sim.py --size 128       # 模拟 128×128 的小圆屏
    python3 eyes_sim.py --scale 3        # 窗口再放大一点
    python3 eyes_sim.py --showcase       # 启动就轮播全部眼睛设计

按键
----
  轻敲：  F           （也可以用 ← 方向键 / 空格）
  重击：  K           （也可以用 → 方向键 / 回车）
                     注意：**不是 J**，J 要留给 9 宫格中间那一格
  9 宫格：T Y U / G H J / B N M   （左边一列=左，右边一列=右，上中下三行对应高低）
  上滑彩蛋：1 秒内先按 B/N/M，再按 G/H/J，再按 T/Y/U
                     手痒想直接看：按 X

  被摸摸：鼠标在画面上按一下      → 弯眼笑；按住 Shift 点 = 害羞（脸红 + 视线躲开）
  跟着看：鼠标在画面上移动        → 眼睛跟着鼠标转
  轮播：  Tab                      → 依次展示全部眼睛设计（再按一次停止）
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

# 轻敲 / 重击。
#
# 注意：重击**不能用 J** —— J 是 9 宫格的中间格，而 on_press 里重击分支
# 排在宫格分支前面，J 会被重击吃掉，于是 B→J→T 这种上滑永远触发不了。
# 改用 K（紧挨着 J，手感一样）。
LIGHT_KEYS = {"f", "space", "Left"}
HEAVY_KEYS = {"k", "Return", "Right"}
GRID_KEYS = set("tyughjbnm")

#: 手痒想直接看星星眼时用（调试/演示）
HYPE_KEYS = {"x"}


class EyeSimApp:
    def __init__(self, root: tk.Tk, screen: int, scale: int,
                 showcase: bool = False):
        self.root = root
        self.screen = screen
        self.px_size = screen * scale
        self.sm = EyeStateMachine()
        self.showcase_on_start = showcase

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
        # 状态栏既显示按键反馈，也显示轮播进度（见 tick）
        self.status.pack(fill="x")

        # corner_color 让画布四角涂成窗口底色 → 看起来是一块圆屏
        self.renderer = TkRenderer(self.canvas, self.px_size,
                                   corner_color="#0a0a0f")
        self._draw_ring()

        # 键盘绑定
        root.bind("<KeyPress>", self.on_press)
        root.bind("<KeyRelease>", self.on_release)
        root.bind("<Escape>", lambda e: root.destroy())
        root.bind("<Tab>", self.on_showcase_key)
        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<Button-1>", self.on_click)
        root.focus_force()

        if showcase:
            self.sm.start_showcase(dur=3.2)

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
        elif key in HYPE_KEYS:
            self.sm.trigger_hype()
            self._flash("★ 上滑彩蛋（直接触发）")
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
    # 鼠标：摸头 / 视线跟随
    # ------------------------------------------------------------------

    def _to_norm(self, event):
        """鼠标像素坐标 → 归一化 -1..1（原点在圆心）。"""
        half = self.px_size / 2.0
        return (event.x - half) / half, (event.y - half) / half

    def on_motion(self, event) -> None:
        x, y = self._to_norm(event)
        self.sm.set_look_at(x, y, strength=0.9)

    def on_click(self, event) -> None:
        shy = bool(event.state & 0x0001)   # Shift
        self.sm.poke(1.0, shy=shy)
        self._flash("摸摸头 → 弯眼笑" + ("（害羞）" if shy else ""))

    def on_showcase_key(self, event) -> str:
        if self.sm.showcase_on:
            self.sm.stop_showcase()
            self._flash("停止轮播")
        else:
            self.sm.start_showcase(dur=3.2)
            self._flash("轮播眼睛设计（Tab 停止）")
        return "break"   # 别让 Tab 跑去切控件

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def tick(self) -> None:
        now = time.perf_counter()
        dt = now - self.last_t
        self.last_t = now

        self.sm.update(dt)
        frame = self.sm.build_frame()
        self.renderer.render(frame)

        if self.sm.showcase_on:
            i = int(self.sm.showcase)
            keys = self.sm.showcase_keys
            if 0 <= i < len(keys):
                d = ec.EYE_DESIGNS.get(keys[i])
                self.status.configure(fg="#c8d2e8")
                self._flash(f"轮播 {i + 1}/{len(keys)}  {d.name if d else keys[i]}")

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


def _check_tk() -> None:
    """
    macOS 上系统自带的 Tk 8.5.9 画不出 Canvas —— 窗口会是一片空白。

    注意：**这里绝对不能创建 Tk() 再 destroy() 去取版本号。**
    那样做之后再开真正的窗口会直接段错误（exit 139）——
    Tk 进程级状态被 destroy 弄坏了，第二个窗口就建不起来。
    这个坑很隐蔽：单独跑版本探测没事，放在 main() 里就必崩。

    所以只读常量 / 环境变量，完全不碰 Tk 运行时。
    """
    try:
        import tkinter
    except Exception:
        return
    ver = getattr(tkinter, "TkVersion", None)
    try:
        major = int(float(ver))
    except (TypeError, ValueError):
        return
    if major < 9:
        sys.stderr.write(
            f"\n  ⚠️  当前 Tk 版本是 {ver}，在 macOS 上可能画不出画面（窗口一片空白）。\n"
            f"     这不是动画代码的问题 —— Canvas 里的图元都是对的，只是 Tk 8.5 不渲染。\n\n"
            f"     用 Tk 9 的 Python 跑就行：\n"
            f"       ./.venv/bin/python eyes_sim.py --showcase\n\n"
        )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="圆屏眼睛原型模拟器")
    ap.add_argument("--size", type=int, default=ec.SCREEN,
                    help="圆屏逻辑分辨率，默认 240")
    ap.add_argument("--scale", type=int, default=2,
                    help="窗口放大倍数，默认 2")
    ap.add_argument("--showcase", action="store_true",
                    help="启动即轮播全部眼睛设计")
    args = ap.parse_args(argv)

    _check_tk()
    ec.SCREEN = args.size

    root = tk.Tk()
    EyeSimApp(root, args.size, max(1, args.scale), showcase=args.showcase)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
