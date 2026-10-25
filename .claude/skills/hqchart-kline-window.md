---
name: hqchart-kline-window
description: 用 HQChart（JS 蜡烛图库）在 Python 桌面程序里渲染 K线图 — pywebview 承载、独立子进程 + JSON 行协议、双保险回退。涉及 HQChart/umychart、K线/蜡烛图、pywebview/WebView2、tkinter 共存的图表窗口时使用。
---

# 用 HQChart 渲染 K线图（嵌入 Python 桌面程序）

本项目的完整参考实现在 `stock_monitor/hqchart_bridge.py`、`hqchart_chart.py`、`assets/hqchart/`。本 skill 讲通用做法，改代码前先看那里。

## 架构：为什么要分两个进程

HQChart 是纯 JavaScript 库，没有 Python 版本，必须借一个浏览器内核。用 pywebview + WebView2 时有一个硬约束：

| 约束 | 后果 |
|------|------|
| `webview.start()` 和 tkinter `mainloop()` 都要占主线程 | **必须分进程**，同进程内没有共存办法 |
| WebView2 的窗口本质是浏览器窗口（F12 / 检查工具可用） | 观感与用途需知情接受（例如"低调/摸鱼"场景要权衡） |

因此：图表窗口在**子进程**里自成一体的事件循环，父子之间用 `stdin`/`stdout` 传 JSON 行。

```
主进程（tkinter）                      子进程（python -m pkg.hqchart_chart）
┌──────────────────────────┐  JSON/stdin  ┌──────────────────────────┐
│ ChartWindow              │ ───────────→ │ ChartApp (pywebview)     │
│  · 拉数据、推 payload    │              │  · index.html            │
│  · 收周期/刷新/关闭事件  │ ←─────────── │  · chart_bridge.js      │
└──────────────────────────┘  JSON/stdout └──────────────────────────┘
```

**数据获取留在父进程，子进程只负责渲染。** 好处：聚合逻辑、重试、缓存都在主进程里可单测，子进程保持"纯渲染"，两侧都不会因为对方不可用而崩。

## 目录布局

```
pkg/
├── hqchart_bridge.py     # 父进程侧：拉子进程、收发 JSON、生命周期
├── hqchart_chart.py      # 子进程侧：pywebview 窗口 + stdin 协议
├── main.py               # 入口分发（打包后靠参数区分，见下文）
└── assets/hqchart/
    ├── index.html        # 页面骨架（本项目）
    ├── app.css           # 页面样式（本项目；别叫 style.css）
    ├── chart_bridge.js   # ★ HQChart 适配层，所有版本相关调用都在这里
    ├── README.md         # 启用说明、缺失文件怎么办
    └── hqchart/          # 官方开源版文件（Apache 2.0）
        ├── umychart.js
        ├── umychart.complier.js
        ├── umychart.index.data.js
        └── NOTICE
```

- 官方文件放**子目录**，与本项目文件分开放；样式命名为 `app.css` 而不是 `style.css`，避免与 HQChart 自带文件混淆。
- 三个 JS 约 5MB，**直接入库**，不要塞进 `.gitignore`。否则别人 checkout 后没有文件，界面只会提示"未找到图表库"。注意 `.gitignore` 里的 `*.exe` 等通配规则不会误伤 `.js`，但要检查 `*.js`/`vendor` 之类的宽泛规则。
- 本项目对接**开源版**（<https://github.com/jones2000/HQChart>，Apache 2.0，无需授权）。商用授权版有额外的 `NetworkFilter` 之外的授权接口，不要混用。

`index.html` 里脚本加载**顺序不能换**：主库运行时用到后两个文件定义的全局对象（麦语言执行器 + 基础指标库）。

## 通信协议：JSON 行

编码：`encode_line(msg)` → `(json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")`

父 → 子：

```
{"cmd": "data", "payload": {...}}   推送一份 K 线数据
{"cmd": "focus"}                    请求把窗口提到前台
{"cmd": "close"}                    关闭窗口
```

子 → 父：

```
{"event": "ready"}                  窗口已创建
{"event": "chart_ready"}            适配层就绪，可以接收数据
{"event": "chart_error", "message"} 图表初始化或数据写入失败
{"event": "period", "period"}       用户切换了周期
{"event": "refresh"}                用户点了刷新
{"event": "close"}                  用户关闭了窗口
{"event": "error", "message"}       本进程发生未捕获异常
```

**走 `stdin.buffer` / `stdout.buffer` 并显式 UTF-8**，不要用 `print()`：Windows 控制台是 cp936，管道不经过它，中文会乱码。两侧都在线程里写管道（父进程多个窗口可能并发写），加锁。

`decode_line` 应当宽容：空行返回 `None`，非法 JSON 抛 `ValueError` 由调用方跳过 —— 管道里混进非预期字节时不该让整个读取循环崩掉。

## 就绪握手：最容易被忽略的一环

子进程刚起来时图表还没初始化，`evaluate_js("applyPayload(...)")` 会把数据推给一个**不存在的函数**，数据静默丢失。需要两段缓冲：

**Python 侧** —— `chart_ready` 之前只保留最新一份载荷：

```python
def _push_to_js(self, payload):
    if not self._ready_notified:
        self._pending_payload = payload      # 丢弃旧的，只留最新
        return
    try:
        self.window.evaluate_js("window.applyPayload(%s);" % json.dumps(payload, ensure_ascii=False))
    except Exception:
        self._pending_payload = payload      # 评估失败留待重试
```

收到 `chart_ready` 时冲刷积压载荷，并带超时（超时上报 `chart_error`，父进程才有机会回退）：

```python
# watchdog 线程
deadline = time.time() + READY_TIMEOUT
while time.time() < deadline and not self._closing:
    if self._ready_notified:
        return
    time.sleep(0.5)
if not self._ready_notified:
    _send({"event": "chart_error", "message": "图表初始化超时（%d 秒）" % READY_TIMEOUT})
```

**JS 侧** —— pywebview 在**页面加载完成后**才注入 `window.pywebview.api`，而脚本常在 `DOMContentLoaded` 就执行了，两者有竞态：早发的 `chart_ready` 直接丢弃，Python 只能干等到超时。所以桥接未就绪的事件要入队补发：

```javascript
var PENDING_EVENTS = [];
var BRIDGE_DEADLINE = Date.now() + 15000;
var bridgeTimer = null;

function bridgeApi() {
    try {
        var api = window.pywebview && window.pywebview.api;
        if (api && typeof api.notify === "function") return api;
    } catch (e) {}
    return null;
}

function notify(event, detail) {
    var api = bridgeApi();
    if (api) { api.notify(event, JSON.stringify(detail || {})); return; }
    PENDING_EVENTS.push({ event: event, detail: detail || {} });
    if (bridgeTimer) return;                 // 已在轮询
    bridgeTimer = setInterval(function () {  // 200ms 轮询，带上限
        var ready = bridgeApi();
        if (ready) {
            clearInterval(bridgeTimer); bridgeTimer = null;
            PENDING_EVENTS.splice(0, PENDING_EVENTS.length)
                .forEach(function (m) { ready.notify(m.event, JSON.stringify(m.detail)); });
            return;
        }
        if (Date.now() > BRIDGE_DEADLINE) {
            clearInterval(bridgeTimer); bridgeTimer = null;
            PENDING_EVENTS.length = 0;
        }
    }, 200);
}
```

## 双保险：任何前置条件不满足都回退

主界面不要只有一条渲染路径。检测**版本**和**文件**两件事，都不满足就静默退回 matplotlib 版本，子进程崩了也要兜住：

```python
def pywebview_compatible():
    """5.x 移除了 @webview.expose，改为 create_window(js_api=实例)。
    版本对不上时子进程会在导入阶段就崩（表现为窗口弹出瞬间又消失），
    所以拉起进程之前先在这里判掉。"""
    if not PYWEBVIEW_AVAILABLE:
        return False
    try:
        params = inspect.signature(webview.create_window).parameters
    except (TypeError, ValueError, AttributeError, NameError):
        return False
    return "js_api" in params and callable(getattr(webview, "start", None))

def is_available():
    return pywebview_compatible() and hqchart_assets_available()

def unavailable_reason():
    """给出可执行的动作，而不是一句"不可用"：
    缺 pywebview → "pip install pywebview"
    版本不对   → "pip install -U pywebview"
    缺库文件   → 说明应随仓库分发，并提示检查 checkout 是否完整、.gitignore 是否误排除"""
```

用**特征检测**（签名里有 `js_api` 吗）而不是版本号比较：`importlib.metadata.version()` 比 `>=5` 之类判断容易被打补丁的版本打破。

主界面侧的回退与清理事件：

```python
def _open_kline_window(self, code, stock_type, name=""):
    if not is_available():
        self._kline_windows[key] = MatplotlibKlineWindow(code, stock_type, name)
        return
    try:
        kw = ChartKlineWindow(self.root, code, stock_type, name)
    except Exception as e:
        self._kline_windows[key] = MatplotlibKlineWindow(code, stock_type, name)  # 启动失败也回退

    kw.on_crash  = lambda msg: self._on_backend_crash(code, stock_type, name, msg)  # 弹窗 + 回退
    kw.on_closed = lambda: self._kline_windows.pop(key, None)                        # 用户关窗，只清记录
```

两种后端暴露**同一套接口**（`alive()` / `activate()` / `close()` / `fetch(period)`），主界面就不需要知道用的是哪个后端。

## JS 适配层：把版本耦合关进一个文件

`chart_bridge.js` 是本项目与 HQChart 的**唯一接触面**。升级 HQChart 只改这一个文件，Python 侧不动。改动点固定为五处：

- `CONFIG` —— 周期 / 指标 / 显示配置
- `toHqchartSymbol()` —— 本项目代码 → HQChart 代码（带市场后缀）
- `buildOption()` —— 配置项键名
- `makeNetworkFilter()` —— 数据注入方式
- `makeChart()` —— 实例化入口

```javascript
// 本地数据，不让库去请求它内置的行情接口
function makeNetworkFilter(symbol, rows, name) {
    return function (req, callback) {
        req.PreventDefault = true;            // 必须同步设置，库靠它判断"上层已接管数据源"
        if (req.Name === "KLineChartContainer::RequestHistoryData") {
            callback({ name: name, symbol: symbol, data: rows, ver: 2.0 });
        }
        // 流通股本 / 信息地雷 / 新闻公告等附加数据不提供：
        // 不调用 callback 即视为取消该次请求，K线主体不受影响
    };
}

function buildOption(symbol, rows, name, period) {
    return {
        Type: "历史K线图",
        Symbol: symbol,
        IsAutoUpdate: false,   // 数据由 Python 推送，不做轮询
        IsApiPeriod: true,     // 周期已由后端聚合，禁止库按日线再聚合一次
        NetworkFilter: makeNetworkFilter(symbol, rows, name),
        Windows: [{ Index: "MA" }, { Index: "VOL" }, { Index: "MACD" }],
        KLine: { Period: 0, MaxRequestDataCount: Math.max(rows.length, 30),
                 DrawType: 0, DragMode: 1 },
        KLineTitle: { IsShowName: true, IsShowSettingInfo: true },
        CorssCursorInfo: { IsShowCorss: true, DateFormatType: 0 },
        EnableZoomIndexWindow: true,
        Border: { Left: 50, Right: 90, Bottom: 25, Top: 25 }
        // 其余可选：Right（复权，0=不复权）、DataWidth、ZoomType、
        // RightSpaceCount、IsShowTooltip、SplashTitle 等
    };
}

// 图表由数据到达时按需创建；不要预先创建空图表
// （否则 HQChart 会尝试为一个不存在的代码去加载数据）
function makeChart(symbol, rows, name, period) {
    if (typeof JSChart === "undefined" || typeof JSChart.Init !== "function") return null;
    var dom = document.getElementById("mainChart");
    dom.innerHTML = "";                      // 开源版没有 destroy()，重建就是清容器
    JSChart.SetStyle({ DisableLogo: true }); // 见下
    var chart = JSChart.Init(dom, false, true);   // 第二、三个参数见库文档
    chart.SetOption(buildOption(symbol, rows, name, period));
    return chart;
}
```

注意：

- **行数下标顺序**固定为 `[date, yclose, open, high, low, close, vol, amount]`（见库源码 `JsonDataToHistoryData()`）。
- **`Open` 非数字的行会被整行丢弃**。新浪这类接口返回的是字符串，转成数字；非数字统一归零。
- **日期要转成 `YYYYMMDD` 整数**：`2026-07-01` / `2026/7/1` / `20260701` 都要能解析，失败返回 0 让该行被丢弃。
- **代码格式是 `"600519.SH"`**（代码 + 市场后缀），不是 Python 侧那种 `CN.600519`。后缀只影响显示精度（A 股 2 位 / 基金 3 位小数），数据本身由我们提供。
- 周期切换时**整体重建**图表，避免残留上一个周期的指标与坐标。
- 开源版右下角的"*仅学习使用*"声明用库自带的 `SetStyle({DisableLogo:true})` 关掉（全局设置，幂等）。**不要去改库文件里的文字** —— 开源版明确写着不要改声明，改库文件的法律风险由修改者自担。
- 窗口尺寸变化自己监听 `resize` 调 `chart.OnSize({Type: 1})`，**不要启用库的 `EnableResize`**：库每次初始化都会新建一个 `ResizeObserver`，周期切几次就攒几个。
- 容器 `#mainChart` 需要 `position: relative`，且给 `#mainChart canvas` 设 `position: absolute`。库创建画布但不设定位，源码里写着"外部自己设置"。
- 页面样式照抄主界面配色（浅灰底、灰色按钮、同一字体），避免"这是个浏览器"的观感。

## 打包 exe

**开发环境用 `-m`，打包后不能用了** —— `sys.executable` 就是应用自己的 exe，`-m pkg.module` 导入不生效。于是复用同一个 exe，靠参数区分入口：

```python
CHART_PROCESS_FLAG = "--hqchart-chart"

def build_chart_command(executable, chart_args, frozen=False):
    """纯函数，便于单测"""
    argv = [executable]
    if frozen:
        argv.append(CHART_PROCESS_FLAG)
    else:
        argv += ["-m", CHART_SCRIPT]
    return argv + list(chart_args)

# main.py
def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if CHART_PROCESS_FLAG in argv:
        from pkg.hqchart_chart import main as chart_main
        return chart_main([a for a in argv if a != CHART_PROCESS_FLAG])
    # ... 正常 GUI 入口
```

PyInstaller 脚本（`build_exe.py`）的三个要点：

```python
ASSETS_REL = "pkg/assets"
args = [
    "--onefile", "--windowed",
    "--add-data", "%s%s%s" % (ASSETS_REL, os.pathsep, ASSETS_REL),  # 源=目标，必须一致
    "--add-data", "LICENSE%s." % os.pathsep,                        # Apache 2.0 第 4(a) 条
    "--collect-all", "webview",                                     # 自带 __pyinstaller 钩子
]
```

1. `--add-data` 的源路径和目标路径要一致（`pkg/assets` → `pkg/assets`）。写错会被**平铺到解包根目录**，而代码用 `Path(__file__).parent / "assets"` 定位，于是静默回退成 matplotlib 版 —— 程序不报错，但交互没了。
2. **许可证必须一起打进去**。Apache 2.0 第 4(a) 条要求把许可证副本交给接收方，4(d) 条要求保留 NOTICE 的归属声明，onefile 不会自动带仓库根目录的文件。第三方依赖的许可证在各自 dist-info 里同样不会被打包，用 `importlib.metadata.distribution(pkg).files` 收集，先拷进暂存目录再整体 `--add-data`（目录名带发行名+版本号，避免不同包的 `LICENSE` 重名互相覆盖）。
3. 体积：Python 运行时 + matplotlib + pywebview + HQChart 的 JS，实测约 130MB。
4. onefile 每次启动先解包到临时目录；拉起 K 线窗口相当于再起一个自身进程，等于重新解一次包（约 1 秒）。
5. 打包后要**实测** K 线窗口是 HQChart 版还是回退版 —— 最容易出的错就是产物里 assets 路径不对而静默降级。

## 常见坑点

| 坑 | 原因 | 修复 |
|-----|------|------|
| 子进程启动即 AttributeError | pywebview **5.x 移除了 `@webview.expose`**，装饰器在类定义时就崩 | 5.x 用 `create_window(js_api=实例)`，实例的公开方法自动暴露；JS 侧调用方式不变 |
| 窗口弹出瞬间又消失 | `window.alive()` / `set_topmost()` 在 5.x 也被移除 | 用实例布尔标志自己追踪关闭状态；置顶用 `window.on_top = True` 属性 |
| 图表一直等到超时 | pywebview 在**页面加载完成后**才注入 `window.pywebview.api`，而 `DOMContentLoaded` 时 `notify()` 已跑过一遍，事件被静默丢弃 | JS 侧入队 + 200ms 轮询补发 + 超时上限（见上文就绪握手） |
| 数据推了但图是空的 | `evaluate_js` 推给了尚未定义的 `applyPayload` | Python 侧 `chart_ready` 之前缓冲，只保留最新一份载荷 |
| 只有第一根 K 线 / 日期错乱 | `Open` 是字符串被整行丢弃；日期没转成 `YYYYMMDD` 整数 | 数据适配层统一转数字 / 转整数 |
| 分时图不显示 / X 轴错位 | 用了 `"历史K线图"` 的 Type，或分时 `time` 传了字符串 | 分时图必须 `Type: "分钟走势图"`，`minute[].time` 是 HHMM 整数、`date` 是 YYYYMMDD 整数，数据格式 `stock[0].minute`（见本仓库 `assets/hqchart/README.md` 的「分时图」节） |
| 分时回放速度还是日K的 400ms | 下拉框发的是写死的日K毫秒数 | 切图形时用 `syncSpeedSelect()` 换下拉框的 `value`（分时 500/200/80/30），Python 侧按 `interval_bounds("minute")` 收敛 |
| 切换周期后指标错乱 | 复用同一个 chart 实例，残留上一周期的指标与坐标 | 整体重建（清容器再 `JSChart.Init`） |
| `OSError: [Errno 22] Invalid argument` | 子进程退出后父进程不关管道，解释器退出时去关已经失效的描述符 | 退出时显式 close stdin/stdout/stderr 临时文件（与写入共用一把锁） |
| 退出回调里调 messagebox 崩溃 | tkinter 不是线程安全的，退出回调在读取线程里 | 统一 `self._root.after(0, handler)` 调度回主线程 |
| 子窗口变孤儿进程 | 父进程被杀时子进程 stdin 收到 EOF 但窗口还开着 | 读取循环走到 EOF 时主动 `destroy()` 窗口 |
| `winfo_destroyed()` 不存在 | 该 API 要 Tk 9.0，国内常见构建仍是 Tk 8.6 | 用 `winfo_exists() == 1` 判断，包一层 try/except |
| 中文乱码 | `print()` 走控制台 cp936 | 走 `sys.stdout.buffer`，显式 UTF-8 编码 |
| 打包后 K 线窗口变成 matplotlib 版 | assets 没打进去 / `--add-data` 目标路径写错 | 见「打包 exe」第 1、5 条 |
| docstring 结尾语法错误 | `"""...区分"用户关窗"和"异常崩溃""""` 四个引号连写 | 结尾短语避开引号 |

## 测试策略

纯函数先抽出来，避免测试真起子进程和真拉网络：

- `encode_line` / `decode_line` —— UTF-8 + `\n`；空行返回 `None`；非法 JSON 抛 `ValueError`
- `build_chart_payload` —— 字段映射、数值类型、空/脏输入、未知周期回退默认周期、保持行顺序
- `build_chart_command` —— 开发模式用 `-m`、打包模式用自身 + 参数、两种模式互斥、不改调用方传入的列表
- `is_available` / `pywebview_compatible` —— monkeypatch `sys.modules` 注入假的 webview（`inspect.Signature` 造出带 `js_api` / 不带 `js_api` 的签名），monkeypatch 资产路径。**测试不真的 import webview**，没装也能跑
- `_dispatch` / `_on_exit` —— 用假 `proc`（带 `poll()`）+ 假 root（`after` 直接执行），覆盖正常关窗 vs 异常退出、幂等（不重复通知）、退出前先把 stderr 取出来再关管道、`terminate()` 关掉所有句柄、子进程退出后 `_send` 静默
- `build_args` —— `--add-data` 的源与目标一致、目标保留包前缀、源文件确实存在、许可证文件被打进解包根目录、`--collect-all webview`、重复调用可重入（**不真的跑 PyInstaller**，只断言配置）

## 清单

- [ ] `hqchart_chart.py`：子进程承载窗口 + stdin 协议 + ready/timeout 握手
- [ ] `hqchart_bridge.py`：父进程拉起、JSON 收发、退出区分「用户关窗」与「异常崩溃」、双保险回退
- [ ] `assets/hqchart/`：`index.html` / `app.css` / `chart_bridge.js` / `hqchart/`（官方三个 JS + NOTICE）/ `README.md`
- [ ] 入口分发：打包后用参数区分图表进程，开发环境用 `-m`
- [ ] 两种后端同一套接口（`alive` / `activate` / `close` / `fetch`）
- [ ] 指定日期分时：`chartType:"minute"` 分流、`Type:"分钟走势图"`、`time` 用 HHMM 整数、09:30 合成点对齐 X 轴、速度档位随图形切换
- [ ] 主窗口关闭时收尾子进程，不留孤儿进程和管道噪声
- [ ] `LICENSE` / `NOTICE` + 第三方许可证随产物分发
- [ ] 打包脚本把 assets 打进正确的包内路径，并实测产物没有静默降级

## 参考实现

- `stock_monitor/hqchart_bridge.py` —— 父进程侧桥接
- `stock_monitor/hqchart_chart.py` —— 子进程承载窗口
- `stock_monitor/assets/hqchart/chart_bridge.js` —— HQChart 适配层
- `stock_monitor/gui.py` 的 `_open_kline_window` / `_on_root_close` —— 主界面接入与回退
- `build_exe.py` —— PyInstaller 配置与许可证收集
- `tests/test_hqchart_bridge.py`、`tests/test_build_exe.py` —— 测试写法
- `stock_monitor/assets/hqchart/README.md` —— 给使用者的启用与排错说明
- `.claude/skills/desktop-finance-monitor.md` 第 5b 节 —— 同一方案的简版记录
