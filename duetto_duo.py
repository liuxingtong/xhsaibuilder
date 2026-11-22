"""
duetto_duo.py — 一台电脑模拟「双人」效果

为什么能在一台机器上测：
  EyeStateMachine 是纯状态对象，没有任何全局状态。
  所以 new 两个实例 = 两个"人"，各自独立跑自己的动画。
  键盘只要把按键分给不同的实例即可。

三种键位模式
-----------
1. SPLIT（默认，单人双手）—— 最接近真实双人
     左手区 = 设备 A（你）      : 轻敲 A / 重击 S / 9宫格 QWE-ASD-ZXC
     右手区 = 设备 B（对方）    : 轻敲 K / 重击 L / 9宫格 UIO-JKL-M,.
   两只手同时敲，就相当于两个人同时敲。

2. AI（单键触发）—— 演示用，不用两只手
     你只操作 A 机；B 机由脚本按概率自动跟随，
     并有一个"协同度"滑杆控制它多容易跟你同步。
     适合路演：一个人就能演示"对方也在反应"。

3. REPLAY（录播）—— 最稳
     把 A 机的一段操作录下来，再用它回放驱动 B 机，
     等价于《说明》里的"播放模式"，用来排查是同步逻辑的问题还是人的问题。

视觉
------
左右两个圆屏并排，各自带一圈灯带。
中间显示：
  · 共鸣状态（是否触发 S1）
  · 两侧灯的"情绪轨"实时条形图
共鸣时两台灯带融为一体 + 两屏对视（左机表情右偏、右机左偏）——
这就是《说明》里 4.2 的"对视彩蛋"，这里先用屏幕侧的镜像偏移模拟。

运行：
    python3 duetto_duo.py                 # SPLIT 模式
    python3 duetto_duo.py --mode ai       # AI 跟随
    python3 duetto_duo.py --mode replay   # 录播
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
import tkinter as tk

import eyes_core as ec
from eyes_render import TkRenderer, hex_color as hexc
from eyes_core import EyeStateMachine

# ---------------------------------------------------------------------------
# 键位
# ---------------------------------------------------------------------------

# A 机：左手
A_KEYS = {
    "light": {"f"},
    "heavy": {"s"},
    "grid": dict(zip("qweasdzxc", "tyughjbnm")),   # Q→T, W→Y, E→U, A→G, S→H ...
}

# B 机：右手
B_KEYS = {
    "light": {"k"},
    "heavy": {"l"},
    "grid": dict(zip("uiojklm,.", "tyughjbnm")),
}

# 注意：A 的重击是 S，而 S 同时是 A 的九宫格中格。
# 为了不冲突，把 A 的重击改到 F 的右边 G？——不行，G 也是格子。
# 简化：重击用空格（A）和回车（B）。
A_KEYS["heavy"] = {"space"}
B_KEYS["heavy"] = {"Return"}
# 中格用单独键
A_KEYS["grid"]["x"] = "h"
B_KEYS["grid"][","] = "h"


def key_to_action(keys: dict, k: str):
    """把一个按键翻译成 (action, payload)。"""
    if k in keys["light"]:
        return ("tap", ec.LIGHT)
    if k in keys["heavy"]:
        return ("tap", ec.HEAVY)
    if k in keys["grid"]:
        return ("grid", keys["grid"][k])
    return (None, None)


# ---------------------------------------------------------------------------
# 灯带颜色
# ---------------------------------------------------------------------------

LIGHT_IDLE = (255, 196, 107)     # 暖白（待机时当"面盘"）
LIGHT_TAP = (255, 214, 140)
LIGHT_TAP_HEAVY = (255, 170, 80)
LIGHT_MINE = (255, 184, 107)     # 我这边：暖
LIGHT_THEIRS = (111, 211, 255)   # 对方：冷
LIGHT_BOND = (255, 236, 170)


def _dim(c, k):
    return (int(c[0] * k), int(c[1] * k), int(c[2] * k))


def light_for(sm: EyeStateMachine, side: int = 0):
    """
    返回 (左半, 右半) 两个颜色。

    side: 本机相对邻机的位置（-1=邻机在左 / +1=邻机在右）。
          看向邻机方向的那一侧要点亮成"对方色"，制造双人视界。
    """
    # 共振优先
    if sm.bond > 0.05:
        return (LIGHT_BOND, LIGHT_BOND)

    dip, _, _, _ = sm._tap_channels()
    if sm.tap_ev is not None and dip > 0.02:
        c = LIGHT_TAP_HEAVY if sm.last_tap_kind == ec.HEAVY else LIGHT_TAP
        return (c, c)

    # 看的方向决定哪半边亮
    col = sm.pos_col
    if col < -0.15:
        return (LIGHT_MINE, _dim(LIGHT_THEIRS, 0.30))
    if col > 0.15:
        return (_dim(LIGHT_MINE, 0.30), LIGHT_THEIRS)
    if side < 0:
        # 邻机在左 → 左半偏冷（对方色）
        return (_dim(LIGHT_THEIRS, 0.55), LIGHT_MINE)
    if side > 0:
        return (LIGHT_MINE, _dim(LIGHT_THEIRS, 0.55))
    return (LIGHT_IDLE, LIGHT_IDLE)


# ---------------------------------------------------------------------------
# 共鸣判定（对应《说明》4.2 的 Python 裁判逻辑）
# ---------------------------------------------------------------------------


class ResonanceJudge:
    """
    过去 1.5 秒内，两边都出现过"激烈情绪"事件 → 触发共鸣。

    激烈情绪 = TAP 或 U（上滑爆发），按《说明》4.2 的定义。
    这里用"事件时间戳表"判，而不是"当前状态"，
    因为敲击是瞬时的，两帧之间可能就错过了。
    """

    def __init__(self, window: float = 1.5, hold: float = 3.5,
                 cooldown: float = 6.0):
        self.window = window
        self.hold = hold
        self.cooldown = cooldown
        self.events: dict[str, list[tuple[float, str]]] = {"A": [], "B": []}
        self.active_until = -99.0
        self.last_end = -99.0
        self.hits = 0

    def feed(self, side: str, t: float, kind: str) -> None:
        """kind: 'tap' / 'hype'"""
        self.events[side].append((t, kind))
        cutoff = t - self.window
        self.events[side] = [e for e in self.events[side] if e[0] >= cutoff]

    def update(self, t: float) -> bool:
        """
        返回当前是否处于共鸣。会自己处理持续时间与冷却。
        """
        # 已在共鸣中
        if t < self.active_until:
            return True

        # 冷却期内不再触发
        if t - self.last_end < self.cooldown:
            return False

        cutoff = t - self.window
        a = [e for e in self.events["A"] if e[0] >= cutoff]
        b = [e for e in self.events["B"] if e[0] >= cutoff]
        self.events = {"A": a, "B": b}

        if a and b:
            self.active_until = t + self.hold
            self.hits += 1
            return True
        return False

    def note_end(self, t: float, active: bool, was_active: bool) -> None:
        if was_active and not active:
            self.last_end = t


# ---------------------------------------------------------------------------
# AI 跟随
# ---------------------------------------------------------------------------


class AiFollower:
    """
    机器 B。看到 A 的动作后，延迟一小段再跟一手。
    synergy 越高，跟得越准；越低则经常走神。
    """

    def __init__(self, sm: EyeStateMachine, synergy: float = 0.65):
        self.sm = sm
        self.synergy = synergy
        self.pending: list[tuple[float, str, object]] = []

    def observe(self, t: float, action: str, payload) -> None:
        if random.random() > self.synergy:
            return                       # 走神了，没跟上
        delay = random.uniform(0.12, 0.42)
        self.pending.append((t + delay, action, payload))

    def update(self, t: float) -> None:
        keep = []
        for due, action, payload in self.pending:
            if t >= due:
                if action == "tap":
                    self.sm.tap(payload)
                elif action == "grid":
                    self.sm.press_grid(payload)
                    self.sm.release_grid()
            else:
                keep.append((due, action, payload))
        self.pending = keep


# ---------------------------------------------------------------------------
# 界面
# ---------------------------------------------------------------------------

SCREEN = 240
SCALE = 2
DEV_PX = SCREEN * SCALE
#: 窗口统一底色（画布四角遮罩也用它，才能看不出方块）
BG = "#0a0908"

# 灯带环的留白：让环贴住圆屏外沿，像一圈实体灯带。
#
# 老版本这里是 0.075，而且每台设备用一个**嵌套画布控件**画眼睛。
# 嵌套控件永远盖在父画布上方，方画布的四个角比环半径还大 31px，
# 于是环被切成几段 —— 看起来就是"两个黑色方框盖住了圆环"。
# 现在眼睛和灯带画在同一块画布上（TkRenderer 的 origin/tag），
# 遮挡问题从结构上消失了，间距只按美观定。
RING_PAD = int(DEV_PX * 0.13)
TOTAL_DEV = DEV_PX + RING_PAD * 2
GAP = 56


class DuoApp:
    def __init__(self, root: tk.Tk, mode: str, synergy: float):
        self.root = root
        self.mode = mode
        self.sm_a = EyeStateMachine()
        self.sm_b = EyeStateMachine()
        self.judge = ResonanceJudge()
        self.ai = AiFollower(self.sm_b, synergy)

        self.pressed: set[str] = set()
        self.t0 = time.perf_counter()
        self.was_resonant = False

        width = TOTAL_DEV * 2 + GAP + 40
        height = TOTAL_DEV + 150
        root.title("Duetto · 单机双人联调")
        self.bg = BG
        root.configure(bg=BG)
        root.resizable(False, False)

        self.canvas = tk.Canvas(root, width=width, height=height,
                                bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack()

        self.pos_a = self._make_device(20, 20, "A · 你", "#ffb86b")
        self.pos_b = self._make_device(20 + TOTAL_DEV + GAP, 20, "B · 对方", "#6fd3ff")

        # 底部状态区
        self.status = tk.Label(root, text="", font=("Helvetica", 12),
                               fg="#9b8d7d", bg=BG, anchor="w",
                               justify="left")
        self.status.pack(fill="x", padx=20, pady=(4, 12))

        self.renderer_a = TkRenderer(self.canvas, DEV_PX, corner_color=BG,
                                     origin=self.pos_a, tag="eyeA", bg_tag="bgA")
        self.renderer_b = TkRenderer(self.canvas, DEV_PX, corner_color=BG,
                                     origin=self.pos_b, tag="eyeB", bg_tag="bgB")

        root.bind("<KeyPress>", self.on_press)
        root.bind("<KeyRelease>", self.on_release)
        root.bind("<Escape>", lambda e: root.destroy())
        root.focus_force()

        self.last_t = time.perf_counter()
        self._ring_cache = {"A": None, "B": None}
        self.tick()

    def _make_device(self, x: int, y: int, label: str, color: str):
        """
        只画标签，返回这台设备的**屏幕左上角**坐标。

        屏幕本身不建单独的画布 —— 眼睛和灯带都画在同一个 Canvas 上，
        否则嵌套的画布控件会把灯带盖掉（方形角压在圆环上）。
        """
        total = TOTAL_DEV
        self.canvas.create_text(x + total // 2, y - 12, text=label,
                                fill=color, font=("Helvetica", 11, "bold"))
        return (x + (total - DEV_PX) / 2.0, y + (total - DEV_PX) / 2.0)

    # -- 输入 ------------------------------------------------------------

    def on_press(self, event) -> None:
        k = event.keysym
        if k in self.pressed:
            return
        self.pressed.add(k)
        t = time.perf_counter() - self.t0

        if self.mode == "ai":
            # 只操作 A，B 由 AI 跟
            act, payload = key_to_action(A_KEYS, k)
            if act:
                self._apply(self.sm_a, act, payload)
                self.ai.observe(t, act, payload)
                self._report(side="A", act=act, payload=payload, t=t)
            return

        act, payload = key_to_action(A_KEYS, k)
        if act:
            self._apply(self.sm_a, act, payload)
            self._report(side="A", act=act, payload=payload, t=t)
            return

        act, payload = key_to_action(B_KEYS, k)
        if act:
            self._apply(self.sm_b, act, payload)
            self._report(side="B", act=act, payload=payload, t=t)

    def on_release(self, event) -> None:
        k = event.keysym
        self.pressed.discard(k)
        # 该键属于哪台设备 → 那台松手回中
        if self.mode != "ai" and k in B_KEYS["grid"]:
            self.sm_b.release_grid()
        elif k in A_KEYS["grid"]:
            self.sm_a.release_grid()

    def _apply(self, sm: EyeStateMachine, action: str, payload) -> None:
        if action == "tap":
            sm.tap(payload)
        elif action == "grid":
            sm.press_grid(payload)

    def _report(self, side: str, act: str, payload, t: float) -> None:
        """把事件喂给共鸣判定。"""
        if act == "tap":
            self.judge.feed(side, t, "tap")
        elif act == "grid":
            col, row = ec.KEY_GRID[payload]
            if row == -1:            # 上排 = 上滑爆发（简化判定）
                self.judge.feed(side, t, "hype")
        sm = self.sm_a if side == "A" else self.sm_b
        if sm.hype > 0.9:
            self.judge.feed(side, t, "hype")

    # -- 渲染 ------------------------------------------------------------

    def tick(self) -> None:
        now = time.perf_counter()
        dt = min(now - self.last_t, 0.05)
        self.last_t = now
        t = now - self.t0

        if self.mode == "ai":
            self.ai.update(t)

        self.sm_a.update(dt)
        self.sm_b.update(dt)

        resonant = self.judge.update(t)

        # 共振刚触发：让两台设备各自进入 bond 状态
        if resonant and not self.was_resonant:
            self.sm_a.trigger_bond(side=+1)   # A 在左，看向右邻机
            self.sm_b.trigger_bond(side=-1)   # B 在右，看向左邻机

        # 共鸣时两台灯带融为一体
        if resonant:
            la = ((255, 236, 170), (255, 236, 170))
            lb = la
        else:
            la = light_for(self.sm_a, side=+1)
            lb = light_for(self.sm_b, side=-1)

        self._render_device("A", self.sm_a, la, resonant)
        self._render_device("B", self.sm_b, lb, resonant)

        self._update_status(resonant, t)
        self.judge.note_end(t, resonant, self.was_resonant)
        self.was_resonant = resonant

        self.root.after(8, self.tick)

    def _render_device(self, tag: str, sm: EyeStateMachine, colors,
                       resonant: bool) -> None:
        renderer = self.renderer_a if tag == "A" else self.renderer_b
        renderer.render(sm.build_frame())
        self._draw_ring(self.canvas, tag, colors)

    def _draw_ring(self, canvas, tag: str, colors) -> None:
        """灯带左右分区。colors = (left, right)。"""
        key = (colors[0], colors[1])
        if self._ring_cache.get(tag) == key:
            return
        self._ring_cache[tag] = key

        canvas.delete(f"ring{tag}")
        n = 44
        inset = self._ring_inset(tag)
        for i in range(n):
            a0 = 360.0 * i / n
            a1 = 360.0 * (i + 1) / n
            mid = (a0 + a1) / 2.0
            col = colors[1] if 90 <= mid < 270 else colors[0]
            canvas.create_arc(inset[0], inset[1], inset[2], inset[3],
                              start=270 - a1, extent=(a1 - a0) - 1.2,
                              style="arc", outline=hexc(col),
                              width=max(2, int(RING_PAD * 0.5)),
                              tags=f"ring{tag}")

    def _ring_inset(self, tag: str):
        x = 20 if tag == "A" else 20 + TOTAL_DEV + GAP
        y = 20
        k = RING_PAD * 0.30
        return (x + k, y + k, x + TOTAL_DEV - k, y + TOTAL_DEV - k)

    def _update_status(self, resonant: bool, t: float) -> None:
        a = "A:" + ("星河共振 ★" if resonant else "……")
        tail = "（共鸣中，灯带已合并）" if resonant else ""
        self.status.config(
            text=f"{a}   {tail}\n"
                 f"{self._hint()}")

    def _hint(self) -> str:
        if self.mode == "ai":
            return "AI 模式：你操作 A 机，B 机自动跟随。Esc 退出。"
        return ("A 机(左) 轻敲 F / 重击 空格 / 格子 QWEA SZXC   ·   "
                "B 机(右) 轻敲 K / 重击 回车 / 格子 UIOMJKL,.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["split", "ai", "replay"], default="split")
    ap.add_argument("--synergy", type=float, default=0.65)
    args = ap.parse_args(argv)

    ec.SCREEN = SCREEN
    root = tk.Tk()
    DuoApp(root, args.mode, args.synergy)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
