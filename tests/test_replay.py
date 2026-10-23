"""
历史回放核心逻辑单元测试
=========================
覆盖 stock_monitor/replay.py 的三层：
    · replay_count() / clamp_interval()   参数收敛
    · ReplaySession                       进度状态机
    · ReplayDriver                        tkinter 定时驱动（用假 root，不真的起定时器）
"""

import pytest

from stock_monitor.replay import (
    ReplaySession, ReplayDriver, replay_count, clamp_interval,
    DEFAULT_INTERVAL, MIN_INTERVAL, MAX_INTERVAL,
    STATE_IDLE, STATE_RUNNING, STATE_PAUSED, STATE_FINISHED,
)


# ──────────────── 测试辅助 ────────────────

class SchedulingRoot:
    """假 tkinter 窗口：只记录 after() 的回调，测试里手动触发。

    真实的 tkinter 会立即执行 after(0, ...)，而这里必须能"卡住"定时链，
    所以回调一律先入队，由 fire_next() 手动放行。
    """

    def __init__(self):
        self.scheduled = []
        self.cancelled = []
        self._next_id = 0

    def after(self, delay, fn, *args):
        self._next_id += 1
        self.scheduled.append((self._next_id, delay, fn, args))
        return self._next_id

    def after_cancel(self, after_id):
        self.cancelled.append(after_id)
        self.scheduled = [it for it in self.scheduled if it[0] != after_id]

    def fire_next(self):
        """放行最早排入的一次回调（模拟时间到了）"""
        item = self.scheduled.pop(0)
        item[2](*item[3])
        return item


def _klines(n, base="2024-01-01"):
    """造 n 根假 K 线"""
    return [{"date": base, "open": 10.0, "close": 10.5,
             "high": 11.0, "low": 9.8, "volume": 100} for _ in range(n)]


def _driver(n=None, **kwargs):
    """造一个连好的 (driver, root, renders, states) 四元组。

    n 不为空时直接开播。
    """
    root = SchedulingRoot()
    renders = []
    states = []

    def render(count):
        renders.append(count)

    def on_state(state, session):
        states.append((state, session.count if session else None))

    driver = ReplayDriver(root, render, on_state=on_state, **kwargs)
    if n is not None:
        driver.start(ReplaySession(n))
    return driver, root, renders, states


# ──────────────── 参数收敛 ────────────────

def test_replay_count_maps_each_period():
    assert replay_count("daily") == 500
    assert replay_count("weekly") == 1500
    assert replay_count("monthly") == 2000


def test_replay_count_unknown_period_falls_back_to_daily():
    assert replay_count("yearly") == replay_count("daily")


def test_replay_count_respects_cap():
    assert replay_count("monthly", cap=100) == 100
    # cap 压到 0 也不能出 0 或负数
    assert replay_count("daily", cap=0) == 1


@pytest.mark.parametrize("raw,expected", [
    (None, DEFAULT_INTERVAL),
    ("", DEFAULT_INTERVAL),
    ("abc", DEFAULT_INTERVAL),
    (0, MIN_INTERVAL),
    (-50, MIN_INTERVAL),
    (100000, MAX_INTERVAL),
    (100, 100),
    (3000, 3000),
    ("250", 250),      # 数字字符串也行
])
def test_clamp_interval_bounds(raw, expected):
    assert clamp_interval(raw) == expected


# ──────────────── ReplaySession ────────────────

def test_session_clamps_start_count_to_total():
    s = ReplaySession(total=3, start_count=50)

    assert s.count == 3
    assert s.finished is True


def test_session_never_starts_below_one():
    assert ReplaySession(total=10, start_count=0).count == 1


def test_session_empty_data_is_not_finished():
    s = ReplaySession(total=0)

    assert s.count == 0
    assert s.total == 0
    assert s.finished is False


def test_session_step_never_below_one():
    assert ReplaySession(total=10, step=0).step == 1


def test_session_total_never_negative():
    assert ReplaySession(total=-5).total == 0


def test_session_advance_increments_and_caps():
    s = ReplaySession(total=5, start_count=2, step=1)

    assert s.count == 2
    assert s.advance() == 3
    assert s.advance() == 4
    assert s.advance() == 5
    assert s.finished is True
    # 播完之后再推也不该越过总数
    assert s.advance() == 5


def test_session_advance_with_bigger_step_overshoots_to_total():
    s = ReplaySession(total=10, start_count=9, step=5)

    assert s.advance() == 10
    assert s.finished is True


def test_session_date_at_tracks_the_revealed_last_bar():
    data = _klines(6)
    s = ReplaySession(total=6, start_count=2)

    assert s.date_at(data) == "2024-01-01"
    s.count = 5
    assert s.date_at(data) == "2024-01-01"


def test_session_date_at_empty_or_dirty_input():
    s = ReplaySession(total=2, start_count=2)

    assert s.date_at([]) == ""
    assert s.date_at([None, "not-a-dict"]) == ""
    assert s.date_at([{}]) == ""


# ──────────────── ReplayDriver ────────────────

def test_driver_renders_first_frame_immediately():
    driver, root, renders, states = _driver(10)

    assert renders == [5]            # 默认第一帧显示 5 根
    assert driver.state == STATE_RUNNING
    assert len(root.scheduled) == 1


def test_driver_uses_configured_step_and_start():
    root = SchedulingRoot()
    renders = []
    driver = ReplayDriver(root, renders.append, interval=200)

    driver.start(ReplaySession(20, start_count=3, step=2))

    assert renders == [3]
    assert root.scheduled[0][1] == 200      # 间隔用的是配置值

    root.fire_next()
    assert renders == [3, 5]               # 一步推进 2 根


def test_driver_tick_advances_and_reschedules():
    driver, root, renders, states = _driver(10)

    root.fire_next()

    assert renders == [5, 6]
    assert len(root.scheduled) == 1


def test_driver_finishes_and_stops_scheduling():
    driver, root, renders, states = _driver(8)

    # 5 → 6 → 7 → 8（播完）
    for _ in range(3):
        root.fire_next()

    assert renders == [5, 6, 7, 8]
    assert driver.state == STATE_FINISHED
    assert driver.session.finished is True
    # 播完后不该再排下一次
    assert root.scheduled == []


def test_driver_tick_after_finish_is_noop():
    driver, root, renders, states = _driver(6)

    root.fire_next()                      # 5 → 6，播完
    assert driver.state == STATE_FINISHED

    driver._tick()                        # 手动再触发一次也不该推进
    assert renders == [5, 6]
    assert root.scheduled == []


def test_driver_start_with_already_finished_session():
    driver, root, renders, states = _driver(None)

    driver.start(ReplaySession(total=4, start_count=4))

    assert renders == [4]
    assert driver.state == STATE_FINISHED
    assert root.scheduled == []


def test_driver_pause_cancels_timer_and_freezes():
    driver, root, renders, states = _driver(10)

    driver.pause()

    assert driver.state == STATE_PAUSED
    assert driver.paused is True
    assert renders == [5]                 # 暂停不会再画
    assert root.scheduled == []
    assert root.cancelled == [root._next_id]


def test_driver_double_pause_is_noop():
    driver, root, renders, states = _driver(10)

    driver.pause()
    driver.pause()

    assert driver.state == STATE_PAUSED
    assert renders == [5]


def test_driver_pause_when_idle_is_noop():
    driver, root, renders, states = _driver(None)

    driver.pause()

    assert driver.state == STATE_IDLE
    assert root.scheduled == []


def test_driver_resume_continues_from_where_it_stopped():
    driver, root, renders, states = _driver(10)

    root.fire_next()                     # 5 → 6
    driver.pause()
    driver.resume()

    assert driver.state == STATE_RUNNING
    assert renders == [5, 6]
    assert len(root.scheduled) == 1

    root.fire_next()
    assert renders == [5, 6, 7]


def test_driver_resume_when_not_paused_is_noop():
    driver, root, renders, states = _driver(10)

    driver.resume()

    assert driver.state == STATE_RUNNING
    assert len(root.scheduled) == 1      # 原来那一个，没有重复排


def test_driver_stop_renders_full_data():
    driver, root, renders, states = _driver(10)

    driver.pause()
    driver.stop(render_all=True)

    assert renders == [5, 10]            # 画回全量 10 根
    assert driver.state == STATE_IDLE
    assert driver.session is None
    assert driver.active is False
    assert root.scheduled == []


def test_driver_stop_without_render_all():
    driver, root, renders, states = _driver(10)

    driver.stop(render_all=False)

    assert renders == [5]                # 不额外画
    assert driver.state == STATE_IDLE
    assert driver.session is None


def test_driver_stop_when_idle_is_noop():
    driver, root, renders, states = _driver(None)

    driver.stop(render_all=True)

    assert renders == []
    assert driver.state == STATE_IDLE


def test_driver_restart_replaces_previous_session():
    driver, root, renders, states = _driver(10)
    root.fire_next()                     # 5 → 6

    driver.stop(render_all=False)
    driver.start(ReplaySession(6, start_count=1))

    assert renders == [5, 6, 1]
    assert len(root.scheduled) == 1
    assert driver.session.total == 6


def test_driver_resume_after_finish_keeps_finished():
    driver, root, renders, states = _driver(6)

    root.fire_next()                     # 5 → 6，播完
    assert driver.state == STATE_FINISHED

    driver.resume()                      # 播完之后再"继续"没有意义

    assert driver.state == STATE_FINISHED
    assert renders == [5, 6]
    assert root.scheduled == []


def test_driver_on_state_callback_gets_state_and_session():
    driver, root, renders, states = _driver(10)

    root.fire_next()                     # 5 → 6
    driver.pause()

    assert (STATE_RUNNING, 5) in states
    assert (STATE_PAUSED, 6) in states


def test_driver_without_on_state_callback():
    root = SchedulingRoot()
    driver = ReplayDriver(root, lambda count: None)     # 不传 on_state

    driver.start(ReplaySession(10))                     # 不该抛异常
    root.fire_next()
    driver.pause()
    driver.stop(render_all=True)


def test_driver_set_interval_clamps_value():
    driver, root, renders, states = _driver(None)

    driver.set_interval("bad")
    assert driver.interval == DEFAULT_INTERVAL

    driver.set_interval(999999)
    assert driver.interval == MAX_INTERVAL


def test_driver_construction_clamps_interval():
    driver, root, renders, states = _driver(None, interval=-999)

    assert driver.interval == MIN_INTERVAL
