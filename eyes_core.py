"""
eyes_core.py — 圆屏「眼睛」动画内核（纯标准库，无第三方依赖）

设计原则
--------
1. 只有状态机 + 浮点数参数。不碰任何绘图库、不碰任何系统时钟。
   → 时间由外部 `update(dt)` 喂进来，所以 PC 版和 Arduino 版可以跑同一份逻辑。
2. 对外只暴露 `EyeStateMachine.build_frame()` 返回的 DrawList，
   DrawList 里只有三种图元：ellipse(椭圆) / polygon(多边形) / arc(弧)，
   它们分别能直译成 Tkinter、Pillow、Arduino 的 GFX 画法。
3. 所有坐标都是「归一化屏幕坐标」，范围 -1..1，原点在屏幕中心，y 轴向下，
   圆屏边缘就是单位圆。这样换 128/240/454 分辨率只改一个 CORNER 常量。

三种交互
--------
① 敲击（轻敲 / 重击键）→ 点头反馈：眼睛眯一下 + 整体上下位移 + 瞳孔缩放
② 9 宫格键（左中右 × 上中下）→ 眼睛水平/垂直平移，带平滑缓动；极左/极右时眼睛形状变化
③ 1 秒内 BNM→GHJ→TYU 三段快速上滑 → 触发「特别嗨」表情
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# 屏幕常量
# ---------------------------------------------------------------------------

#: 圆屏分辨率（像素）。改这一行就能适配 128×128 / 240×240 / 454×454。
SCREEN = 240


# ---------------------------------------------------------------------------
# 缓动函数
# ---------------------------------------------------------------------------


def clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def ease_out_cubic(t: float) -> float:
    t = clamp(t, 0.0, 1.0)
    return 1.0 - (1.0 - t) ** 3


def ease_out_back(t: float, s: float = 1.9) -> float:
    """带一点点过冲的回弹，眼睛移动到位时会有"啵"的手感。"""
    t = clamp(t, 0.0, 1.0)
    t -= 1.0
    return t * t * ((s + 1.0) * t + s) + 1.0


def ease_in_out_sine(t: float) -> float:
    t = clamp(t, 0.0, 1.0)
    return -(math.cos(math.pi * t) - 1.0) / 2.0


def spring_step(current: float, target: float, vel: float, dt: float,
                stiffness: float, damping: float) -> Tuple[float, float]:
    """临界阻尼弹簧的单步积分，用来做「平滑过去」的跟随。"""
    accel = (target - current) * stiffness - vel * damping
    vel += accel * dt
    current += vel * dt
    return current, vel


# ---------------------------------------------------------------------------
# 绘制指令（平台无关）
# ---------------------------------------------------------------------------


@dataclass
class Ellipse:
    """归一化椭圆。cx/cy 中心，rx/ry 半轴。"""

    cx: float
    cy: float
    rx: float
    ry: float


@dataclass
class Polygon:
    """归一化多边形，points 为 (x, y) 列表。用于眯眼时的眼皮覆盖、心形等。"""

    points: List[Tuple[float, float]]


@dataclass
class Arc:
    """
    归一化圆弧/扇形（用于高光、汗滴、爆发光芒）。
    start/end 为角度（度，0 度指向 +x，顺时针递增，和 Tkinter/GFX 一致）。
    """

    cx: float
    cy: float
    rx: float
    ry: float
    start: float
    end: float
    width: float


def ellipse_polygon(cx: float, cy: float, rx: float, ry: float,
                    n: int = 28, squash_bottom: float = 0.0) -> List[Tuple[float, float]]:
    """把椭圆采样成多边形；squash_bottom>0 时下半部分压扁（做"被压到的眼皮"）。"""
    pts: List[Tuple[float, float]] = []
    for i in range(n):
        a = 2.0 * math.pi * i / n
        x = cx + rx * math.cos(a)
        y = cy + ry * math.sin(a)
        if squash_bottom > 0.0 and y > cy:
            y = cy + (y - cy) * (1.0 - squash_bottom)
        pts.append((x, y))
    return pts


@dataclass
class DrawList:
    """一帧画面。按顺序绘制，后画的盖住先画的。"""

    items: List[object] = field(default_factory=list)

    def add(self, item: object) -> None:
        self.items.append(item)


# ---------------------------------------------------------------------------
# 敲击事件
# ---------------------------------------------------------------------------

LIGHT = "light"
HEAVY = "heavy"

#: 轻敲：快速点头 + 眯眼
LIGHT_TAP = dict(
    dur=0.38,        # 总时长
    dip=0.26,        # 向下位移幅度（归一化，正数=向下）
    squash=0.46,     # 最大压扁比例
    pop=1.10,        # 瞳孔最大缩放
    bounce=0.42,     # 回弹过冲比例（向上弹多高）
    pupil_kick=0.0,  # 瞳孔额外位移
)

#: 重击：更嗨 —— 更长、更深、带晃动和张大眼睛
HEAVY_TAP = dict(
    dur=0.80,
    dip=0.40,
    squash=0.56,
    pop=1.60,
    bounce=0.75,
    pupil_kick=0.06,
)


@dataclass
class TapEvent:
    t: float = 0.0
    dur: float = 0.0
    dip: float = 0.0
    squash: float = 0.0
    pop: float = 1.0
    bounce: float = 0.0
    pupil_kick: float = 0.0

    @property
    def active(self) -> bool:
        return self.t < self.dur


def make_tap(kind: str) -> TapEvent:
    cfg = HEAVY_TAP if kind == HEAVY else LIGHT_TAP
    return TapEvent(t=0.0, **cfg)


# ---------------------------------------------------------------------------
# 9 宫格
# ---------------------------------------------------------------------------

#: 9 宫格键 → (列, 行)，列 -1=左 0=中 1=右，行 -1=上 0=中 1=下
KEY_GRID = {
    "t": (-1, -1), "y": (0, -1), "u": (1, -1),
    "g": (-1, 0),  "h": (0, 0),  "j": (1, 0),
    "b": (-1, 1),  "n": (0, 1),  "m": (1, 1),
}

KEY_ROW_NAME = {-1: "上", 0: "中", 1: "下"}
KEY_COL_NAME = {-1: "左", 0: "中", 1: "右"}

#: 左中右三行各自的「形状个性」
#: stretch 越大越横向拉伸（极左/极右看起来像在"推"出去）
COLUMN_STYLE = {
    -1: dict(stretch=1.14, tilt=-13.0, openness=1.04),
    0: dict(stretch=1.00, tilt=0.0, openness=1.00),
    1: dict(stretch=1.14, tilt=13.0, openness=1.04),
}

ROW_STYLE = {
    -1: dict(openness=1.10),  # 上排：眼睛睁大（看起来在往上看）
    0: dict(openness=1.00),
    1: dict(openness=0.90),   # 下排：眼睛微眯（看起来在往下看）
}

#: 行位置对整体垂直偏移的系数。row_f 约定：-1=上，+1=下，y 轴向下，
#: 所以整体位移 = row_f * ROW_SHIFT（正数向下）。
ROW_SHIFT = 0.20

#: 左右位置对整对眼睛水平平移的系数。
#: 经验值：小于 0.15 才像"整体move"，超过就会变成两眼互相靠拢/分开。
EYE_PAN_X = 0.30


# ---------------------------------------------------------------------------
# 上滑彩蛋检测
# ---------------------------------------------------------------------------


@dataclass
class SwipeSample:
    t: float
    row: int
    col: int


@dataclass
class SwipeDetector:
    """
    检测「很短的时间内，先按在下面一排，再中间一排，再上面一排」的向上滑。
    检查顺序：每次按键进来，从最近往前找，看能否还原 row 递减的三段序列。
    """

    window: float = 1.0     # 整段上滑要发生在多少秒内
    max_gap: float = 0.55   # 相邻两段的最大间隔
    history: List[SwipeSample] = field(default_factory=list)

    def reset(self) -> None:
        self.history.clear()

    def feed(self, t: float, row: int, col: int) -> bool:
        """
        喂入一次按键。返回 True 表示完成了一次「下→中→上」的快速上滑。

        判定：在 history 里按时间顺序找一段连续子序列，它的 row 依次出现
        1（下）→ 0（中）→ -1（上），且整段跨度 <= window。
        """
        self.history.append(SwipeSample(t, row, col))
        cutoff = t - self.window
        self.history = [s for s in self.history if s.t >= cutoff]

        # 按时间正序扫，维护当前"进行到第几段"
        stage = 0          # 0=还没开始 1=已按下排 2=已按中排
        stage_t = 0.0
        for sample in self.history:
            if stage == 0:
                if sample.row == 1:
                    stage, stage_t = 1, sample.t
            elif stage == 1:
                if sample.row == 0 and (sample.t - stage_t) <= self.max_gap:
                    stage = 2
                elif sample.row == 1:
                    stage_t = sample.t          # 重新从下排开始
            elif stage == 2:
                if sample.row == -1 and (sample.t - stage_t) <= self.window:
                    self.history.clear()
                    return True
                if sample.row == 1:
                    stage, stage_t = 1, sample.t
                elif sample.row == 0:
                    stage_t = sample.t
        return False


# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------


@dataclass
class EyeStateMachine:
    """所有动画状态。外部每帧调用 update(dt)，然后 build_frame()。"""

    # 位置（临界阻尼弹簧跟随）
    target_col: int = 0
    target_row: int = 0
    pos_col: float = 0.0
    vel_col: float = 0.0
    pos_row: float = 0.0
    vel_row: float = 0.0

    # 敲击
    tap_ev: Optional[TapEvent] = None
    tap_count: int = 0
    last_tap_kind: str = LIGHT

    # 呼吸 / 待机
    clock: float = 0.0
    blink_in: float = 3.0
    blink_t: float = -1.0
    blink_len: float = 0.16

    # 上滑彩蛋
    hype: float = 0.0        # 0..1 强度
    hype_t: float = 0.0      # 已经持续时长
    hype_len: float = 2.6
    sparkle_seed: int = 1

    # 待机时的随机微瞟（让它活着感强一点）
    idle_look_x: float = 0.0
    idle_look_y: float = 0.0
    idle_next: float = 2.5

    # 双人共振（由外部判定后写入，不是自己触发的）
    # 这是"两个人"层面的状态，单机看不到，所以单独一个通道，
    # 不混进 hype（hype 是自己的上滑彩蛋）。
    bond: float = 0.0          # 0..1 共振强度
    bond_t: float = 0.0
    bond_side: int = 0         # -1 看向左邻机 / +1 看向右邻机 / 0 居中
    bond_len: float = 3.5

    detector: SwipeDetector = field(default_factory=SwipeDetector)

    # ---- 输入 -------------------------------------------------------------

    def tap(self, kind: str) -> None:
        """轻敲/重击。重击会打断并覆盖掉正在播放的轻敲。"""
        if kind == HEAVY or self.tap_ev is None or not self.tap_ev.active:
            self.tap_ev = make_tap(kind)
            self.tap_count += 1
            self.last_tap_kind = kind
        else:
            # 连续快速轻敲：叠加一点力度，形成渐进的节奏感
            self.tap_count += 1
            boost = min(0.35, 0.08 * self.tap_count)
            self.tap_ev.t = 0.0
            self.tap_ev.dip *= 1.0 + boost
            self.tap_ev.squash = min(0.9, self.tap_ev.squash * (1.0 + boost))

    def press_grid(self, key: str) -> Optional[str]:
        """9 宫格按键。返回 'hype' 表示触发了上滑彩蛋。"""
        key = key.lower()
        if key not in KEY_GRID:
            return None
        col, row = KEY_GRID[key]
        self.target_col, self.target_row = col, row

        if self.detector.feed(self.clock, row, col):
            self.trigger_hype()
            return "hype"

        # 每按一下格子，也给一点点"点一下"的反馈，手感不至于太干
        if self.tap_ev is None or not self.tap_ev.active:
            t = make_tap(LIGHT)
            t.dip *= 0.22
            t.squash *= 0.22
            t.pop = 1.0 + (t.pop - 1.0) * 0.4
            t.dur *= 0.70
            self.tap_ev = t
        return None

    def release_grid(self) -> None:
        """松开所有 9 宫格键 → 回到中间。"""
        self.target_col, self.target_row = 0, 0

    def trigger_bond(self, side: int = 0) -> None:
        """
        触发双人共振。

        side: 邻机在屏幕的哪一侧（-1 左 / +1 右），决定眼睛往哪边偏，
              形成"互相看着对方"的效果。
        """
        self.bond = 1.0
        self.bond_t = 0.0
        self.bond_side = side

    def trigger_hype(self) -> None:
        self.hype = 1.0
        self.hype_t = 0.0
        self.sparkle_seed = random.randint(1, 9999)

    # ---- 更新 -------------------------------------------------------------

    def update(self, dt: float) -> None:
        dt = min(dt, 0.05)  # 防止窗口拖动/卡顿造成参数爆炸
        self.clock += dt

        # 位置弹簧：stiffness/damping 决定"平滑过去"的快慢
        self.pos_col, self.vel_col = spring_step(
            self.pos_col, float(self.target_col), self.vel_col, dt, 190.0, 24.0)
        self.pos_row, self.vel_row = spring_step(
            self.pos_row, float(self.target_row), self.vel_row, dt, 160.0, 22.0)

        # 敲击推进
        if self.tap_ev is not None:
            self.tap_ev.t += dt
            if not self.tap_ev.active:
                self.tap_ev = None
                self.tap_count = 0

        # 眨眼
        if self.blink_t >= 0.0:
            self.blink_t += dt
            if self.blink_t >= self.blink_len:
                self.blink_t = -1.0
                self.blink_in = random.uniform(2.4, 6.5)
        else:
            self.blink_in -= dt
            if self.blink_in <= 0.0:
                self.blink_t = 0.0

        # 彩蛋衰减
        if self.hype > 0.0:
            self.hype_t += dt
            if self.hype_t >= self.hype_len:
                self.hype = 0.0
                self.hype_t = 0.0

        # 共振衰减
        if self.bond > 0.0:
            self.bond_t += dt
            if self.bond_t >= self.bond_len:
                self.bond = 0.0
                self.bond_t = 0.0
                self.bond_side = 0

        # 待机微瞟
        self.idle_next -= dt
        if self.idle_next <= 0.0:
            self.idle_next = random.uniform(2.0, 5.0)
            self.idle_look_x = random.uniform(-0.045, 0.045)
            self.idle_look_y = random.uniform(-0.035, 0.035)

    # ---- 计算各通道 -------------------------------------------------------

    def _tap_channels(self) -> Tuple[float, float, float, float]:
        """返回 (垂直位移, 压扁比例, 瞳孔缩放, 水平抖动)。"""
        if self.tap_ev is None:
            return 0.0, 0.0, 1.0, 0.0
        tp = self.tap_ev
        p = clamp(tp.t / tp.dur, 0.0, 1.0)

        if self.last_tap_kind == HEAVY:
            # 重击：快速砸下 → 回弹过冲 → 稳定
            if p < 0.22:
                k = ease_out_cubic(p / 0.22)
                dip = tp.dip * k
                squash = tp.squash * k
            elif p < 0.55:
                k = ease_in_out_sine((p - 0.22) / 0.33)
                dip = lerp(tp.dip, -tp.dip * tp.bounce, k)
                squash = lerp(tp.squash, -tp.squash * 0.18, k)
            else:
                k = ease_out_cubic((p - 0.55) / 0.45)
                dip = lerp(-tp.dip * tp.bounce, 0.0, k)
                squash = lerp(-tp.squash * 0.18, 0.0, k)
            # 嗨起来的左右晃（幅度随进度衰减）
            wobble = math.sin(self.tap_ev.t * 30.0) * 0.058 * (1.0 - p) ** 1.5
            pop = 1.0 + (tp.pop - 1.0) * math.sin(math.pi * min(1.0, p * 1.25))
        else:
            # 轻敲：一个干净的下沉 + 小回弹
            if p < 0.35:
                k = ease_out_cubic(p / 0.35)
                dip = tp.dip * k
                squash = tp.squash * k
            else:
                k = ease_out_cubic((p - 0.35) / 0.65)
                dip = lerp(tp.dip, -tp.dip * tp.bounce, k)
                squash = lerp(tp.squash, 0.0, k)
            wobble = 0.0
            pop = 1.0 + (tp.pop - 1.0) * math.sin(math.pi * p)

        # 结束前把位移收回 0，避免最后跳变
        if p > 0.92:
            f = (p - 0.92) / 0.08
            dip *= (1.0 - f)
            squash *= (1.0 - f)

        return dip, squash, pop, wobble

    def _blink_amount(self) -> float:
        if self.blink_t < 0.0:
            return 0.0
        p = clamp(self.blink_t / self.blink_len, 0.0, 1.0)
        return math.sin(math.pi * p)

    # ---- 出帧 -------------------------------------------------------------

    def build_frame(self) -> DrawList:
        dl = DrawList()
        col_f = self.pos_col
        row_f = self.pos_row

        # 列/行的插值风格：在 -1/0/1 之间做分段平滑
        style = _blend_style(col_f, row_f)

        dip, squash, pop, wobble = self._tap_channels()
        blink = self._blink_amount()
        hype = self.hype
        bond = self.bond

        # 共振包络：前 0.25s 冲上去，之后缓慢回落
        if bond > 0.0:
            bp = clamp(self.bond_t / self.bond_len, 0.0, 1.0)
            bond_env = bond * (1.0 - ease_in_out_sine(clamp((bp - 0.25) / 0.75, 0, 1)) * 0.55)
        else:
            bond_env = 0.0

        # 上滑彩蛋对整体姿态的叠加
        hype_pulse = 1.0
        if hype > 0.0:
            # 衰减包络：起得快、尾段慢慢落
            env = hype * (1.0 - clamp(self.hype_t / self.hype_len, 0.0, 1.0) * 0.25)
            hype_pulse = 1.0 + 0.06 * math.sin(self.clock * 12.0) * env

        # 整体垂直偏移：敲击点头 + 行位置 + 彩蛋跳动
        base_y = row_f * ROW_SHIFT
        if hype > 0.0:
            base_y += math.sin(self.clock * 9.0) * 0.045 * hype
        if bond_env > 0.0:
            # 上浮 + 轻快的上下跳动
            base_y -= 0.055 * bond_env
            base_y += math.sin(self.clock * 7.0) * 0.022 * bond_env
        center_y = base_y + dip

        # 眼睛的水平中心：左右位置主要由「两眼中心整体平移」表达，
        # 平移量要比两眼间距小得多，否则看起来像两只眼睛在互相靠拢。
        eye_dx = col_f * EYE_PAN_X
        eye_dx += self.idle_look_x * (1.0 - abs(col_f))
        eye_dx += wobble
        # 共振：整对眼睛往邻机方向偏，形成"对望"
        eye_dx += self.bond_side * 0.085 * bond_env

        # 两眼间距与尺寸
        spread = 0.255 * style["spread"] * (1.0 + 0.06 * bond_env)   # 心形更宽，间距略放开
        eye_rx = 0.132 * style["stretch"] * (1.0 + 0.26 * hype + 0.55 * bond_env)
        eye_ry = 0.186 * style["openness"] * pop * (1.0 + 0.38 * hype + 0.55 * bond_env)

        # 眨眼 + 敲击压扁合并
        squash_total = clamp(squash + blink, 0.0, 0.98)
        eye_ry *= (1.0 - squash_total)
        eye_ry = max(eye_ry, 0.012)

        # 倾斜通道：双眼同一个倾斜方向（同一侧眉毛抬起），而不是左右镜像
        tilt = style["tilt"] * (0.4 + 0.6 * abs(col_f))
        tilt += math.sin(self.clock * 1.7) * 1.5 * abs(col_f)  # 轻微呼吸

        for side in (-1, 1):
            cx = eye_dx + side * spread
            cy = center_y
            # 极左/极右时，朝向移动方向的那只眼睛略微放大(近大远小)，另一只缩小
            near = 1.0 if side * col_f > 0 else 0.0
            size_k = 1.0 + (0.12 * near - 0.07 * (1 - near)) * abs(col_f)
            self._draw_eye(dl, cx, cy, eye_rx * size_k, eye_ry * size_k, side,
                           style, pop, squash_total, hype, tilt,
                           bond_env=bond_env, bond_side=self.bond_side)

        if bond_env > 0.01:
            _draw_bond_ring(dl, bond_env, self.clock, eye_dx, center_y)

        if hype > 0.0:
            _draw_sparkles(dl, hype, self.clock, self.sparkle_seed)

        return dl

    # 眼睛本体
    def _draw_eye(self, dl: DrawList, cx: float, cy: float, rx: float, ry: float,
                  side: int, style: dict, pop: float, squash: float,
                  hype: float, tilt: float, bond_env: float = 0.0,
                  bond_side: int = 0) -> None:
        # 共振：眼睛直接变成心形（最醒目、最不会看错的表情）
        if bond_env > 0.03:
            k = clamp(bond_env, 0.0, 1.0)
            # 从普通眼睛形状平滑过渡到心：用圆 ↔ 心的插值
            circle = ellipse_polygon(cx, cy, rx * 1.06, ry * 1.06, 40)
            # 心形比椭圆宽，横向收窄一点，避免两个心粘在一起
            heart = _heart_poly(cx, cy + ry * 0.02, rx * 0.98, ry * 1.02, 40)
            blended = [(lerp(circle[i][0], heart[i][0], k),
                        lerp(circle[i][1], heart[i][1], k))
                       for i in range(40)]
            dl.add(("bond_heart", blended))
            return   # 心形期间不再画瞳孔等细节

        # 1) 眼白（浅色底，让眼睛在黑屏上有体积）
        dl.add(Ellipse(cx, cy, rx * 1.06, ry * 1.06))

        # 2) 瞳孔：整体往看的方向偏一点，形状也跟着变
        pupil_scale = 0.46 * (1.0 + 0.18 * abs(self.pos_col)) * (1.0 + 0.22 * hype)
        prx = rx * pupil_scale * (1.0 - 0.28 * squash)
        pry = ry * pupil_scale * (1.0 + 0.08 * squash)
        # 看向左边 → 瞳孔左移；看向右边 → 瞳孔右移
        pshift = self.pos_col * 0.022
        pshift_y = self.pos_row * 0.016
        dl.add(("pupil", cx + pshift, cy + pshift_y, prx, pry))

        # 3) 高光点
        dl.add(("glint", cx + pshift + prx * 0.38, cy + pshift_y - pry * 0.42,
                prx * 0.30, pry * 0.30))

        # 4) 极左/极右时的斜眼皮：只盖住上方一条，营造成"斜眼看"
        if abs(tilt) > 0.8:
            self._draw_lid_tilt(dl, cx, cy, rx, ry, tilt)

    def _draw_lid_tilt(self, dl: DrawList, cx: float, cy: float,
                       rx: float, ry: float, tilt: float) -> None:
        """
        上眼皮：贴着椭圆上缘的一条覆盖带，再向上补满。

        约束（重要）：
          - 只在眼睛自身的水平范围内 (|x-cx| <= rx) 覆盖，
            超出部分不画，否则会在眼睛两角糊出一块。
          - 下沿最深 = 上缘再往下 depth*ry，depth <= 0.13，
            所以眼睛主体始终完整。
          - 倾斜让左右两端压得不一样深，形成"斜眼看"。
        """
        ang = math.radians(tilt)
        depth = min(0.13, 0.07 + abs(tilt) / 90.0 * 0.06)

        n = 20
        bottom = []
        for i in range(n + 1):
            t = i / n
            xn = -1.0 + 2.0 * t            # -1..1，眼睛自身横向范围
            x = cx + rx * xn
            arch = math.sqrt(max(0.0, 1.0 - xn * xn))
            y_top = cy - ry * arch         # 椭圆上缘
            lean = math.tan(ang) * (t - 0.5) * ry * 1.4
            bottom.append((x, y_top + ry * depth + lean))

        top = cy - ry * 2.0
        pts = bottom + [(cx + rx, top), (cx - rx, top)]
        dl.add(("lid", pts))


def _blend_style(col_f: float, row_f: float) -> dict:
    """把 -1/0/1 的连续位置映射成形状参数。"""
    def one(col: int) -> dict:
        return COLUMN_STYLE[col]

    if col_f <= 0.0:
        a, b, t = one(-1), one(0), clamp(col_f + 1.0, 0.0, 1.0)
    else:
        a, b, t = one(0), one(1), clamp(col_f, 0.0, 1.0)
    t = ease_in_out_sine(t)

    out = {k: lerp(a[k], b[k], t) for k in a}
    # 两眼间距基本恒定（否则平移会被间距变化抵消，看起来像抖动）
    out["spread"] = 1.0

    if row_f <= 0.0:
        ra, rb, rt = ROW_STYLE[-1], ROW_STYLE[0], clamp(row_f + 1.0, 0.0, 1.0)
    else:
        ra, rb, rt = ROW_STYLE[0], ROW_STYLE[1], clamp(row_f, 0.0, 1.0)
    rt = ease_in_out_sine(rt)
    out["openness"] *= lerp(ra["openness"], rb["openness"], rt)
    return out


def _heart_poly(cx: float, cy: float, rx: float, ry: float,
                n: int = 40) -> List[Tuple[float, float]]:
    """
    心形轮廓（共振时眼睛变成心）。

    用经典极坐标心形曲线：
        x = 16 sin^3(t)
        y = -(13 cos t - 5 cos2t - 2 cos3t - cos4t)
    归一化到约 ±1，再乘 rx/ry。y 轴向下，所以整体不翻转。
    """
    pts: List[Tuple[float, float]] = []
    for i in range(n):
        t = 2.0 * math.pi * i / n
        hx = 16.0 * math.sin(t) ** 3
        hy = -(13.0 * math.cos(t) - 5.0 * math.cos(2 * t)
               - 2.0 * math.cos(3 * t) - math.cos(4 * t))
        # 心形曲线的自然范围约 ±17 / ±17（上下略不对称），统一缩放到 ±1
        pts.append((cx + rx * (hx / 17.0), cy + ry * (hy / 17.0)))
    return pts


def _draw_bond_ring(dl: DrawList, amount: float, clock: float,
                    cx: float, cy: float) -> None:
    """
    共振光环：从眼睛中心向四周扩散的两道同心环。
    用多边形拼（Arduino 上可以用 fillCircle 叠画）。
    """
    for k in (0, 1):
        phase = (clock * 0.9 + k * 0.5) % 1.0
        r = 0.46 + 0.50 * phase
        alpha = (1.0 - phase) * amount
        if alpha < 0.05:
            continue
        ring_pts = []
        n = 40
        for i in range(n):
            a = 2.0 * math.pi * i / n
            ring_pts.append((cx + r * math.cos(a), cy + r * math.sin(a) * 0.92))
        dl.add(("bond_ring", ring_pts, alpha))


def _draw_sparkles(dl: DrawList, hype: float, clock: float, seed: int) -> None:
    """彩蛋期间的爆发光芒 / 小星星。"""
    rng = random.Random(seed)
    n = 14
    for i in range(n):
        a0 = rng.uniform(0, math.tau)
        r0 = rng.uniform(0.40, 0.78)
        speed = rng.uniform(0.55, 1.4)
        phase = (clock * speed + rng.uniform(0, 1)) % 1.0
        rr = 0.30 + r0 * phase
        cx = math.cos(a0) * rr
        cy = math.sin(a0) * rr
        size = 0.032 * (1.0 - phase) * hype
        if size > 0.002:
            dl.add(("spark", cx, cy, size, a0, phase))


# ---------------------------------------------------------------------------
# 自检：不依赖任何 GUI，直接跑一小段看数值是否合理
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sm = EyeStateMachine()
    print("screen:", SCREEN)
    for i in range(60):
        sm.update(1 / 60)
    print("idle frame items:", len(sm.build_frame().items))

    sm.tap(HEAVY)
    ys = []
    for i in range(50):
        sm.update(1 / 60)
        ys.append(round(sm._tap_channels()[0], 3))
    print("heavy tap dip curve:", ys[:24])

    sm.press_grid("t")
    cols = []
    for i in range(30):
        sm.update(1 / 60)
        cols.append(round(sm.pos_col, 2))
    print("grid to left col:", cols[:16])

    sm.press_grid("b")
    sm.update(1 / 60)
    sm.press_grid("h")
    sm.update(1 / 60)
    r = sm.press_grid("t")
    print("swipe B->H->T detected:", r, "hype:", sm.hype)
