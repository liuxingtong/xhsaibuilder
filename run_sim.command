#!/bin/bash
cd "$(dirname "$0")"
./.venv/bin/python eyes_sim.py --showcase 2>&1 | tee /tmp/eyes_term.log
echo
echo "=== 退出码: ${PIPESTATUS[0]} ==="
echo "按回车关闭窗口"
read
