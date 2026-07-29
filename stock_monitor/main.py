"""
A 股实时行情查询工具 — 程序入口
==================================
启动 GUI 界面，进入主事件循环。

使用方法：
    python main.py
"""

import tkinter as tk
import sys
import os

# 确保项目根目录在 sys.path 中（兼容直接运行）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock_monitor.gui import StockMonitorApp


def main():
    """程序主入口"""
    root = tk.Tk()
    app = StockMonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()