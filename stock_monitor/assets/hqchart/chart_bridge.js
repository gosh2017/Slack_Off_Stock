/*
 * chart_bridge.js — HQChart 适配层
 * ================================
 * 本项目与 HQChart 之间的唯一接触面。
 *
 * 职责：
 *   1. 把 Python 推来的数据（JSON）适配成 HQChart 需要的格式
 *   2. 负责图表实例的创建 / 重建（周期切换时整体重建）
 *   3. 历史回放：每帧数据由 Python 按进度切好推来，就地重画 + 更新进度
 *   4. 把工具栏按钮事件上报给 Python（notify）
 *
 * ★★★ 为什么所有 HQChart 调用都收拢在这个文件 ★★★
 *   按 HQChart 开源版的接口编写（github.com/jones2000/HQChart，Apache 2.0）：
 *     · 入口       JSChart.Init(div) → chart.SetOption(option)
 *     · 数据注入   option.NetworkFilter 回调，同步返回本地数据
 *     · 指标       option.Windows 声明（依赖 umychart.complier.js 的
 *                  麦语言执行器 + umychart.index.data.js 的基础指标库）
 *     · 代码格式   "600519.SH"（代码 + 市场后缀），不是 "CN.600519"
 *     · 授权       开源版没有授权接口，无需配置
 *
 *   升级 HQChart 版本时只需要改这一个文件，Python 侧
 *   （hqchart_bridge.py / hqchart_chart.py）不用动。改动点：
 *     - CONFIG              周期 / 指标 / 显示配置
 *     - toHqchartSymbol()   本项目代码 → HQChart 代码
 *     - buildOption()       配置项键名
 *     - makeNetworkFilter() 数据注入方式
 *     - makeChart()         实例化入口
 */
(function () {
    "use strict";

    // ════════════════ 配置 ════════════════

    // HQChart 的 KLine.Period：0=日线 1=周线 2=月线 3=年线
    var PERIOD_TO_HQCHART = { daily: 0, weekly: 1, monthly: 2 };

    var PERIOD_LABEL = { daily: "日K", weekly: "周K", monthly: "月K" };

    // 开源版右下角会画一行 "*仅学习使用*" 的声明。库自带开关（SetStyle 的
    // DisableLogo），不是去改库文件里的文字——umychart.js 第 81448 行明确写着
    // 不要改声明，改库文件的法律风险由修改者自担。
    var SHOW_LEARNING_LOGO = false;

    // ════════════════ 运行状态 ════════════════
    var STATE = {
        chart: null,      // JSChart 实例
        period: "daily",
        symbol: "",
        chartType: "kline"   // "kline" | "minute"（同一窗口两种图）
    };

    // 回放状态。定拍在 Python 侧（每隔一段时间推来"前 N 根"的数据），
    // 这里只负责照着画和更新按钮
    var REPLAY = {
        state: "idle",    // idle / running / paused / finished
        count: 0,         // 当前已揭示的 bar 数
        total: 0,         // 总 bar 数
        date: ""          // 当前最后一根 bar 的日期
    };

    // ════════════════ 与 Python 通信 ════════════════

    // pywebview 在页面加载完成后才把 api 对象注入到 window.pywebview，
    // 而本文件在 DOMContentLoaded 就会执行 init()。两者存在竞态：
    // 早发的 chart_ready 如果直接丢弃，Python 侧只能等到超时。
    // 所以把桥接未就绪时的事件先入队，桥接可用后统一补发。
    var PENDING_EVENTS = [];
    var BRIDGE_DEADLINE = new Date().getTime() + 15000;
    var bridgeTimer = null;

    function bridgeApi() {
        try {
            var api = window.pywebview && window.pywebview.api;
            if (api && typeof api.notify === "function") {
                return api;
            }
        } catch (e) {
            // 取不到就当作未就绪
        }
        return null;
    }

    function flushPending(api) {
        if (bridgeTimer) {
            clearInterval(bridgeTimer);
            bridgeTimer = null;
        }
        var batch = PENDING_EVENTS.splice(0, PENDING_EVENTS.length);
        for (var i = 0; i < batch.length; i++) {
            api.notify(batch[i].event, JSON.stringify(batch[i].detail));
        }
    }

    /**
     * 上报事件给 Python 侧（由 hqchart_chart.py 的 JsApi.notify 接收）。
     * @param {string} event  事件名
     * @param {object} detail 结构化附加信息（序列化为 JSON 字符串传递）
     */
    function notify(event, detail) {
        var api = bridgeApi();
        if (api) {
            api.notify(event, JSON.stringify(detail || {}));
            return;
        }
        PENDING_EVENTS.push({ event: event, detail: detail || {} });
        if (bridgeTimer) {
            return;   // 已在轮询
        }
        bridgeTimer = setInterval(function () {
            var ready = bridgeApi();
            if (ready) {
                flushPending(ready);
                return;
            }
            if (new Date().getTime() > BRIDGE_DEADLINE) {
                clearInterval(bridgeTimer);
                bridgeTimer = null;
                PENDING_EVENTS.length = 0;
            }
        }, 200);
    }

    // ════════════════ 工具栏 ════════════════

    function setTitle(payload) {
        var name = payload.name || payload.code || "";
        var text = name ? (name + " (" + payload.code + ")") : (payload.code || "--");
        var el = document.getElementById("title");
        if (el) {
            el.textContent = text;
        }
        document.title = "K线 — " + text;
    }

    function setStatus(text) {
        var el = document.getElementById("status");
        if (el) {
            el.textContent = text || "";
        }
    }

    function setButtonsDisabled(disabled) {
        var btns = document.querySelectorAll(".tb-btn");
        for (var i = 0; i < btns.length; i++) {
            btns[i].disabled = !!disabled;
        }
    }

    function setActiveButton(period) {
        var btns = document.querySelectorAll(".tb-btn[data-period]");
        for (var i = 0; i < btns.length; i++) {
            if (btns[i].getAttribute("data-period") === period) {
                btns[i].classList.add("active");
            } else {
                btns[i].classList.remove("active");
            }
        }
    }

    /** 分时模式没有"当前周期"，清掉周期按钮的高亮 */
    function clearActiveButton() {
        var btns = document.querySelectorAll(".tb-btn[data-period]");
        for (var i = 0; i < btns.length; i++) {
            btns[i].classList.remove("active");
        }
    }

    /** 分时模式下禁掉日期输入框和「分时」按钮（得先退出分时才能再开） */
    function syncMinuteControls() {
        var inp = document.getElementById("inp-date");
        var btn = document.getElementById("btn-minute");
        var disabled = (STATE.chartType === "minute");
        if (inp) { inp.disabled = disabled; }
        if (btn) { btn.disabled = disabled; }
    }

    // ════════════════ 回放工具栏 ════════════════

    // 速度下拉框的值随图形类型切换：分时一天两百多根，要比日K快一个量级。
    // 下拉框直接发毫秒数给 Python，所以切图时要把 option 的 value 一起换掉，
    // 否则分时模式会收到日K的 400ms（那是日K的"快"）。
    var SPEED_VALUES = {
        kline:  [2000, 1000, 400, 150],
        minute: [500, 200, 80, 30]
    };

    /** 读速度下拉框（毫秒/根），取不到时用默认值 */
    function getReplaySpeed() {
        var sel = document.getElementById("sel-speed");
        var v = sel ? parseInt(sel.value, 10) : 400;
        return (isFinite(v) && v > 0) ? v : 400;
    }

    /** 按当前图形类型更新速度下拉框的毫秒值，保住当前选中的档位（慢/中/快/极快） */
    function syncSpeedSelect() {
        var sel = document.getElementById("sel-speed");
        if (!sel) {
            return;
        }
        var values = SPEED_VALUES[STATE.chartType] || SPEED_VALUES.kline;
        var currentLabel = null;
        for (var i = 0; i < sel.options.length; i++) {
            if (sel.options[i].selected) {
                currentLabel = sel.options[i].text;
                break;
            }
        }
        for (var j = 0; j < sel.options.length && j < values.length; j++) {
            sel.options[j].value = values[j];
        }
        if (currentLabel) {
            for (var k = 0; k < sel.options.length; k++) {
                if (sel.options[k].text === currentLabel) {
                    sel.selectedIndex = k;
                    break;
                }
            }
        }
    }

    /** 回放进度文案 */
    function replayStatusText(state, count, total, date) {
        if (!total) {
            return "";
        }
        var head = (state === "finished") ? "回放完成" : "回放";
        var text = head + (date ? " " + date : "");
        return text + "  " + count + "/" + total;
    }

    /**
     * 同步回放三个按钮的可点状态和文案。
     * 暂停/继续共用一个按钮；"最新"在回放中才能点。
     */
    function syncReplayButtons(state) {
        var replay = document.getElementById("btn-replay");
        var pause = document.getElementById("btn-pause");
        var latest = document.getElementById("btn-latest");

        if (replay) {
            // idle 能开始；finished 也能再点一次重播，不必先回"最新"
            replay.disabled = !(state === "idle" || state === "finished");
        }
        if (pause) {
            pause.disabled = (state !== "running" && state !== "paused");
            pause.textContent = (state === "paused") ? "继续" : "暂停";
        }
        if (latest) {
            latest.disabled = (state === "idle");
        }
    }

    /** 按回放状态同步工具栏按钮和状态栏文字 */
    function setReplayUI(state, count, total, date) {
        syncReplayButtons(state);
        setStatus(replayStatusText(state, count, total, date));
    }

    /**
     * 放开按钮：先放开全部工具栏按钮，再按当前回放状态收紧。
     *
     * 顺序不能反过来——setButtonsDisabled 选的是全部 .tb-btn（含回放三个
     * 按钮），排在后面会把 syncReplayButtons 刚设好的禁用状态清掉，
     * 导致播放中「回放」也能点、回放结束后「暂停」「最新」也能点。
     * 统一走这个入口，避免各调用点自己拼顺序。
     */
    function releaseButtons() {
        setButtonsDisabled(false);
        syncReplayButtons(REPLAY.state);
        syncMinuteControls();
    }

    function bindToolbar() {
        var btns = document.querySelectorAll(".tb-btn[data-period]");
        for (var i = 0; i < btns.length; i++) {
            btns[i].addEventListener("click", function () {
                var period = this.getAttribute("data-period");
                // 分时模式下点周期按钮：交给 Python 退出分时再取该周期
                if (period === STATE.period && STATE.chartType !== "minute") {
                    return;
                }
                setButtonsDisabled(true);
                notify("period", { period: period });
            });
        }
        var refresh = document.getElementById("btn-refresh");
        if (refresh) {
            refresh.addEventListener("click", function () {
                setButtonsDisabled(true);
                notify("refresh", {});
            });
        }

        // ── 分时 ──
        // 输入日期后点「分时」或按回车 → 打开那天的分时。日期留空就用"今天"，
        // Python 侧对休市日会自动回退到最近交易日并在状态栏说明。
        var minuteBtn = document.getElementById("btn-minute");
        var dateInput = document.getElementById("inp-date");
        if (minuteBtn) {
            minuteBtn.addEventListener("click", function () {
                if (minuteBtn.disabled) {
                    return;
                }
                var date = dateInput ? dateInput.value.trim() : "";
                setButtonsDisabled(true);
                notify("minute_open", { date: date });
            });
        }
        if (dateInput) {
            dateInput.addEventListener("keydown", function (e) {
                if (e.key !== "Enter" || dateInput.disabled) {
                    return;
                }
                setButtonsDisabled(true);
                notify("minute_open", { date: dateInput.value.trim() });
            });
        }

        // ── 回放控制 ──
        // 点谁做什么都以按钮自身呈现的状态为准（disabled / 文案），不用 REPLAY.state
        // 判断——按钮状态由 setReplayUI() 从同一份状态算出，用户看到什么就按什么发，
        // 即使内部状态和 UI 不同步也不会发出用户理解不了的指令。
        var replay = document.getElementById("btn-replay");
        if (replay) {
            replay.addEventListener("click", function () {
                if (replay.disabled) {
                    return;
                }
                notify("replay_start", { interval: getReplaySpeed() });
            });
        }
        var pause = document.getElementById("btn-pause");
        if (pause) {
            pause.addEventListener("click", function () {
                if (pause.disabled) {
                    return;
                }
                notify(pause.textContent === "继续" ? "replay_resume" : "replay_pause", {});
            });
        }
        var latest = document.getElementById("btn-latest");
        if (latest) {
            latest.addEventListener("click", function () {
                if (latest.disabled) {
                    return;
                }
                notify("replay_stop", {});
            });
        }
        var speed = document.getElementById("sel-speed");
        if (speed) {
            speed.addEventListener("change", function () {
                notify("replay_speed", { interval: getReplaySpeed() });
            });
        }
    }

    // 窗口尺寸变化时让图表重排。这里自己监听而不启用库的 EnableResize，
    // 是因为库每次初始化都会新建一个 ResizeObserver，周期切换多次后会累积。
    function bindWindowResize() {
        window.addEventListener("resize", function () {
            if (STATE.chart && typeof STATE.chart.OnSize === "function") {
                STATE.chart.OnSize({ Type: 1 });
            }
        });
    }

    // ════════════════ 兜底提示 ════════════════

    function showNotice(html) {
        var chart = document.getElementById("mainChart");
        var notice = document.getElementById("notice");
        if (chart) { chart.style.display = "none"; }
        if (notice) {
            notice.innerHTML = html;
            notice.style.display = "flex";
        }
    }

    var MISSING_HTML = [
        '<div class="err">未加载到 HQChart 库</div>',
        "请确认 <code>assets/hqchart/hqchart/umychart.js</code> 存在。",
        "开源版从 <code>https://github.com/jones2000/HQChart</code> 下载，",
        "需要 <code>umychart.js</code> / <code>umychart.complier.js</code> /",
        "<code>umychart.index.data.js</code> 三个文件。"
    ].join("<br>");

    // ════════════════ 数据适配 ════════════════

    /** 非数字统一归零（HQChart 会丢弃 Open 非数字的行） */
    function num(value) {
        var n = Number(value);
        return isFinite(n) ? n : 0;
    }

    /**
     * 把日期转成 HQChart 需要的 YYYYMMDD 整数。
     * 支持 "2026-07-01" / "2026/7/1" / "2026.07.01" / "20260701"。
     * 解析失败返回 0，该行会被丢弃。
     */
    function toHqchartDate(value) {
        var s = String(value == null ? "" : value).trim();
        if (!s) {
            return 0;
        }
        if (/^\d{8}$/.test(s)) {
            return parseInt(s, 10);
        }
        var m = s.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/);
        if (!m) {
            return 0;
        }
        return parseInt(m[1], 10) * 10000 +
               parseInt(m[2], 10) * 100 +
               parseInt(m[3], 10);
    }

    /**
     * 把 Python 的 K 线列表转成 HQChart 的行数组。
     * 行格式固定为 [date, yclose, open, high, low, close, vol, amount]，
     * 下标顺序见 KLineChartContainer.JsonDataToHistoryData()。
     */
    function buildRows(items) {
        var rows = [];
        var prevClose = 0;
        for (var i = 0; i < items.length; i++) {
            var k = items[i];
            if (!k || typeof k !== "object") {
                continue;
            }
            var date = toHqchartDate(k.date);
            if (!date) {
                continue;
            }
            var open  = num(k.open);
            var close = num(k.close);
            rows.push([date, prevClose, open, num(k.high), num(k.low),
                       close, num(k.volume), num(k.amount)]);
            prevClose = close || open;
        }
        return rows;
    }

    /**
     * 本项目代码 → HQChart 代码（带市场后缀）。
     * 后缀只影响显示精度（A股2位 / 基金3位小数），数据本身由我们提供。
     * 精确判断见 umychart.js 的 MARKET_SUFFIX_NAME.IsSH() / IsSHSZFund()。
     */
    function toHqchartSymbol(code, stockType) {
        var c = String(code || "").trim().toUpperCase();
        var suffix;
        if (stockType === "index") {
            // 399xxx 是深证系列，其余（000xxx / 880xxx 等）归上交所
            suffix = (c.charAt(0) === "3" && c.charAt(1) === "9") ? "SZ" : "SH";
        } else if (stockType === "etf") {
            suffix = (c.charAt(0) === "1") ? "SZ" : "SH";
        } else {
            var f = c.charAt(0);
            suffix = (f === "6" || f === "9") ? "SH"
                   : (f === "0" || f === "2" || f === "3") ? "SZ"
                   : "SH";
        }
        return c + "." + suffix;
    }

    // ════════════════ 分时数据适配 ════════════════

    /** "09:31" → 931（HQChart 的分钟 Time 用 HHMM 整数，见 UpdateLastPoint） */
    function hhmmToInt(time) {
        var m = String(time == null ? "" : time).match(/^(\d{1,2}):(\d{2})$/);
        if (!m) {
            return 0;
        }
        return parseInt(m[1], 10) * 100 + parseInt(m[2], 10);
    }

    /**
     * 把 Python 的分时 bars 转成 HQChart 单日分时的 stock 对象。
     *
     * 字段格式对应 MinuteChartContainer.JsonDataToMinuteData()：
     *   minute[i] = { price, open, high, low, vol, amount, avprice, time }
     *   time 是 HHMM 整数；date 是 YYYYMMDD 整数。
     *
     * X 轴的 0 号槽位刻着 "09:30"，而行情源的第一根是 09:31（时间和成交量
     * 的口径见 minute_api.py 的模块头注释，几个源都一样），所以前面补一根
     * 09:30 的合成点（用当天第一根的开盘价），让 09:31~15:00 正好落在
     * 1..240 号槽位上。
     */
    function buildMinuteStock(payload) {
        var items = payload.items || [];
        var minute = [];
        var firstOpen = 0;
        for (var i = 0; i < items.length; i++) {
            var b = items[i];
            if (!b || typeof b !== "object") {
                continue;
            }
            if (i === 0) {
                firstOpen = num(b.open);
            }
            minute.push({
                price: num(b.close),
                open: num(b.open),
                high: num(b.high),
                low: num(b.low),
                vol: num(b.volume),
                amount: num(b.amount),
                avprice: num(b.avPrice),
                time: hhmmToInt(b.time)
            });
        }
        if (!minute.length) {
            return null;
        }

        var open = firstOpen || num(payload.preClose) || minute[0].open;
        minute.unshift({
            price: open, open: open, high: open, low: open,
            vol: 0, amount: 0, avprice: open, time: 930
        });

        var high = open, low = open;
        for (var j = 1; j < minute.length; j++) {
            if (minute[j].high > high) { high = minute[j].high; }
            if (minute[j].low > 0 && minute[j].low < low) { low = minute[j].low; }
        }

        return {
            symbol: STATE.symbol,
            name: payload.name || STATE.symbol,
            date: toHqchartDate(payload.date),
            yclose: num(payload.preClose),
            high: high,
            low: low,
            IsHistoryMinute: true,          // 标题栏显示日期
            minute: minute
        };
    }

    /** 分时的 NetworkFilter：拦截单日分时请求，同步返回本地数据 */
    function makeMinuteNetworkFilter(symbol, stock, name) {
        return function (req, callback) {
            req.PreventDefault = true;
            if (req.Name === "MinuteChartContainer::RequestMinuteData") {
                if (callback) {
                    callback({
                        dataType: 0,             // 全量数据（非增量）
                        stock: [stock],
                        AutoUpdate: false        // 数据由 Python 推送，不自动刷新
                    });
                }
                return;
            }
            // 流通股本 / 信息地雷等附加数据本项目不提供
        };
    }

    /** 分时图的 option：Type 必须是 "分钟走势图"（见 JSChart.SetOption） */
    function buildMinuteOption(symbol, stock, name) {
        return {
            Type: "分钟走势图",

            Symbol: symbol,
            IsAutoUpdate: false,        // 数据由 Python 推送
            IsApiPeriod: false,
            EnableVerifyRecvData: false, // 数据是我们自己给的，不用校验 symbol

            NetworkFilter: makeMinuteNetworkFilter(symbol, stock, name),

            Minute: {
                IsShowTooltip: false,
                DragMode: 1
            },

            CorssCursorInfo: {
                IsShowCorss: true,
                DateFormatType: 0
            },

            EnableYDrag: { Right: true, Left: true },

            Border: {
                Left: 50, Right: 90, Bottom: 25, Top: 25,
                AutoLeft: { Blank: 10, MinWidth: 60 },
                AutoRight: { Blank: 5, MinWidth: 60 }
            },

            SplashTitle: "加载数据中……"
        };
    }

    /** 创建分时图实例 */
    function makeMinuteChart(symbol, stock, name) {
        if (typeof JSChart === "undefined" ||
            typeof JSChart.Init !== "function") {
            return null;
        }
        var dom = document.getElementById("mainChart");
        if (!dom) {
            return null;
        }
        dom.innerHTML = "";
        dom.style.display = "block";

        applyStyle();
        var chart = JSChart.Init(dom, false, true);
        chart.SetOption(buildMinuteOption(symbol, stock, name));
        return chart;
    }

    /** 复用现有实例重画分时（回放每帧都走这里，避免反复 Init） */
    function renderMinuteRows(symbol, stock, name) {
        if (!STATE.chart) {
            var notice = document.getElementById("notice");
            if (notice) {
                notice.style.display = "none";
            }
            var chart = makeMinuteChart(symbol, stock, name);
            if (!chart) {
                return null;
            }
            STATE.chart = chart;
            return chart;
        }
        try {
            var updated = STATE.chart.SetOption(
                buildMinuteOption(symbol, stock, name));
            if (updated) {
                return updated;
            }
        } catch (e) {
            // 就地更新失败，落到整体重建兜底
        }
        destroyChart();
        var rebuilt = makeMinuteChart(symbol, stock, name);
        STATE.chart = rebuilt;
        return rebuilt;
    }

    // ════════════════ 图表初始化 ════════════════

    /**
     * 构造 NetworkFilter。
     * HQChart 每次要数据都会回调这里：同步返回本地数据，并把
     * PreventDefault 置 true，库就不会再去请求它内置的行情接口。
     */
    function makeNetworkFilter(symbol, rows, name) {
        return function (req, callback) {
            // 必须在同步返回前设置，库靠它判断"上层已接管数据源"
            req.PreventDefault = true;

            if (req.Name === "KLineChartContainer::RequestHistoryData") {
                if (callback) {
                    callback({ name: name, symbol: symbol, data: rows, ver: 2.0 });
                }
                return;
            }

            // 流通股本 / 信息地雷 / 新闻公告等附加数据本项目不提供：
            // 不调用 callback 即视为取消该次请求，K线主体不受影响。
        };
    }

    function buildOption(symbol, rows, name, period) {
        return {
            Type: "历史K线图",

            Symbol: symbol,
            IsAutoUpdate: false,   // 数据由 Python 推送，不做轮询
            IsApiPeriod: true,     // 周期已由后端聚合，禁止库按日线再聚合一次
            NetworkFilter: makeNetworkFilter(symbol, rows, name),

            // 主图均线（默认 MA5/10/20），副图成交量、MACD
            Windows: [
                { Index: "MA" },
                { Index: "VOL" },
                { Index: "MACD" }
            ],

            KLine: {
                Period: PERIOD_TO_HQCHART[period] || 0,
                Right: 0,                   // 不复权（后端返回原始价）
                MaxRequestDataCount: Math.max(rows.length, 30),
                DragMode: 1,                // 1=数据拖拽
                DrawType: 0,                // 0=实心K线
                IsShowTooltip: false,
                KLineDoubleClick: false,
                RightSpaceCount: 2,
                ZoomType: 0,
                DataWidth: 8
            },

            KLineTitle: {
                IsShowName: true,
                IsShowSettingInfo: true,
                IsTitleShowLatestData: true
            },

            CorssCursorInfo: {
                IsShowCorss: true,
                DateFormatType: 0           // YYYY-MM-DD
            },

            EnableZoomIndexWindow: true,
            EnableYDrag: { Right: true, Left: true },

            Border: {
                Left: 50, Right: 90, Bottom: 25, Top: 25,
                AutoLeft: { Blank: 10, MinWidth: 60 },
                AutoRight: { Blank: 5, MinWidth: 60 }
            },

            SplashTitle: "加载数据中……"
        };
    }

    /**
     * 关掉开源版右下角的"*仅学习使用*"声明。
     *
     * 用库自带的开关：SetStyle({DisableLogo:true}) 会置空
     * g_JSChartResource.FrameLogo.Text（umychart.js:84127），DrawLogo()
     * 发现不是字符串就整段跳过（umychart.js:14504）。这是全局设置，
     * 改一次所有图表实例都生效，所以这里幂等，重复调用没副作用。
     */
    function applyStyle() {
        if (SHOW_LEARNING_LOGO) {
            return;
        }
        try {
            if (typeof JSChart !== "undefined" &&
                typeof JSChart.SetStyle === "function") {
                JSChart.SetStyle({ DisableLogo: true });
            }
        } catch (e) {
            // 纯装饰项，失败不影响图表
        }
    }

    /**
     * 创建图表实例。
     * @returns {object|null} JSChart 实例，库缺失或创建失败返回 null
     */
    function makeChart(symbol, rows, name, period) {
        if (typeof JSChart === "undefined" ||
            typeof JSChart.Init !== "function") {
            return null;
        }
        var dom = document.getElementById("mainChart");
        if (!dom) {
            return null;
        }
        dom.innerHTML = "";
        dom.style.display = "block";

        applyStyle();
        var chart = JSChart.Init(dom, false, true);
        chart.SetOption(buildOption(symbol, rows, name, period));
        return chart;
    }

    function destroyChart() {
        // 开源版没有 destroy() 接口，重建就是清掉容器里的画布
        var dom = document.getElementById("mainChart");
        if (dom) {
            dom.innerHTML = "";
        }
        STATE.chart = null;
    }

    /**
     * 把一组数据画出来。
     *
     * 首次画（还没有图表实例）走完整的 Init + SetOption；之后复用同一个实例
     * 直接 SetOption —— 库会在原画布上重画，不新建画布、不重建 DOM，
     * 所以回放每一步都很轻，也不会闪白。
     *
     * @returns {object|null} 画成功返回图表实例，失败返回 null
     */
    function renderRows(rows, name, period) {
        if (!STATE.chart) {
            var notice = document.getElementById("notice");
            if (notice) {
                notice.style.display = "none";
            }
            var chart = makeChart(STATE.symbol, rows, name, period);
            if (!chart) {
                return null;
            }
            STATE.chart = chart;
            return chart;
        }
        try {
            var updated = STATE.chart.SetOption(
                buildOption(STATE.symbol, rows, name, period));
            if (updated) {
                return updated;
            }
        } catch (e) {
            // 就地更新失败（库版本差异等），落到整体重建兜底
        }
        destroyChart();
        var rebuilt = makeChart(STATE.symbol, rows, name, period);
        STATE.chart = rebuilt;
        return rebuilt;
    }

    function showChartMissing() {
        showNotice(MISSING_HTML);
        notify("chart_error", { message: "未加载到 HQChart 库" });
        releaseButtons();
        return false;
    }

    /**
     * 应用一份完整的数据载荷：重建图表 → 写入数据。
     * 周期切换时整体重建，避免残留上一个周期的指标与坐标。
     */
    function applyFull(payload) {
        var items = payload.items || [];
        if (!items.length) {
            setStatus("数据不足");
            releaseButtons();
            return;
        }

        STATE.chartType = "kline";
        STATE.period = payload.period || "daily";
        // payload.symbol 是 Python 侧的 "CN.600519" 形式，HQChart 不认，
        // 这里按代码 + 市场后缀重新生成
        STATE.symbol = toHqchartSymbol(payload.code, payload.stockType);
        syncSpeedSelect();

        var rows = buildRows(items);
        if (!rows.length) {
            setStatus("数据不足（日期格式无法解析）");
            releaseButtons();
            notify("chart_error", { message: "K线数据日期格式无法解析" });
            return;
        }

        // 完整载荷意味着退出回放模式（周期切换 / 刷新 / 点"最新"）
        destroyChart();
        STATE.chart = null;

        var chart = makeChart(STATE.symbol, rows,
                              payload.name || STATE.symbol, STATE.period);
        if (!chart) {
            return showChartMissing();
        }
        STATE.chart = chart;

        REPLAY.state = "idle";
        REPLAY.count = 0;
        REPLAY.total = 0;
        REPLAY.date = "";

        setTitle(payload);
        setActiveButton(STATE.period);
        releaseButtons();
        setReplayUI("idle", 0, 0, "");
    }

    /**
     * 应用一帧回放数据。
     *
     * Python 已经把 K 线按进度切好推过来，这里只做就地重画 + 更新进度文案。
     * 不重建图表实例，否则几百步回放要 Init 几百次，会明显卡顿和闪屏。
     */
    function applyReplay(payload) {
        var items = payload.items || [];
        var rows = buildRows(items);
        if (!rows.length) {
            setStatus("回放数据不足");
            releaseButtons();
            return;
        }

        STATE.period = payload.period || STATE.period;
        STATE.symbol = toHqchartSymbol(payload.code, payload.stockType);

        var chart = renderRows(rows, payload.name || STATE.symbol, STATE.period);
        if (!chart) {
            return showChartMissing();
        }

        var state = payload.replayState || "running";
        var count = parseInt(payload.replayCount, 10);
        var total = parseInt(payload.replayTotal, 10);
        if (!isFinite(count) || count <= 0) { count = rows.length; }
        if (!isFinite(total) || total <= 0) { total = rows.length; }

        REPLAY.state = state;
        REPLAY.count = count;
        REPLAY.total = total;
        REPLAY.date = payload.replayDate || "";

        setTitle(payload);
        setActiveButton(STATE.period);
        releaseButtons();
        setReplayUI(state, count, total, REPLAY.date);
    }

    /** 分时的标题：名称 + 日期 */
    function setMinuteTitle(payload) {
        var name = payload.name || payload.code || "";
        var date = payload.date || "";
        var text = name ? (name + " (" + payload.code + ")") : (payload.code || "--");
        if (date) {
            text += "  " + date;
        }
        var el = document.getElementById("title");
        if (el) {
            el.textContent = text;
        }
        document.title = "分时 — " + text;
    }

    /** 应用一份完整的分时数据：重建分时图 → 写入数据 */
    function applyMinuteFull(payload) {
        STATE.chartType = "minute";
        STATE.period = "minute";
        STATE.symbol = toHqchartSymbol(payload.code, payload.stockType);
        syncSpeedSelect();

        var stock = buildMinuteStock(payload);
        if (!stock) {
            setStatus("数据不足");
            releaseButtons();
            return;
        }

        destroyChart();
        STATE.chart = null;

        var chart = makeMinuteChart(STATE.symbol, stock,
                                    payload.name || STATE.symbol);
        if (!chart) {
            return showChartMissing();
        }
        STATE.chart = chart;

        REPLAY.state = "idle";
        REPLAY.count = 0;
        REPLAY.total = 0;
        REPLAY.date = "";

        setMinuteTitle(payload);
        clearActiveButton();
        releaseButtons();
        setReplayUI("idle", 0, 0, "");
        // setReplayUI 会写一次状态栏，所以提示语得排在它后面才能显示出来
        // （休市日回退说明 / 走了新浪兜底源；回放帧会用进度文字覆盖）
        if (payload.note) {
            setStatus(payload.note);
        }
    }

    /** 应用一帧分时回放：就地重画 + 更新进度，不重建实例 */
    function applyMinuteReplay(payload) {
        STATE.chartType = "minute";
        STATE.period = "minute";
        STATE.symbol = toHqchartSymbol(payload.code, payload.stockType);
        syncSpeedSelect();

        var stock = buildMinuteStock(payload);
        if (!stock) {
            setStatus("回放数据不足");
            releaseButtons();
            return;
        }

        var chart = renderMinuteRows(STATE.symbol, stock,
                                     payload.name || STATE.symbol);
        if (!chart) {
            return showChartMissing();
        }

        var state = payload.replayState || "running";
        var count = parseInt(payload.replayCount, 10);
        var total = parseInt(payload.replayTotal, 10);
        if (!isFinite(count) || count <= 0) { count = payload.items.length; }
        if (!isFinite(total) || total <= 0) { total = payload.items.length; }

        REPLAY.state = state;
        REPLAY.count = count;
        REPLAY.total = total;
        REPLAY.date = (payload.replayDate || "") +
                      (payload.replayTime ? " " + payload.replayTime : "");

        setMinuteTitle(payload);
        clearActiveButton();
        releaseButtons();
        setReplayUI(state, count, total, REPLAY.date);
    }

    /**
     * 只更新回放状态（暂停 / 继续 / 结束），不动图表。
     * 由父进程在状态变化时单独推过来，省掉一次整图重画。
     */
    function applyReplayState(msg) {
        msg = msg || {};
        var state = msg.state || "idle";
        REPLAY.state = state;                          // 必须写回，否则按钮文案和状态会不同步
        if (typeof msg.count === "number") { REPLAY.count = msg.count; }
        if (typeof msg.total === "number") { REPLAY.total = msg.total; }
        if (typeof msg.date === "string") {
            REPLAY.date = msg.date + (typeof msg.time === "string" && msg.time
                                      ? " " + msg.time : "");
        }
        setReplayUI(state, REPLAY.count, REPLAY.total, REPLAY.date);
    }

    /**
     * 入口：Python 侧通过 window.evaluate_js 调用。
     */
    function applyPayload(payload) {
        if (!payload) {
            setStatus("");
            releaseButtons();
            return;
        }
        // Python 侧拉数据失败 / 提示：保留当前图，只在状态栏显示
        if (payload.error) {
            setStatus(payload.error);
            releaseButtons();
            return;
        }
        if (payload.chartType === "minute") {
            if (payload.replay === true) {
                applyMinuteReplay(payload);
            } else {
                applyMinuteFull(payload);
            }
            return;
        }
        if (payload.replay === true) {
            applyReplay(payload);
            return;
        }
        applyFull(payload);
    }

    // ════════════════ 入口 ════════════════

    function init() {
        bindToolbar();
        bindWindowResize();
        applyStyle();
        syncSpeedSelect();               // 初始化速度下拉框（kline 档位）
        syncMinuteControls();            // 初始化分时按钮状态
        setReplayUI("idle", 0, 0, "");   // 初始化回放按钮状态

        // chart_ready 的含义是"适配层可以接收 applyPayload"。
        // 库缺失时也先上报，让 Python 立刻把数据推过来，
        // 由 applyPayload() 显示缺失提示 —— 否则会一直等到超时才反馈。
        notify("chart_ready", {});

        // 不预先创建空图表 —— 否则 HQChart 会尝试为一个不存在的代码加载数据。
        // 图表实例由 applyPayload() 在数据到达时按需创建。
        if (typeof JSChart === "undefined" ||
            typeof JSChart.Init !== "function") {
            showNotice(MISSING_HTML);
            notify("chart_error", { message: "未加载到 HQChart 库" });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

    // Python 侧通过 window.evaluate_js 调用
    window.applyPayload = applyPayload;
    window.applyReplayState = applyReplayState;
})();
