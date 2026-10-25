"""
HQChart K线窗口 — 承载进程
===========================
由 gui.py 通过 subprocess 单独拉起的一个 Python 进程，进程内只跑 pywebview，
完全不使用 tkinter。

为什么要单独一个进程？
    pywebview 的 webview.start() 需要占用主线程的事件循环，而 tkinter 自己的
    mainloop() 也要主线程，两者无法共存于同一进程。K线窗口本来就是独立窗口，
    所以让它在子进程里自成一体的事件循环，父子之间用 stdin/stdout 传 JSON。

通信协议（JSON 行，UTF-8）：
    父 → 子：
        {"cmd": "data", "payload": {...}}       推送一份 K 线 / 分时数据
                                               （分时载荷带 chartType="minute"）
        {"cmd": "replay_state", ...}           推送回放状态（不重画图表）
        {"cmd": "focus"}                       请求把窗口提到前台
        {"cmd": "close"}                       关闭窗口
    子 → 父：
        {"event": "ready"}                  窗口已创建
        {"event": "chart_ready"}            图表适配层已就绪，可以接收数据
        {"event": "chart_error", "message"} 图表初始化或数据写入失败
        {"event": "period", "period"}       用户切换了周期
        {"event": "refresh"}                用户点了刷新
        {"event": "minute_open", "date"}    用户打开某天的分时
        {"event": "minute_close"}           用户退出分时
        {"event": "close"}                  用户关闭了窗口
        {"event": "error", "message"}       本进程发生未捕获异常

命令行用法（一般无需手工运行，由 hqchart_bridge 拉起）：
    开发环境：  python -m stock_monitor.hqchart_chart 600000 stock 浦发银行
    打包环境：  stock_monitor.exe --hqchart-chart 600000 stock 浦发银行
               （argv 的拼装见 hqchart_bridge.build_chart_command，
                 入口分发见 stock_monitor/main.py）
"""

import argparse
import json
import sys
import threading
import time
import traceback
from pathlib import Path

from stock_monitor.hqchart_bridge import encode_line, decode_line

# ──────────────── 可选依赖：pywebview ────────────────
# 本模块只在 pywebview 可用时才会被拉起（父进程已用 is_available() 检查），
# 但直接命令行运行时不检查，所以保留一个软导入 + 运行时判断。
try:
    import webview
    PYWEBVIEW_AVAILABLE = True
except ImportError:
    webview = None
    PYWEBVIEW_AVAILABLE = False

# ──────────────── 本地资产 ────────────────
ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "hqchart"
INDEX_HTML = ASSETS_DIR / "index.html"
# HQChart 开源版（Apache 2.0）已随仓库分发，放在 hqchart/ 子目录，
# 避免与本项目自有的 index.html / app.css / chart_bridge.js 混淆
HQCHART_JS = ASSETS_DIR / "hqchart" / "umychart.js"

WINDOW_WIDTH = 900
WINDOW_HEIGHT = 640
WINDOW_MIN_SIZE = (640, 420)

# 等待 chart_ready 的上限；超时说明 JS 侧初始化失败
READY_TIMEOUT = 25

_OUT_LOCK = threading.Lock()


def _send(msg: dict):
    """向父进程写一行 JSON（走 stdout.buffer，避免受控制台编码影响）"""
    with _OUT_LOCK:
        sys.stdout.buffer.write(encode_line(msg))
        sys.stdout.buffer.flush()


def _fallback_html() -> str:
    """HQChart 文件缺失时的兜底页面（纯内联 HTML，无外部依赖）"""
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>K线</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body { height: 100%; }
    body {
        font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
        background: #FFFFFF; color: #666;
        display: flex; align-items: center; justify-content: center;
        text-align: center; padding: 32px;
    }
    .box { max-width: 560px; line-height: 2; font-size: 13px; }
    .err { color: #C00000; font-weight: bold; font-size: 14px; }
    code {
        font-family: Consolas, monospace; font-size: 12px;
        background: #EEEEEE; padding: 2px 6px; border-radius: 2px;
    }
    a { color: #1565C0; }
    button {
        margin-top: 14px; font-family: inherit; font-size: 13px;
        padding: 5px 22px; background: #E0E0E0; color: #333;
        border: 1px solid #CCCCCC; border-radius: 2px; cursor: pointer;
    }
</style>
</head>
<body>
<div class="box">
    <div class="err">未找到 HQChart 图表库</div>
    <div>下列文件应当随仓库一起分发，但当前缺失：</div>
    <div><code>assets/hqchart/hqchart/umychart.js</code></div>
    <div><code>assets/hqchart/hqchart/umychart.complier.js</code></div>
    <div><code>assets/hqchart/hqchart/umychart.index.data.js</code></div>
    <div>请先检查仓库 checkout 是否完整，以及 <code>.gitignore</code></div>
    <div>是否误排除了 <code>stock_monitor/assets/hqchart/hqchart/</code>。</div>
    <div>确实缺失时可以从 <a href="https://github.com/jones2000/HQChart">HQChart 开源仓库</a>
        的 <code>webhqchart.demo/jscommon/</code> 重新获取这三个文件。</div>
    <button onclick="notifyClose()">关闭</button>
</div>
<script>
    function notifyClose() {
        try {
            window.pywebview.api.notify("close", "null");
        } catch (e) {}
        setTimeout(function () { window.close(); }, 100);
    }
</script>
</body>
</html>"""


class JsApi:
    """暴露给页面的 JS 接口。

    pywebview 5.x 移除了 @webview.expose 装饰器，改为在 create_window() 里
    传入一个实例（js_api=），该实例的所有公开方法自动暴露给页面。
    JS 侧调用方式不变：window.pywebview.api.notify(event, detail)
    """

    def __init__(self, owner):
        self._owner = owner

    def notify(self, event: str, detail: str = "null"):
        self._owner.notify(event, detail)


def _set_on_top(window, on_top: bool):
    """设置窗口置顶。5.x 用 on_top 属性，旧版是 set_topmost()。失败静默。"""
    try:
        window.on_top = on_top
        return True
    except (AttributeError, TypeError):
        pass
    try:
        window.set_topmost(on_top)
        return True
    except Exception:
        return False


class ChartApp:
    """承载 HQChart 页面的窗口：负责 stdin 协议与 JS 双向通信"""

    def __init__(self, code: str, stock_type: str, name: str):
        self.code = code
        self.stock_type = stock_type
        self.name = name

        self._pending_payload = None       # chart_ready 之前到达的数据（只保留最新一份）
        self._ready_notified = False       # 是否已上报 chart_ready
        self._closing = False
        self._window_closed = False        # 用户是否关掉了窗口（5.x 没有 window.alive()）
        self.js_api = JsApi(self)

        title = "K线 — %s (%s)" % (name or code, code)

        if INDEX_HTML.is_file() and HQCHART_JS.is_file():
            self.window = webview.create_window(
                title,
                url=INDEX_HTML.resolve().as_uri(),
                js_api=self.js_api,
                width=WINDOW_WIDTH, height=WINDOW_HEIGHT,
                min_size=WINDOW_MIN_SIZE,
                resizable=True, text_select=False,
            )
        else:
            # HQChart 文件缺失：给出提示页而不是让 JS 报错
            self.window = webview.create_window(
                title, html=_fallback_html(),
                width=WINDOW_WIDTH, height=WINDOW_HEIGHT,
                min_size=WINDOW_MIN_SIZE,
                resizable=True, text_select=False,
            )

        self.window.events.closed += self._on_window_closed

        # 子线程读取父进程的 stdin（主线程留给 webview 事件循环）
        threading.Thread(target=self._read_stdin, daemon=True).start()
        threading.Thread(target=self._watchdog, daemon=True).start()

    # ──────────────── JS → Python ────────────────

    def notify(self, event: str, detail: str = "null"):
        """接收来自 chart_bridge.js 的事件。

        detail 是 JSON 字符串（"null" 表示无附加信息）。
        该回调由 webview 的后台线程池调用，内部只做缓冲和写管道，
        不会阻塞 UI 线程。
        """
        if self._closing:
            return

        payload = None
        if detail:
            try:
                payload = json.loads(detail)
            except (ValueError, TypeError):
                payload = None

        if event == "chart_ready":
            self._on_chart_ready()
            return

        out = {"event": event}
        if payload:
            out.update(payload)
        _send(out)

    def _on_chart_ready(self):
        """图表适配层就绪：冲刷在就绪前积压的数据"""
        if self._ready_notified:
            return
        self._ready_notified = True
        _send({"event": "chart_ready"})
        if self._pending_payload is not None:
            self._push_to_js(self._pending_payload)

    def _watchdog(self):
        """超时未就绪 → 上报错误，让父进程有机会回退到 matplotlib"""
        deadline = time.time() + READY_TIMEOUT
        while time.time() < deadline and not self._closing:
            if self._ready_notified or self._window_closed:
                return
            time.sleep(0.5)
        if not self._ready_notified and not self._closing:
            _send({"event": "chart_error",
                   "message": "图表初始化超时（%d 秒）" % READY_TIMEOUT})

    # ──────────────── Python → JS ────────────────

    def _push_to_js(self, payload: dict):
        """把数据载荷交给页面执行 applyPayload()"""
        if not self._ready_notified:
            self._pending_payload = payload
            return
        try:
            js = "window.applyPayload(%s);" % json.dumps(payload, ensure_ascii=False)
            self.window.evaluate_js(js)
        except Exception:
            # 评估失败时保留载荷，等待下一次 chart_ready 重试
            self._pending_payload = payload

    def _push_replay_state(self, msg: dict):
        """把回放状态交给页面：只更新按钮和状态栏，不重画图表。

        和 _push_to_js 不同，这里不做"未就绪就缓存"——状态消息是实时的，
        错过一次也没关系，下一帧的数据载荷会带上同样的状态。
        """
        if not self._ready_notified:
            return
        try:
            js = "window.applyReplayState(%s);" % json.dumps(msg, ensure_ascii=False)
            self.window.evaluate_js(js)
        except Exception:
            pass

    # ──────────────── 父进程命令 ────────────────

    def _read_stdin(self):
        """读取父进程发来的 JSON 行命令"""
        try:
            for raw in sys.stdin.buffer:
                try:
                    msg = decode_line(raw)
                except ValueError:
                    continue
                if msg is None:
                    continue
                self._handle_command(msg)
                if self._closing:
                    break
        except (OSError, ValueError):
            pass
        finally:
            # stdin 走到 EOF 说明父进程已退出，关掉窗口避免留下孤儿进程
            if not self._closing:
                self._request_close()

    def _handle_command(self, msg: dict):
        cmd = msg.get("cmd")
        if cmd == "data":
            self._push_to_js(msg.get("payload") or {})
        elif cmd == "replay_state":
            self._push_replay_state(msg)
        elif cmd == "focus":
            self._focus_window()
        elif cmd == "close":
            self._request_close()
        else:
            _send({"event": "error", "message": "未知命令：%s" % cmd})

    def _focus_window(self):
        """把窗口提到前台。

        跨进程无法直接 SetForegroundWindow，这里用"置顶后短暂取消"
        的方式近似实现，失败静默。
        """
        try:
            self.window.show()
            if _set_on_top(self.window, True):
                threading.Timer(0.6, self._unset_topmost, daemon=True).start()
        except Exception:
            pass

    def _unset_topmost(self):
        if not self._window_closed:
            _set_on_top(self.window, False)

    def _request_close(self):
        if self._closing:
            return
        self._closing = True
        self._window_closed = True
        try:
            self.window.destroy()
        except Exception:
            pass

    def _on_window_closed(self, *args, **kwargs):
        """用户点击窗口的关闭按钮"""
        if not self._closing:
            _send({"event": "close"})
        self._closing = True
        self._window_closed = True

    # ──────────────── 运行 ────────────────

    def run(self):
        try:
            webview.start(debug=False)
        finally:
            if not self._closing:
                _send({"event": "close"})


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="HQChart K线窗口（由主程序拉起）")
    parser.add_argument("code", help="股票代码")
    parser.add_argument("stock_type", nargs="?", default="stock",
                        help="stock / index / etf")
    parser.add_argument("name", nargs="?", default="", help="股票名称")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if not PYWEBVIEW_AVAILABLE:
        _send({"event": "error",
               "message": "缺少 pywebview，请执行：pip install pywebview"})
        return 1

    try:
        app = ChartApp(args.code, args.stock_type, args.name)
        _send({"event": "ready"})
        app.run()
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        _send({"event": "error", "message": str(e)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
