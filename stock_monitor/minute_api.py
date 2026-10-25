"""
分时数据获取模块（东方财富为主，新浪兜底）
=========================================
获取「指定交易日」的分钟级分时数据：1 分钟优先，取不到时自动放粗到 5 / 15 分钟。

为什么要有两个数据源
    ----------------
    东方财富能回溯最远，正常网络下一直是主源；但它的行情域名（push2his / push2 /
    push2delay.eastmoney.com）在某些网络环境下会被直接掐断，本机就是：TLS 握手
    成功之后立刻被对方断开（RemoteDisconnected / "Empty reply from server"），
    HTTPS、纯 HTTP、十几个 8x.push2his 镜像域名、走不走本机代理都是这个结果，
    而 quote.eastmoney.com 的网页却完全正常——所以"先走代理、失败再直连"根本救不
    回来，这不是代理问题。新浪的 getKLineData 在这种网络下正常，就拿来兜底。

    代价是新浪只能给"最近"的一段（datalen 上限 1023 根：1 分钟约 2 个交易日、
    5 分钟约 1 个月、15 分钟约 3 个月），没有按日期回溯的参数。所以顺序是：
    先用东方财富要最远的历史，它连不上再退回新浪要最近的这一段，至少让用户
    近几个月的历史分时还能回放，而不是一直看到"网络连接失败"。

接口
    ----
    东方财富  GET https://push2his.eastmoney.com/api/qt/stock/kline/get
        secid    "1.600000"（1=上交所，0=深交所）
        klt      分钟粒度：1 / 5 / 15
        lmt      返回条数上限（接口会自行截断，给大一点无害）
        fqt      0=不复权，与日K线的不复权口径一致
        fields2  f51..f61 → 时间 / 开 / 收 / 高 / 低 / 量 / 额 / 振幅 / 涨跌幅 / 涨跌 / 换手

        返回 data.klines 是一堆逗号分隔串，例如：
            "2026-10-23 09:31,9.69,9.65,9.73,9.64,32290,31271508.00,0.94,0.31,0.03,0.01"

    新浪      GET https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketData.getKLineData
        symbol   "sh600000"（sh=上交所 / sz=深交所 / bj=北交所，与 stock_api 一致）
        scale    分钟粒度：1 / 5 / 15（日K 是 240，本模块只用分钟）
        datalen  返回条数，上限 1023；给到 2000 会直接返回 null，别多要

        返回是一个数组，元素形如
            {"day":"2026-10-23 15:00:00","open":"9.530","high":"9.730",
             "low":"9.520","close":"9.530","volume":"75592632","amount":"726344396.0"}

两个容易踩坑的口径
    ----------------
    · 两个源的时间都是这根分钟K线的"结束时间"，所以一天第一根是 09:31 而不是 09:30
    · 成交量单位不一致：东方财富是「手」，本模块统一换算成「股」（×100）；
      新浪的分钟量本来就是「股」，直接照搬，不要再乘 100

响应体编码
    --------
    服务端有时会用 GBK 字节 + UTF-8 的 Content-Type，导致 requests.json() 里的
    中文名称变成乱码。这里统一按 UTF-8 重新解码后再解析；名称字段本项目也用不到
    （窗口标题用调用方传来的名字），但解析过程不应因此报错。
"""

import json
import time
from datetime import datetime

import requests

from stock_monitor.config import HEADERS as _SINA_HEADERS, TIMEOUT
# 复用新浪代码前缀规则（sh / sz / bj），别在这里再抄一份判断逻辑
from stock_monitor.stock_api import _build_code as _sina_symbol_of

# ──────────────── 常量 ────────────────

# 数据源名：用在前端的提示 / 错误信息里，也在下面的切换逻辑里判断
EM_SOURCE = "东方财富"
SINA_SOURCE = "新浪"

EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

# 新浪的分钟接口（和日K用的是同一个，只是 scale 换成分钟）
SINA_KLINE_URL = (
    "https://quotes.sina.cn/cn/api/json_v2.php/"
    "CN_MarketData.getKLineData"
)

# 新浪 datalen 的硬上限：给 1023 正常，给 2000 直接返回 null
SINA_MAX_DATALEN = 1023

# 东方财富连续连不上时，记这么长时间不再先试它（省掉每次调用白等两轮超时）。
# 只针对"连不上"这种错误；HTTP 报错 / 数据为空都说明接口活着，不用记
_EM_DOWN_TTL = 300.0
_EM_DOWN_UNTIL = 0.0

# 东方财富是独立的行情源，Referer 必须是它自己的站点，否则会被当成爬虫
EM_HEADERS = {
    "Referer": "https://quote.eastmoney.com/",
    "User-Agent": _SINA_HEADERS["User-Agent"],
}

EM_FIELDS1 = "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
EM_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"

# 粒度依次放粗：越粗每根覆盖越长时间，同样多拉能回溯越远
KLT_CANDIDATES = (1, 5, 15)

# 每种粒度拉多少根。给得宽松一点（接口会自己截断），目的是让最近一两年的
# 日期都能被某个粒度覆盖到
LMT_BY_KLT = {1: 8000, 5: 20000, 15: 16000}

# 请求的日期没有数据时，最多往前/往后找几天的最近交易日
MAX_BACKTRACK_DAYS = 10

# 东方财富的成交量是「手」，换算成「股」的系数
LOTS_PER_SHARE_UNIT = 100

_DATE_FMT = "%Y-%m-%d"

# klines 串里的字段位置（对应 EM_FIELDS2）
_F_TIME, _F_OPEN, _F_CLOSE, _F_HIGH, _F_LOW = 0, 1, 2, 3, 4
_F_VOL, _F_AMOUNT, _F_AMP, _F_PCT, _F_CHANGE, _F_TURNOVER = 5, 6, 7, 8, 9, 10


# ──────────────── 日期与代码 ────────────────

def normalize_date(value) -> str:
    """把各种写法归一化成 "YYYY-MM-DD"，取不到合法日期返回空串。

    支持 "2026-10-23" / "2026/10/23" / "2026.10.23" / "20261023" / 带时间的串。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.split()[0]                       # 截掉可能带的时间部分
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).strftime(_DATE_FMT)
        except ValueError:
            continue
    return ""


def _date_ord(date_str: str) -> int:
    """转成可比较的天序号，解析失败返回 0"""
    return datetime.strptime(date_str, _DATE_FMT).toordinal()


def em_secid(code: str, stock_type: str = "stock") -> str:
    """把本项目的代码 / 类型转成东方财富的 secid，例如 "1.600000"。

    市场号：1=上交所，0=深交所。判断规则与 stock_api._build_code 保持一致，
    北交所（4 / 8 开头）归到 0，属于尽力而为。
    """
    digits = str(code or "").strip()
    if not digits.isdigit() or len(digits) != 6:
        return ""

    if stock_type == "index":
        market = "0" if digits.startswith("399") else "1"
    elif stock_type == "etf":
        # 深市 ETF / LOF 是 1 开头，其余（51/56/58 等）归上交所
        market = "0" if digits.startswith("1") else "1"
    elif digits[0] in ("6", "9"):
        market = "1"
    elif digits[0] in ("0", "3"):
        market = "0"
    elif digits[0] == "4":
        market = "0"                     # 北交所：尽力归到深交所一侧
    else:
        market = "1"                     # 与 stock_api._build_code 一致，未知归上海

    return "%s.%s" % (market, digits)


# ──────────────── 解析 ────────────────

def _to_number(text) -> float:
    """成交额 / 价格等非数字统一归零（接口偶尔会返回 "-"）"""
    try:
        return float(str(text).strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _make_row(date_str, time_label, open_, high, low, close,
              volume, amount) -> dict:
    """拼一根分钟K线。volume 一律按「股」传入，各源的换算在调用处做。"""
    return {
        "date": date_str,
        "time": time_label,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": int(volume),
        "amount": amount,
    }


def _split_stamp(stamp) -> tuple:
    """把 "2026-10-23 09:31" 拆成 ("2026-10-23", "09:31")，格式不对返回 None。

    两个源的分钟时间有的带秒有的不带，但都是"日期 + 空格 + HH:MM"开头；
    不补零的时间（9:5）也照常吃，最后统一补零输出。
    """
    stamp = str(stamp or "").strip()
    try:
        day = datetime.strptime(stamp[:10], _DATE_FMT)
        clock = datetime.strptime(stamp[11:16], "%H:%M")
    except ValueError:
        return None
    return (day.strftime(_DATE_FMT),
            "%02d:%02d" % (clock.hour, clock.minute))


def _parse_kline_row(row) -> dict:
    """把东方财富的 "2026-10-23 09:31,9.69,..." 拆成 {date, time, open, ...}。

    返回 None 表示这行没法用（缺字段 / 时间格式不对）。
    """
    if not isinstance(row, str):
        return None
    parts = row.split(",")
    if len(parts) <= _F_AMOUNT:
        return None

    stamp = _split_stamp(parts[_F_TIME])
    if stamp is None:
        return None
    date_str, time_label = stamp

    # 东方财富的成交量单位是「手」，这里换算成「股」
    return _make_row(date_str, time_label,
                     _to_number(parts[_F_OPEN]),
                     _to_number(parts[_F_HIGH]),
                     _to_number(parts[_F_LOW]),
                     _to_number(parts[_F_CLOSE]),
                     _to_number(parts[_F_VOL]) * LOTS_PER_SHARE_UNIT,
                     _to_number(parts[_F_AMOUNT]))


def _parse_sina_row(row) -> dict:
    """把新浪的 {"day":"2026-10-23 15:00:00","open":"9.530",...} 拆成同一形状。

    返回 None 表示这行没法用。新浪的成交量本来就是「股」，不要再乘 100。
    """
    if not isinstance(row, dict):
        return None

    stamp = _split_stamp(row.get("day"))
    if stamp is None:
        return None
    date_str, time_label = stamp

    return _make_row(date_str, time_label,
                     _to_number(row.get("open")),
                     _to_number(row.get("high")),
                     _to_number(row.get("low")),
                     _to_number(row.get("close")),
                     _to_number(row.get("volume")),
                     _to_number(row.get("amount")))


def _is_network_error(error: Exception) -> bool:
    """是不是"连不上"这类错误（超时 / 连接被掐断）"""
    return isinstance(error,
                      (requests.exceptions.ConnectionError,
                       requests.exceptions.Timeout))


def _eastmoney_down() -> bool:
    """东方财富在冷静期内，这次不用先试它"""
    return time.monotonic() < _EM_DOWN_UNTIL


def _mark_eastmoney_down():
    global _EM_DOWN_UNTIL
    _EM_DOWN_UNTIL = time.monotonic() + _EM_DOWN_TTL


def _reset_source_state():
    """清掉东方财富的冷静期（测试用）"""
    global _EM_DOWN_UNTIL
    _EM_DOWN_UNTIL = 0.0


def _fetch_klines_em(secid: str, klt: int) -> list:
    """拉一段分钟K线（东方财富），返回解析后的行列表（按时间升序）。失败抛异常。"""
    params = {
        "secid": secid,
        "fields1": EM_FIELDS1,
        "fields2": EM_FIELDS2,
        "klt": klt,
        "fqt": 0,
        "lmt": LMT_BY_KLT.get(klt, 8000),
        "end": "20500101",
    }

    last_error = None
    proxies = None                               # None → 走系统代理（requests 默认行为）
    data = None
    for _ in range(2):                           # 先按默认代理试，代理被掐断时改直连
        try:
            response = requests.get(EM_KLINE_URL, params=params,
                                    headers=EM_HEADERS, timeout=TIMEOUT,
                                    proxies=proxies)
            response.raise_for_status()
            data = json.loads(response.content.decode("utf-8", "replace"))
            break
        except requests.exceptions.ConnectionError as e:
            # 系统代理软件（加速器 / VPN 等）会掐断对东方财富域名的请求，
            # 有的表现为 ProxyError、有的直接是 ConnectionError（RemoteDisconnected）。
            # 网络本身是通的，首次失败就先退回直连再试一次。
            last_error = e
            if proxies is None:
                proxies = {"http": None, "https": None}
        except Exception as e:
            # 非连接错误（超时 / HTTP 状态码等）：保持同一设置再试一次，容忍网络偶发抖动
            last_error = e
    else:
        raise last_error

    body = (data or {}).get("data") or {}
    raw_rows = body.get("klines") or []

    rows = []
    for raw in raw_rows:
        parsed = _parse_kline_row(raw)
        if parsed is not None:
            rows.append(parsed)

    rows.sort(key=lambda r: (r["date"], r["time"]))
    return rows


def _fetch_klines_sina(symbol: str, klt: int) -> list:
    """拉最近一段分钟K线（新浪）。没有按日期回溯的参数，失败抛异常。"""
    if not symbol:
        return []

    params = {
        "symbol": symbol,
        "scale": klt,
        "datalen": SINA_MAX_DATALEN,
    }

    payload = None
    last_error = None
    for _ in range(2):
        try:
            response = requests.get(SINA_KLINE_URL, params=params,
                                    headers=_SINA_HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
            payload = json.loads(response.content.decode("utf-8", "replace"))
            break
        except requests.exceptions.RequestException as e:
            # 和东方财富一样容忍一次抖动：单次 blip 不该冒充"全源都连不上"
            # 而触发提前收手，那会把本来能拿到的数据丢掉
            last_error = e
    else:
        raise last_error

    rows = []
    for raw in (payload or []):
        parsed = _parse_sina_row(raw)
        if parsed is not None:
            rows.append(parsed)

    rows.sort(key=lambda r: (r["date"], r["time"]))
    return rows


class FetchError(Exception):
    """所有数据源都取不到分时数据。消息已经拼好，可以直接给用户看。

    network=True 表示全是"连不上"（超时 / 连接被掐断）。这种情况下换更粗的
    粒度也救不回来，调用方可以提前收手，别为了放粗粒度再白等一轮超时。
    """

    def __init__(self, message, network: bool = False):
        super().__init__(message)
        self.network = network


def _fetch_klines(secid: str, sina_symbol: str, klt: int) -> tuple:
    """按顺序试各个数据源，返回 (行列表, 数据源名)；都失败抛 FetchError。

    东方财富能回溯最远，正常优先；它连不上（本机会被直接掐断，见模块头注释）
    就退回新浪，新浪只给最近这一段，但至少不至于全是"网络连接失败"。
    """
    sources = (
        (EM_SOURCE, lambda: _fetch_klines_em(secid, klt)),
        (SINA_SOURCE, lambda: _fetch_klines_sina(sina_symbol, klt)),
    )

    last_errors = []
    network_only = False
    for source, fetcher in sources:
        if source == EM_SOURCE and _eastmoney_down():
            # 冷静期内不试：上次也是连不上。还是记进错误信息，
            # 免得最后只说"新浪连不上"，用户不知道主源也没试
            last_errors.append("%s：本次未试（刚才连不上）" % EM_SOURCE)
            continue
        try:
            rows = fetcher()
        except Exception as e:
            if source == EM_SOURCE and _is_network_error(e):
                _mark_eastmoney_down()
            network_only = network_only or _is_network_error(e)
            last_errors.append("%s：%s" % (source, _friendly_error(e)))
            continue
        if rows:
            if source == EM_SOURCE:
                _reset_source_state()            # 又能用了，清掉冷静期
            return rows, source
        last_errors.append("%s：接口返回数据为空" % source)

    raise FetchError("；".join(last_errors) or "未知原因",
                     network=network_only)


# ──────────────── 按日截取 ────────────────

def _bars_of_day(rows: list, date_str: str) -> list:
    """从整段数据里取出某一天的分时（跳过无法解析的行）"""
    return [r for r in rows
            if isinstance(r, dict) and r.get("date") == date_str]


def _prev_close(rows: list, date_str: str) -> float:
    """分时基准线：上一个交易日（严格早于 date_str）的收盘价。取不到返回 0"""
    best_date = ""
    for r in rows:
        if not isinstance(r, dict):
            continue
        day = r.get("date", "")
        if day and day < date_str and day > best_date:
            best_date = day
    if not best_date:
        return 0.0
    bars = _bars_of_day(rows, best_date)
    return bars[-1]["close"] if bars else 0.0


def _nearest_date(dates: list, date_str: str):
    """离 date_str 最近的一个交易日，返回 (日期, 相差天数)；没有返回 ("", 0)"""
    if not dates:
        return "", 0
    target = _date_ord(date_str)
    best = min(dates, key=lambda d: abs(_date_ord(d) - target))
    return best, abs(_date_ord(best) - target)


def _enrich_bars(bars: list, pre_close: float) -> list:
    """补上分时图要用的派生字段：均价线 / 涨跌 / 涨跌幅（原地修改后返回）"""
    cum_vol = 0.0
    cum_amount = 0.0
    closes = []
    for bar in bars:
        close = bar.get("close") or 0.0
        cum_vol += bar.get("volume") or 0
        cum_amount += bar.get("amount") or 0
        closes.append(close)

        # 均价 = 累计成交额 / 累计成交量；成交额缺失时退化成累计平均收盘价
        if cum_vol > 0 and cum_amount > 0:
            vwap = cum_amount / cum_vol
            mean_close = sum(closes) / len(closes)
            if mean_close > 0 and (vwap < mean_close / 2
                                   or vwap > mean_close * 2):
                # 成交量和成交额不是同一量纲（指数的分钟量级和个股不是一回事，
                # 算出来的是成分股均价而不是指数点位），这时均价线只能用累计
                # 平均收盘价，否则图上会画出一条毫无意义的线
                bar["av_price"] = round(mean_close, 4)
            else:
                bar["av_price"] = round(vwap, 4)
        elif closes:
            bar["av_price"] = round(sum(closes) / len(closes), 4)
        else:
            bar["av_price"] = pre_close

        if pre_close > 0:
            bar["change"] = round(close - pre_close, 4)
            bar["change_percent"] = round((close - pre_close) / pre_close * 100, 2)
        else:
            bar["change"] = 0.0
            bar["change_percent"] = 0.0

    return bars


# ──────────────── 对外入口 ────────────────

def _friendly_error(error: Exception) -> str:
    if isinstance(error, requests.exceptions.Timeout):
        return "请求超时（%d秒）" % TIMEOUT
    if isinstance(error, requests.exceptions.ConnectionError):
        return "网络连接失败"
    if isinstance(error, requests.exceptions.HTTPError):
        return "请求失败，请稍后再试"
    return "请求异常：%s" % error


def query_minute_data(stock_code: str, stock_type: str = "stock",
                      date: str = "") -> dict:
    """查询指定交易日的分钟级分时数据。

    参数：
        stock_code: 纯数字代码
        stock_type: "stock" / "index" / "etf"
        date:       日期，支持 "2026-10-23" 等多种写法

    返回：
        {
            "error":       None / 错误信息,
            "requested":   用户要的日期,
            "date":        实际拿到数据的日期（休市日会自动回退到最近交易日）,
            "note":        自动回退时的说明,
            "klt":         实际使用的分钟粒度（1 / 5 / 15）,
            "source":      实际用的数据源名（"东方财富" / "新浪"）,
            "pre_close":   前一交易日收盘价，分时图的基准线,
            "bars": [      按时间升序
                {"date","time","open","high","low","close","volume","amount",
                 "av_price","change","change_percent"}
            ],
            "latest_date":     本次接口里最新的一天,
            "earliest_date":   本次接口能回溯到的最早一天,
        }

    网络错误、超时、解析失败等都捕获并写进 error，不抛异常。
    """
    result = {
        "error": None,
        "requested": "",
        "date": "",
        "note": "",
        "klt": 0,
        "source": "",
        "pre_close": 0.0,
        "bars": [],
        "latest_date": "",
        "earliest_date": "",
    }

    target = normalize_date(date)
    if not target:
        result["error"] = "日期格式无效，应为 YYYY-MM-DD"
        return result
    result["requested"] = target

    # em_secid 是严格的代码校验（6 位数字）；stock_api._build_code 太宽松，
    # 什么都会拼成 shXXX，不能拿它当校验用
    secid = em_secid(stock_code, stock_type)
    if not secid:
        result["error"] = "%s 不是有效的6位代码" % (stock_code or "")
        return result
    sina_symbol = _sina_symbol_of(stock_code, stock_type)

    rows = []
    fetch_error = ""
    used_klt = 0
    used_source = ""
    for klt in KLT_CANDIDATES:
        try:
            fetched, source = _fetch_klines(secid, sina_symbol, klt)
        except FetchError as e:
            fetch_error = str(e)                     # 消息已经拼好，直接用
            if e.network:
                break                                # 全连不上：放粗粒度也救不回来
        except Exception as e:
            # 兜底：任何意外都不许抛到界面上
            fetch_error = _friendly_error(e)
        else:
            if not fetched:
                fetch_error = fetch_error or "接口返回数据为空"
                continue
            rows, used_klt, used_source = fetched, klt, source
            if _bars_of_day(rows, target):
                break
            # 粒度 1 分钟拉不到这么远，放粗再试一次

    if not rows:
        result["error"] = "分时数据获取失败：%s" % (fetch_error or "未知原因")
        return result

    all_dates = sorted({r["date"] for r in rows
                        if isinstance(r, dict) and r.get("date")})
    result["latest_date"] = all_dates[-1]
    result["earliest_date"] = all_dates[0]

    bars = _bars_of_day(rows, target)
    if not bars:
        nearest, gap = _nearest_date(all_dates, target)
        if nearest and gap <= MAX_BACKTRACK_DAYS:
            bars = _bars_of_day(rows, nearest)
            result["date"] = nearest
            result["note"] = "%s 没有交易数据，已自动切换到最近交易日 %s" % (
                target, nearest)
        else:
            result["error"] = "没有 %s 的分时数据（当前可回溯 %s ~ %s）" % (
                target, result["earliest_date"], result["latest_date"])
            return result
    else:
        result["date"] = target

    # 走到兜底源时说明主源没连上，而且它只能回溯最近一段，值得在状态栏说一声
    if used_source and used_source != EM_SOURCE:
        fallback_note = "数据来自%s（%s接口暂时连不上，只能回溯最近一段）" % (
            used_source, EM_SOURCE)
        result["note"] = ("%s；%s" % (result["note"], fallback_note)
                          if result["note"] else fallback_note)

    pre_close = _prev_close(rows, result["date"])
    if pre_close <= 0:
        # 拉到的窗口里恰好没有更早的一个交易日：常见于「想要的那天正好是
        # 兜底源能回溯到的第一天的边界」——新浪的窗口只有最近一段时间，比
        # 东方财富短得多，所以这个情况在兜底时更容易遇到。这里退成当天开盘
        # 价，代价是分时基准线和涨跌幅都相对"当天开盘"而不是"昨收"（跳空
        # 那天会显示成 0%）。要真值得另拉一次日K，为一条辅助线不值得。
        pre_close = bars[0].get("open") or bars[0].get("close") or 0.0

    # 取到的一天数据再排一次序：正常来说 _fetch_klines 已经排过，
    # 这里兜底一下，别让哪天换数据源把分时图的先后顺序弄乱
    bars.sort(key=lambda r: (r["date"], r["time"]))

    result["klt"] = used_klt
    result["source"] = used_source
    result["pre_close"] = pre_close
    result["bars"] = _enrich_bars(bars, pre_close)
    return result


# 测试入口（直接运行此文件时执行）
if __name__ == "__main__":
    import sys

    def _say(text):
        """GBK 控制台打不出 emoji（UnicodeEncodeError），兜底按问号打印。

        中文 Windows 默认控制台就是 GBK，除非开了"使用 Unicode UTF-8"。
        """
        try:
            print(text)
        except UnicodeEncodeError:
            encoding = sys.stdout.encoding or "ascii"
            print(text.encode(encoding, "replace").decode(encoding, "replace"))

    code = sys.argv[1] if len(sys.argv) > 1 else "600000"
    day = sys.argv[2] if len(sys.argv) > 2 else ""
    _say("正在查询 %s %s 的分时数据 ..." % (code, day or "（默认）"))
    data = query_minute_data(code, "stock", day)
    if data["error"]:
        _say("❌ %s" % data["error"])
    else:
        _say("✅ %s  %d 根 %s分钟K线  昨收 %.2f  数据源 %s" % (
            data["date"], len(data["bars"]), data["klt"], data["pre_close"],
            data["source"]))
        if data["note"]:
            print("   %s" % data["note"])
        for bar in data["bars"][:3]:
            print("   %s %s  开%.2f 收%.2f  均价%.2f" % (
                bar["time"], bar["date"], bar["open"], bar["close"],
                bar["av_price"]))
