"""
K线历史回放（共享核心逻辑）
=============================
从最早的 K 线 bar 开始，逐根"揭示"直到最新一根，支持暂停 / 继续 / 回到最新。

为什么单独一个模块：
    两个 K线后端（HQChart 网页版、matplotlib 回退版）都要这个功能，
    把"进度推进"和"定时驱动"抽出来共用，两端的差异只剩各自的画图画法。

三层结构：
    replay_count()      回放需要多少数据（比平时看盘要多得多）
    ReplaySession       进度状态机：当前揭示到第几根、是否播完
    ReplayDriver        tkinter 定时驱动：每隔一段时间推进一步，回调重绘

ReplaySession / ReplayDriver 都不碰网络也不碰具体图表库，可以直接单测。
"""

from typing import Callable, Optional

# ──────────────── 默认参数 ────────────────

DEFAULT_INTERVAL = 400     # 每步间隔（毫秒）—— 2.5 根/秒，看盘回放常用节奏
MIN_INTERVAL = 100         # 太快看不出变化，也压垮渲染
MAX_INTERVAL = 3000        # 太慢没意义，也给个上限
DEFAULT_STEP = 1           # 每步推进的 bar 数
DEFAULT_START_COUNT = 5    # 第一帧已经显示出来的 bar 数（只有 1 根看不出形状）

# 回放各周期需要拉取的日线条数。平时看盘只要最近 120 根，回放要从"起始日"
# 播起来，所以数据量得放宽不少：
#   daily   500 根 ≈ 2 年
#   weekly 1500 根日线 ≈ 7 年（聚合后约 300 根周线）
#   monthly 2000 根日线 ≈ 8 年（聚合后约 100 根月线）
REPLAY_COUNT_MAP = {
    "daily":   500,
    "weekly":  1500,
    "monthly": 2000,
}
# 新浪接口的 datalen 不能无限大，超过这个数要么报错要么被截断
REPLAY_FETCH_CAP = 2000

# ──────────────── 回放状态 ────────────────

STATE_IDLE = "idle"        # 未开始
STATE_RUNNING = "running"  # 播放中
STATE_PAUSED = "paused"    # 已暂停
STATE_FINISHED = "finished"  # 已播到最新

IDLE = STATE_IDLE


def clamp_interval(ms) -> int:
    """把速度参数收敛到合法区间，非法输入回落到默认值。"""
    try:
        value = int(ms)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL
    return max(MIN_INTERVAL, min(value, MAX_INTERVAL))


def replay_count(period: str, cap: int = REPLAY_FETCH_CAP) -> int:
    """回放模式下该周期需要拉取的 K 线条数（纯函数，便于单测）。"""
    want = REPLAY_COUNT_MAP.get(period, REPLAY_COUNT_MAP["daily"])
    return max(1, min(want, cap))


# ──────────────── 进度状态机 ────────────────

class ReplaySession:
    """一次回放播放的进度。

    参数：
        total:        K 线总根数
        start_count:  第一帧显示多少根（至少 1，最多 total）
        step:         每步推进多少根

    count 表示"当前已经揭示出来的 bar 数"，是闭区间 [start_count, total]，
    对应 K 线列表的 klines[:count]。
    """

    def __init__(self, total: int, start_count: int = DEFAULT_START_COUNT,
                 step: int = DEFAULT_STEP):
        self.total = max(0, int(total or 0))
        self.start_count = max(1, min(int(start_count), self.total or 1))
        self.step = max(1, int(step))
        self.count = self.start_count if self.total else 0

    @property
    def finished(self) -> bool:
        """是否已经播到最新一根"""
        return self.total > 0 and self.count >= self.total

    @property
    def index(self) -> int:
        """当前最后一根 bar 的下标（未开始时为 -1）"""
        return self.count - 1

    @property
    def done(self) -> bool:
        """已揭示 / 总数"""
        if not self.total:
            return 0.0
        return min(1.0, self.count / self.total)

    def advance(self) -> int:
        """推进一步，返回推进后的已揭示 bar 数（不会超过 total）。"""
        if self.finished:
            return self.count
        self.count = min(self.total, self.count + self.step)
        return self.count

    def slice(self, klines: list) -> list:
        """当前进度对应的 K 线切片"""
        return (klines or [])[:self.count]

    def date_at(self, klines: list) -> str:
        """当前最后一根 bar 的日期（用于状态栏显示），取不到返回空串"""
        if not klines or self.count < 1:
            return ""
        i = self.count - 1
        if i >= len(klines):        # 进度比手上的数据多时也不要越界
            return ""
        k = klines[i]
        return k.get("date", "") if isinstance(k, dict) else ""


# ──────────────── tkinter 定时驱动 ────────────────

class ReplayDriver:
    """用 tkinter 的 after() 定时推进回放。

    参数：
        root:     tkinter 窗口，提供 after() / after_cancel()
        render:   callable(reveal_count) —— 每推进一步时调用，负责重绘
        on_state: callable(state, session) —— 可选，用于更新按钮与状态栏
        interval: 每步间隔（毫秒）
    """

    def __init__(self, root, render: Callable[[int], None],
                 on_state: Optional[Callable] = None,
                 interval: int = DEFAULT_INTERVAL):
        self._root = root
        self._render = render
        self._on_state = on_state
        self.interval = clamp_interval(interval)
        self.session = None          # type: Optional[ReplaySession]
        self.state = STATE_IDLE
        self._after_id = None

    # ── 状态查询 ──

    @property
    def running(self) -> bool:
        return self.state == STATE_RUNNING

    @property
    def paused(self) -> bool:
        return self.state == STATE_PAUSED

    @property
    def active(self) -> bool:
        """回放是否在进行中（播放或暂停都算）"""
        return self.session is not None and self.state != STATE_IDLE

    # ── 控制 ──

    def start(self, session: ReplaySession,
              interval: Optional[int] = None):
        """开始（或重新开始）一次回放。第一帧立即画出。"""
        self._stop_timer()
        self.session = session
        if interval is not None:
            self.interval = clamp_interval(interval)
        self._set_state(STATE_RUNNING)
        self._render(self.session.count)
        if self.session.finished:
            self._set_state(STATE_FINISHED)
        else:
            self._schedule()

    def pause(self):
        """暂停：停掉定时器，画面停在当前位置"""
        if not self.active or self.state != STATE_RUNNING:
            return
        self._stop_timer()
        self._set_state(STATE_PAUSED)

    def resume(self):
        """继续播放"""
        if not self.active or self.state != STATE_PAUSED:
            return
        self._set_state(STATE_RUNNING)
        if self.session.finished:
            self._set_state(STATE_FINISHED)
            return
        self._schedule()

    def stop(self, render_all: bool = True):
        """结束回放。render_all=True 时把完整数据画回去。"""
        self._stop_timer()
        had_session = self.session is not None
        total = self.session.total if had_session else 0
        self.session = None
        self._set_state(STATE_IDLE)
        # 本来就没在回放时不必画，免得白跑一次重绘
        if render_all and had_session:
            self._render(total)

    def set_interval(self, ms):
        """调整播放速度（播放中立即生效）"""
        self.interval = clamp_interval(ms)

    # ── 内部 ──

    def _schedule(self):
        self._after_id = self._root.after(self.interval, self._tick)

    def _stop_timer(self):
        if self._after_id is not None:
            try:
                self._root.after_cancel(self._after_id)
            except (ValueError, TypeError, RuntimeError):
                pass
            self._after_id = None

    def _tick(self):
        self._after_id = None
        if self.state != STATE_RUNNING or self.session is None:
            return
        self.session.advance()
        self._render(self.session.count)
        if self.session.finished:
            self._set_state(STATE_FINISHED)
            return
        self._schedule()

    def _set_state(self, state: str):
        self.state = state
        if self._on_state is not None:
            self._on_state(state, self.session)
