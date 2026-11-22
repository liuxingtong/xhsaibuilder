"""
让 archive/owl/ 里的脚本能独立运行（它们依赖仓库根目录的 eyes_core.py）。

用法：在 archive/owl/ 里每个脚本的开头加
    from _bootstrap import *   # noqa
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))

# 根目录（eyes_core / eyes_render）和本目录（duetto_*）都要在 path 上
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
