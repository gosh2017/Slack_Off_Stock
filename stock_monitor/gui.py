"""
GUI 界面模块
============
使用 tkinter 构建 A 股行情查询工具界面。
支持手动查询、自动刷新、涨跌颜色显示。
数据获取在后台线程中执行，防止界面卡顿。
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import queue
from datetime import datetime

from stock_monitor.stock_api import query_stock


# 颜色定义
COLOR_UP = "#FF0000"      # 涨 → 红色
COLOR_DOWN = "#00AA00"    # 跌 → 绿色
COLOR_FLAT = "#000000"    # 平 → 黑色
COLOR_BG = "#F0F0F0"      # 背景色
COLOR_INFO = "#555555"    # 辅助信息颜色


class StockMonitorApp:
    """A 股行情监控 GUI 主类"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("A 股行情查询工具")
        self.root.geometry("480x520")
        self.root.resizable(False, False)
        self.root.configure(bg=COLOR_BG)

        # 可选：窗口置顶
        self.root.attributes("-topmost", False)

        # 数据队列：后台线程 → GUI 通信
        self.data_queue = queue.Queue()

        # 自动刷新控制
        self.auto_refresh = False       # 是否正在自动刷新
        self.refresh_interval = 60      # 刷新间隔（秒）
        self.after_id = None            # after() 任务 ID

        # 当前股票代码
        self.current_code = ""

        # 查询类型：stock / index / etf
        self.current_type = "stock"

        # 构建界面
        self._build_ui()

        # 定期检查队列中的后台数据
        self._poll_queue()

    # ──────────────── UI 构建 ────────────────

    def _build_ui(self):
        """构建整个界面布局"""
        self._build_title()
        self._build_input_area()
        self._build_info_area()
        self._build_control_area()
        self._build_status_bar()

    def _build_title(self):
        """标题栏"""
        title_frame = tk.Frame(self.root, bg=COLOR_BG)
        title_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        tk.Label(
            title_frame,
            text="📈 A 股实时行情查询",
            font=("微软雅黑", 16, "bold"),
            bg=COLOR_BG,
            fg="#333333",
        ).pack()

    def _build_input_area(self):
        """输入区域：股票代码输入框 + 查询按钮"""
        input_frame = tk.Frame(self.root, bg=COLOR_BG)
        input_frame.pack(fill=tk.X, padx=20, pady=10)

        tk.Label(
            input_frame,
            text="股票代码：",
            font=("微软雅黑", 11),
            bg=COLOR_BG,
        ).pack(side=tk.LEFT)

        self.code_entry = tk.Entry(
            input_frame,
            font=("微软雅黑", 12),
            width=10,
            justify=tk.CENTER,
        )
        self.code_entry.pack(side=tk.LEFT, padx=5)
        self.code_entry.bind("<Return>", lambda e: self._on_query())
        # 设置焦点
        self.code_entry.focus()

        # 查询类型选择（股票 / 指数 / ETF）
        self.type_var = tk.StringVar(value="stock")
        self.type_combo = ttk.Combobox(
            input_frame,
            textvariable=self.type_var,
            values=["股票", "指数", "ETF"],
            width=5,
            state="readonly",
            font=("微软雅黑", 10),
        )
        self.type_combo.pack(side=tk.LEFT, padx=5)

        self.query_btn = tk.Button(
            input_frame,
            text="🔍 查询",
            font=("微软雅黑", 10),
            width=10,
            bg="#4A90D9",
            fg="white",
            relief=tk.RAISED,
            command=self._on_query,
        )
        self.query_btn.pack(side=tk.LEFT, padx=5)

    def _build_info_area(self):
        """信息显示区域：股票名称、价格、涨跌幅、辅助信息"""
        info_frame = tk.Frame(
            self.root,
            bg="white",
            relief=tk.SUNKEN,
            bd=1,
        )
        info_frame.pack(fill=tk.BOTH, padx=20, pady=5, expand=True)

        # 股票名称（大号显示）
        self.name_label = tk.Label(
            info_frame,
            text="--",
            font=("微软雅黑", 18, "bold"),
            bg="white",
            fg="#333333",
        )
        self.name_label.pack(pady=(20, 5))

        # 最新价（超大号显示）
        self.price_label = tk.Label(
            info_frame,
            text="--",
            font=("微软雅黑", 36, "bold"),
            bg="white",
            fg=COLOR_FLAT,
        )
        self.price_label.pack(pady=(0, 5))

        # 涨跌幅（大号显示）
        self.change_label = tk.Label(
            info_frame,
            text="--",
            font=("微软雅黑", 20, "bold"),
            bg="white",
            fg=COLOR_FLAT,
        )
        self.change_label.pack(pady=(0, 15))

        # 辅助信息（最高 / 最低 / 昨收）
        detail_frame = tk.Frame(info_frame, bg="white")
        detail_frame.pack(pady=(0, 20))

        info_font = ("微软雅黑", 10)
        self.high_label = tk.Label(
            detail_frame, text="最高: --", font=info_font, bg="white", fg=COLOR_INFO
        )
        self.high_label.pack(side=tk.LEFT, padx=15)

        self.low_label = tk.Label(
            detail_frame, text="最低: --", font=info_font, bg="white", fg=COLOR_INFO
        )
        self.low_label.pack(side=tk.LEFT, padx=15)

        self.close_label = tk.Label(
            detail_frame, text="昨收: --", font=info_font, bg="white", fg=COLOR_INFO
        )
        self.close_label.pack(side=tk.LEFT, padx=15)

        # 时间
        self.time_label = tk.Label(
            info_frame,
            text="--",
            font=("微软雅黑", 9),
            bg="white",
            fg="#AAAAAA",
        )
        self.time_label.pack(pady=(0, 10))

    def _build_control_area(self):
        """控制区域：开始/停止刷新按钮 + 间隔设置"""
        ctrl_frame = tk.Frame(self.root, bg=COLOR_BG)
        ctrl_frame.pack(fill=tk.X, padx=20, pady=10)

        # 刷新间隔设置
        tk.Label(
            ctrl_frame,
            text="刷新间隔（秒）：",
            font=("微软雅黑", 9),
            bg=COLOR_BG,
        ).pack(side=tk.LEFT)

        self.interval_var = tk.StringVar(value="60")
        self.interval_spin = tk.Spinbox(
            ctrl_frame,
            from_=5,
            to=600,
            increment=5,
            width=6,
            textvariable=self.interval_var,
            font=("微软雅黑", 9),
            justify=tk.CENTER,
        )
        self.interval_spin.pack(side=tk.LEFT, padx=5)

        # 开始刷新按钮
        self.start_btn = tk.Button(
            ctrl_frame,
            text="▶ 开始刷新",
            font=("微软雅黑", 10),
            bg="#5CB85C",
            fg="white",
            width=10,
            command=self._on_start_refresh,
        )
        self.start_btn.pack(side=tk.LEFT, padx=5)

        # 停止刷新按钮
        self.stop_btn = tk.Button(
            ctrl_frame,
            text="■ 停止刷新",
            font=("微软雅黑", 10),
            bg="#D9534F",
            fg="white",
            width=10,
            state=tk.DISABLED,
            command=self._on_stop_refresh,
        )
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        # 置顶复选框
        self.topmost_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            ctrl_frame,
            text="置顶",
            variable=self.topmost_var,
            font=("微软雅黑", 9),
            bg=COLOR_BG,
            command=self._toggle_topmost,
        ).pack(side=tk.LEFT, padx=10)

    def _build_status_bar(self):
        """底部状态栏"""
        self.status_var = tk.StringVar(value="就绪 — 请输入股票代码后点击「查询」")
        status_bar = tk.Label(
            self.root,
            textvariable=self.status_var,
            font=("微软雅黑", 9),
            bg="#DDDDDD",
            fg="#555555",
            anchor=tk.W,
            padx=10,
            pady=5,
        )
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    # ──────────────── 事件处理 ────────────────

    def _on_query(self):
        """点击「查询」按钮 — 在后台线程中获取数据"""
        code = self.code_entry.get().strip()
        if not code:
            messagebox.showwarning("提示", "请输入代码")
            return

        if not code.isdigit():
            messagebox.showwarning("提示", "代码必须为纯数字")
            return

        # 读取选择的类型
        type_map = {"股票": "stock", "指数": "index", "ETF": "etf"}
        self.current_type = type_map.get(self.type_var.get(), "stock")

        self.current_code = code
        type_label = self.type_var.get()
        self.status_var.set(f"正在查询 {code}（{type_label}）...")
        self._disable_buttons()

        # 启动后台线程获取数据
        thread = threading.Thread(
            target=self._fetch_data_thread,
            args=(code, self.current_type),
            daemon=True,
        )
        thread.start()

    def _on_start_refresh(self):
        """点击「开始刷新」按钮"""
        code = self.code_entry.get().strip()
        if not code:
            messagebox.showwarning("提示", "请输入代码后再开始刷新")
            return

        if not code.isdigit():
            messagebox.showwarning("提示", "代码必须为纯数字")
            return

        # 读取选择的类型
        type_map = {"股票": "stock", "指数": "index", "ETF": "etf"}
        self.current_type = type_map.get(self.type_var.get(), "stock")

        self.current_code = code

        # 读取刷新间隔
        try:
            interval = int(self.interval_var.get())
            if interval < 5:
                interval = 5
            self.refresh_interval = interval
        except ValueError:
            self.refresh_interval = 60

        self.auto_refresh = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.query_btn.config(state=tk.DISABLED)
        self.code_entry.config(state=tk.DISABLED)
        self.interval_spin.config(state=tk.DISABLED)
        self.type_combo.config(state=tk.DISABLED)

        type_label = self.type_var.get()
        self.status_var.set(
            f"自动刷新已启动（每 {self.refresh_interval} 秒）— {type_label}: {code}"
        )

        # 立即执行一次查询
        self._schedule_refresh()

    def _on_stop_refresh(self):
        """点击「停止刷新」按钮"""
        self.auto_refresh = False
        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None

        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.query_btn.config(state=tk.NORMAL)
        self.code_entry.config(state=tk.NORMAL)
        self.interval_spin.config(state=tk.NORMAL)
        self.type_combo.config(state=tk.NORMAL)

        self.status_var.set("自动刷新已停止")

    def _toggle_topmost(self):
        """切换窗口置顶状态"""
        self.root.attributes("-topmost", self.topmost_var.get())

    # ──────────────── 后台线程 ────────────────

    def _fetch_data_thread(self, code: str, stock_type: str = "stock"):
        """
        后台线程函数：调用 API 获取数据，将结果放入队列。

        参数：
            code: 代码
            stock_type: 查询类型（stock / index / etf）
        """
        data = query_stock(code, stock_type)
        self.data_queue.put(data)

    def _poll_queue(self):
        """
        定期轮询数据队列，将后台线程返回的结果更新到 GUI。

        使用 after() 每 100ms 检查一次队列。
        """
        try:
            while True:
                data = self.data_queue.get_nowait()
                self._update_display(data)
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._poll_queue)

    def _schedule_refresh(self):
        """
        调度下一次自动刷新。

        如果 auto_refresh 为 True，在指定间隔后再次执行查询。
        """
        if not self.auto_refresh:
            return

        # 在后台线程中执行查询
        thread = threading.Thread(
            target=self._fetch_data_thread,
            args=(self.current_code, self.current_type),
            daemon=True,
        )
        thread.start()

        # 调度下一次刷新
        interval_ms = self.refresh_interval * 1000
        self.after_id = self.root.after(interval_ms, self._schedule_refresh)

        # 更新状态栏倒计时提示
        now = datetime.now().strftime("%H:%M:%S")
        self.status_var.set(
            f"自动刷新中（每 {self.refresh_interval} 秒）— {self.current_code} — "
            f"下次刷新: {now}"
        )

    # ──────────────── 界面更新 ────────────────

    def _update_display(self, data: dict):
        """
        将数据更新到界面显示。

        参数：
            data: query_stock() 返回的字典
        """
        # 恢复按钮状态
        self._enable_buttons()

        if data["error"]:
            # 显示错误信息
            self.name_label.config(text="查询失败", fg=COLOR_INFO)
            self.price_label.config(text="--", fg=COLOR_FLAT)
            self.change_label.config(text="--", fg=COLOR_FLAT)
            self.high_label.config(text="最高: --")
            self.low_label.config(text="最低: --")
            self.close_label.config(text="昨收: --")
            self.time_label.config(text="--")
            self.status_var.set(f"❌ {data['error']}")
            return

        # 更新股票名称
        self.name_label.config(text=data["name"], fg="#333333")

        # 更新价格和颜色
        price = data["price"]
        change = data["change_percent"]
        yesterday_close = data["yesterday_close"]

        # 判断涨跌颜色
        if price > yesterday_close:
            color = COLOR_UP
            arrow = "▲"
        elif price < yesterday_close:
            color = COLOR_DOWN
            arrow = "▼"
        else:
            color = COLOR_FLAT
            arrow = "—"

        self.price_label.config(text=f"{price:.2f}", fg=color)
        self.change_label.config(
            text=f"{arrow} {change:+.2f}%",
            fg=color,
        )

        # 更新辅助信息
        self.high_label.config(text=f"最高: {data['high']:.2f}")
        self.low_label.config(text=f"最低: {data['low']:.2f}")
        self.close_label.config(text=f"昨收: {data['yesterday_close']:.2f}")
        self.time_label.config(text=f"{data['date']} {data['time']}")

        self.status_var.set(
            f"✅ 数据已更新 — {data['name']} ({data['time']})"
        )

    # ──────────────── 辅助方法 ────────────────

    def _disable_buttons(self):
        """禁用查询按钮，防止重复点击"""
        self.query_btn.config(state=tk.DISABLED)

    def _enable_buttons(self):
        """恢复按钮状态"""
        if not self.auto_refresh:
            self.query_btn.config(state=tk.NORMAL)


# 测试入口
if __name__ == "__main__":
    root = tk.Tk()
    app = StockMonitorApp(root)
    root.mainloop()