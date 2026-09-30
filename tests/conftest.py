"""
pytest 全局配置
==============
确保项目根目录位于 sys.path 中，使测试可以直接 `import stock_monitor`。
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
