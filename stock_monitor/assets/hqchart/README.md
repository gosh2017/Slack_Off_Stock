# HQChart K线图表资源

K线窗口用 HQChart 渲染。本项目对接的是 **HQChart 开源版**
（<https://github.com/jones2000/HQChart>，Apache 2.0），授权声明见 `hqchart/NOTICE`。

## 目录内容

| 文件 | 来源 | 说明 |
|------|------|------|
| `index.html` | 本项目 | 页面骨架：工具栏 + 图表容器 |
| `app.css` | 本项目 | 页面样式（与 HQChart 官方文件分处不同目录，便于区分本项目与官方文件） |
| `chart_bridge.js` | 本项目 | **HQChart 适配层，所有与 HQChart 版本相关的调用都在这里** |
| `hqchart/umychart.js` | HQChart 开源版 | 主库（约 4MB），入口 `JSChart.Init()` |
| `hqchart/umychart.complier.js` | HQChart 开源版 | 麦语言解析执行器，指标靠它计算 |
| `hqchart/umychart.index.data.js` | HQChart 开源版 | 基础指标库（MA / VOL / MACD 等） |
| `hqchart/NOTICE` | HQChart 开源版 | 版权与授权声明 |

`hqchart/` 里的三个 JS 共约 5MB，**已随仓库分发，不需要再手动下载**。

## 如何启用

1. **安装 pywebview**（需要 **5.x**，旧版接口不兼容）：

   ```bash
   pip install -U pywebview
   ```

   Windows 需要 Edge WebView2 运行时（Windows 11 自带）。

   本项目用的是 5.x 的写法：`create_window(js_api=实例)` 暴露 JS 接口。
   旧版（4.x）用的是 `@webview.expose` 装饰器，两者不通用。装错版本时
   `is_available()` 会检测到并回退到 matplotlib 版本，界面提示
   「pywebview 版本不兼容」。

2. 重新运行程序，点击「K线」即可。

## 如果提示「未找到 HQChart 图表库」

这说明 `hqchart/` 下的 JS 文件缺失，按顺序检查：

1. 仓库 checkout 是否完整（浅克隆、稀疏 checkout、`.gitattributes`
   的 LFS 指针都会导致文件缺失）；
2. `.gitignore` 是否误排除了 `stock_monitor/assets/hqchart/hqchart/`
   ——这个目录**不应该**被排除，它是随仓库分发的内容。

确实缺失时，从 <https://github.com/jones2000/HQChart> 的
`webhqchart.demo/jscommon/` 目录取同名文件补上：

```
umychart.js              主库
umychart.complier.js     麦语言执行器（没有它指标算不出来）
umychart.index.data.js   基础指标库
```

**顺序不能换**：`index.html` 按上表顺序加载，主库运行时用到后两个文件
定义的全局对象。

其余文件（`umychart.style.js`、`umychart.network.js`、`umychart.*.js` 的
弹窗类、`umychart.resource/` 里的 jquery 与图标字体等）都是官方 demo 页面
自己的东西，本项目不用，不用复制。

## 关于授权

开源版**没有授权接口**，不需要申请授权码（`umychart.js` 里也没有
`HQChart.setConfig` 这类 API）。

Apache 2.0 只有一条相关要求：**再分发时需随附版权声明**。项目根目录已经带了
两个文件：

- `LICENSE` — Apache License 2.0 正文（对应第 4(a) 条）
- `NOTICE` — HQChart 的归属声明：仓库地址、作者、用到哪几个文件（对应第 4(d) 条）

打包发布时这两个文件也要一起带上，`build_exe.py` 已经处理好了。
三个 JS 文件头部的原始版权声明请保留。

## 前置条件不满足时

`gui.py` 会自动回退到内置的 matplotlib 版本（`KlineWindow`），程序不会崩溃。
可用 `stock_monitor.hqchart_bridge.is_available()` 检查，
`unavailable_reason()` 返回具体的缺失项。

## 排障

`chart_bridge.js` 的报错只出现在浏览器控制台，而子进程默认以
`webview.start(debug=False)` 启动（见 `hqchart_chart.py`），F12 打不开。
需要看 JS 报错时，把它临时改成 `debug=True` 后重启程序。
子进程异常退出时，主程序的错误弹窗会附带其 stderr 尾部内容。

如果提示「图表初始化超时」：pywebview 是在**页面加载完成后**才把
`window.pywebview.api` 注入页面的，而 `chart_bridge.js` 在 `DOMContentLoaded`
就会执行。`notify()` 已经把桥接未就绪时的事件入队并按 200ms 轮询补发
（上限 15 秒），所以正常不会丢。改动 `notify()` 时注意别退回到
"桥接没好就直接忽略"的写法，否则第一个 `chart_ready` 会静默丢失，
Python 侧只能干等到超时。

## 如果要调整图表行为

只需要改 `chart_bridge.js`，不用动 Python 代码，也不要改 `hqchart/` 下的
任何文件：

- 周期不对（0=日线 1=周线 2=月线）→ 改 `PERIOD_TO_HQCHART`
- 指标不对（想加 RSI / BOLL，或改均线参数）→ 改 `buildOption()` 里的 `Windows`
  和 `KLine`，例如 `{ Index: "MA", Overlay: true, Args: [{Name:"M1",Value:5}] }`
- 显示不对（边框、十字光标、标题、拖拽）→ 改 `buildOption()`
- 代码格式不对（`.SH` / `.SZ` 后缀规则）→ 改 `toHqchartSymbol()`
- 日期格式不对 → 改 `toHqchartDate()`（输出必须是 YYYYMMDD 整数）
- 数据写入方式不对 → 改 `makeNetworkFilter()` 和 `buildRows()`
  （行格式固定 `[date, yclose, open, high, low, close, vol, amount]`）
- 实例化入口不对（部分版本用 `new ChartDom(...)`）→ 改 `makeChart()`
- 右下角的「*仅学习使用*」声明 → 改 `SHOW_LEARNING_LOGO`。走的是库自带的
  `SetStyle({DisableLogo:true})` 开关，**不要**去改 `hqchart/umychart.js`
  里那句文字：原文件 `// 不要修改声明` 明确写了改库声明的法律风险由修改者自担，
  而库自己提供了 `DisableLogo`，用开关就够了

## 架构

```
tkinter 主进程                            子进程（hqchart_chart.py）
┌───────────────────────┐  JSON/stdin  ┌───────────────────────┐
│ HqChartKlineWindow    │ ────────────→ │ ChartApp             │
│  · 拉取 K 线数据       │              │  pywebview + WebView2│
│  · 推送 payload        │ ←──────────── │  index.html          │
│  · 处理周期/刷新/关闭  │  JSON/stdout └───────────────────────┘
└───────────────────────┘
```

K 线窗口放在**独立进程**是因为 pywebview 的 `webview.start()` 和 tkinter 的
`mainloop()` 都要占用主线程，无法共存于同一进程。

## 历史回放

回放的**定拍在主进程**（`stock_monitor/replay.py` 的 `ReplayDriver`，用
`root.after()` 定时），子进程只负责照着画，所以暂停 / 继续都不需要重新拉数据。

- 载荷：回放帧的 `data` 载荷带 `replay: true`，另有 `replayCount` / `replayTotal`
  / `replayDate` / `replayState` 供状态栏显示
- `replay_state` 消息：只同步按钮和状态栏，不重画图表（暂停瞬间不该闪一下）
  回放被停掉时（换周期、刷新、点"最新"）也会来一条，此时
  `state: "idle"` 且 `count` / `total` 都是 0 —— 页面靠它把按钮复位，
  否则按钮会停在停掉之前的样子（还亮着"暂停"）
- 页面 → 主进程的事件：`replay_start`（带 `interval`）、`replay_pause`、
  `replay_resume`、`replay_stop`、`replay_speed`

回放每一步都复用同一个图表实例重新 `SetOption`（`renderRows()`），而不是
`destroyChart()` + `JSChart.Init()` 重建：重建会让几百步回放每次都重建 canvas 和
事件绑定，既卡又闪。matplotlib 版本走的是 `KlineWindow`，两端的回放操作一致，
差异只在各自的画图画法。

## 分时图（指定日期）

同一张窗口还能看某一天的分时。工具栏输入日期点「分时」→ Python 拉分钟数据
（东方财富优先，它的行情域名连不上时退回新浪，见仓库根 `minute_api.py` 的模块
头注释）→ 推一个 `chartType: "minute"` 的载荷；分时回放复刻上面的回放机制，
只是间隔比日K快一个量级。

- 载荷：`applyPayload()` 按 `payload.chartType === "minute"` 分流到
  `applyMinuteFull()` / `applyMinuteReplay()`；分时数据在 `payload.items` 里，
  单位是股，另带 `date` / `preClose`（昨收基准线）/ `klt` / `note`。
  `note` 是状态栏提示（休市日回退说明、走了新浪兜底源），`applyMinuteFull()`
  末尾 `setStatus(payload.note)` 显示——必须排在 `setReplayUI("idle",…)` 后面，
  因为后者内部也调 `setStatus`，顺序反了提示语显示不出来
- 分时图走的是 `Type: "分钟走势图"`（不是 `"历史K线图"`），数据格式是
  `stock[0].minute` 数组：`{price, open, high, low, vol, amount, avprice, time}`
  —— `time` 必须是 **HHMM 整数**（如 931），`date` 是 YYYYMMDD 整数
- 单日分时只有 240 根左右，而 X 轴的 0 号槽位刻着 "09:30"，所以 JS 侧会补一根
  09:30 合成点（取当天开盘价），让 09:31~15:00 正好落在 1..240 号槽位上
- 页面 → 主进程的事件：`minute_open`（带 `date`）、`minute_close`；
  Python 侧拿到周期切换 / 刷新事件时会先退出分时模式
- 速度下拉框的值随图形类型切换（`syncSpeedSelect()`）：分时是
  500 / 200 / 80 / 30 毫秒一根，比日K的 2000 / 1000 / 400 / 150 快一个量级
