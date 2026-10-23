"""
hqchart_bridge 模块单元测试
============================
覆盖进程间通信协议的编解码、Python K 线数据到 HQChart 载荷的适配，
以及子进程事件的分发逻辑。全部使用 mock，不启动真实子进程，不访问网络。
"""

import contextlib
import inspect
import io
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from stock_monitor import hqchart_bridge


# ──────────────── 测试辅助 ────────────────

_KLINES = [
    {"date": "2026-07-01", "open": 18.50, "close": 18.88,
     "high": 19.00, "low": 18.40, "volume": 12345678},
    {"date": "2026-07-02", "open": 18.88, "close": 18.60,
     "high": 18.95, "low": 18.50, "volume": 9876543},
    {"date": "2026-07-03", "open": 18.60, "close": 18.72,
     "high": 18.80, "low": 18.55, "volume": 0},
]


class FakeRoot:
    """模拟 tkinter 根窗口，记录 after() 的回调并立即执行。"""

    def __init__(self):
        self.calls = []

    def after(self, delay, fn, *args):
        self.calls.append((delay, fn, args))
        fn(*args)
        return None


class FakeProc:
    """模拟 subprocess.Popen，仅提供被测代码用到的接口。"""

    def __init__(self, stdout_bytes=b"", code=0):
        self.stdout = io.BytesIO(stdout_bytes)
        self._code = code
        self.stdin = io.BytesIO()

    def poll(self):
        return self._code


def must_not_be_called(msg):
    """返回一个一旦被调用就断言失败的占位函数。"""
    def _raise(*args, **kwargs):
        raise AssertionError(msg)
    return _raise


def _bare_bridge(root=None):
    """构造一个跳过 __init__ 的桥接对象，便于单独测试各方法。"""
    bridge = hqchart_bridge.HqChartKlineWindow.__new__(
        hqchart_bridge.HqChartKlineWindow)
    bridge._root = root or FakeRoot()
    bridge.code = "600000"
    bridge.stock_type = "stock"
    bridge.name = "浦发银行"
    bridge.period = "daily"
    bridge._loading = False
    bridge._closed = False
    bridge._exit_notified = False
    bridge._stderr = io.BytesIO(b"")
    bridge.proc = None
    bridge._send_lock = threading.RLock()
    bridge.on_crash = None
    bridge.on_closed = None
    return bridge


# ──────────────── 周期映射 ────────────────

def test_period_mapping():
    assert hqchart_bridge.PERIOD_TO_HQCHART["daily"] == "K_DAY"
    assert hqchart_bridge.PERIOD_TO_HQCHART["weekly"] == "K_WEEK"
    assert hqchart_bridge.PERIOD_TO_HQCHART["monthly"] == "K_MON"


def test_period_count_map_covers_all_periods():
    assert set(hqchart_bridge.PERIOD_COUNT_MAP) == set(hqchart_bridge.PERIOD_TO_HQCHART)


# ──────────────── 数据适配 ────────────────

def test_build_payload_maps_fields():
    payload = hqchart_bridge.build_chart_payload(
        "600000", "stock", "浦发银行", "daily", _KLINES)

    assert payload["code"] == "600000"
    assert payload["stockType"] == "stock"
    assert payload["name"] == "浦发银行"
    assert payload["symbol"] == "CN.600000"
    assert payload["period"] == "daily"
    assert payload["periodType"] == "K_DAY"

    items = payload["items"]
    assert len(items) == 3
    first = items[0]
    assert first["id"] == "CN.600000.K_DAY"
    assert first["periodType"] == "K_DAY"
    assert first["open"] == 18.50
    assert first["high"] == 19.00
    assert first["low"] == 18.40
    assert first["close"] == 18.88
    assert first["volume"] == 12345678
    assert first["amount"] == 0
    assert first["date"] == "2026-07-01"


def test_build_payload_keeps_order_and_zero_volume():
    payload = hqchart_bridge.build_chart_payload(
        "600000", "stock", "", "daily", _KLINES)

    assert [i["date"] for i in payload["items"]] == \
        [k["date"] for k in _KLINES]
    # 成交量为 0 的 bar 不应被丢弃
    assert payload["items"][2]["volume"] == 0
    assert len(payload["items"]) == 3


def test_build_payload_weekly_uses_week_period_type():
    payload = hqchart_bridge.build_chart_payload(
        "000001", "index", "上证指数", "weekly", _KLINES)

    assert payload["period"] == "weekly"
    assert payload["periodType"] == "K_WEEK"
    assert payload["items"][0]["id"] == "CN.000001.K_WEEK"


def test_build_payload_unknown_period_falls_back_to_daily():
    payload = hqchart_bridge.build_chart_payload(
        "600000", "stock", "", "invalid", _KLINES)

    assert payload["period"] == "invalid"
    assert payload["periodType"] == "K_DAY"


def test_build_payload_empty_and_dirty_input():
    assert hqchart_bridge.build_chart_payload(
        "600000", "stock", "", "daily", [])["items"] == []

    assert hqchart_bridge.build_chart_payload(
        "600000", "stock", "", "daily", None)["items"] == []

    # 非 dict 元素被跳过，缺失字段补零
    payload = hqchart_bridge.build_chart_payload(
        "600000", "stock", "", "daily", [None, "bad", {}])

    assert len(payload["items"]) == 1
    assert payload["items"][0]["open"] == 0.0
    assert payload["items"][0]["volume"] == 0
    assert payload["items"][0]["date"] == ""


def test_build_payload_casts_numeric_strings():
    payload = hqchart_bridge.build_chart_payload(
        "600000", "stock", "", "daily",
        [{"date": "2026-07-01", "open": "18.5", "high": "19",
          "low": "18.4", "close": "18.88", "volume": "100"}])

    item = payload["items"][0]
    assert item["open"] == 18.5
    assert item["high"] == 19.0
    assert item["low"] == 18.4
    assert item["close"] == 18.88
    assert item["volume"] == 100


# ──────────────── 子进程命令行拼装 ────────────────

def test_chart_command_dev_mode_uses_module_invocation():
    argv = hqchart_bridge.build_chart_command(
        "/usr/bin/python3", ["600000", "stock", "浦发银行"])

    assert argv == ["/usr/bin/python3", "-m",
                    "stock_monitor.hqchart_chart",
                    "600000", "stock", "浦发银行"]
    assert "--hqchart-chart" not in argv


def test_chart_command_frozen_mode_uses_self_invocation():
    """打包后不能 -m，改用同一个 exe 加参数区分入口"""
    argv = hqchart_bridge.build_chart_command(
        "C:/app/stock_monitor.exe", ["600000", "index", "上证指数"],
        frozen=True)

    assert argv == ["C:/app/stock_monitor.exe", "--hqchart-chart",
                    "600000", "index", "上证指数"]
    assert "-m" not in argv


def test_chart_command_freezes_are_mutually_exclusive():
    """同一个命令行里不该同时出现 -m 和 --hqchart-chart"""
    for frozen in (False, True):
        argv = hqchart_bridge.build_chart_command(
            "py", ["600000", "stock", ""], frozen=frozen)

        flags = [a for a in argv if a.startswith("-")]
        assert len(flags) == 1


def test_chart_command_default_is_dev_mode():
    assert hqchart_bridge.build_chart_command("py", ["600000", "stock", ""]) \
        == ["py", "-m", "stock_monitor.hqchart_chart", "600000", "stock", ""]


def test_chart_command_does_not_mutate_input():
    """argv 是新列表，调用方的列表不被改动"""
    args = ["600000", "stock", "浦发银行"]

    hqchart_bridge.build_chart_command("py", args)
    hqchart_bridge.build_chart_command("py", args, frozen=True)

    assert args == ["600000", "stock", "浦发银行"]


def test_chart_command_flag_constant_is_used_by_spawn():
    """main.py 按这个常量分发，必须和 build_chart_command 里的分支一致"""
    assert hqchart_bridge.CHART_PROCESS_FLAG in \
        hqchart_bridge.build_chart_command("py", [], frozen=True)
    assert hqchart_bridge.CHART_SCRIPT == "stock_monitor.hqchart_chart"


def test_is_frozen_false_in_dev():
    assert hqchart_bridge._is_frozen() is False


# ──────────────── 协议编解码 ────────────────

def test_encode_line_is_utf8_json_with_newline():
    line = hqchart_bridge.encode_line({"cmd": "data", "name": "浦发银行"})

    assert line.endswith(b"\n")
    assert b"\\n" not in line
    assert line.decode("utf-8").startswith('{"cmd"')


def test_decode_line_roundtrip():
    msg = {"cmd": "data", "payload": {"name": "浦发银行", "items": [1, 2, 3]}}

    assert hqchart_bridge.decode_line(
        hqchart_bridge.encode_line(msg)) == msg


def test_decode_line_empty_returns_none():
    assert hqchart_bridge.decode_line(b"") is None
    assert hqchart_bridge.decode_line("   \n") is None


def test_decode_line_invalid_raises():
    with pytest.raises(ValueError):
        hqchart_bridge.decode_line(b"not json")
    # 非对象（数组、字符串）同样视为非法
    with pytest.raises(ValueError):
        hqchart_bridge.decode_line(b"[1, 2, 3]")
    with pytest.raises(ValueError):
        hqchart_bridge.decode_line('"hello"')


# ──────────────── 可用性检测 ────────────────

def test_is_available_false_without_assets(monkeypatch, tmp_path):
    monkeypatch.setattr(
        hqchart_bridge, "INDEX_HTML", tmp_path / "index.html")
    monkeypatch.setattr(
        hqchart_bridge, "HQCHART_JS", tmp_path / "umychart.js")
    monkeypatch.setattr(hqchart_bridge, "PYWEBVIEW_AVAILABLE", True)
    monkeypatch.setattr(hqchart_bridge, "pywebview_compatible", lambda: True)

    assert hqchart_bridge.hqchart_assets_available() is False
    assert hqchart_bridge.is_available() is False
    assert "umychart.js" in hqchart_bridge.unavailable_reason()


def test_is_available_requires_pywebview(monkeypatch, tmp_path):
    html = tmp_path / "index.html"
    html.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(hqchart_bridge, "INDEX_HTML", html)
    monkeypatch.setattr(
        hqchart_bridge, "HQCHART_JS", tmp_path / "umychart.js")
    monkeypatch.setattr(hqchart_bridge, "PYWEBVIEW_AVAILABLE", False)

    assert hqchart_bridge.is_available() is False
    assert "pywebview" in hqchart_bridge.unavailable_reason()


def test_is_available_true_when_all_present(monkeypatch, tmp_path):
    html = tmp_path / "index.html"
    html.write_text("<html></html>", encoding="utf-8")
    js = tmp_path / "umychart.js"
    js.write_text("/* hqchart */", encoding="utf-8")
    monkeypatch.setattr(hqchart_bridge, "INDEX_HTML", html)
    monkeypatch.setattr(hqchart_bridge, "HQCHART_JS", js)
    monkeypatch.setattr(hqchart_bridge, "PYWEBVIEW_AVAILABLE", True)
    monkeypatch.setattr(hqchart_bridge, "pywebview_compatible", lambda: True)

    assert hqchart_bridge.hqchart_assets_available() is True
    assert hqchart_bridge.is_available() is True
    assert hqchart_bridge.unavailable_reason() == "原因未知"


def test_is_available_false_when_pywebview_version_unsupported(monkeypatch):
    """pywebview 太旧（create_window 没有 js_api 参数）时判为不可用"""
    monkeypatch.setattr(hqchart_bridge, "PYWEBVIEW_AVAILABLE", True)
    monkeypatch.setattr(hqchart_bridge, "pywebview_compatible", lambda: False)

    assert hqchart_bridge.is_available() is False
    assert "版本不兼容" in hqchart_bridge.unavailable_reason()


def _install_fake_webview(monkeypatch, params, has_start=True):
    """装一个签名为 (params) 的假 webview 模块，用来模拟不同版本"""
    fake = SimpleNamespace()
    fake.create_window = lambda *a, **k: None
    if has_start:
        fake.start = lambda *a, **k: None
    monkeypatch.setattr(hqchart_bridge, "webview", fake)
    monkeypatch.setattr(hqchart_bridge, "inspect",
                        SimpleNamespace(signature=lambda _obj: inspect.Signature(params)))
    return fake


def _inspect_param(name, kind=inspect.Parameter.POSITIONAL_OR_KEYWORD):
    return inspect.Parameter(name, kind)


def test_pywebview_compatible_accepts_js_api(monkeypatch):
    _install_fake_webview(
        monkeypatch,
        [_inspect_param("title"),
         _inspect_param("js_api", inspect.Parameter.VAR_KEYWORD)])

    assert hqchart_bridge.pywebview_compatible() is True


def test_pywebview_compatible_rejects_legacy_api(monkeypatch):
    """旧版 create_window 没有 js_api 参数（5.x 才引入）"""
    _install_fake_webview(monkeypatch, [_inspect_param("title")])

    assert hqchart_bridge.pywebview_compatible() is False


def test_pywebview_compatible_handles_broken_modules(monkeypatch):
    """无法取签名、缺少 start()、模块不存在，都要返回 False 而不是抛异常"""
    _install_fake_webview(monkeypatch, [_inspect_param("title")], has_start=False)
    assert hqchart_bridge.pywebview_compatible() is False

    def _explode(_obj):
        raise ValueError("不能内省这个调用对象")

    monkeypatch.setattr(inspect, "signature", _explode)
    assert hqchart_bridge.pywebview_compatible() is False

    monkeypatch.delattr(hqchart_bridge, "webview", raising=False)
    assert hqchart_bridge.pywebview_compatible() is False


# ──────────────── 子进程事件分发 ────────────────

def test_dispatch_period_fetches_new_period():
    bridge = _bare_bridge()
    fetched = []
    bridge.fetch = lambda period=None: fetched.append(period)

    bridge._dispatch({"event": "period", "period": "weekly"})

    assert fetched == ["weekly"]


def test_dispatch_same_period_is_noop():
    bridge = _bare_bridge()
    bridge.fetch = must_not_be_called("同一周期不应重新拉取")

    bridge._dispatch({"event": "period", "period": "daily"})


def test_dispatch_missing_period_is_ignored():
    bridge = _bare_bridge()
    bridge.fetch = must_not_be_called("缺少 period 字段时不应拉取")

    bridge._dispatch({"event": "period"})


def test_dispatch_refresh_calls_fetch():
    bridge = _bare_bridge()
    bridge.fetch = lambda period=None: "fetched"
    captured = bridge._root.calls

    bridge._dispatch({"event": "refresh"})

    assert captured and captured[0][0] == 0
    assert captured[0][2] == ()


def test_dispatch_close_only_marks_state():
    """close 事件只记录状态，回调用 _on_exit 统一触发（避免重复通知）"""
    bridge = _bare_bridge()
    bridge.on_closed = must_not_be_called("close 事件本身不应触发回调")

    bridge._dispatch({"event": "close"})

    assert bridge._closed is True


def test_dispatch_ignores_unknown_event():
    bridge = _bare_bridge()
    bridge.fetch = must_not_be_called("未知事件不应触发任何动作")
    bridge.on_closed = must_not_be_called("未知事件不应触发关闭回调")

    bridge._dispatch({"event": "something_else"})


def test_on_exit_clean_close_calls_on_closed():
    bridge = _bare_bridge()
    bridge.proc = FakeProc(code=0)
    bridge._closed = True
    closed = []
    bridge.on_closed = lambda: closed.append(True)

    bridge._on_exit()

    assert closed == [True]
    assert bridge.proc is None
    assert bridge._exit_notified is True


def test_on_exit_without_close_reports_crash_with_stderr():
    bridge = _bare_bridge()
    bridge.proc = FakeProc(code=1)
    bridge._stderr = io.BytesIO(b"Traceback (most recent call last):\n  boom\n")
    crashes = []
    bridge.on_crash = lambda msg: crashes.append(msg)

    bridge._on_exit()

    assert len(crashes) == 1
    assert "1" in crashes[0]           # 包含退出码
    assert "boom" in crashes[0]        # 包含 stderr 尾部


def test_on_exit_is_idempotent():
    bridge = _bare_bridge()
    bridge.proc = FakeProc(code=0)
    bridge._closed = True
    count = []
    bridge.on_closed = lambda: count.append(1)

    bridge._on_exit()
    bridge._on_exit()

    assert count == [1]


class _TrackedHandle:
    """记录是否被关闭的管道句柄替身。"""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_cleanup_closes_all_handles():
    stderr = _TrackedHandle()
    stdin = _TrackedHandle()
    stdout = _TrackedHandle()
    bridge = _bare_bridge()
    bridge._stderr = stderr
    bridge.proc = SimpleNamespace(
        poll=lambda: 0, stdin=stdin, stdout=stdout, stderr=_TrackedHandle())

    bridge._cleanup(bridge.proc)

    assert stderr.closed and stdin.closed and stdout.closed


def test_cleanup_survives_broken_handles():
    """关管道时的任何异常都要吞掉，不能让收尾流程崩掉"""
    class Broken:
        def close(self):
            raise OSError(22, "Invalid argument")

    bridge = _bare_bridge()
    bridge._stderr = Broken()
    proc = SimpleNamespace(poll=lambda: 0, stdin=Broken(), stdout=Broken())

    bridge._cleanup(proc)  # 不应抛异常
    bridge._cleanup(None)  # proc 为空也要安全


def test_on_exit_reads_stderr_before_closing():
    """stderr 内容必须先取出来，再关管道，否则崩溃提示里就没有错误了"""
    bridge = _bare_bridge()
    bridge.proc = FakeProc(code=1)
    bridge._stderr = io.BytesIO(b"Traceback (most recent call last):\n  boom\n")
    crashes = []
    bridge.on_crash = lambda msg: crashes.append(msg)

    bridge._on_exit()

    assert crashes and "boom" in crashes[0]
    assert bridge._stderr.closed is True


def test_terminate_kills_child_and_closes_pipes():
    stderr = _TrackedHandle()
    stdin = _TrackedHandle()
    stdout = _TrackedHandle()
    bridge = _bare_bridge()
    bridge._stderr = stderr
    bridge.proc = SimpleNamespace(
        poll=lambda: None, stdin=stdin, stdout=stdout, stderr=_TrackedHandle())

    bridge.terminate()

    assert bridge.proc is None
    assert bridge.alive() is False
    assert stderr.closed and stdin.closed and stdout.closed
    assert bridge.terminate() is None   # 重复调用安全


def test_send_is_silent_after_exit():
    bridge = _bare_bridge()
    bridge.proc = None            # 子进程已退出
    bridge._send_lock = _RLock()

    bridge._send({"cmd": "data"})  # 不应抛异常


class _RLock:
    """兼容 acquire/release 的最小锁（供 _send 使用）。"""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_send_writes_encoded_line(monkeypatch):
    bridge = _bare_bridge()

    written = io.BytesIO()
    bridge.proc = FakeProc()
    bridge.proc.stdin = written

    bridge._send({"cmd": "focus"})

    assert written.getvalue() == hqchart_bridge.encode_line({"cmd": "focus"})


# ──────────────── 历史回放 ────────────────

class NoopRoot:
    """假 tkinter 窗口：记录 after() 但不自动执行，测试里手动放行。"""

    def __init__(self):
        self.scheduled = []
        self._next_id = 0

    def after(self, delay, fn, *args):
        self._next_id += 1
        self.scheduled.append((self._next_id, delay, fn, args))
        return self._next_id

    def after_cancel(self, after_id):
        self.scheduled = [it for it in self.scheduled if it[0] != after_id]

    def fire_next(self):
        item = self.scheduled.pop(0)
        item[2](*item[3])
        return item


def _replay_klines(n=10, base="2024-01-01"):
    """造 n 根假 K 线"""
    return [{"date": base, "open": 10.0, "close": 10.5, "high": 11.0,
             "low": 9.8, "volume": 100} for _ in range(n)]


def _replay_bridge(n=10):
    """在 _bare_bridge 上补上回放相关的状态，返回 (bridge, root)"""
    bridge = _bare_bridge()
    root = NoopRoot()
    bridge._root = root
    bridge._last_klines = _replay_klines(n)
    bridge._replay_wanted = False
    bridge._replay_interval = hqchart_bridge.DEFAULT_INTERVAL
    bridge.replay = hqchart_bridge.ReplayDriver(
        root, bridge._render_replay, on_state=bridge._on_replay_state)
    bridge.proc = FakeProc()
    return bridge, root


def _drain_messages(bridge):
    """取出并清空子进程收到的所有消息"""
    text = bridge.proc.stdin.getvalue().decode("utf-8")
    msgs = [hqchart_bridge.decode_line(line)
            for line in text.splitlines() if line.strip()]
    bridge.proc.stdin.seek(0)
    bridge.proc.stdin.truncate()
    return msgs


def test_replay_start_sends_reveal_frame_and_state():
    bridge, root = _replay_bridge(10)

    bridge.start_replay(400, _replay_klines(10))

    msgs = _drain_messages(bridge)
    # 先一条纯状态消息（不重画），再一条带进度的数据帧
    assert [m["cmd"] for m in msgs] == ["replay_state", "data"]

    frame = msgs[1]["payload"]
    assert frame["replay"] is True
    assert frame["replayCount"] == 5            # 默认第一帧 5 根
    assert frame["replayTotal"] == 10
    assert frame["replayState"] == "running"
    assert len(frame["items"]) == 5
    assert bridge.replay.running is True
    assert root.scheduled and root.scheduled[0][1] == 400


def test_replay_tick_advances_the_revealed_count():
    bridge, root = _replay_bridge(10)

    bridge.start_replay(400, _replay_klines(10))
    _drain_messages(bridge)

    root.fire_next()

    msgs = _drain_messages(bridge)
    assert len(msgs) == 1
    assert msgs[0]["payload"]["replayCount"] == 6


def test_replay_reaches_total_and_finishes():
    bridge, root = _replay_bridge(6)

    bridge.start_replay(100, _replay_klines(6))
    _drain_messages(bridge)
    root.fire_next()                            # 5 → 6，播完

    assert bridge.replay.state == "finished"
    assert bridge.replay.session.finished is True
    assert root.scheduled == []                 # 播完不再排下一次


def test_replay_pause_freezes_and_stop_restores_full_data():
    bridge, root = _replay_bridge(10)

    bridge.start_replay(400, _replay_klines(10))
    _drain_messages(bridge)

    bridge.pause_replay()
    paused = _drain_messages(bridge)
    assert paused[0]["cmd"] == "replay_state"
    assert paused[0]["state"] == "paused"
    assert root.scheduled == []

    bridge.stop_replay()
    stopped = _drain_messages(bridge)
    # 一条"空闲"复位状态栏，再一份完整数据重画（不带 replay 标记 = 退出回放）
    assert [m["cmd"] for m in stopped] == ["replay_state", "data"]
    assert stopped[0]["state"] == "idle"
    assert "replay" not in stopped[1]["payload"]
    assert len(stopped[1]["payload"]["items"]) == 10
    assert bridge.replay.session is None
    assert bridge.replay.state == "idle"


def test_replay_start_without_data_triggers_a_fetch():
    bridge, root = _replay_bridge(10)
    fetched = []
    bridge.fetch = lambda period=None, count=None: fetched.append(count)

    bridge.start_replay(700)

    assert bridge._replay_wanted is True
    assert fetched == [hqchart_bridge.replay_count("daily")]
    assert bridge.replay.session is None


def test_replay_start_while_a_fetch_is_in_flight_waits():
    """上一次请求还没回来时点"回放"要先等一下。

    否则它记着"想回放"，等那批普通的 120 根一回来就会用 120 根开播，
    而不是回放该有的 500 根。
    """
    bridge, root = _replay_bridge(10)
    fetched = []
    bridge.fetch = lambda period=None, count=None: fetched.append(count)
    bridge._loading = True

    bridge.start_replay(700)

    assert fetched == []
    assert bridge._replay_wanted is False
    assert bridge.replay.session is None


def test_replay_fetch_result_starts_the_replay(monkeypatch):
    bridge, root = _replay_bridge(10)
    bridge._replay_wanted = True
    bridge._replay_interval = 300

    bridge._on_data_ready({"error": None, "klines": _replay_klines(12)})

    msgs = _drain_messages(bridge)
    assert [m["cmd"] for m in msgs] == ["replay_state", "data"]
    # 关键：回放开播时不能先推一份全量数据，否则第一帧会闪一下"全部可见"
    assert msgs[1]["payload"]["replay"] is True
    assert len(msgs[1]["payload"]["items"]) == 5
    assert msgs[1]["payload"]["replayTotal"] == 12
    assert bridge._replay_wanted is False


def test_normal_fetch_still_sends_full_payload(monkeypatch):
    bridge, root = _replay_bridge(10)

    bridge._on_data_ready({"error": None, "klines": _replay_klines(7)})

    msgs = _drain_messages(bridge)
    assert len(msgs) == 1
    assert msgs[0]["cmd"] == "data"
    assert "replay" not in msgs[0]["payload"]
    assert len(msgs[0]["payload"]["items"]) == 7
    assert bridge._last_klines == _replay_klines(7)


def test_replay_fetch_error_resets_the_wanted_flag():
    bridge, root = _replay_bridge(10)
    bridge._replay_wanted = True

    bridge._on_data_ready({"error": "网络连接失败，请检查网络", "klines": []})

    msgs = _drain_messages(bridge)
    assert msgs[0]["payload"]["error"] == "网络连接失败，请检查网络"
    assert bridge._replay_wanted is False


def test_begin_replay_refuses_too_little_data():
    bridge, root = _replay_bridge(10)

    bridge._begin_replay(_replay_klines(2))

    msgs = _drain_messages(bridge)
    assert msgs[0]["payload"]["error"] == "数据不足，无法回放"
    assert bridge.replay.session is None


def test_fetch_stops_an_active_replay(monkeypatch):
    bridge, root = _replay_bridge(10)
    bridge.start_replay(400, _replay_klines(10))
    assert bridge.replay.active is True

    # 挡掉后台线程，只验证主线程上的状态变化
    fake_thread = SimpleNamespace(start=lambda: None)
    monkeypatch.setattr(
        hqchart_bridge.threading, "Thread", lambda **kwargs: fake_thread)

    _drain_messages(bridge)                      # 清空开播时的消息
    bridge.fetch(period="weekly", count=1500)

    assert bridge.replay.active is False
    assert bridge.replay.state == "idle"
    assert bridge.replay.session is None
    assert bridge.period == "weekly"
    assert bridge._loading is True
    # 停掉回放就要发一条"空闲"让页面复位（全量数据还没到手，没有载荷可用）：
    # 否则按钮还停在"暂停"，而 Python 这边已经空闲了
    msgs = _drain_messages(bridge)
    assert [m["cmd"] for m in msgs] == ["replay_state"]
    assert msgs[0]["state"] == "idle"

    # 新请求又失败了：只该带错误信息，不能把页面状态弄乱
    bridge._on_data_ready({"error": "网络连接失败，请检查网络", "klines": []})
    msgs = _drain_messages(bridge)
    assert [m["cmd"] for m in msgs] == ["data"]
    assert msgs[0]["payload"]["error"] == "网络连接失败，请检查网络"
    assert bridge._replay_wanted is False
    assert bridge.replay.active is False


def test_set_replay_interval_clamps_and_applies():
    bridge, root = _replay_bridge(10)

    bridge.set_replay_interval(1234)
    assert bridge._replay_interval == 1234
    assert bridge.replay.interval == 1234

    bridge.set_replay_interval("垃圾值")
    assert bridge._replay_interval == hqchart_bridge.DEFAULT_INTERVAL


def test_shutdown_replay_stops_the_timer_without_rendering():
    bridge, root = _replay_bridge(10)
    bridge.start_replay(400, _replay_klines(10))
    assert root.scheduled
    _drain_messages(bridge)

    bridge._shutdown_replay()

    assert root.scheduled == []
    assert bridge.replay.session is None
    # 只会带一条"空闲"状态，不会重画任何图
    assert [m["cmd"] for m in _drain_messages(bridge)] == ["replay_state"]


def test_shutdown_replay_is_safe_without_replay_attribute():
    bridge = _bare_bridge()                      # 没有 replay 属性
    bridge._shutdown_replay()                    # 不应抛异常


# ──────────────── 回放事件的进程间分发 ────────────────

class _RecordingBridge:
    """只记录被调用的桥接替身"""

    def __init__(self, root=None):
        self._root = root or FakeRoot()
        self.calls = []

    def start_replay(self, interval=None, klines=None):
        self.calls.append(("start", interval, klines))

    def pause_replay(self):
        self.calls.append(("pause",))

    def resume_replay(self):
        self.calls.append(("resume",))

    def stop_replay(self, render_all=True):
        self.calls.append(("stop", render_all))

    def set_replay_interval(self, ms):
        self.calls.append(("speed", ms))


def _bridge_for_dispatch(root):
    """给 _dispatch 用的最小替身：只关心事件被路由到了哪个方法"""
    bridge = hqchart_bridge.HqChartKlineWindow.__new__(
        hqchart_bridge.HqChartKlineWindow)
    bridge._root = root
    return bridge


def test_dispatch_routes_replay_events():
    root = FakeRoot()
    bridge = _bridge_for_dispatch(root)
    recorder = _RecordingBridge()
    for name in ("start_replay", "pause_replay", "resume_replay",
                 "stop_replay", "set_replay_interval"):
        setattr(bridge, name, getattr(recorder, name))

    bridge._dispatch({"event": "replay_start", "interval": 300})
    bridge._dispatch({"event": "replay_pause"})
    bridge._dispatch({"event": "replay_resume"})
    bridge._dispatch({"event": "replay_stop"})
    bridge._dispatch({"event": "replay_speed", "interval": 800})

    assert recorder.calls == [
        ("start", 300, None),
        ("pause",),
        ("resume",),
        ("stop", True),
        ("speed", 800),
    ]
    # 事件回调都调度到了主线程（delay=0）
    assert root.calls and all(c[0] == 0 for c in root.calls)


def test_dispatch_missing_replay_interval_defaults():
    root = FakeRoot()
    bridge = _bridge_for_dispatch(root)
    recorder = _RecordingBridge()
    bridge.start_replay = recorder.start_replay

    bridge._dispatch({"event": "replay_start"})

    assert recorder.calls == [("start", None, None)]

