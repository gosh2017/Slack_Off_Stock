---
name: desktop-finance-monitor
description: 构建桌面实时行情监控 GUI 程序的设计原则、架构模式和实现技巧
---

# 桌面实时行情监控 GUI 构建指南

## 适用场景

构建类似 A 股实时行情查询工具的桌面 GUI 程序，需要满足以下特征的项目：

- 实时数据监控（股票、加密币、天气、IoT 等）
- 紧凑隐蔽的桌面界面（"摸鱼"场景）
- 定时自动刷新 + 后台线程
- 多目标列表监控
- 悬浮小窗预览
- 搜索 + 历史记录
- K线图（蜡烛图 + 均线）

## 核心架构模式

### 1. 分层结构

```
.
├── stock_monitor/
│   ├── main.py                # 程序入口
│   ├── gui.py                 # GUI 主界面（含 K线独立窗口实现）
│   ├── kline_chart.py         # K线图模块（备用版本，含 NavigationToolbar）
│   ├── stock_api.py           # 数据获取模块（新浪财经 API）
│   ├── stock_search.py        # 名称搜索模块
│   ├── history_manager.py     # 查询历史管理（JSON 持久化）
│   ├── watchlist_manager.py   # 监控列表管理（JSON 持久化）
│   ├── config.py              # 共享配置（请求头、超时等）
│   └── __init__.py            # 包入口
├── config/                    # 自动生成，已加入 .gitignore
│   ├── query_history.json     # 查询历史
│   └── watchlist.json         # 监控列表
├── README.md
└── Requirement.md
```

**关键原则**：`stock_api.py`、`stock_search.py`、`history_manager.py`、`watchlist_manager.py` 不 import `gui.py` 的任何东西，可独立测试。

### 2. 线程安全的数据流

```
GUI 按钮点击 → 后台线程 Thread(target=fetch, args=(code,))
    → fetch() 调用 API → 数据放入 queue.Queue
    → GUI 的 after(100) 循环轮询 queue
    → 取到数据后更新 Label / Treeview
```

**为什么不用锁？** `queue.Queue` 是线程安全的，天然适合生产者-消费者模式。Tkinter 的所有控件更新必须在主线程，`after()` 轮询是标准做法。

### 3. 紧凑布局策略

| 难度 | 手法 | 适用场景 |
|------|------|----------|
| ★☆☆ | 减小 padding / 缩小字体 / 去掉 emoji | 快速紧凑化 |
| ★★☆ | 用 `pack(fill=tk.X)` 代替 `grid()` | 简单垂直堆叠布局 |
| ★★☆ | 缩减窗口尺寸 + 去掉 `resizable` | 固定尺寸小窗 |
| ★★★ | 用 `place()` 绝对定位覆盖层 | 悬浮窗、角标、Toast |
| ★★★ | 用 `ttk.Treeview` 代替多个 Label | 表格化数据展示（紧凑且可滚动） |

### 4. 主窗口布局（多股票监控版）

```
┌─ 输入区 ─────────────────────────────────────────────────┐
│  代码/名称: [ 600000 ]  [股票]  [名称搜索] [查询] [加入监控] │
├─ 搜索结果（按需显示）──────────────────────────────────────┤
│  搜索结果（双击查询）...                                      │
├─ 监控列表（主体区域，占据最大空间）──────────────────────────┤
│  监控列表（双击查询，右键打开K线/移除）        (3)            │
│  ┌────────────────────────────────────────────────────┐   │
│  │ 代码    类型  名称        最新价  涨跌    时间      │   │
│  │ 600000  股票  浦发银行     18.88  +2.13%  14:30   │   │
│  │ 000001  指数  上证指数    2900.00  -0.52%  14:30   │   │
│  └────────────────────────────────────────────────────┘   │
├─ 控制区 ─────────────────────────────────────────────────┤
│  刷新: [60] [开始刷新] [停止刷新]  [置顶☐] [悬浮窗] [K线]    │
├─ 底部：历史 ─────────────────────────────────────────────┤
│  查询历史（双击重查）...                                  │
├─ 状态栏 ─────────────────────────────────────────────────┤
│  监控列表：3 只股票                                       │
└──────────────────────────────────────────────────────────┘
```

**核心思想**：监控列表作为主体区域（`pack(fill=tk.BOTH, expand=True)`），始终可见；输入区和控制区紧凑。

### 5. K线图表窗口（独立 Toplevel）

K线图作为独立窗口弹出（非嵌入标签页）：

```python
class KlineWindow:
    def __init__(self, code: str, stock_type: str, name: str = ""):
        self.win = tk.Toplevel()            # 独立窗口
        self.win.title(f"K线 — {name or code} ({code})")
        self.win.geometry("780x560")
        self._build_toolbar()               # 日K/周K/月K 按钮 + 刷新
        self._init_figure()                 # matplotlib Figure
```

**K线图实现要点**：
- 蜡烛线：`plot()` 画影线（最高-最低），`Rectangle` 画实体（开-收）
- 均线：移动平均线，MA5/MA10/MA20，用 `plot()` 绘制
- 成交量：`bar()` 画柱，按涨跌着色
- 周期切换：日K（120根）、周K（400根，日线聚合）、月K（800根，日线聚合）
- 由 `_schedule_refresh` 的 `after()` 回调驱动定时刷新（若启用）

### 6. 多股票监控列表架构

监控列表是一个完整的子模块，涉及 4 层交互：

```
┌─────────────────────────────────────────────────┐
│                用户操作层                        │
│  "加入监控" 按钮 / 右键菜单"打开K线"/"移除" / 双击查询 │
├─────────────────────────────────────────────────┤
│              watchlist_manager.py               │
│  load_watchlist / add_to_watchlist /            │
│  remove_from_watchlist / is_in_watchlist        │
│  → config/watchlist.json（JSON 数组）             │
├─────────────────────────────────────────────────┤
│               GUI 层（gui.py）                  │
│  _refresh_watchlist()          → 渲染 Treeview   │
│  _refresh_watchlist_data()     → 异步刷新所有     │
│  _watchlist_fetch_thread()     → 每只开一线程     │
│  _update_watchlist_display()   → 更新 Treeview行 │
│  _update_watchlist_row_sync()  → 同步更新单行     │
├─────────────────────────────────────────────────┤
│             定时调度层                           │
│  _on_start_refresh() → 启动 → _schedule_refresh() │
│  _schedule_refresh() → _refresh_watchlist_data() │
│                    → after(interval) → 递归       │
└─────────────────────────────────────────────────┘
```

**关键设计决策**：
- 监控列表数据存储在 `watchlist_manager.py`（JSON 持久化），重启后保留
- 列表非空时面板始终显示；空列表时显示占位提示"暂无监控股票，请先加入"
- 每只股票各自开一个线程查询，完成后通过 `_update_watchlist_display` 更新 Treeview 行
- **"开始刷新"按钮只刷新监控列表**，不依赖输入框中的代码
- 当前查询股票的行情会同步更新监控列表中对应的行（`_update_watchlist_row_sync`）

### 7. 接口适配层

不同数据源（股票、币、指数）的代码规则不同，不要硬编码。用一个适配层来隔离：

```python
def _build_code(raw_code: str, query_type: str) -> str:
    """根据类型和前缀规则，返回数据源所需的完整代码"""
    if query_type == "index":
        return INDEX_MAP.get(raw_code, f"sh{raw_code}")
    elif query_type == "etf":
        return f"sh{raw_code}" if raw_code[:2] in ("51", "56") else f"sz{raw_code}"
    else:
        return EXCHANGE_MAP.get(raw_code[:1], f"sh{raw_code}")
```

## 摸鱼界面设计原则

### 配色

- **不要用纯红/纯绿**：`#C00000` / `#005900` 比 `#FF0000` / `#00FF00` 低调得多
- **按钮统一灰色**：`bg="#E0E0E0", fg="#333"`，不扎眼
- **背景用浅灰**：`#F5F5F5`，接近 Office 默认底色
- **等宽字体**：`Consolas` 显示数字更整齐，看起来像 Excel

### 布局

- 小窗口（400~500px）、`resizable(True, True)` 允许调整大小
- 标题栏去掉 emoji，只保留文字
- 主数据区用 `ttk.Treeview` 表格化展示
- 底部状态栏精简（只保留必要信息）

### 悬浮窗

- 用 `Toplevel` + `overrideredirect(True)` 实现无边框窗口
- 用 `wm_attributes("-transparentcolor", color)` 实现背景透明
- 用 `wm_attributes("-topmost", True)` 实现置顶
- 拖拽绑定：`<ButtonPress-1>` + `<B1-Motion>`，用 `winfo_pointerx/y` 计算偏移
- 独立刷新周期（60s），不受主窗口刷新影响

## 监控列表自动刷新机制

**执行流**（点击"开始刷新"后）：

```
_on_start_refresh()
  → 检查 watchlist 非空，否则弹窗提示
  → 读取 refresh_interval，auto_refresh = True
  → _schedule_refresh()
    → _refresh_watchlist_data()
      → 为每只股票开一个线程 → _watchlist_fetch_thread()
        → query_stock() → data_queue.put({"_watchlist_refresh": True, ...})
      → _poll_queue (100ms 轮询)
        → msg["_watchlist_refresh"] → _update_watchlist_display()
          → 直接更新 Treeview 行
          → _watchlist_pending -= 1；归零时复位状态栏
    → after(interval * 1000, _schedule_refresh)   ← 递归定时
```

**重要：不要使用批次号（batch id）过滤回调**

```
❌ 错误做法：每次刷新递增 batch，回调检查 batch 匹配，否则丢弃
   → 结果：旧批次的回调全部被丢弃，监控列表不更新

✅ 正确做法：直接更新 Treeview 行，新旧数据自然覆盖
   → 结果：所有股票都正常更新
```

## 常见坑点

### tkinter 篇

| 坑 | 原因 | 修复 |
|-----|------|------|
| `ttk.Treeview` 不支持 `font` 参数 | ttk 控件字体由主题控制 | 使用时去掉 `font=` 参数 |
| `winfo_pointerx()` 在鼠标离开窗口时报错 | 鼠标移出窗口时返回 -1 | 检查返回值 > 0 |
| Label 控件默认有 1px 边框 | 平台默认样式 | `borderwidth=0, highlightthickness=0` |
| `.after(10, fn)` 在窗口未渲染时无效 | 窗口还没准备好 | 用 `.after_idle(fn)` 代替 |
| `-transparentcolor` 让文字边缘有白边 | 抗锯齿像素非精确匹配，不被透掉 | 背景用 `#000000`（黑），文字用 `#FFFFFF`（白），边缘灰色像素不显眼 |
| `pack_forget` 后再 `pack` 面板位置错乱 | 用布尔标志 `_watchlist_visible` 管理显隐状态，避免 `winfo_ismapped()` 不稳定 | 手动追踪 pack 状态 |

### 多线程篇

| 坑 | 原因 | 修复 |
|-----|------|------|
| 在子线程中更新 Label 导致崩溃 | Tkinter 不是线程安全的 | 所有控件更新放在 `queue.Queue` 回调中，在主线程 `after()` 里处理 |
| 连续快速点击查询按钮 → 多个线程并发 | 无防重 | 查询前 `disable_buttons()`，回调后 `enable_buttons()` |
| 监控列表多只股票只更新一只 | 用 `batch` 过滤回调时，下一轮 refresh 的 batch 不匹配 | **不要过滤**，直接更新，新旧数据自然覆盖 |
| `_maybe_finish` 被多次调度（N只股票 → N个after） | 每个线程完成都调 `root.after`，第一个就复位了 `_watchlist_loading` | 用计数器 `_watchlist_pending`，每次完成 -1，归零才复位 |

### 数据层篇

| 坑 | 原因 | 修复 |
|-----|------|------|
| 新浪接口返回 GBK 编码 | 新浪旧系统 | `response.encoding = "gbk"` |
| 搜索接口返回非 A 股结果 | 搜索结果混入美股/港股 | 过滤 `type=11`（A 股） |
| `000001` 既是平安银行又是上证指数 | 代码冲突 | 用下拉菜单让用户选择类型，数据层根据类型决定前缀 |

### 持久化篇

| 坑 | 原因 | 修复 |
|-----|------|------|
| watchlist.json 不存在时报错 | 首次运行文件不存在 | `_load()` 捕获 `FileNotFoundError`，返回空列表 |
| JSON 文件损坏 | 写入中途崩溃 | 用 `try/except json.JSONDecodeError` |
| 同一代码重复添加 | 未去重 | `add_to_watchlist` 先查 `is_in_watchlist` |

## 技术栈选择树

```
需要行情监控 GUI？
├── 需要图形界面？
│   ├── 要跨平台、轻量、无需安装 → tkinter
│   ├── 要现代化、Web 风格 → Electron / Tauri
│   └── 要移动端 → Flutter / React Native
├── 需要实时更新？
│   ├── 数据变化慢（5秒+）→ 定时轮询 (after + thread)
│   ├── 数据变化快（1秒内）→ WebSocket / SSE
│   └── 需要推送 → 用消息队列
├── 需要持久化？
│   ├── 简单 JSON 足够 → json.dump + load
│   ├── 需要查询 → SQLite
│   └── 需要分布式 → 服务端数据库
├── 需要多目标监控？
│   ├── 用 ttk.Treeview 表格展示 → 支持右键菜单、双击、排序
│   ├── 用 Listbox → 简单但功能有限
│   └── 用多 Label 堆叠 → 不推荐（难以管理）
├── 需要图表？
│   ├── 内嵌 → matplotlib FigureCanvasTkAgg
│   └── 独立窗口 → 更灵活，避免占用主窗口空间
└── 需要隐蔽使用？
    ├── 小窗口 + 灰色调 → 参考「摸鱼配色」
    ├── 悬浮小窗 → 置顶 Toplevel + 透明
    └── 快捷键隐藏 → 全局热键绑定 (keyboard 库)
```

## 成长路径

### 你可以从这个项目继续扩展

1. **增加数据源**：在 `stock_api.py` 中新增适配器（EastMoney、Tushare 等）
2. **增加市场**：在 `_build_code` 中增加美股、港股的代码规则
3. **增加通知**：接入系统通知（`plyer` 库）或钉钉/微信推送，价格突破阈值时告警
4. **增加图表**：用 `matplotlib` 嵌入分时图或增加更多技术指标（RSI、MACD）
5. **打包分发**：用 `PyInstaller` 打包成独立的 `.exe`，无需 Python 环境
6. **增加快捷键**：全局热键显示/隐藏窗口，快速切换代码
7. **增加策略回测**：基于历史数据实现简单的交易策略回测
8. **增加 WebSocket 实时推送**：从新浪轮询升级到 WebSocket 实时行情，减少刷新间隔
9. **优化 K线图**：整合 `kline_chart.py`（含 NavigationToolbar 的备用版本），增加鼠标交互缩放
