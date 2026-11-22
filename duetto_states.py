"""
duetto_states.py — 把 eyes_core 的交互状态翻译成猫头鹰的表情参数

这是"动画逻辑"和"形象"之间的适配层。

为什么需要它：
  eyes_core 里已经调好了敲击曲线、弹簧跟随、上滑检测。
  猫头鹰需要的参数（眼睑高度、眉羽角度、喙张开度）和眼睛版不同，
  但它们由同一批信号驱动，所以在这一层做映射，两边都不用改。

关键设计（来自竞品调研）：
  · 猫头鹰的眼球不能转动 → 左右滑动时"转头"（head_x / head_tilt），
    而不是让瞳孔左右移动。这也让舵机有了存在理由。
  · 一次只动一条边 → 眯眼只压上缘，笑意只抬上缘中段，不同时变形。
  · 屏幕/灯带/舵机是三条独立通道 → 表情不等硬件到位，本文件只管屏幕。
"""

from __future__ import annotations

from duetto_owl import OwlParams
from eyes_core import EyeStateMachine, clamp, lerp

# 灯带颜色（给 Arduino / 上位机用，屏幕版只做参考）
LIGHT_IDLE = (255, 196, 107)      # 暖白常亮（当"面盘"）
LIGHT_TAP_LIGHT = (255, 214, 140)
LIGHT_TAP_HEAVY = (255, 170, 80)
LIGHT_LEFT = (255, 184, 107)      # 我这边：暖
LIGHT_RIGHT = (111, 211, 255)     # 对方：冷
LIGHT_HYPE = (255, 236, 170)


def state_to_owl(sm: EyeStateMachine) -> OwlParams:
    """从状态机读出一帧的猫头鹰参数。"""
    p = OwlParams()
    p.clock = sm.clock
    p.spark_seed = sm.sparkle_seed
    p.hype = sm.hype

    col_f = sm.pos_col        # -1 左 / 0 中 / +1 右
    row_f = sm.pos_row        # -1 上 / 0 中 / +1 下

    dip, squash, pop, wobble = sm._tap_channels()
    blink = sm._blink_amount()

    # ---- 转头代替转眼 ----
    # 水平位置 → 整张脸平移 + 旋转（模拟舵机左右摆头）
    p.head_x = col_f * 0.115 + wobble * 1.4
    p.head_tilt = col_f * 9.0
    p.head_y = row_f * 0.10 + dip * 0.85

    # 待机微动：让静止时也有细微的姿态变化（调研：Moflin 无屏却成立，
    # 证明"活着"可以由非大动作承担）
    p.idle_x = sm.idle_look_x * 0.55
    p.idle_y = sm.idle_look_y * 0.75

    # ---- 眼睑 ----
    # squash 是"压扁"，映射成上眼睑下压；blink 直接闭合
    lid = clamp(squash * 1.55 + blink * 1.25, 0.0, 1.0)
    openness = 1.0 - lid
    # 瞪大：重击的 pop 会 >1，映射成眼睛略微放大 + 睁满
    if pop > 1.0:
        openness = 1.0 + (pop - 1.0) * 0.55
    p.eye_open = openness

    # ---- 眉羽 ----
    # 敲击时眉羽上扬（受惊/兴奋）；下排时压低（专注）；彩蛋时炸开
    p.brow_lift = clamp(dip * 1.9, -0.10, 0.14)
    p.brow_asym = -col_f * 0.030           # 转头时一高一低，增强方向感
    p.brow_flare = clamp(sm.hype, 0.0, 1.0)

    # ---- 笑意 / 专注 ----
    # 上滑彩蛋：明显笑眼；重击：专注（眉压、眼瞪）
    p.smile = clamp(sm.hype * 0.85, 0.0, 1.0)
    if sm.last_tap_kind == "heavy" and dip > 0.05:
        p.smile = 0.0
        p.angry = clamp(dip * 1.2, 0.0, 0.9)

    # ---- 喙 ----
    p.beak_open = clamp(sm.hype * 1.1, 0.0, 1.0)

    return p


def light_for(sm: EyeStateMachine) -> tuple:
    """当前该点成什么颜色的灯带（左右各一半）。"""
    left = LIGHT_IDLE
    right = LIGHT_IDLE

    if sm.hype > 0.05:
        return LIGHT_HYPE, LIGHT_HYPE

    dip, _, pop, _ = sm._tap_channels()
    if sm.tap_ev is not None and dip > 0.02:
        c = LIGHT_TAP_HEAVY if sm.last_tap_kind == "heavy" else LIGHT_TAP_LIGHT
        return c, c

    # 双人视界预热：左右位置决定哪一侧更亮
    col_f = sm.pos_col
    if col_f < -0.15:
        return LIGHT_LEFT, _dim(LIGHT_RIGHT, 0.35)
    if col_f > 0.15:
        return _dim(LIGHT_LEFT, 0.35), LIGHT_RIGHT
    return left, right


def _dim(c, k: float):
    return (int(c[0] * k), int(c[1] * k), int(c[2] * k))
