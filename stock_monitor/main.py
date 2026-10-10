"""
A 股实时行情查询工具 — 程序入口
==================================
启动 GUI 界面，进入主事件循环。

使用方法：
    python main.py

K线承载进程入口（一般不用手工运行，由程序自行拉起）：
    python main.py --hqchart-chart 600000 stock 浦发银行

为什么会有第二个入口：
    打包成 exe 后 sys.executable 就是本程序自身，无法再 `python -m` 拉子进程，
    于是复用同一个 exe、靠 --hqchart-chart 参数区分入口（见 hqchart_bridge.py
    的 build_chart_command）。开发环境走的是 -m 那条路。
"""

import os
import sys
import tkinter as tk

# 确保项目根目录在 sys.path 中（兼容直接运行）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock_monitor.gui import StockMonitorApp
from stock_monitor.hqchart_bridge import CHART_PROCESS_FLAG


def main(argv=None):
    """程序主入口

    参数：
        argv: 命令行参数（不含程序名），默认取 sys.argv[1:]
    """
    argv = sys.argv[1:] if argv is None else argv

    # 打包模式下 hqchart_bridge 用「同一个 exe + 这个参数」拉起 K线承载进程，
    # 这里改走图表入口，而不是去创建 Tk 根窗口
    if CHART_PROCESS_FLAG in argv:
        from stock_monitor.hqchart_chart import main as chart_main
        return chart_main([a for a in argv if a != CHART_PROCESS_FLAG])

    root = tk.Tk()
    app = StockMonitorApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
