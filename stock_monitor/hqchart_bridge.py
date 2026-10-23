"""
HQChart K线窗口 — 主进程侧桥接
===============================
负责拉起承载进程（hqchart_chart.py）、推送 K 线数据、接收子进程事件。

架构：
    tkinter 主进程                          子进程（hqchart_chart.py）
    ┌───────────────────────┐   JSON/stdin  ┌──────────────────────┐
    │ HqChartKlineWindow    │ ────────────→ │ ChartApp             │
    │  · 拉取数据            │              │  pywebview + WebView2│
    │  · 接收事件            │ ←──────────── │  index.html          │
    └───────────────────────┘   JSON/stdout └──────────────────────┘

所有与 HQChart 版本相关的代码都收拢在 assets/hqchart/chart_bridge.js 里，
本模块只关心"数据进出"和"进程生命周期"。

前置条件：
    pip install pywebview                        （Windows 需要 Edge WebView2 运行时）
    HQChart 的 JS 文件已随仓库分发在 assets/hqchart/hqchart/ 下（Apache 2.0）

不满足条件时 gui.py 会自动回退到 matplotlib 版本（KlineWindow），程序不会崩溃。

打包后怎么跑（PyInstaller）：
    开发环境用 `python -m stock_monitor.hqchart_chart` 拉起承载进程；打包后
    sys.executable 就是应用自身的 exe，不能再 -m 导入模块，于是改用同一个 exe
    加 CHART_PROCESS_FLAG 参数区分入口，由 main.py 分发。argv 的拼装逻辑收在
    build_chart_command() 里，两种模式只差一个分支。
    注意：打包时必须把 assets/ 一起打进去（见 build_exe.py），否则打包产物会
    回退成 matplotlib 版 K线窗口。
"""

import inspect
import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from stock_monitor.stock_api import query_kline_data
from stock_monitor.replay import (
    ReplaySession, ReplayDriver,
    replay_count, clamp_interval,
    DEFAULT_INTERVAL, STATE_IDLE,
)

# ──────────────── 可选依赖：pywebview ────────────────
try:
    import webview            # noqa: F401  仅用于能力检测
    PYWEBVIEW_AVAILABLE = True
except ImportError:
    PYWEBVIEW_AVAILABLE = False

# ──────────────── 本地资产路径 ────────────────
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
ASSETS_DIR = PACKAGE_DIR / "assets" / "hqchart"
INDEX_HTML = ASSETS_DIR / "index.html"
# HQChart 开源版（Apache 2.0）已随仓库分发，放在 hqchart/ 子目录，
# 避免与本项目自有的 index.html / app.css / chart_bridge.js 混淆。
# 主库文件叫 umychart.js；完整加载列表见 index.html
HQCHART_JS = ASSETS_DIR / "hqchart" / "umychart.js"

# ──────────────── 周期参数 ────────────────
# PERIOD_TO_HQCHART：映射到 chart_bridge.js 里的 periodConstant
PERIOD_TO_HQCHART = {
    "daily":   "K_DAY",
    "weekly":  "K_WEEK",
    "monthly": "K_MON",
}
# 各周期请求的日线聚合条数（与 gui.py 中原 KlineWindow 的 count_map 一致）
PERIOD_COUNT_MAP = {
    "daily":   120,
    "weekly":  400,
    "monthly": 800,
}
DEFAULT_PERIOD = "daily"

# ──────────────── 子进程拉起方式 ────────────────
# 开发环境：python -m stock_monitor.hqchart_chart <code> <type> <name>
# 打包环境：stock_monitor.exe --hqchart-chart <code> <type> <name>
# 打包后 sys.executable 就是本程序自身，-m 无法生效，所以要用参数区分入口，
# 由 main.py 看到 CHART_PROCESS_FLAG 时改走 hqchart_chart.main()
CHART_SCRIPT = "stock_monitor.hqchart_chart"
CHART_PROCESS_FLAG = "--hqchart-chart"


def _is_frozen() -> bool:
    """是否运行在 PyInstaller 等打包产物里"""
    return bool(getattr(sys, "frozen", False))


def build_chart_command(executable, chart_args, frozen=False):
    """组合出拉起承载进程的完整命令行。

    纯函数，便于单元测试。

    参数：
        executable: 可执行文件（开发环境是 python，打包后是 exe）
        chart_args:  承载进程的位置参数 [代码, 类型, 名称]
        frozen:      是否运行在打包产物里

    返回：argv 列表
    """
    argv = [executable]
    if frozen:
        argv.append(CHART_PROCESS_FLAG)
    else:
        argv += ["-m", CHART_SCRIPT]
    return argv + list(chart_args)


# ──────────────── 可用性检测 ────────────────

def hqchart_assets_available() -> bool:
    """本地是否已放置 HQChart 的 JS 文件"""
    return INDEX_HTML.is_file() and HQCHART_JS.is_file()


def pywebview_compatible() -> bool:
    """已安装的 pywebview 是否支持本模块用到的 API。

    pywebview 5.x 移除了 @webview.expose，改为 create_window(js_api=实例)。
    版本对不上时子进程会在导入阶段就崩掉（表现为窗口弹出瞬间又消失），
    所以在拉起进程之前先在这里判掉，直接回退到 matplotlib 版本。
    """
    if not PYWEBVIEW_AVAILABLE:
        return False
    try:
        params = inspect.signature(webview.create_window).parameters
    except (TypeError, ValueError, AttributeError, NameError):
        return False
    return "js_api" in params and callable(getattr(webview, "start", None))


def is_available() -> bool:
    """HQChart 方案是否可用（pywebview 版本可用 且 本地 JS 已就位）"""
    return pywebview_compatible() and hqchart_assets_available()


def unavailable_reason() -> str:
    """说明不可用的原因，用于界面提示"""
    if not PYWEBVIEW_AVAILABLE:
        return "缺少 pywebview，请执行：pip install pywebview"
    if not pywebview_compatible():
        return ("pywebview 版本不兼容（需要 5.x，当前版本缺少 create_window 的 js_api 参数），"
                "请执行：pip install -U pywebview")
    if not HQCHART_JS.is_file():
        return ("缺少 HQChart 库文件（%s）。该文件应当随仓库一起分发，"
                "请检查 checkout 是否完整，以及 .gitignore 是否误排除了 "
                "assets/hqchart/hqchart/ 目录" % HQCHART_JS)
    if not INDEX_HTML.is_file():
        return "缺少 %s\\index.html" % ASSETS_DIR
    return "原因未知"


# ──────────────── 通信协议编解码 ────────────────

def encode_line(msg: dict) -> bytes:
    """把一条消息编码成一行 UTF-8 JSON（以 \\n 结尾）"""
    return (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")


def decode_line(line):
    """解析一行 JSON 消息。

    返回：
        dict   — 解析成功
        None   — 空行
    抛出：
        ValueError — 内容不是合法的 JSON 对象
    """
    if isinstance(line, bytes):
        line = line.decode("utf-8", "replace")
    line = line.strip()
    if not line:
        return None
    try:
        msg = json.loads(line)
    except (ValueError, TypeError) as e:
        raise ValueError("非法消息：%s" % e)
    if not isinstance(msg, dict):
        raise ValueError("消息必须是 JSON 对象")
    return msg


# ──────────────── 数据适配 ────────────────

def build_chart_payload(code: str, stock_type: str, name: str,
                        period: str, klines: list) -> dict:
    """把 query_kline_data 的结果转成 HQChart 需要的一次数据载荷。

    纯函数，便于单元测试。
    """
    period = period or DEFAULT_PERIOD
    period_type = PERIOD_TO_HQCHART.get(period, PERIOD_TO_HQCHART[DEFAULT_PERIOD])
    symbol = "CN.%s" % code

    items = []
    for k in (klines or []):
        if not isinstance(k, dict):
            continue
        items.append({
            "id":         "%s.%s" % (symbol, period_type),
            "periodType": period_type,
            "open":       float(k.get("open", 0) or 0),
            "high":       float(k.get("high", 0) or 0),
            "low":        float(k.get("low", 0) or 0),
            "close":      float(k.get("close", 0) or 0),
            "volume":     int(k.get("volume", 0) or 0),
            "amount":     0,                 # 新浪接口不提供成交额
            "date":       k.get("date", ""),
        })

    return {
        "code":       code,
        "stockType":  stock_type,
        "name":       name or "",
        "symbol":     symbol,
        "period":     period,
        "periodType": period_type,
        "items":      items,
    }


# ──────────────── K线窗口（HQChart 后端） ────────────────

class HqChartKlineWindow:
    """HQChart K线窗口句柄。

    对外接口与 gui.py 中的 matplotlib 版 KlineWindow 对齐：
        alive() / activate() / close() / fetch(period)

    历史回放接口（两个后端一致）：
        start_replay() / pause_replay() / resume_replay() / stop_replay()
    回放的定拍在父进程（见 stock_monitor/replay.py 的 ReplayDriver），
    子进程只负责照着画。
    """

    def __init__(self, root, code: str, stock_type: str, name: str = ""):
        """
        参数：
            root:       tkinter 根窗口，用于把子进程事件调度回主线程
            code:       股票代码
            stock_type: stock / index / etf
            name:       股票名称
        """
        self._root = root
        self.code = code
        self.stock_type = stock_type
        self.name = name
        self.period = DEFAULT_PERIOD
        self._loading = False

        # 回调（由调用方设置）
        self.on_crash = None     # callable(message)  子进程异常退出
        self.on_closed = None    # callable()         用户正常关闭窗口

        self._send_lock = threading.Lock()
        self._closed = False          # 是否收到子进程的 close 事件
        self._exit_notified = False
        self._stderr = tempfile.TemporaryFile()

        # ── 历史回放 ──
        # 回放由父进程定拍：每隔一段时间把"揭示前 N 根"的载荷推给子进程，
        # 子进程只是照着画。完整 K 线一直留在本地，暂停/继续都不用重新拉数据。
        self._last_klines = []        # 最近一次取到的完整 K 线（未切片）
        self._replay_wanted = False   # 数据回来后是否要开始回放
        self._replay_interval = DEFAULT_INTERVAL
        self.replay = ReplayDriver(
            self._root, self._render_replay, on_state=self._on_replay_state,
            interval=DEFAULT_INTERVAL,
        )

        self.proc = self._spawn()
        threading.Thread(target=self._reader, daemon=True).start()
        self.fetch(DEFAULT_PERIOD)

    # ── 子进程生命周期 ──

    def _spawn(self) -> subprocess.Popen:
        """拉起承载进程（argv 由 build_chart_command 按运行模式拼装）"""
        cmd = build_chart_command(sys.executable, self._chart_args())
        return subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
        )

    def _chart_args(self) -> list:
        """承载进程需要的位置参数：代码 / 类型 / 名称"""
        return [self.code, self.stock_type, self.name or ""]

    def _send(self, msg: dict):
        """向子进程写一行 JSON，写入失败静默（子进程可能已退出）"""
        if self.proc is None:
            return
        try:
            with self._send_lock:
                self.proc.stdin.write(encode_line(msg))
                self.proc.stdin.flush()
        except (OSError, ValueError, BrokenPipeError):
            pass

    def _stderr_tail(self, limit: int = 800) -> str:
        """取出子进程 stderr 的尾部，用于崩溃提示"""
        try:
            self._stderr.seek(0)
            text = self._stderr.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            text = ""
        return text[-limit:].strip()

    # ── 读取子进程事件 ──

    def _reader(self):
        """读取子进程 stdout 的事件流"""
        try:
            for raw in self.proc.stdout:
                try:
                    msg = decode_line(raw)
                except ValueError:
                    continue
                if msg is None:
                    continue
                self._dispatch(msg)
        except (OSError, ValueError):
            pass
        finally:
            self._on_exit()

    def _dispatch(self, msg: dict):
        """把子进程事件调度到 tkinter 主线程处理"""
        event = msg.get("event")
        if event == "period":
            period = msg.get("period")
            if period:
                self._root.after(0, self._on_period_event, period)
        elif event == "refresh":
            self._root.after(0, self.fetch)
        elif event == "replay_start":
            interval = msg.get("interval")
            self._root.after(0, self.start_replay, interval)
        elif event == "replay_pause":
            self._root.after(0, self.pause_replay)
        elif event == "replay_resume":
            self._root.after(0, self.resume_replay)
        elif event == "replay_stop":
            self._root.after(0, self.stop_replay)
        elif event == "replay_speed":
            self._root.after(0, self.set_replay_interval, msg.get("interval"))
        elif event == "chart_error":
            self._root.after(0, self._on_chart_error, msg.get("message", ""))
        elif event == "error":
            self._root.after(0, self._on_error, msg.get("message", ""))
        elif event == "close":
            # 只记录状态；回调统一由 _on_exit() 触发，避免重复通知
            self._closed = True

    def _cleanup(self, proc):
        """关闭与子进程之间的管道和 stderr 临时文件。

        子进程退出后如果父进程不主动收尾，解释器退出时会去关闭那些已经失效的
        文件描述符，打印 `OSError: [Errno 22] Invalid argument` 的噪声。
        这里与 _send 共用一把锁，避免一边关管道一边还有写入在跑。
        """
        with self._send_lock:
            for handle in (self._stderr,
                           getattr(proc, "stdout", None),
                           getattr(proc, "stdin", None),
                           getattr(proc, "stderr", None)):
                if handle is None:
                    continue
                try:
                    handle.close()
                except Exception:
                    pass
        if proc is None:
            return
        try:
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _on_exit(self):
        """子进程退出：区分"用户关窗"和"异常崩溃"两种情况。

        本方法在读取线程中执行，因此回调必须调度到 tkinter 主线程，
        不能直接调用（tkinter 不是线程安全的）。
        """
        if self._exit_notified:
            return
        self._exit_notified = True
        proc = self.proc
        self.proc = None
        code = proc.poll() if proc is not None else None
        closed = self._closed

        detail = self._stderr_tail()      # 必须先取，之后再关管道
        self._cleanup(proc)

        # 回放定时器必须停掉，否则这个已经死掉的窗口还会一直给自己发消息
        self._root.after(0, self._shutdown_replay)

        if closed:
            self._root.after(0, self._handle_closed)
            return

        message = "HQChart 窗口意外退出（退出码 %s）" % code
        if detail:
            message += "\n\n错误输出：\n%s" % detail
        self._root.after(0, self._handle_crash, message)

    # ── 子进程事件处理（均在 tkinter 主线程执行） ──

    def _on_period_event(self, period: str):
        """用户在页面上切换了周期"""
        if period != self.period:
            self.fetch(period)

    def _on_chart_error(self, message: str):
        """图表初始化或数据写入失败 —— 提示一次，不重复弹窗"""
        if getattr(self, "_chart_error_shown", False):
            return
        self._chart_error_shown = True
        self._on_error(message)

    def _on_error(self, message: str):
        """向用户报告错误（子进程窗口仍在，不影响其显示）"""
        from tkinter import messagebox
        messagebox.showerror("K线图表", message or "未知错误", parent=self._root)

    def _handle_closed(self):
        """用户关闭了窗口（在 tkinter 主线程执行）"""
        if self.on_closed:
            self.on_closed()

    def _handle_crash(self, message: str):
        """子进程异常退出（在 tkinter 主线程执行）"""
        if self.on_crash:
            self.on_crash(message)

    # ── 数据获取 ──

    def fetch(self, period: str = None, count: int = None):
        """按指定周期拉取并推送 K 线数据。

        count 不为空时按它决定拉多少根（回放要用比平时多得多），
        否则用该周期的默认条数。
        """
        if period:
            self.period = period
        # 换周期 / 手动刷新都会整图重画，进行中的回放先退出
        if self.replay.active:
            self.replay.stop(render_all=False)
        if self._loading:
            return
        self._loading = True
        want = count if count else PERIOD_COUNT_MAP.get(
            self.period, PERIOD_COUNT_MAP[DEFAULT_PERIOD])
        threading.Thread(target=self._fetch_thread, args=(want,),
                         daemon=True).start()

    def _fetch_thread(self, count: int):
        result = query_kline_data(self.code, self.stock_type,
                                  period=self.period, count=count)
        self._root.after(0, self._on_data_ready, result)

    def _on_data_ready(self, result: dict):
        self._loading = False
        if not isinstance(result, dict):
            return
        if result.get("error"):
            # 为回放拉取失败也要复位，否则"回放"按钮会一直亮着
            self._replay_wanted = False
            payload = build_chart_payload(self.code, self.stock_type,
                                          self.name, self.period, [])
            payload["error"] = result["error"]
            self._send({"cmd": "data", "payload": payload})
            return

        klines = result.get("klines") or []
        self._last_klines = klines

        # 回放模式下多要了一批数据：直接开播，且先不推全量——否则第一帧会
        # 闪一下"全部可见"，回放就没意思了
        if self._replay_wanted:
            self._replay_wanted = False
            self.start_replay(self._replay_interval, klines)
            return

        payload = build_chart_payload(self.code, self.stock_type,
                                      self.name, self.period, klines)
        self._send({"cmd": "data", "payload": payload})

    # ── 历史回放 ──

    def start_replay(self, interval=None, klines=None):
        """从最早的 bar 开始回放，一直播到最新一根。

        平时看盘只要最近 120 根，回放要从最左边播起来，所以先多要一批历史
        再开播。已经拿到数据（klines 不为空）时直接开播，省掉一次网络往返。
        """
        self._replay_interval = clamp_interval(interval)
        if klines is None:
            self._replay_wanted = True
            self.fetch(count=replay_count(self.period))
            return
        self._begin_replay(klines)

    def _begin_replay(self, klines: list):
        """数据就位：建一个回放进度并开播"""
        klines = klines or []
        if len(klines) < 3:
            self._notify_status("数据不足，无法回放")
            return
        self._last_klines = klines
        session = ReplaySession(len(klines))
        self.replay.interval = self._replay_interval
        self.replay.start(session, self._replay_interval)

    def pause_replay(self):
        """暂停回放（画面停在当前位置）"""
        self.replay.pause()

    def resume_replay(self):
        """继续回放"""
        self.replay.resume()

    def stop_replay(self, render_all: bool = True):
        """结束回放。默认把完整数据画回去（相当于"回到最新"）"""
        self.replay.stop(render_all=render_all)

    def set_replay_interval(self, ms):
        """调整播放速度"""
        self._replay_interval = clamp_interval(ms)
        self.replay.set_interval(ms)

    def _shutdown_replay(self):
        """收尾：停掉回放定时器，别再往死掉的管道发消息"""
        replay = getattr(self, "replay", None)
        if replay is not None:
            replay.stop(render_all=False)

    def _render_replay(self, count: int):
        """回放每推进一步：把前 count 根推给子进程重画。

        进度归零（stop 之后回调）时发一份完整载荷，让图表退出回放模式。
        """
        klines = self._last_klines
        if not klines:
            return
        if self.replay.session is None:
            self._send({"cmd": "data", "payload": build_chart_payload(
                self.code, self.stock_type, self.name, self.period, klines)})
            return
        self._send_replay_payload(self.replay.session.count, self.replay.state)

    def _send_replay_payload(self, count: int, state: str):
        """推送"揭示前 count 根"的回放载荷"""
        klines = self._last_klines
        count = max(1, min(count, len(klines)))
        payload = build_chart_payload(self.code, self.stock_type,
                                      self.name, self.period, klines[:count])
        payload["replay"] = True
        payload["replayCount"] = count
        payload["replayTotal"] = len(klines)
        payload["replayDate"] = klines[count - 1].get("date", "")
        payload["replayState"] = state
        self._send({"cmd": "data", "payload": payload})

    def _on_replay_state(self, state: str, session):
        """回放状态变了（暂停/继续/结束）：只同步状态，不重画图表"""
        if session is None or not self._last_klines:
            return
        klines = self._last_klines
        count = max(1, min(session.count, len(klines)))
        self._send({"cmd": "replay_state",
                    "state": state,
                    "count": count,
                    "total": len(klines),
                    "date": klines[count - 1].get("date", "")})

    def _notify_status(self, message: str):
        """推一条提示信息给子进程：只在状态栏显示，不动当前图表"""
        payload = build_chart_payload(self.code, self.stock_type,
                                      self.name, self.period, [])
        payload["error"] = message
        self._send({"cmd": "data", "payload": payload})

    # ── 对外接口 ──

    def alive(self) -> bool:
        """子进程是否仍存活"""
        return self.proc is not None and self.proc.poll() is None

    def activate(self):
        """请求把窗口提到前台"""
        if self.alive():
            self._send({"cmd": "focus"})

    def close(self):
        """请求关闭窗口"""
        if self.alive():
            self._send({"cmd": "close"})

    def terminate(self):
        """立即结束子进程并收尾管道（主程序退出时调用）。

        不这么做的话，子进程会被解释器退出时的清理带着一起收尾，
        关闭已经失效的管道句柄就会打印 OSError 噪声。
        """
        if self.proc is None:
            return
        self._shutdown_replay()
        proc = self.proc
        self.proc = None
        try:
            proc.terminate()
        except Exception:
            pass
        self._cleanup(proc)
