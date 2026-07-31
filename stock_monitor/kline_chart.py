"""
K线图显示模块
============
使用 matplotlib 绘制股票 K 线图（蜡烛图），嵌入 tkinter 窗口。

依赖：matplotlib（可选安装，缺失时弹窗提示）
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading

# ──────────────── 可选依赖：matplotlib ────────────────
try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import (
        FigureCanvasTkAgg, NavigationToolbar2Tk
    )
    from matplotlib.lines import Line2D
    from matplotlib.patches import Rectangle
    import matplotlib.dates as mdates
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

from stock_monitor.stock_api import query_kline_data

# ──────────────── 配色（与主窗口保持一致） ────────────────
COLOR_UP = "#C00000"
COLOR_DOWN = "#005900"
COLOR_BG = "#F5F5F5"
COLOR_MA5 = "#FF8C00"      # MA5 — 橙色
COLOR_MA10 = "#4169E1"     # MA10 — 蓝色
COLOR_MA20 = "#8B008B"     # MA20 — 紫色
COLOR_VOLUME_UP = "#FFCCCC"
COLOR_VOLUME_DOWN = "#CCE0CC"
COLOR_GRID = "#F0F0F0"

# 周期参数
PERIOD_OPTIONS = [
    {"key": "daily",   "label": "日K", "count": 120},
    {"key": "weekly",  "label": "周K", "count": 80},
    {"key": "monthly", "label": "月K", "count": 60},
]


class KlineChartWindow:
    """K线图窗口（matplotlib 嵌入 tkinter）"""

    def __init__(self, parent: tk.Widget, code: str,
                 stock_type: str = "stock", stock_name: str = "--"):
        if not MATPLOTLIB_AVAILABLE:
            messagebox.showerror(
                "缺少依赖",
                "请先安装 matplotlib：\n\n"
                "pip install matplotlib\n\n"
                "安装后重新运行程序。"
            )
            return

        self.parent = parent
        self.code = code
        self.stock_type = stock_type
        self.stock_name = stock_name
        self.current_period = "daily"
        self.klines = []
        self._loading = False

        # 创建窗口
        self.window = tk.Toplevel(parent)
        self.window.title(f"K线图 - {stock_name} ({code})")
        self.window.geometry("820x540")
        self.window.configure(bg=COLOR_BG)
        self.window.minsize(600, 400)

        self._build_ui()
        self._fetch_and_draw()

    # ──────────────── UI 构建 ────────────────

    def _build_ui(self):
        # 顶部工具栏
        toolbar = tk.Frame(self.window, bg=COLOR_BG)
        toolbar.pack(fill=tk.X, padx=10, pady=(8, 4))

        title_text = f"{self.stock_name} ({self.code})"
        self.title_label = tk.Label(
            toolbar, text=title_text,
            font=("微软雅黑", 12, "bold"), bg=COLOR_BG, fg="#333",
        )
        self.title_label.pack(side=tk.LEFT, padx=(0, 10))

        # 周期切换按钮
        self.period_btns = {}
        btn_style = {"font": ("微软雅黑", 9), "width": 5,
                     "bg": "#E0E0E0", "fg": "#333", "relief": tk.RAISED}
        for opt in PERIOD_OPTIONS:
            btn = tk.Button(
                toolbar, text=opt["label"], **btn_style,
                command=lambda p=opt["key"]: self._switch_period(p),
            )
            btn.pack(side=tk.LEFT, padx=2)
            self.period_btns[opt["key"]] = btn

        # 刷新按钮
        self.refresh_btn = tk.Button(
            toolbar, text="刷新", font=("微软雅黑", 9), width=5,
            bg="#E0E0E0", fg="#333", command=self._fetch_and_draw,
        )
        self.refresh_btn.pack(side=tk.LEFT, padx=10)

        # 状态文字
        self.status_label = tk.Label(
            toolbar, text="", font=("微软雅黑", 8),
            bg=COLOR_BG, fg="#888",
        )
        self.status_label.pack(side=tk.LEFT, padx=4)

        # 关闭按钮
        tk.Button(
            toolbar, text="关闭", font=("微软雅黑", 9), width=5,
            bg="#E0E0E0", fg="#333",
            command=self.window.destroy,
        ).pack(side=tk.RIGHT, padx=4)

        # 图表区域
        self._setup_figure()

    def _setup_figure(self):
        """初始化 matplotlib 图表"""
        # 使用中文字体
        matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
        matplotlib.rcParams["axes.unicode_minus"] = False

        self.fig = Figure(figsize=(10, 5.5), dpi=100, facecolor="white")
        self.fig.subplots_adjust(left=0.06, right=0.97, bottom=0.08,
                                 top=0.96, hspace=0.12)

        # 价格图（上）
        self.ax_price = self.fig.add_subplot(3, 1, (1, 2))
        # 成交量图（下）
        self.ax_volume = self.fig.add_subplot(3, 1, 3, sharex=self.ax_price)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.window)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True,
                                         padx=8, pady=(0, 8))

    # ──────────────── 数据获取 ────────────────

    def _switch_period(self, period: str):
        if period == self.current_period or self._loading:
            return
        self.current_period = period
        self._fetch_and_draw()

    def _fetch_and_draw(self):
        if self._loading:
            return
        self._loading = True
        self._set_buttons_state(tk.DISABLED)
        self.status_label.config(text="加载中...")
        threading.Thread(target=self._fetch_thread, daemon=True).start()

    def _fetch_thread(self):
        count = 120
        for opt in PERIOD_OPTIONS:
            if opt["key"] == self.current_period:
                count = opt["count"]
                break
        result = query_kline_data(self.code, self.stock_type,
                                  period=self.current_period, count=count)
        self.window.after(0, self._on_data_fetched, result)

    def _on_data_fetched(self, result):
        self._loading = False
        self._set_buttons_state(tk.NORMAL)

        if result["error"]:
            self.status_label.config(text="")
            messagebox.showerror("查询失败", result["error"],
                                 parent=self.window)
            return

        self.klines = result["klines"]
        name = result.get("name", self.stock_name)
        self.stock_name = name
        self.window.title(f"K线图 - {name} ({self.code})")
        self.title_label.config(text=f"{name} ({self.code})")
        self.status_label.config(text="")
        self._draw_chart()

    # ──────────────── 绘制图表 ────────────────

    def _draw_chart(self):
        """绘制蜡烛图 + 均线 + 成交量"""
        klines = self.klines
        if len(klines) < 2:
            messagebox.showinfo("提示", "K线数据不足，无法绘制",
                                parent=self.window)
            return

        # 清空坐标轴
        self.ax_price.clear()
        self.ax_volume.clear()

        # 准备数据
        dates = [k["date"] for k in klines]
        x = list(range(len(klines)))  # 用索引作为 x 轴（避免日期空隙）
        opens = [k["open"] for k in klines]
        closes = [k["close"] for k in klines]
        highs = [k["high"] for k in klines]
        lows = [k["low"] for k in klines]
        volumes = [k["volume"] for k in klines]

        # ── 绘制蜡烛线 ──
        # 蜡烛宽度
        candle_width = 0.6
        for i in range(len(klines)):
            color = COLOR_UP if closes[i] >= opens[i] else COLOR_DOWN
            # 影线（最高-最低）
            self.ax_price.plot([x[i], x[i]], [lows[i], highs[i]],
                               color=color, linewidth=1)
            # 实体（开盘-收盘）
            bottom = min(opens[i], closes[i])
            height = abs(closes[i] - opens[i]) or 0.01  # 一字板时给最小高度
            rect = Rectangle((x[i] - candle_width / 2, bottom),
                             candle_width, height,
                             facecolor=color, edgecolor=color)
            self.ax_price.add_patch(rect)

        # ── 绘制均线 ──
        self._draw_ma(x, closes, 5, COLOR_MA5, "MA5")
        self._draw_ma(x, closes, 10, COLOR_MA10, "MA10")
        self._draw_ma(x, closes, 20, COLOR_MA20, "MA20")

        # ── 价格图样式 ──
        self.ax_price.set_title(
            f"{self.stock_name} ({self.code})  —  "
            f"{[p for p in PERIOD_OPTIONS if p['key'] == self.current_period][0]['label']}",
            fontsize=11, fontweight="bold", pad=6,
        )
        self.ax_price.set_ylabel("价格", fontsize=9)
        self.ax_price.grid(True, color=COLOR_GRID, linewidth=0.5)
        self.ax_price.set_xlim(-1, len(x))
        self.ax_price.tick_params(labelsize=8)
        # 隐藏 x 轴标签（给成交量图用）
        self.ax_price.tick_params(labelbottom=False)

        # ── 绘制成交量 ──
        max_vol = max(volumes) if volumes else 1
        vol_width = 0.6
        for i in range(len(klines)):
            color = COLOR_VOLUME_UP if closes[i] >= opens[i] else COLOR_VOLUME_DOWN
            self.ax_volume.bar(x[i], volumes[i] / max_vol,
                               width=vol_width, color=color, edgecolor=color)

        # ── 成交量图样式 ──
        self.ax_volume.set_ylabel("成交量", fontsize=9)
        self.ax_volume.grid(True, color=COLOR_GRID, linewidth=0.5)
        self.ax_volume.set_xlim(-1, len(x))
        self.ax_volume.tick_params(labelsize=8)

        # x 轴标签：显示日期（每隔 N 个显示一个）
        n = max(1, len(dates) // 10)
        visible_dates = []
        visible_ticks = []
        for i in range(0, len(dates), n):
            visible_ticks.append(i)
            # 显示短日期格式 MM-DD
            d = dates[i]
            visible_dates.append(d[-5:] if len(d) > 5 else d)
        self.ax_volume.set_xticks(visible_ticks)
        self.ax_volume.set_xticklabels(visible_dates, fontsize=7)

        # ── 图例 ──
        legend_lines = [
            Line2D([0], [0], color=COLOR_MA5, linewidth=1.5, label="MA5"),
            Line2D([0], [0], color=COLOR_MA10, linewidth=1.5, label="MA10"),
            Line2D([0], [0], color=COLOR_MA20, linewidth=1.5, label="MA20"),
        ]
        self.ax_price.legend(handles=legend_lines, loc="upper left",
                             fontsize=8, framealpha=0.8)

        # 刷新画布
        self.fig.tight_layout()
        self.canvas.draw()

    def _draw_ma(self, x: list, closes: list, period: int,
                 color: str, label: str):
        """绘制移动平均线"""
        if len(closes) < period:
            return
        ma_values = []
        for i in range(len(closes)):
            if i < period - 1:
                ma_values.append(None)
            else:
                ma_values.append(sum(closes[i - period + 1:i + 1]) / period)
        # 过滤 None
        valid_x = [x[i] for i in range(len(ma_values))
                   if ma_values[i] is not None]
        valid_ma = [v for v in ma_values if v is not None]
        if valid_x:
            self.ax_price.plot(valid_x, valid_ma, color=color,
                               linewidth=1.2, label=label, alpha=0.85)

    # ──────────────── 辅助 ────────────────

    def _set_buttons_state(self, state: str):
        for btn in self.period_btns.values():
            btn.config(state=state)
        self.refresh_btn.config(state=state)

    def focus(self):
        """将窗口提到前台"""
        if hasattr(self, "window") and self.window:
            self.window.lift()
            self.window.focus_force()