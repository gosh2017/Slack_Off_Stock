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

from stock_monitor.stock_api import query_stock, query_kline_data
from stock_monitor.stock_search import search_stocks
from stock_monitor.history_manager import save_query, load_history, clear_history
from stock_monitor.watchlist_manager import (
    load_watchlist, add_to_watchlist,
    remove_from_watchlist, is_in_watchlist,
)

# ──────────────── 可选依赖：matplotlib（嵌入 K线图用） ────────────────
try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.lines import Line2D
    from matplotlib.patches import Rectangle
    MATPLOTLIB_OK = True
except ImportError:
    MATPLOTLIB_OK = False

# ──────────────── 低调配色 ────────────────
COLOR_UP = "#C00000"      # 涨 — 深红（不像荧光红那么刺眼）
COLOR_DOWN = "#005900"    # 跌 — 深绿
COLOR_FLAT = "#333333"    # 平 / 默认文字
COLOR_BG = "#F5F5F5"      # 窗口背景
COLOR_INFO = "#666666"    # 辅助文字
COLOR_SEARCH_BG = "#FAFAFA"
COLOR_TABLE_HEADER = "#D0D0D0"
COLOR_TABLE_ROW = "#FFFFFF"
COLOR_MA5 = "#FF8C00"     # MA5 — 橙色
COLOR_MA10 = "#4169E1"    # MA10 — 蓝色
COLOR_MA20 = "#8B008B"    # MA20 — 紫色

# 字体：等宽数字更整齐
FONT_MONO = ("Consolas", 10)
FONT_MONO_SM = ("Consolas", 9)
FONT_LABEL = ("微软雅黑", 9)
FONT_BTN = ("微软雅黑", 8)

# 类型中英文映射
_TYPE_LABEL = {"stock": "股票", "index": "指数", "etf": "ETF"}


# ──────────────── K线图表独立窗口 ────────────────

class KlineWindow:
    """独立的 K线图表窗口（Toplevel），支持日K/周K/月K。"""

    def __init__(self, code: str, stock_type: str, name: str = ""):
        self.code = code
        self.stock_type = stock_type
        self.name = name
        self.kline_data = []
        self.period = "daily"
        self._loading = False

        # 窗口
        self.win = tk.Toplevel()
        self.win.title(f"K线 — {name or code} ({code})")
        self.win.geometry("780x560")
        self.win.minsize(520, 380)
        self.win.configure(bg=COLOR_BG)

        # UI
        self._build_toolbar()
        self.kline_fig = None
        self.kline_canvas = None
        self.kline_ax_price = None
        self.kline_ax_volume = None
        self.kline_placeholder = tk.Label(
            self.win, text="", font=("微软雅黑", 9),
            bg="white", fg="#AAA", anchor=tk.CENTER,
        )
        self.kline_placeholder.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))

        if not MATPLOTLIB_OK:
            self.kline_placeholder.config(
                text="请安装 matplotlib 以显示 K线图\n\npip install matplotlib")
        else:
            self._fetch_kline()

    # ── 工具栏 ──

    def _build_toolbar(self):
        toolbar = tk.Frame(self.win, bg=COLOR_BG)
        toolbar.pack(fill=tk.X, padx=6, pady=(6, 2))

        self.status_label = tk.Label(
            toolbar, text="", font=("微软雅黑", 8),
            bg=COLOR_BG, fg="#888",
        )
        self.status_label.pack(side=tk.LEFT, padx=2)

        self.period_btns = {}
        btn_style = {"font": ("微软雅黑", 8), "width": 4,
                     "bg": "#E0E0E0", "fg": "#333"}
        for key, label in [("daily", "日K"), ("weekly", "周K"),
                           ("monthly", "月K")]:
            btn = tk.Button(
                toolbar, text=label, **btn_style,
                command=lambda p=key: self._switch_period(p),
            )
            btn.pack(side=tk.RIGHT, padx=1)
            self.period_btns[key] = btn

        self.refresh_btn = tk.Button(
            toolbar, text="刷新", font=("微软雅黑", 8), width=4,
            bg="#E0E0E0", fg="#333", command=self._fetch_kline,
        )
        self.refresh_btn.pack(side=tk.RIGHT, padx=4)

    # ── 数据获取 ──

    def _fetch_kline(self):
        if not MATPLOTLIB_OK or self._loading or not self.code:
            return
        self._loading = True
        self.status_label.config(text="加载中...")
        self._set_btn_state(tk.DISABLED)

        count_map = {"daily": 120, "weekly": 400, "monthly": 800}
        count = count_map.get(self.period, 120)
        threading.Thread(
            target=self._fetch_thread,
            args=(count,), daemon=True,
        ).start()

    def _fetch_thread(self, count: int):
        result = query_kline_data(
            self.code, self.stock_type,
            period=self.period, count=count,
        )
        self.win.after(0, self._on_data_ready, result)

    def _on_data_ready(self, result):
        self._loading = False
        self._set_btn_state(tk.NORMAL)

        if result["error"]:
            self.status_label.config(text="查询失败")
            if self.kline_ax_price:
                self.kline_ax_price.clear()
                self.kline_ax_volume.clear()
                self.kline_ax_price.text(
                    0.5, 0.5, f"查询失败: {result['error']}",
                    transform=self.kline_ax_price.transAxes,
                    ha="center", va="center", fontsize=9, color="#888")
                self.kline_canvas.draw()
            return

        self.kline_data = result["klines"]
        self.status_label.config(text="")
        self._init_figure()
        self._draw()

    # ── matplotlib 初始化 ──

    def _init_figure(self):
        if self.kline_fig is not None:
            return
        matplotlib.rcParams["font.sans-serif"] = [
            "Microsoft YaHei", "SimHei"]
        matplotlib.rcParams["axes.unicode_minus"] = False

        self.kline_fig = Figure(figsize=(7, 4.2), dpi=100,
                                facecolor="white")
        self.kline_fig.subplots_adjust(left=0.07, right=0.97,
                                       bottom=0.12, top=0.95,
                                       hspace=0.10)

        self.kline_ax_price = self.kline_fig.add_subplot(3, 1, (1, 2))
        self.kline_ax_volume = self.kline_fig.add_subplot(
            3, 1, 3, sharex=self.kline_ax_price)

        self.kline_placeholder.pack_forget()
        self.kline_canvas = FigureCanvasTkAgg(self.kline_fig,
                                              master=self.win)
        self.kline_canvas.get_tk_widget().pack(
            fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))

    # ── 绘制 ──

    def _draw(self):
        klines = self.kline_data
        if len(klines) < 2:
            self.status_label.config(text="数据不足")
            return

        period_label = {"daily": "日K", "weekly": "周K",
                        "monthly": "月K"}
        label = period_label.get(self.period, "日K")

        self.kline_ax_price.clear()
        self.kline_ax_volume.clear()

        x = list(range(len(klines)))
        opens = [k["open"] for k in klines]
        closes = [k["close"] for k in klines]
        highs = [k["high"] for k in klines]
        lows = [k["low"] for k in klines]
        volumes = [k["volume"] for k in klines]
        dates = [k["date"] for k in klines]

        cw = 0.6
        for i in range(len(klines)):
            color = COLOR_UP if closes[i] >= opens[i] else COLOR_DOWN
            self.kline_ax_price.plot([x[i], x[i]], [lows[i], highs[i]],
                                     color=color, linewidth=0.8)
            bottom = min(opens[i], closes[i])
            height = abs(closes[i] - opens[i]) or 0.01
            rect = Rectangle((x[i] - cw / 2, bottom), cw, height,
                             facecolor=color, edgecolor=color)
            self.kline_ax_price.add_patch(rect)

        self._draw_ma(x, closes, 5, COLOR_MA5)
        self._draw_ma(x, closes, 10, COLOR_MA10)
        self._draw_ma(x, closes, 20, COLOR_MA20)

        self.kline_ax_price.set_title(
            f"{self.name or ''} ({self.code}) — {label}",
            fontsize=11, fontweight="bold", pad=4)
        self.kline_ax_price.grid(True, color="#F0F0F0", linewidth=0.5)
        self.kline_ax_price.set_xlim(-1, len(x))
        self.kline_ax_price.tick_params(labelbottom=False,
                                        labelsize=7)

        max_vol = max(volumes) if volumes else 1
        for i in range(len(klines)):
            color = "#FFCCCC" if closes[i] >= opens[i] else "#CCE0CC"
            self.kline_ax_volume.bar(x[i], volumes[i] / max_vol,
                                     width=0.6, color=color,
                                     edgecolor=color)

        self.kline_ax_volume.grid(True, color="#F0F0F0", linewidth=0.5)
        self.kline_ax_volume.set_xlim(-1, len(x))
        self.kline_ax_volume.tick_params(labelsize=7)

        n = max(1, len(dates) // 8)
        visible_ticks = list(range(0, len(dates), n))
        visible_dates = [dates[i][-5:] for i in visible_ticks]
        self.kline_ax_volume.set_xticks(visible_ticks)
        self.kline_ax_volume.set_xticklabels(visible_dates, fontsize=6.5)

        legend_lines = [
            Line2D([0], [0], color=COLOR_MA5, linewidth=1.2,
                   label="MA5"),
            Line2D([0], [0], color=COLOR_MA10, linewidth=1.2,
                   label="MA10"),
            Line2D([0], [0], color=COLOR_MA20, linewidth=1.2,
                   label="MA20"),
        ]
        self.kline_ax_price.legend(handles=legend_lines,
                                   loc="upper left",
                                   fontsize=7, framealpha=0.8)

        self.kline_fig.tight_layout()
        self.kline_canvas.draw()

    def _draw_ma(self, x: list, closes: list,
                 period: int, color: str):
        if len(closes) < period:
            return
        ma_values = []
        for i in range(len(closes)):
            if i < period - 1:
                ma_values.append(None)
            else:
                ma_values.append(
                    sum(closes[i - period + 1:i + 1]) / period)
        valid_x = [x[i] for i in range(len(ma_values))
                   if ma_values[i] is not None]
        valid_ma = [v for v in ma_values if v is not None]
        if valid_x:
            self.kline_ax_price.plot(valid_x, valid_ma,
                                     color=color,
                                     linewidth=1.0, alpha=0.85)

    # ── 事件 ──

    def _switch_period(self, period: str):
        if period == self.period or self._loading:
            return
        self.period = period
        self._fetch_kline()

    def _set_btn_state(self, state: str):
        for btn in self.period_btns.values():
            btn.config(state=state)
        self.refresh_btn.config(state=state)



class StockMonitorApp:
    """A 股行情监控 GUI（紧凑版）"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("行情")
        self.root.geometry("480x640")
        self.root.resizable(True, True)
        self.root.minsize(400, 500)
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

        # K线图嵌入相关（已移至独立 KlineWindow，不再使用）

        # 监控列表相关
        self.watchlist = []         # 当前监控列表 [{code, type, name, price}]
        self._watchlist_pending = 0 # 本轮未完成的查询线程数（用于复位）
        self._watchlist_batch = 0   # 当前刷新批次号，用于丢弃过期回调
        self._context_menu = None   # 右键菜单

        # 当前查询股票名称（用于 K线按钮）
        self._current_name = ""

        self._build_ui()
        self._refresh_watchlist()
        self._refresh_history_table()
        self._poll_queue()

    # ──────────────── UI 构建 ────────────────

    def _build_ui(self):
        self._build_input_area()
        self._build_search_results()
        self._build_watchlist()      # 监控列表，占据主体区域
        self._build_control_area()
        self._build_bottom_area()
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

        self.add_watch_btn = tk.Button(
            input_frame, text="加入监控", font=FONT_BTN, width=8,
            bg="#E0E0E0", fg="#333", command=self._on_add_to_watch,
        )
        self.add_watch_btn.pack(side=tk.LEFT, padx=1)

    # ── 监控列表 ──

    def _build_watchlist(self):
        """监控列表：占据主体区域，始终可见"""
        self.watch_frame = tk.Frame(self.root, bg="#EFEFEF",
                                    relief=tk.SUNKEN, bd=1)
        self.watch_frame.pack(fill=tk.BOTH, expand=True,
                              padx=12, pady=(4, 4))

        # 标题行
        title_row = tk.Frame(self.watch_frame, bg="#EFEFEF")
        title_row.pack(fill=tk.X, padx=4, pady=(3, 1))

        tk.Label(title_row, text="监控列表（双击查询，右键打开K线/移除）：",
                 font=FONT_LABEL, bg="#EFEFEF", fg="#333"
                 ).pack(side=tk.LEFT, anchor=tk.W)

        self.watch_count_label = tk.Label(
            title_row, text="(0)", font=FONT_LABEL,
            bg="#EFEFEF", fg="#888",
        )
        self.watch_count_label.pack(side=tk.RIGHT, padx=4)

        # Treeview 容器
        tree_frame = tk.Frame(self.watch_frame, bg="#EFEFEF")
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        columns = ("code", "type", "name", "price", "change", "time")
        self.watch_tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings",
        )

        watch_col_defs = {
            "code":   ("代码",  60, tk.CENTER),
            "type":   ("类型",  40, tk.CENTER),
            "name":   ("名称",  110, tk.W),
            "price":  ("最新价", 60, tk.E),
            "change": ("涨跌",  60, tk.E),
            "time":   ("时间",  100, tk.CENTER),
        }
        for col_id, (heading, width, anchor) in watch_col_defs.items():
            self.watch_tree.heading(col_id, text=heading)
            self.watch_tree.column(col_id, width=width, anchor=anchor)

        # 空列表时的占位提示
        self.watch_placeholder = tk.Label(
            tree_frame, text="暂无监控股票，请先加入。",
            font=("微软雅黑", 9), bg="#EFEFEF", fg="#999",
            anchor=tk.CENTER,
        )

        # 双击查询
        self.watch_tree.bind("<Double-Button-1>", self._on_select_watch)
        # 右键菜单
        self.watch_tree.bind("<Button-3>", self._on_watch_right_click)

    def _on_watch_right_click(self, event):
        """监控列表右键：弹出菜单，移除股票"""
        row = self.watch_tree.identify_row(event.y)
        if not row:
            return
        self.watch_tree.selection_set(row)
        if self._context_menu is None:
            self._context_menu = tk.Menu(self.root, tearoff=False)
            self._context_menu.add_command(
                label="打开K线窗口",
                command=self._on_watch_open_kline,
            )
            self._context_menu.add_command(
                label="移除",
                command=self._on_remove_from_watch,
            )
        self._context_menu.tk_popup(event.x_root, event.y_root)

    def _on_remove_from_watch(self):
        """从监控列表移除当前选中的股票"""
        sel = self.watch_tree.selection()
        if not sel:
            return
        values = self.watch_tree.item(sel[0], "values")
        code = values[0]
        type_label = values[1]
        type_map = {"股票": "stock", "指数": "index", "ETF": "etf"}
        stock_type = type_map.get(type_label, "stock")

        remove_from_watchlist(code, stock_type)
        self._refresh_watchlist()
        self.status_var.set(f"已移除 {code}")

    def _on_select_watch(self, event=None):
        """监控列表双击：查询该股票（同查询行为）"""
        sel = self.watch_tree.selection()
        if not sel:
            return
        values = self.watch_tree.item(sel[0], "values")
        code = values[0]
        type_label = values[1]
        type_map = {"股票": "stock", "指数": "index", "ETF": "etf"}
        self.type_var.set(type_label)
        self.code_entry.delete(0, tk.END)
        self.code_entry.insert(0, code)
        self.code_entry.focus()
        self.status_var.set(f"查询 {code} ...")
        self._on_query()

    def _on_add_to_watch(self):
        """按钮：将当前输入框中的代码加入监控列表"""
        code = self.code_entry.get().strip()
        if not code:
            messagebox.showwarning("提示", "请输入股票代码")
            return
        if not code.isdigit():
            messagebox.showwarning("提示", "代码必须为纯数字")
            return
        type_map = {"股票": "stock", "指数": "index", "ETF": "etf"}
        stock_type = type_map.get(self.type_var.get(), "stock")
        if is_in_watchlist(code, stock_type):
            messagebox.showinfo("提示", f"{code} 已在监控列表中")
            return
        # 先查询一次以获取名称
        data = query_stock(code, stock_type)
        if data["error"]:
            messagebox.showwarning("提示", f"查询失败：{data['error']}")
            return
        if add_to_watchlist(code, stock_type, data["name"], data["price"]):
            self._refresh_watchlist()
            self._refresh_watchlist_data()
            self.status_var.set(f"已加入监控：{data['name']}")
        else:
            messagebox.showinfo("提示", f"{code} 已在监控列表中")

    def _refresh_watchlist(self):
        """重新加载并渲染监控列表（有内容时显示面板）"""
        for item in self.watch_tree.get_children():
            self.watch_tree.delete(item)
        self.watchlist = load_watchlist()
        self.watch_count_label.config(text=f"({len(self.watchlist)})")
        for item in self.watchlist:
            type_label = _TYPE_LABEL.get(item.get("type", ""), "股票")
            price = item.get("price", 0.0)
            self.watch_tree.insert("", tk.END, values=(
                item.get("code", ""),
                type_label,
                item.get("name", ""),
                f"{price:.2f}" if price else "--",
                "--",
                "--",
            ))
        # 面板始终显示；用 Treeview 与占位提示切换
        if self.watchlist:
            self.watch_placeholder.pack_forget()
            self.watch_tree.pack(fill=tk.BOTH, expand=True)
        else:
            self.watch_tree.pack_forget()
            self.watch_placeholder.pack(fill=tk.BOTH, expand=True)

    def _refresh_watchlist_data(self):
        """异步刷新监控列表中每只股票的最新数据"""
        if not self.watchlist:
            return
        # 递增批次号，让旧批次的回调能够自我丢弃
        self._watchlist_batch += 1
        batch = self._watchlist_batch
        self._watchlist_pending = len(self.watchlist)
        self.status_var.set(f"刷新监控列表（{self._watchlist_pending}只）...")
        for item in self.watchlist:
            threading.Thread(
                target=self._watchlist_fetch_thread,
                args=(batch, item["code"], item.get("type", "stock")),
                daemon=True,
            ).start()

    def _watchlist_fetch_thread(self, batch, code, stock_type):
        """后台线程：获取监控列表中一只股票的行情"""
        data = query_stock(code, stock_type)
        self.data_queue.put({"_watchlist_refresh": True,
                             "_batch": batch,
                             "code": code,
                             "type": stock_type,
                             "data": data})

    def _update_watchlist_display(self, msg):
        """监控列表数据更新：更新 Treeview 行"""
        # 丢弃过期批次的回调
        if msg.get("_batch", 0) != self._watchlist_batch:
            return

        code = msg["code"]
        stock_type = msg["type"]
        data = msg["data"]

        type_label = _TYPE_LABEL.get(stock_type, "股票")
        for item_id in self.watch_tree.get_children():
            values = self.watch_tree.item(item_id, "values")
            if values[0] == code:
                if data["error"]:
                    self.watch_tree.item(item_id, values=(
                        code, type_label, "--", "--", "--", "--",
                    ))
                else:
                    price = data["price"]
                    change = data["change_percent"]
                    name = data.get("name", "")
                    time_str = f"{data.get('time', '--')}"
                    if price > data.get("yesterday_close", 0):
                        change_str = f"{change:+.2f}%"
                    elif price < data.get("yesterday_close", 0):
                        change_str = f"{change:+.2f}%"
                    else:
                        change_str = f"{change:.2f}%"
                    self.watch_tree.item(item_id, values=(
                        code, type_label, name,
                        f"{price:.2f}",
                        change_str,
                        time_str,
                    ))
                    # 同步持久化名称和价格
                    for wi in self.watchlist:
                        if wi.get("code") == code and wi.get("type") == stock_type:
                            wi["name"] = name
                            wi["price"] = price
                            break
                    # 若当前正在查询的就是这只，同步详情
                    if self.current_code == code and self.current_type == stock_type:
                        self._update_display(msg)
                break
        # 一个线程完成，计数递减；归零时表示本轮全部完成
        self._watchlist_pending -= 1
        if self._watchlist_pending <= 0:
            self._watchlist_pending = 0
            self.status_var.set(
                f"监控列表：{len(self.watchlist)} 只股票")

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

        # K线窗口按钮
        self.kline_btn = tk.Button(
            ctrl_frame, text="K线", font=FONT_BTN, width=6,
            bg="#E0E0E0", fg="#333", command=self._on_open_kline,
        )
        self.kline_btn.pack(side=tk.LEFT, padx=4)

    # ── 底部区域（历史记录） ──

    def _build_bottom_area(self):
        """底部：历史记录"""
        self.bottom_notebook = ttk.Notebook(self.root)
        self.bottom_notebook.pack(fill=tk.BOTH, expand=True, padx=12, pady=(2, 0))

        # ── Tab: 历史记录 ──
        history_frame = tk.Frame(self.bottom_notebook, bg=COLOR_BG)
        self.bottom_notebook.add(history_frame, text="历史")
        self._build_history_tab(history_frame)

    def _build_history_tab(self, parent):
        """构建历史记录 tab"""
        tk.Label(parent, text="查询历史（双击重查）：",
                 font=FONT_LABEL, bg=COLOR_BG, fg="#333"
                 ).pack(anchor=tk.W, padx=4, pady=(4, 0))

        tk.Button(parent, text="清空", font=FONT_BTN,
                  bg="#E0E0E0", fg="#333", command=self._on_clear_history
                  ).pack(anchor=tk.E, padx=4, pady=(0, 2))

        tree_frame = tk.Frame(parent, bg=COLOR_BG)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        columns = ("code", "type", "name", "price", "change", "time")
        self.history_tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings",
            height=5, yscrollcommand=scrollbar.set,
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

        self.history_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
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

    # ──────────────── K线窗口 ────────────────

    def _on_open_kline(self):
        """打开当前查询股票的 K线窗口"""
        if not self.current_code:
            messagebox.showinfo("提示", "请先查询一只股票后再打开 K线窗口")
            return
        self._open_kline_window(
            self.current_code, self.current_type, self._current_name)

    def _on_watch_open_kline(self):
        """监控列表右键：打开选中股票的 K线窗口"""
        sel = self.watch_tree.selection()
        if not sel:
            return
        values = self.watch_tree.item(sel[0], "values")
        code = values[0]
        type_label = values[1]
        name = values[2] if values[2] not in (None, "--") else code
        type_map = {"股票": "stock", "指数": "index", "ETF": "etf"}
        stock_type = type_map.get(type_label, "stock")
        self._open_kline_window(code, stock_type, name)

    def _open_kline_window(self, code, stock_type, name=""):
        """创建/打开指定股票的 K线窗口（单例：同类只开一个）"""
        # 若已有，则聚焦
        if hasattr(self, "_kline_windows"):
            key = (code, stock_type)
            if key in self._kline_windows and not self._kline_windows[key].win.winfo_destroyed():
                self._kline_windows[key].win.lift()
                return
        else:
            self._kline_windows = {}
        kw = KlineWindow(code, stock_type, name)
        self._kline_windows[(code, stock_type)] = kw

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
                elif msg.get("_watchlist_refresh"):
                    self._update_watchlist_display(msg)
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
        # 同时刷新监控列表
        self._refresh_watchlist_data()
        self.after_id = self.root.after(
            self.refresh_interval * 1000, self._schedule_refresh)

    # ──────────────── 界面更新 ────────────────

    def _update_display(self, msg):
        self._enable_buttons()
        data = msg["data"]
        code = msg["code"]
        stock_type = msg["type"]

        if data["error"]:
            self.status_var.set(data["error"])
            return

        # 保存历史
        try:
            save_query(code, stock_type, data)
            self._refresh_history_table()
        except Exception:
            pass

        # 记录当前查询名称（供 K线按钮使用）
        self._current_name = data["name"]

        # 若当前股票在监控列表中，也更新列表
        for item in self.watchlist:
            if item.get("code") == code and item.get("type") == stock_type:
                item["name"] = data["name"]
                item["price"] = data["price"]
                break

        # 同步刷新监控列表显示
        if code in {w.get("code") for w in self.watchlist}:
            self._refresh_watchlist()
            self._refresh_watchlist_data()

        self.status_var.set(
            f"{data['name']}  {data['price']:.2f}  {data['change_percent']:+.2f}%  {data['time']}")

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
