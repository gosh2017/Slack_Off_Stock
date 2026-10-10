# 📈 A 股实时行情查询工具（摸鱼版）

一个基于 Python `tkinter` 的桌面 GUI 程序，用于实时查询并显示 A 股股票、指数和场内 ETF 的行情数据，同时支持**多股票监控列表**和 **K线图**分析。

窗口紧凑、颜色低调，适合在办公环境中隐蔽使用。

## 功能特性

- ✅ **多股票监控列表** — 主界面以列表形式同时监控多只股票，实时显示最新价和涨跌幅
- ✅ **股票名称搜索** — 输入名称（如"浦发"）按回车或点击「名称搜索」，从结果中双击即可查询并加入监控
- ✅ **支持三种查询类型** — 股票 / 指数 / 场内 ETF 自由切换
- ✅ **代码输入** — 输入代码即可查询
- ✅ **加入监控** — 输入代码后点击「加入监控」，将该股票加入监控列表
- ✅ **监控列表操作** — 双击列表项可切换到该股票；右键菜单支持「打开K线窗口」和「移除」
- ✅ **自动刷新** — 点击「开始刷新」后，按设定的时间间隔（默认 10 秒，可自定义 5~600 秒）**自动刷新监控列表中所有股票**
- ✅ **涨跌颜色** — 深红显示上涨，深绿显示下跌，低调不扎眼
- ✅ **K线图** — 独立窗口显示蜡烛图 + MA5/MA10/MA20 均线 + 成交量柱 + 十字光标 + 缩放平移，支持切换日K/周K/月K。可选 HQChart 渲染（交互体验更好），未配置时自动回退到内置 matplotlib 版本
- ✅ **悬浮窗** — 独立小窗口置顶显示当前查询股票的股价和涨跌幅，默认在桌面右下角，每 30 秒自动刷新，可拖拽移动
- ✅ **查询历史** — 底部「历史」标签页记录查询过的股票，双击可快速重新查询，支持清空
- ✅ **窗口置顶** — 可选置顶显示，方便盯盘
- ✅ **后台线程** — 数据获取在后台执行，界面不卡顿
- ✅ **错误处理** — 网络异常、无效代码等均有友好提示

## 环境要求

- **Python 3.6+**
- 操作系统：Windows / macOS / Linux

## 安装依赖

```bash
pip install requests matplotlib
```

> `tkinter` 是 Python 标准库，通常无需额外安装。
> `matplotlib` 仅用于 K线图功能，如果不需要可跳过安装。
> 如果 Linux 下提示 `ModuleNotFoundError: No module named 'tkinter'`，请执行：
> - Ubuntu/Debian：`sudo apt install python3-tk`
> - CentOS/RHEL：`sudo yum install python3-tkinter`

### 可选：HQChart 渲染 K线图

K线图默认为内置 matplotlib 版本。如需更专业的交互体验（十字光标、鼠标缩放平移、
副图指标等），可改用 HQChart 渲染：

```bash
pip install -U pywebview
```

HQChart 开源版（Apache 2.0）的三个 JS 文件已随仓库分发在
`stock_monitor/assets/hqchart/hqchart/` 下，**不需要额外下载**，开源版也无需授权码。
版权归属和许可证见根目录的 `LICENSE` / `NOTICE`。

> pywebview 需要 **5.x** 版本（本项目用 `create_window(js_api=...)` 暴露 JS 接口，
> 与旧版的 `@webview.expose` 写法不通用）。版本不匹配时会自动回退到
> matplotlib 版本并提示升级。

> Windows 需 Edge WebView2 运行时（Windows 11 自带）。
> 不满足条件时自动回退到 matplotlib 版本，程序不会崩溃。

详细说明见 `stock_monitor/assets/hqchart/README.md`。

## 运行程序

```bash
cd stock_monitor
python main.py
```

或在项目根目录下执行：

```bash
python -m stock_monitor.main
```

## 打包发布（Windows）

```bash
pip install pyinstaller
python build_exe.py
```

产物是单文件 `dist/stock_monitor.exe`，直接拷到其他机器即可运行。
目标机器需要 Python 之外的两样东西：**Edge WebView2 运行时**（Windows 11 自带）
和联网访问新浪接口的网络。体积约 130MB（Python 运行时 + matplotlib + HQChart 的 JS）。

### 为什么必须用 build_exe.py，不能自己敲 PyInstaller 命令

K线窗口是**独立子进程**（`stock_monitor/hqchart_chart.py`）。开发环境用
`python -m stock_monitor.hqchart_chart` 拉起它，但打包后 `sys.executable`
就是 exe 本身，`-m` 不再有效。所以打包模式下改成用**同一个 exe 加参数**
区分入口：

```
开发环境：  python -m stock_monitor.hqchart_chart 600000 stock 浦发银行
打包环境：  stock_monitor.exe --hqchart-chart 600000 stock 浦发银行
```

argv 的拼装逻辑在 `stock_monitor/hqchart_bridge.py` 的
`build_chart_command()`，入口分发在 `stock_monitor/main.py`。

另外必须把 `assets/` 一起打进去（`--add-data`），**漏了的话程序不会报错，
只会静默回退成 matplotlib 版 K线窗口**。`build_exe.py` 已经处理好了这两点。

### 打包版的两个已知限制

1. **查询历史 / 监控列表不会持久化。** `history_manager.py` 和
   `watchlist_manager.py` 用 `__file__` 定位 `config/`，打包后指向解包用的
   临时目录，退出即清空。要让它们生效，需要把这两个文件的 config 路径改成
   `sys.executable` 所在目录。
2. **onefile 每次启动先解包到临时目录。** 点开 K线窗口相当于再启动一个自身
   进程（会再解一次包，约 1 秒）。想避免这个开销可改用 `--onedir`，
   但产物会变成一个目录。

打包后的 K线窗口如果打不开，先看 `is_available()` 是否返回 False——
说明 `assets/` 没被打进去，而不是其他原因。

## 第三方依赖与授权

### HQChart（Apache 2.0）

K线图由 **HQChart 开源版**渲染：

- 原始仓库：<https://github.com/jones2000/HQChart>
- 作者：jones <jones_2000@163.com>，Copyright (c) 2018 jones
- 许可证：Apache License 2.0，正文见根目录 [LICENSE](LICENSE)，归属声明见 [NOTICE](NOTICE)
- 本项目用到的文件：`stock_monitor/assets/hqchart/hqchart/` 下的
  `umychart.js`、`umychart.complier.js`、`umychart.index.data.js`

三个 JS 文件**原样复制、未做任何修改**，文件头部的原始版权声明已保留。
本项目与 HQChart 之间的全部适配逻辑在
`stock_monitor/assets/hqchart/chart_bridge.js`，没有改动 `hqchart/` 下的任何一行。

再分发要求（Apache 2.0 第 4 条）：分发时必须随附 LICENSE 和 NOTICE。
`build_exe.py` 已经把这两个文件打进 `dist/stock_monitor.exe`。
如果只拷 `dist/stock_monitor.exe` 给别人，请把根目录的 LICENSE / NOTICE
一起放过去，或者用打包脚本重新构建。

### 其他第三方依赖

下面的许可证标识都从各自已安装包元数据里核过（`importlib.metadata`）。

| 依赖 | 用途 | 许可证 |
|------|------|--------|
| pywebview | 承载 HQChart 页面（Windows 上走 WebView2） | BSD-3-Clause |
| matplotlib | K线图回退方案 | BSD-like（matplotlib 自有许可协议，3-clause BSD 同源） |
| requests | 行情数据请求 | Apache-2.0 |
| Pillow | 悬浮窗图标绘制 | MIT-CMU |

这些库的许可证文本在各自的 `dist-info/licenses/` 下。`build_exe.py` 会把
这四个包的 LICENSE / NOTICE 收集进产物的 `third_party/` 目录，
所以分发 onefile 产物时许可证也是齐的。

## 使用说明

### 基本操作流程

1. 程序启动后，在输入框中输入**股票代码或名称**
2. 在下拉菜单中选择查询类型：**股票 / 指数 / ETF**
3. 按回车键：
   - 输入为纯数字（如 `600000`）→ 直接查询
   - 输入含文字（如 `浦发`）→ 自动名称搜索，双击结果即可查询
4. 点击「名称搜索」按钮可手动搜索

### 监控列表（核心功能）

5. 输入代码后点击「**加入监控**」，将该股票加入监控列表
6. 监控列表显示在界面中央，实时显示所有监控股票的最新价和涨跌幅
7. **双击**列表中的股票 → 切换到该股票并显示详情
8. **右键**列表中的股票 → 弹出菜单：
   - 「打开K线窗口」→ 弹出该股票的独立 K线图窗口
   - 「移除」→ 从监控列表中移除该股票
9. 点击「**开始刷新**」→ 程序按设定间隔（默认 10 秒）自动刷新监控列表中所有股票的数据
10. 点击「**停止刷新**」→ 暂停自动更新

### K线图

11. 在控制区点击「**K线**」按钮 → 弹出当前查询股票的独立 K线图窗口
12. 或右键监控列表中的股票 → 「打开K线窗口」
13. K线图支持切换 **日K / 周K / 月K**，显示 MA5/MA10/MA20 均线，可手动刷新

### 其他功能

14. 点击「**悬浮**」按钮打开独立置顶悬浮窗，显示当前查询股票的股价和涨跌幅，每 30 秒自动刷新，可拖拽到任意位置
15. 底部「**历史**」标签页记录每次查询，**双击历史行**可快速重新查询，点击「清空」可清空记录
16. 勾选「**置顶**」可使主窗口始终显示在最前面

> 💡 **小技巧**：不确定股票代码时，直接输入名称拼音或简称（如"茅台"、"平安"）搜索即可。将感兴趣的股票逐一加入监控列表，点击「开始刷新」即可自动跟踪所有股票。

### 支持的代码类型

| 类型 | 代码前缀 | 示例 |
|------|----------|------|
| **股票** — 上海主板 | 6xxxxx | 600000（浦发银行） |
| **股票** — 深圳主板 | 00xxxx | 000002（万科A） |
| **股票** — 创业板 | 30xxxx | 300750（宁德时代） |
| **股票** — 北交所 | 4xxxxx | 430090（同辉信息） |
| **指数** — 上证 | 000xxx | 000001（上证指数） |
| **指数** — 深证 | 399xxx | 399001（深证成指） |
| **ETF** — 沪市 | 51xxxx / 56xxxx | 510300（沪深300ETF） |
| **ETF** — 深市 | 159xxx | 159915（300ETF） |

> ⚠️ **注意**：`000001` 既是平安银行（股票）也是上证指数（指数），请在下拉菜单中选择对应类型。

## 数据来源

本程序使用 **新浪财经免费接口**（`https://hq.sinajs.cn/list=`）获取实时行情数据，使用 **新浪财经 K线接口**（`https://quotes.sina.cn/cn/api/`）获取 K线数据，无需申请 API Key。

## 项目结构

```
.
├── README.md                  # 本文件
├── Requirement.md             # 项目需求文档
├── build_exe.py               # Windows 打包脚本（PyInstaller onefile）
├── config/                    # 配置目录（自动生成，已加入 .gitignore）
│   ├── query_history.json     # 查询历史
│   └── watchlist.json         # 监控列表
├── stock_monitor/             # 主程序包
│   ├── main.py                # 程序入口
│   ├── gui.py                 # GUI 主界面（含监控列表、查询、K线独立窗口、悬浮窗等）
│   ├── kline_chart.py         # K线图模块（matplotlib 绘制，含 NavigationToolbar，当前为备用版本）
│   ├── hqchart_bridge.py      # HQChart K线窗口（主进程侧：拉进程、推数据、收事件）
│   ├── hqchart_chart.py       # HQChart K线窗口（子进程：pywebview 承载窗口）
│   ├── assets/
│   │   └── hqchart/           # K线网页资产（index.html / app.css / chart_bridge.js / README.md）
│   │       └── hqchart/       # HQChart 开源版 JS（Apache 2.0，已随仓库分发，见 NOTICE）
│   ├── stock_api.py           # 数据获取模块（新浪财经 API）
│   ├── stock_search.py        # 名称搜索模块
│   ├── history_manager.py     # 查询历史管理（JSON 持久化）
│   ├── watchlist_manager.py   # 监控列表管理（JSON 持久化）
│   ├── config.py              # 共享配置（请求头、超时等）
│   └── __init__.py            # 包入口
├── tests/                     # 单元测试（pytest）
│   ├── conftest.py            # 测试配置（将项目根目录加入 sys.path）
│   ├── test_stock_api.py      # 代码前缀 / 行情解析 / K线聚合 / 网络异常
│   ├── test_history_manager.py # 查询历史读写与去重
│   ├── test_watchlist_manager.py # 监控列表读写与去重
│   ├── test_hqchart_bridge.py # HQChart 数据适配 / 通信协议 / 事件分发
│   └── test_stock_search.py   # 名称搜索解析与过滤
├── pytest.ini                 # pytest 配置
└── .gitignore
```

## 运行测试

```bash
pip install pytest
pytest
```

测试全部使用 mock 替代真实网络请求，不会访问新浪接口，也不会读写 `config/` 下的真实数据文件。

## 常见问题

**Q: 查询结果显示「股票代码无效或不存在」？**
A: 请确认输入的股票代码是否正确，A 股代码为 6 位数字。

**Q: 数据一直显示「--」？**
A: 请检查网络连接是否正常，或尝试关闭防火墙/代理。

**Q: 非交易时间能查到数据吗？**
A: 可以，但价格数据为最近一个交易日的收盘数据，不会实时变动。

**Q: 如何同时监控多只股票？**
A: 逐一输入代码并点击「加入监控」，然后点击「开始刷新」即可自动刷新所有股票。

**Q: 监控列表的数据不更新怎么办？**
A: 确认网络连接正常，且已点击「开始刷新」。监控列表的刷新是异步的，每次刷新所有股票各自开一个线程，完成后自动更新。

**Q: K线窗口打不开或提示缺少依赖？**
A: K线默认使用 matplotlib 渲染（`pip install matplotlib`）。想换成 HQChart 渲染只需再装 pywebview（`pip install -U pywebview`），HQChart 的 JS 文件已随仓库分发，无需下载。详见 `stock_monitor/assets/hqchart/README.md`。条件不满足时会自动回退到 matplotlib 版本，不会崩溃。

**Q: 为什么 K线窗口要多起一个 Python 进程？**
A: HQChart 是纯 JavaScript 库，需要通过浏览器内核渲染。pywebview 的事件循环和 tkinter 的 `mainloop()` 都要占用主线程，无法共存于同一进程，所以 K线窗口在独立子进程里跑 pywebview，父子之间用 stdin/stdout 传 JSON。
