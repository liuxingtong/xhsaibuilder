"""
duo_bridge.py — 双机串口桥接（对应《Duetto Demo 说明》4.1 / 4.2）

这是 PC 端中枢：
  · 读两台设备的串口状态（I / T / L / R / U）
  · 做共鸣裁判（1.5 秒窗口内两边都"激烈"→ 下发 S1）
  · 把对方状态转发给本机（P_X）驱动灯带另一半
  · 记录模式：带毫秒时间戳写入 JSON
  · --simulate：没有硬件时用虚拟设备跑通全链路

协议（波特率 115200，行尾 \\n）
--------------------------------
  硬件 → PC :  I / T / L / R / U
  PC → 硬件 :  S1 / S0 / P_X

用法
----
  # 有硬件
  python3 duo_bridge.py --a /dev/cu.usbserial-A --b /dev/cu.usbserial-B --record

  # 没硬件，跑通逻辑（推荐先跑这个）
  python3 duo_bridge.py --simulate --record

  # 看串口列表
  python3 duo_bridge.py --list
"""

from __future__ import annotations

import argparse
import json
import queue
import random
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import eyes_core as ec

# ---------------------------------------------------------------------------
# 协议常量
# ---------------------------------------------------------------------------

STATE_IDLE = "I"
STATE_TAP = "T"
STATE_LEFT = "L"
STATE_RIGHT = "R"
STATE_UP = "U"

#: 哪些状态算"激烈情绪"，用于共鸣判定（对应说明 4.2）
INTENSE = {STATE_TAP, STATE_UP}

BAUD = 115200


# ---------------------------------------------------------------------------
# 共鸣裁判（与 duetto_duo.ResonanceJudge 同一套逻辑，这里做设备版）
# ---------------------------------------------------------------------------


@dataclass
class DeviceState:
    name: str
    last: str = STATE_IDLE
    last_change: float = 0.0
    events: list = field(default_factory=list)   # [(t, state)]
    port: Optional[object] = None


class Bridge:
    def __init__(self, window: float = 1.5, hold: float = 3.5,
                 cooldown: float = 6.0, quiet: bool = False):
        self.window = window
        self.hold = hold
        self.cooldown = cooldown
        self.quiet = quiet

        self.t0 = time.monotonic()
        self.dev: dict[str, DeviceState] = {
            "A": DeviceState("A"),
            "B": DeviceState("B"),
        }

        self.active_until = -1e9
        self.last_end = -1e9
        self.hits = 0
        self.records: list[dict] = []

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._writer = threading.Thread(target=self._loop, daemon=True)

    # -- 时间 ------------------------------------------------------------

    def now(self) -> float:
        return time.monotonic() - self.t0

    # -- 外部输入 --------------------------------------------------------

    def feed(self, side: str, state: str) -> None:
        """喂入一次状态变化。"""
        state = state.strip().upper()[:1]
        if state not in (STATE_IDLE, STATE_TAP, STATE_LEFT, STATE_RIGHT, STATE_UP):
            return
        with self._lock:
            t = self.now()
            d = self.dev[side]
            if state == d.last:
                return
            d.last = state
            d.last_change = t
            d.events.append((t, state))
            # 只保留窗口内的
            cutoff = t - self.window
            d.events = [e for e in d.events if e[0] >= cutoff]

            self.records.append({
                "t_ms": int(round(t * 1000)),
                "device": side,
                "state": state,
            })
            if not self.quiet:
                print(f"  [{t:6.2f}s] {side} → {state}", flush=True)

    # -- 共鸣判定 --------------------------------------------------------

    def is_resonant(self) -> bool:
        with self._lock:
            t = self.now()
            if t < self.active_until:
                return True
            if t - self.last_end < self.cooldown:
                return False

            cutoff = t - self.window
            a = [e for e in self.dev["A"].events if e[0] >= cutoff and e[1] in INTENSE]
            b = [e for e in self.dev["B"].events if e[0] >= cutoff and e[1] in INTENSE]
            if a and b:
                self.active_until = t + self.hold
                self.hits += 1
                return True
            return False

    def tick(self) -> bool:
        """每次主循环调用。返回当前是否共鸣，并负责发指令、更新冷却。"""
        was = self.now() < self.active_until
        res = self.is_resonant()

        if res and not was:
            self._send_all("S1")
            if not self.quiet:
                print(f"\n  ★ 共鸣触发！(第 {self.hits} 次) → 下发 S1\n", flush=True)

        if was and not res:
            self._send_all("S0")
            self.last_end = self.now()
            if not self.quiet:
                print("  共鸣结束 → 下发 S0\n", flush=True)

        # 把对方状态转给本机（P_X）
        if not res:
            self._send("A", f"P_{self.dev['B'].last}")
            self._send("B", f"P_{self.dev['A'].last}")

        return res

    def _send(self, side: str, cmd: str) -> None:
        port = self.dev[side].port
        if port is None:
            return
        try:
            port.write((cmd + "\n").encode())
        except Exception as e:
            print(f"  !! 写 {side} 失败: {e}", flush=True)

    def _send_all(self, cmd: str) -> None:
        for side in ("A", "B"):
            self._send(side, cmd)

    # -- 后台 ------------------------------------------------------------

    def start(self) -> None:
        self._writer.start()

    def stop(self) -> None:
        self._stop.set()
        self._writer.join(timeout=1.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.tick()
            time.sleep(0.05)

    # -- 记录 ------------------------------------------------------------

    def save(self, path: str, meta: dict | None = None) -> None:
        payload = {
            "meta": {
                "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "resonance_hits": self.hits,
                "protocol": "I/T/L/R/U @115200",
                **(meta or {}),
            },
            "events": self.records,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"已保存 {len(self.records)} 条事件 → {path}")


# ---------------------------------------------------------------------------
# 串口
# ---------------------------------------------------------------------------


def list_ports() -> None:
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        print("需要 pyserial：pip install pyserial")
        return
    ports = list(lp.comports())
    if not ports:
        print("没有发现串口设备。")
        return
    for p in ports:
        print(f"  {p.device:32s} {p.description}")


class SerialReader(threading.Thread):
    """读一个串口，每收到一行就回调。"""

    def __init__(self, port, side: str, bridge: Bridge):
        super().__init__(daemon=True)
        self.port = port
        self.side = side
        self.bridge = bridge
        self._stop = threading.Event()

    def run(self) -> None:
        buf = b""
        while not self._stop.is_set():
            try:
                data = self.port.read(64)
            except Exception as e:
                print(f"  !! 读 {self.side} 失败: {e}", flush=True)
                return
            if not data:
                continue
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                s = line.decode(errors="ignore").strip().upper()
                if s:
                    self.bridge.feed(self.side, s)

    def stop(self) -> None:
        self._stop.set()


def open_serial(device: str):
    import serial
    return serial.Serial(device, BAUD, timeout=0.1)


# ---------------------------------------------------------------------------
# 模拟设备（没有硬件时用）
# ---------------------------------------------------------------------------


class SimDevice(threading.Thread):
    """
    虚拟设备。

    role="lead"  : 自己按节奏产生动作（演示里的"我"）
    role="follow": 看到 lead 的激烈动作后跟一手，prob 决定多容易跟上
                   （用来验证共鸣判定是否在该触发的时候触发）
    """

    def __init__(self, name: str, bridge: Bridge, role: str = "lead",
                 prob: float = 0.7, period: tuple = (1.2, 2.6)):
        super().__init__(daemon=True)
        self.name = name
        self.bridge = bridge
        self.role = role
        self.prob = prob
        self.period = period
        self._stop = threading.Event()
        self._last_seen = -1

    def run(self) -> None:
        while not self._stop.is_set():
            if self.role == "lead":
                time.sleep(random.uniform(*self.period))
                state = random.choice([STATE_TAP, STATE_UP, STATE_LEFT, STATE_RIGHT])
                self.bridge.feed(self.name, state)
                time.sleep(random.uniform(0.15, 0.35))
                self.bridge.feed(self.name, STATE_IDLE)
            else:
                # 找对方最近的激烈事件
                peer = "A" if self.name == "B" else "B"
                recent = [e for e in self.bridge.dev[peer].events
                          if e[1] in INTENSE and e[0] > self._last_seen]
                if recent:
                    self._last_seen = recent[-1][0]
                    if random.random() < self.prob:
                        delay = random.uniform(0.10, 0.45)
                        time.sleep(delay)
                        self.bridge.feed(self.name, random.choice([STATE_TAP, STATE_UP]))
                        time.sleep(random.uniform(0.15, 0.3))
                        self.bridge.feed(self.name, STATE_IDLE)
                time.sleep(0.06)

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# 主程序
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Duetto 双机串口桥接")
    ap.add_argument("--a", help="设备 A 串口，如 /dev/cu.usbserial-110")
    ap.add_argument("--b", help="设备 B 串口")
    ap.add_argument("--record", action="store_true", help="记录模式：写 JSON")
    ap.add_argument("--out", default="record_song1.json", help="记录文件名")
    ap.add_argument("--simulate", action="store_true", help="无硬件，用虚拟设备")
    ap.add_argument("--quiet", action="store_true", help="不打印每次状态变化")
    ap.add_argument("--window", type=float, default=1.5,
                    help="共鸣判定窗口（秒）。文档给的 1.5s 偏宽松，"
                         "真实双人建议 0.6~0.9")
    ap.add_argument("--seconds", type=float, default=0,
                    help="运行多少秒后退出（0=一直跑）")
    ap.add_argument("--list", action="store_true", help="列出串口")
    args = ap.parse_args(argv)

    if args.list:
        list_ports()
        return 0

    bridge = Bridge(window=args.window, quiet=args.quiet)

    readers: list[SerialReader] = []
    sims: list[SimDevice] = []

    if args.simulate:
        print("=== 模拟模式（没有硬件）===")
        print("  A 主动产生节奏，B 按 70% 概率跟随\n")
        a = SimDevice("A", bridge, role="lead")
        b = SimDevice("B", bridge, role="follow", prob=0.7)
        a.start(); b.start()
        sims.extend([a, b])
    else:
        if not (args.a and args.b):
            print("需要 --a 和 --b 指定两个串口，或用 --simulate 跑无硬件测试。")
            print("用 --list 查看可用串口。")
            return 1
        try:
            for side, dev in (("A", args.a), ("B", args.b)):
                port = open_serial(dev)
                bridge.dev[side].port = port
                r = SerialReader(port, side, bridge)
                r.start()
                readers.append(r)
                print(f"已连接 {side}: {dev}")
        except ImportError:
            print("需要 pyserial：pip install pyserial")
            return 1
        except Exception as e:
            print(f"连接失败: {e}")
            return 1

    bridge.start()

    print("开始运行。Ctrl-C 结束。\n" if not args.quiet else "", flush=True)
    t_start = time.monotonic()
    try:
        while True:
            time.sleep(0.2)
            if args.seconds and time.monotonic() - t_start >= args.seconds:
                break
    except KeyboardInterrupt:
        print("\n收到中断。")

    bridge.stop()
    for r in readers:
        r.stop()
    for s in sims:
        s.stop()

    print(f"\n共鸣共触发 {bridge.hits} 次")
    if args.record:
        bridge.save(args.out, meta={
            "mode": "simulate" if args.simulate else "serial",
            "a": args.a, "b": args.b,
        })
    return 0


if __name__ == "__main__":
    sys.exit(main())
