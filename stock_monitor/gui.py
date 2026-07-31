"""
GUI 界面模块（摸鱼版）
=====================
低调、紧凑的行情监控界面，适合在办公环境中隐蔽使用。

特点：
  - 小窗口、紧凑布局，不抢眼
  - 无 emoji 装饰，标题简洁
  - 颜色低调（深红/深绿），不易引起注意
  - 数据以表格形式展示，类似 Excel
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import queue
from datetime import datetime

from stock_monitor.stock_api import query_stock
from stock_monitor.stock_search import search_stocks
from stock_monitor.history_manager import save_query, load_history, clear_history

# ──────────────── 低调配色 ────────────────
COLOR_UP = "#C00000"      # 涨 — 深红（不像荧光红那么刺眼）
COLOR_DOWN = "#005900"    # 跌 — 深绿
COLOR_FLAT = "#333333"    # 平 / 默认文字
COLOR_BG = "#F5F5F5"      # 窗口背景
COLOR_INFO = "#666666"    # 辅助文字
COLOR_SEARCH_BG = "#FAFAFA"
COLOR_TABLE_HEADER = "#D0D0D0"
COLOR_TABLE_ROW = "#FFFFFF"

# 字体：等宽数字更整齐
FONT_MONO = ("Consolas", 10)
FONT_MONO_SM = ("Consolas", 9)
FONT_LABEL = ("微软雅黑", 9)
FONT_BTN = ("微软雅黑", 8)

# 类型中英文映射
_TYPE_LABEL = {"stock": "股票", "index": "指数", "etf": "ETF"}



class StockMonitorApp:
    """A 股行情监控 GUI（紧凑版）"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("行情")
        self.root.geometry("400x500")
        self.root.resizable(False, False)
        self.root.configure(bg=COLOR_BG)

        # 可选：窗口置顶
        self.root.attributes("-topmost", False)

        # 数据队列
        self.data_queue = queue.Queue()

        # 自动刷新
        self.auto_refresh = False
        self.refresh_interval = 60
        self.after_id = None

        # 当前查询信息
        self.current_code = ""
        self.current_type = "stock"

        # 搜索相关
        self.search_results = []

        # 悬浮文字框（无边框透明 Toplevel，支持拖拽）
        self._popup_visible = False
        self._popup_window = None
        self._popup_refresh_timer = None

        self._build_ui()
        self._refresh_history_table()
        self._poll_queue()

    # ──────────────── UI 构建 ────────────────

    def _build_ui(self):
        self._build_input_area()
        self._build_search_results()
        self._build_info_table()
        self._build_control_area()
        self._build_history_area()
        self._build_status_bar()

    # ── 输入区 ──

    def _build_input_area(self):
        input_frame = tk.Frame(self.root, bg=COLOR_BG)
        input_frame.pack(fill=tk.X, padx=12, pady=(8, 4))

        tk.Label(input_frame, text="代码/名称:", font=FONT_LABEL,
                 bg=COLOR_BG).pack(side=tk.LEFT)

        self.code_entry = tk.Entry(
            input_frame, font=("微软雅黑", 11), width=10, justify=tk.CENTER
        )
        self.code_entry.pack(side=tk.LEFT, padx=4)
        self.code_entry.bind("<Return>", lambda e: self._on_enter_key())
        self.code_entry.focus()

        self.type_var = tk.StringVar(value="stock")
        self.type_combo = ttk.Combobox(
            input_frame, textvariable=self.type_var,
            values=["股票", "指数", "ETF"], width=4,
            state="readonly", font=FONT_LABEL,
        )
        self.type_combo.pack(side=tk.LEFT, padx=2)

        self.search_btn = tk.Button(
            input_frame, text="名称搜索", font=FONT_BTN, width=8,
            bg="#E0E0E0", fg="#333", command=self._on_name_search,
        )
        self.search_btn.pack(side=tk.LEFT, padx=1)

        self.query_btn = tk.Button(
            input_frame, text="查询", font=FONT_BTN, width=6,
            bg="#E0E0E0", fg="#333", command=self._on_query,
        )
        self.query_btn.pack(side=tk.LEFT, padx=1)

    # ── 搜索结果显示 ──

    def _build_search_results(self):
        self.search_frame = tk.Frame(self.root, bg=COLOR_SEARCH_BG,
                                     relief=tk.SUNKEN, bd=1)
        self.search_frame.pack_forget()

        tk.Label(self.search_frame, text="搜索结果（双击查询）：",
                 font=FONT_LABEL, bg=COLOR_SEARCH_BG, fg="#444"
                 ).pack(fill=tk.X, anchor=tk.W, padx=5, pady=(4, 1))

        self.search_listbox = tk.Listbox(
            self.search_frame, font=("Consolas", 9), bg="white",
            activestyle="none", selectbackground="#E0E0E0",
        )
        self.search_listbox.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))
        self.search_listbox.bind("<Double-Button-1>", self._on_select_search_result)

    # ── 行情数据表格（紧凑表格式） ──

    def _build_info_table(self):
        table_frame = tk.Frame(self.root, bg="white",
                               relief=tk.SUNKEN, bd=1)
        table_frame.pack(fill=tk.BOTH, padx=12, pady=4, expand=True)

        # 表头行
        tk.Label(table_frame, text="名称 / 代码", font=FONT_LABEL,
                 bg=COLOR_TABLE_HEADER, fg="#333", anchor=tk.W,
                 pady=3).pack(fill=tk.X, padx=2, pady=2)

        # 主数据区：网格布局（2列4行）
        grid = tk.Frame(table_frame, bg="white")
        grid.pack(fill=tk.BOTH, expand=True)

        self.name_label = tk.Label(grid, text="--", font=("微软雅黑", 13, "bold"),
                                   bg="white", fg="#333")
        self.name_label.grid(row=0, column=0, columnspan=2, sticky=tk.W,
                             padx=6, pady=(8, 2))

        # 最新价 + 涨跌幅 行
        row = tk.Frame(grid, bg="white")
        row.grid(row=1, column=0, columnspan=2, sticky=tk.W + tk.E,
                 padx=6, pady=(2, 4), ipady=2)

        self.price_label = tk.Label(row, text="--",
                                    font=("Consolas", 26, "bold"),
                                    bg="white", fg=COLOR_FLAT)
        self.price_label.pack(side=tk.LEFT)

        self.change_label = tk.Label(row, text="--",
                                     font=("Consolas", 14, "bold"),
                                     bg="white", fg=COLOR_FLAT)
        self.change_label.pack(side=tk.RIGHT)

        # 辅助信息行
        detail_row = tk.Frame(grid, bg="white")
        detail_row.grid(row=2, column=0, columnspan=2, sticky=tk.W,
                        padx=8, pady=(2, 4))

        self.high_label = tk.Label(detail_row, text="高  --",
                                   font=FONT_MONO, bg="white", fg=COLOR_INFO)
        self.high_label.pack(side=tk.LEFT, padx=10)

        self.low_label = tk.Label(detail_row, text="低  --",
                                  font=FONT_MONO, bg="white", fg=COLOR_INFO)
        self.low_label.pack(side=tk.LEFT, padx=10)

        self.close_label = tk.Label(detail_row, text="昨收 --",
                                    font=FONT_MONO, bg="white", fg=COLOR_INFO)
        self.close_label.pack(side=tk.LEFT, padx=10)

        self.time_label = tk.Label(grid, text="--",
                                   font=("微软雅黑", 8),
                                   bg="white", fg="#AAAAAA")
        self.time_label.grid(row=3, column=0, columnspan=2, sticky=tk.W,
                             padx=8, pady=(2, 8))

    # ── 控制区 ──

    def _build_control_area(self):
        ctrl_frame = tk.Frame(self.root, bg=COLOR_BG)
        ctrl_frame.pack(fill=tk.X, padx=12, pady=4)

        tk.Label(ctrl_frame, text="刷新:", font=FONT_LABEL,
                 bg=COLOR_BG).pack(side=tk.LEFT)

        self.interval_var = tk.StringVar(value="60")
        self.interval_spin = tk.Spinbox(
            ctrl_frame, from_=5, to=600, increment=5, width=4,
            textvariable=self.interval_var, font=FONT_MONO_SM,
            justify=tk.CENTER,
        )
        self.interval_spin.pack(side=tk.LEFT, padx=2)

        self.start_btn = tk.Button(
            ctrl_frame, text="开始", font=FONT_BTN, width=6,
            bg="#E0E0E0", fg="#333", command=self._on_start_refresh,
        )
        self.start_btn.pack(side=tk.LEFT, padx=2)

        self.stop_btn = tk.Button(
            ctrl_frame, text="停止", font=FONT_BTN, width=6,
            bg="#E0E0E0", fg="#333", state=tk.DISABLED,
            command=self._on_stop_refresh,
        )
        self.stop_btn.pack(side=tk.LEFT, padx=2)

        self.topmost_var = tk.BooleanVar(value=False)
        tk.Checkbutton(ctrl_frame, text="置顶", variable=self.topmost_var,
                       font=FONT_LABEL, bg=COLOR_BG,
                       command=self._toggle_topmost
                       ).pack(side=tk.LEFT, padx=8)

        # 悬浮窗口按钮
        self.popup_btn = tk.Button(
            ctrl_frame, text="悬浮窗", font=FONT_BTN, width=6,
            bg="#E0E0E0", fg="#333", command=self._toggle_popup,
        )
        self.popup_btn.pack(side=tk.LEFT, padx=4)

    # ── 历史表格 ──

    def _build_history_area(self):
        history_outer = tk.Frame(self.root, bg=COLOR_BG)
        history_outer.pack(fill=tk.X, padx=12, pady=(2, 0))

        tk.Label(history_outer, text="查询历史（双击重查）：",
                 font=FONT_LABEL, bg=COLOR_BG, fg="#333"
                 ).pack(side=tk.LEFT)

        tk.Button(history_outer, text="清空", font=FONT_BTN,
                  bg="#E0E0E0", fg="#333", command=self._on_clear_history
                  ).pack(side=tk.RIGHT)

        tree_frame = tk.Frame(history_outer, bg=COLOR_BG)
        tree_frame.pack(fill=tk.X)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        columns = ("code", "type", "name", "price", "change", "time")
        self.history_tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings",
            height=3, yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self.history_tree.yview)

        col_defs = {
            "code":   ("代码",  50, tk.W),
            "type":   ("类型",  35, tk.CENTER),
            "name":   ("名称",  90, tk.W),
            "price":  ("价格",  55, tk.E),
            "change": ("涨跌",  55, tk.E),
            "time":   ("时间",  130, tk.CENTER),
        }
        for col_id, (heading, width, anchor) in col_defs.items():
            self.history_tree.heading(col_id, text=heading)
            self.history_tree.column(col_id, width=width, anchor=anchor)

        self.history_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.history_tree.bind("<Double-Button-1>", self._on_select_history)

    # ── 状态栏 ──

    def _build_status_bar(self):
        self.status_var = tk.StringVar(value="就绪")
        tk.Label(self.root, textvariable=self.status_var, font=FONT_LABEL,
                 bg="#E8E8E8", fg="#666", anchor=tk.W, padx=10, pady=3
                 ).pack(fill=tk.X, side=tk.BOTTOM)

    # ──────────────── 事件处理 ────────────────

    def _on_enter_key(self):
        text = self.code_entry.get().strip()
        if not text:
            return
        if text.isdigit():
            self._on_query()
        else:
            self._on_name_search()

    def _on_name_search(self):
        keyword = self.code_entry.get().strip()
        if not keyword:
            messagebox.showwarning("提示", "请输入股票名称或关键词")
            return
        self.search_frame.pack_forget()
        self.status_var.set(f"搜索「{keyword}」...")
        self.search_btn.config(state=tk.DISABLED)
        threading.Thread(target=self._search_thread, args=(keyword,),
                         daemon=True).start()

    def _on_query(self):
        code = self.code_entry.get().strip()
        if not code:
            messagebox.showwarning("提示", "请输入代码")
            return
        if not code.isdigit():
            messagebox.showwarning("提示", "请通过「名称搜索」或输入6位代码查询")
            return

        type_map = {"股票": "stock", "指数": "index", "ETF": "etf"}
        self.current_type = type_map.get(self.type_var.get(), "stock")
        self.search_frame.pack_forget()
        self.current_code = code
        self.status_var.set(f"查询 {code} ...")
        self._disable_buttons()
        threading.Thread(target=self._fetch_data_thread,
                         args=(code, self.current_type),
                         daemon=True).start()

    def _on_select_search_result(self, event=None):
        sel = self.search_listbox.curselection()
        if not sel:
            return
        result = self.search_results[sel[0]]
        self.search_frame.pack_forget()

        if result.get("type_label") == "指数":
            self.type_var.set("指数")
        elif result.get("type_label") == "ETF":
            self.type_var.set("ETF")
        else:
            self.type_var.set("股票")

        self.code_entry.delete(0, tk.END)
        self.code_entry.insert(0, result["code"])
        self.code_entry.focus()
        self._on_query()

    def _on_select_history(self, event=None):
        sel = self.history_tree.selection()
        if not sel:
            return
        values = self.history_tree.item(sel[0], "values")
        self.type_var.set(values[1])
        self.code_entry.delete(0, tk.END)
        self.code_entry.insert(0, values[0])
        self.code_entry.focus()
        self.status_var.set(f"从历史查询 {values[0]} ...")
        self._on_query()

    def _on_clear_history(self):
        if not messagebox.askyesno("确认", "确定清空查询历史？"):
            return
        clear_history()
        self._refresh_history_table()
        self.status_var.set("历史已清空")

    def _on_start_refresh(self):
        code = self.code_entry.get().strip()
        if not code:
            messagebox.showwarning("提示", "请先输入代码")
            return
        if not code.isdigit():
            messagebox.showwarning("提示", "代码必须为纯数字")
            return

        type_map = {"股票": "stock", "指数": "index", "ETF": "etf"}
        self.current_type = type_map.get(self.type_var.get(), "stock")
        self.current_code = code

        try:
            interval = int(self.interval_var.get())
            if interval < 5:
                interval = 5
            self.refresh_interval = interval
        except ValueError:
            self.refresh_interval = 60

        self.auto_refresh = True
        for w in (self.start_btn, self.query_btn, self.code_entry,
                  self.interval_spin, self.type_combo, self.search_btn):
            w.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)

        type_label = self.type_var.get()
        self.status_var.set(
            f"自动刷新 {self.refresh_interval}s — {type_label}: {code}")
        self._schedule_refresh()

    def _on_stop_refresh(self):
        self.auto_refresh = False
        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        for w in (self.start_btn, self.query_btn, self.code_entry,
                  self.interval_spin, self.type_combo, self.search_btn):
            w.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.status_var.set("自动刷新已停止")

    def _toggle_topmost(self):
        self.root.attributes("-topmost", self.topmost_var.get())

    # ──────────────── 悬浮文字框 ────────────────
    # 无边框置顶窗口，用 -transparentcolor 背景透明。
    # 黑底 + 白字，抗锯齿边缘为灰色像素，不会出现白边。

    _POPUP_BG = "#000000"  # 透明占位色（黑底，抗锯齿边缘为灰，不显眼）

    def _toggle_popup(self):
        if not self.current_code:
            messagebox.showinfo("提示", "请先查询一只股票后再打开悬浮框")
            return
        if self._popup_visible:
            self._hide_popup()
        else:
            self._show_popup()

    def _show_popup(self):
        if self._popup_window is not None:
            return

        self._popup_window = tk.Toplevel(self.root)
        self._popup_window.overrideredirect(True)
        self._popup_window.attributes("-topmost", True)
        self._popup_window.configure(bg=self._POPUP_BG,
                                     borderwidth=0, highlightthickness=0)
        # 位置：桌面右下角（留 20px 边距）
        sw = self._popup_window.winfo_screenwidth()
        sh = self._popup_window.winfo_screenheight()
        self._popup_window.geometry(f"160x80+{sw-100}+{sh-200}")

        # 容器
        container = tk.Frame(self._popup_window, relief=tk.FLAT, bd=0,
                             bg=self._POPUP_BG)
        container.pack(fill=tk.BOTH, expand=True)

        # 名称（白字）
        self.popup_name_label = tk.Label(
            container, text="--", font=("微软雅黑", 10, "bold"),
            fg="#FFFFFF", bg=self._POPUP_BG,
            borderwidth=0, highlightthickness=0)
        self.popup_name_label.pack(anchor=tk.W, padx=4, pady=(2, 0))

        # 价格（白字）
        self.popup_price_label = tk.Label(
            container, text="--", font=("Consolas", 18, "bold"),
            fg="#FFFFFF", bg=self._POPUP_BG,
            borderwidth=0, highlightthickness=0)
        self.popup_price_label.pack(anchor=tk.W, padx=4, pady=(0, 0))

        # 涨跌幅（白字）
        self.popup_change_label = tk.Label(
            container, text="--", font=("Consolas", 11, "bold"),
            fg="#FFFFFF", bg=self._POPUP_BG,
            borderwidth=0, highlightthickness=0)
        self.popup_change_label.pack(anchor=tk.W, padx=4, pady=(0, 2))

        # 拖拽
        self._drag_x = 0
        self._drag_y = 0
        for w in (self._popup_window, container,
                  self.popup_name_label, self.popup_price_label,
                  self.popup_change_label):
            w.bind("<ButtonPress-1>", self._on_popup_press)
            w.bind("<B1-Motion>", self._on_popup_motion)

        # 透明化（延迟应用，确保窗口已渲染）
        self._popup_window.after_idle(
            lambda: self._popup_window.wm_attributes(
                "-transparentcolor", self._POPUP_BG))

        self._popup_visible = True
        self.popup_btn.config(text="隐藏", bg="#F5B7B1")
        self._fetch_popup_data()
        self._schedule_popup_refresh()

    def _on_popup_press(self, event):
        self._drag_x = (self._popup_window.winfo_pointerx()
                        - self._popup_window.winfo_x())
        self._drag_y = (self._popup_window.winfo_pointery()
                        - self._popup_window.winfo_y())

    def _on_popup_motion(self, event):
        x = self._popup_window.winfo_pointerx() - self._drag_x
        y = self._popup_window.winfo_pointery() - self._drag_y
        self._popup_window.geometry(f"+{x}+{y}")

    def _hide_popup(self):
        if self._popup_window is not None:
            self._popup_window.destroy()
            self._popup_window = None
        if self._popup_refresh_timer is not None:
            self.root.after_cancel(self._popup_refresh_timer)
            self._popup_refresh_timer = None
        self._popup_visible = False
        self.root.title("行情")
        self.popup_btn.config(text="悬浮", bg="#E0E0E0")

    def _fetch_popup_data(self):
        threading.Thread(
            target=self._fetch_popup_thread,
            args=(self.current_code, self.current_type),
            daemon=True,
        ).start()

    def _fetch_popup_thread(self, code, stock_type):
        data = query_stock(code, stock_type)
        self.data_queue.put({"_popup": True, "data": data})

    def _schedule_popup_refresh(self):
        if self._popup_window is None:
            return
        if not self.current_code:
            return
        self._fetch_popup_data()
        self._popup_refresh_timer = self.root.after(
            60000, self._schedule_popup_refresh)

    def _update_popup(self, data):
        if self._popup_window is None:
            return
        if data["error"]:
            self.popup_name_label.config(text="查询失败", fg=COLOR_INFO)
            self.popup_price_label.config(text="--", fg="#FFFFFF")
            self.popup_change_label.config(text="--", fg="#FFFFFF")
            return
        price = data["price"]
        change = data["change_percent"]
        yesterday_close = data["yesterday_close"]
        if price > yesterday_close:
            color = "#FF4040"
        elif price < yesterday_close:
            color = "#00CC00"
        else:
            color = "#FFFFFF"
        self.popup_name_label.config(text=data["name"], fg="#FFFFFF")
        self.popup_price_label.config(text=f"{price:.2f}", fg=color)
        self.popup_change_label.config(text=f"{change:+.2f}%", fg=color)

    # ──────────────── 后台线程 ────────────────

    def _search_thread(self, keyword):
        results = search_stocks(keyword, limit=15)
        self.data_queue.put({"_search": True, "keyword": keyword,
                             "results": results})

    def _fetch_data_thread(self, code, stock_type="stock"):
        data = query_stock(code, stock_type)
        self.data_queue.put({"_search": False, "code": code,
                             "type": stock_type, "data": data})

    def _poll_queue(self):
        try:
            while True:
                msg = self.data_queue.get_nowait()
                if msg.get("_popup"):
                    self._update_popup(msg["data"])
                elif msg.get("_search"):
                    self._show_search_results(msg)
                else:
                    self._update_display(msg)
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._poll_queue)

    def _show_search_results(self, msg):
        self.search_btn.config(state=tk.NORMAL)
        results = msg["results"]
        if not results:
            self.status_var.set(f"未找到「{msg['keyword']}」")
            messagebox.showinfo("提示", f"未找到「{msg['keyword']}」")
            return

        self.search_results = results
        self.search_listbox.delete(0, tk.END)
        for r in results:
            self.search_listbox.insert(tk.END,
                                       f"  {r['full_code']}  {r['name']}")
        self.search_frame.pack(fill=tk.X, padx=12, pady=(0, 4))
        self.status_var.set(
            f"{len(results)} 条匹配（双击查询）")

    def _schedule_refresh(self):
        if not self.auto_refresh:
            return
        threading.Thread(target=self._fetch_data_thread,
                         args=(self.current_code, self.current_type),
                         daemon=True).start()
        self.after_id = self.root.after(
            self.refresh_interval * 1000, self._schedule_refresh)

    # ──────────────── 界面更新 ────────────────

    def _update_display(self, msg):
        self._enable_buttons()
        data = msg["data"]
        code = msg["code"]
        stock_type = msg["type"]

        if data["error"]:
            self.name_label.config(text="查询失败", fg=COLOR_INFO)
            self.price_label.config(text="--", fg=COLOR_FLAT)
            self.change_label.config(text="--", fg=COLOR_FLAT)
            self.high_label.config(text="高  --")
            self.low_label.config(text="低  --")
            self.close_label.config(text="昨收 --")
            self.time_label.config(text="--")
            self.status_var.set(data["error"])
            return

        # 保存历史
        try:
            save_query(code, stock_type, data)
            self._refresh_history_table()
        except Exception:
            pass

        self.name_label.config(text=data["name"], fg="#333")

        price = data["price"]
        change = data["change_percent"]
        yesterday_close = data["yesterday_close"]

        if price > yesterday_close:
            color = COLOR_UP
            arrow = "+"
        elif price < yesterday_close:
            color = COLOR_DOWN
            arrow = ""
        else:
            color = COLOR_FLAT
            arrow = ""

        self.price_label.config(text=f"{price:.2f}", fg=color)
        self.change_label.config(
            text=f"{arrow}{change:.2f}%", fg=color)

        self.high_label.config(text=f"高  {data['high']:.2f}")
        self.low_label.config(text=f"低  {data['low']:.2f}")
        self.close_label.config(
            text=f"昨收 {data['yesterday_close']:.2f}")
        self.time_label.config(text=f"{data['date']} {data['time']}")

        self.status_var.set(
            f"{data['name']} {data['time']}")

        # 同步悬浮窗口显示
        if self._popup_visible and self.current_code == code:
            self._update_popup(data)

    # ──────────────── 历史表格 ────────────────

    def _refresh_history_table(self):
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        history = load_history()
        for record in history:
            price = record.get("price", 0.0)
            change = record.get("change_percent", 0.0)
            self.history_tree.insert("", tk.END, values=(
                record.get("code", ""),
                _TYPE_LABEL.get(record.get("type", ""), "股票"),
                record.get("name", ""),
                f"{price:.2f}",
                f"{change:+.2f}%",
                record.get("time", ""),
            ))

    # ──────────────── 辅助 ────────────────

    def _disable_buttons(self):
        self.query_btn.config(state=tk.DISABLED)

    def _enable_buttons(self):
        if not self.auto_refresh:
            self.query_btn.config(state=tk.NORMAL)
            self.search_btn.config(state=tk.NORMAL)


# 测试入口
if __name__ == "__main__":
    root = tk.Tk()
    app = StockMonitorApp(root)
    root.mainloop()
