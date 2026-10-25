"""
minute_api 模块单元测试
=======================
覆盖东方财富分钟K线的代码转换、行解析、按日截取、均价线计算，
以及 query_minute_data 的成功 / 回退 / 失败各分支。
所有网络请求都被 mock，测试不访问真实接口。
"""

import json
import time

import pytest
import requests

from stock_monitor import minute_api


# ──────────────── 测试辅助 ────────────────

def _row(date, time_, open_, close, high, low, volume_lots, amount=0.0):
    """按接口的逗号串格式造一行（volume_lots 单位是手）"""
    return "%s %s,%.2f,%.2f,%.2f,%.2f,%d,%.2f,0.10,1.00,0.10,0.50" % (
        date, time_, open_, close, high, low, volume_lots, amount)


def _day_rows(date="2026-10-23", n=3, base_open=9.60, step=0.10,
              volume_lots=100, amount=96000.0):
    """造某一天的 n 根分钟K线（已解析成 dict，与 _fetch_klines 的返回一致）"""
    return [minute_api._parse_kline_row(_row(
        date, "09:3%d" % (1 + i),
        base_open + i * step, base_open + i * step + 0.05,
        base_open + i * step + 0.15, base_open + i * step - 0.05,
        volume_lots, amount))
        for i in range(n)]


def _patch_fetch(monkeypatch, responses, source="东方财富"):
    """把 _fetch_klines 换成按 klt 顺序返回的假实现。

    responses: [klt -> (行列表 | 异常)] 的列表，元素耗尽后继续用最后一个。
    """
    calls = []
    def _fake(secid, sina_symbol, klt):
        calls.append((secid, sina_symbol, klt))
        index = min(len(calls) - 1, len(responses) - 1)
        item = responses[index]
        if isinstance(item, Exception):
            raise item
        return list(item), source
    monkeypatch.setattr(minute_api, "_fetch_klines", _fake)
    return calls


# ──────────────── normalize_date ────────────────

@pytest.mark.parametrize("raw,expected", [
    ("2026-10-23", "2026-10-23"),
    ("2026/10/23", "2026-10-23"),
    ("2026.10.23", "2026-10-23"),
    ("20261023", "2026-10-23"),
    ("2026-1-5", "2026-01-05"),
    ("2026-10-23 09:31", "2026-10-23"),
    ("  2026-10-23  ", "2026-10-23"),
])
def test_normalize_date_accepts_common_formats(raw, expected):
    assert minute_api.normalize_date(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "  ", "2026-13-40", "hello", 0])
def test_normalize_date_rejects_invalid(raw):
    assert minute_api.normalize_date(raw) == ""


# ──────────────── em_secid ────────────────

@pytest.mark.parametrize("code,stock_type,expected", [
    ("600000", "stock", "1.600000"),      # 上海主板
    ("601398", "stock", "1.601398"),
    ("000002", "stock", "0.000002"),      # 深圳主板
    ("300750", "stock", "0.300750"),      # 创业板
    ("430090", "stock", "0.430090"),      # 北交所：尽力归深
    ("999999", "stock", "1.999999"),      # 未知前缀 → 与 _build_code 一致归沪
    ("000001", "index", "1.000001"),      # 上证指数
    ("399001", "index", "0.399001"),      # 深证成指
    ("399006", "index", "0.399006"),
    ("510300", "etf", "1.510300"),        # 沪市 ETF
    ("159915", "etf", "0.159915"),        # 深市 ETF
    ("160000", "etf", "0.160000"),        # 深市 LOF
])
def test_em_secid_maps_markets(code, stock_type, expected):
    assert minute_api.em_secid(code, stock_type) == expected


@pytest.mark.parametrize("code", ["", "60000", "6000001", "60000A", "sh600000"])
def test_em_secid_rejects_bad_code(code):
    assert minute_api.em_secid(code, "stock") == ""


def test_em_secid_default_type_is_stock():
    assert minute_api.em_secid("600000") == "1.600000"


# ──────────────── _parse_kline_row ────────────────

def test_parse_kline_row_maps_all_fields():
    parsed = minute_api._parse_kline_row(_row(
        "2026-10-23", "09:31", 9.69, 9.65, 9.73, 9.64, 32290, 31271508.0))

    assert parsed["date"] == "2026-10-23"
    assert parsed["time"] == "09:31"
    assert parsed["open"] == 9.69
    assert parsed["close"] == 9.65
    assert parsed["high"] == 9.73
    assert parsed["low"] == 9.64
    # 手 → 股
    assert parsed["volume"] == 3229000
    assert parsed["amount"] == 31271508.0


def test_parse_kline_row_zero_pads_time():
    parsed = minute_api._parse_kline_row(
        "2026-10-23 9:5,9.69,9.65,9.73,9.64,100,9600.0,0.1,1.0,0.1,0.5")

    assert parsed["time"] == "09:05"


def test_parse_kline_row_rejects_too_few_fields():
    assert minute_api._parse_kline_row("2026-10-23 09:31,9.69,9.65") is None


def test_parse_kline_row_rejects_bad_timestamp():
    assert minute_api._parse_kline_row(
        "2026-99-99 09:31,9.69,9.65,9.73,9.64,100,0,0,0,0,0") is None


@pytest.mark.parametrize("row", [None, 42, ["not", "a", "string"]])
def test_parse_kline_row_rejects_non_string(row):
    assert minute_api._parse_kline_row(row) is None


def test_parse_kline_row_non_numeric_volume_becomes_zero():
    parsed = minute_api._parse_kline_row(
        "2026-10-23 09:31,9.69,9.65,9.73,9.64,-,0,0,0,0,0")

    assert parsed["volume"] == 0


# ──────────────── 按日截取与基准价 ────────────────

def test_bars_of_day_filters_only_that_day():
    data = _day_rows("2026-10-22", 2) + _day_rows("2026-10-23", 3)

    assert len(minute_api._bars_of_day(data, "2026-10-23")) == 3
    assert minute_api._bars_of_day(data, "2026-10-25") == []


def test_prev_close_takes_last_bar_of_previous_trading_day():
    # _day_rows 的收盘 = base_open + i * step + 0.05 → 9.65 / 9.75
    first_day = _day_rows("2026-10-22", 2)
    target = _day_rows("2026-10-23", 1)

    assert minute_api._prev_close(first_day + target, "2026-10-23") == 9.75


def test_prev_close_zero_when_no_earlier_day():
    assert minute_api._prev_close(_day_rows("2026-10-23", 3), "2026-10-23") == 0.0


@pytest.mark.parametrize("dates,target,nearest,gap", [
    (["2026-10-20", "2026-10-22", "2026-10-23"], "2026-10-23", "2026-10-23", 0),
    (["2026-10-20", "2026-10-23"], "2026-10-25", "2026-10-23", 2),
    (["2026-10-22", "2026-10-23"], "2026-10-21", "2026-10-22", 1),
])
def test_nearest_date_finds_closest(dates, target, nearest, gap):
    assert minute_api._nearest_date(dates, target) == (nearest, gap)


def test_nearest_date_empty_input():
    assert minute_api._nearest_date([], "2026-10-23") == ("", 0)


# ──────────────── _enrich_bars ────────────────

def test_enrich_bars_computes_running_vwap():
    bars = [
        {"close": 10.0, "volume": 100, "amount": 1000.0},
        {"close": 10.2, "volume": 200, "amount": 2040.0},
    ]

    enriched = minute_api._enrich_bars(bars, 9.9)

    assert enriched[0]["av_price"] == 10.0
    # (1000 + 2040) / (100 + 200) = 10.1333...
    assert enriched[1]["av_price"] == 10.1333


def test_enrich_bars_changes_are_relative_to_pre_close():
    bars = [{"close": 10.1, "volume": 100, "amount": 1010.0},
            {"close": 9.8, "volume": 50, "amount": 490.0}]

    enriched = minute_api._enrich_bars(bars, 10.0)

    assert enriched[0]["change"] == 0.1
    assert enriched[0]["change_percent"] == 1.0
    assert enriched[1]["change"] == -0.2
    assert enriched[1]["change_percent"] == -2.0


def test_enrich_bars_falls_back_when_amount_missing():
    # 没有成交额时退化成"到目前为止的平均收盘价"：(10 + 12) / 2 = 11
    bars = [{"close": 10.0, "volume": 100, "amount": 0},
            {"close": 12.0, "volume": 100, "amount": 0}]

    enriched = minute_api._enrich_bars(bars, 9.9)

    assert enriched[1]["av_price"] == 11.0


def test_enrich_bars_zero_pre_close_gives_zero_change():
    enriched = minute_api._enrich_bars([{"close": 10.0, "volume": 0, "amount": 0}], 0)

    assert enriched[0]["change"] == 0.0
    assert enriched[0]["change_percent"] == 0.0


def test_enrich_bars_ignores_vwap_that_cannot_be_a_price():
    """指数的分钟成交量/成交额和个股不是同一量纲：除出来约等于成分股均价
    （十几块）而不是指数点位（上万），这种成交额要当成不可用。"""
    bars = [{"close": 11187.55, "volume": 100000, "amount": 2502000000},
            {"close": 11666.85, "volume": 100000, "amount": 2400000000}]

    enriched = minute_api._enrich_bars(bars, 11357.0)

    # 退化成累计平均收盘价：(11187.55 + 11666.85) / 2
    assert enriched[1]["av_price"] == 11427.2


def test_enrich_bars_keeps_vwap_when_units_match():
    """量纲对得上（成交额/成交量 ≈ 收盘价）时，均价线仍按 VWAP 算。"""
    bars = [{"close": 9.53, "volume": 405300, "amount": 3866000},
            {"close": 9.52, "volume": 326700, "amount": 3116419}]

    enriched = minute_api._enrich_bars(bars, 9.62)

    assert enriched[1]["av_price"] == 9.5388


# ──────────────── query_minute_data ────────────────

def test_query_rejects_invalid_date():
    result = minute_api.query_minute_data("600000", "stock", "明年")

    assert "日期格式无效" in result["error"]
    assert result["bars"] == []


def test_query_rejects_invalid_code():
    result = minute_api.query_minute_data("abc", "stock", "2026-10-23")

    assert "不是有效的6位代码" in result["error"]
    assert result["bars"] == []


def test_query_reports_date_in_result(monkeypatch):
    _patch_fetch(monkeypatch, [_day_rows("2026-10-23", 3)])

    result = minute_api.query_minute_data(" 600000 ", "stock", "2026-10-23")

    assert result["error"] is None
    assert result["requested"] == "2026-10-23"
    assert result["date"] == "2026-10-23"
    assert result["klt"] == 1
    assert len(result["bars"]) == 3


def test_query_returns_enriched_bars(monkeypatch):
    _patch_fetch(monkeypatch, [
        _day_rows("2026-10-22", 2) + _day_rows("2026-10-23", 3)])

    result = minute_api.query_minute_data("600000", "stock", "2026-10-23")

    assert result["error"] is None
    # 基准线取前一交易日最后一根收盘价 9.75
    assert result["pre_close"] == 9.75
    first = result["bars"][0]
    for field in ("date", "time", "open", "high", "low", "close",
                  "volume", "amount", "av_price", "change", "change_percent"):
        assert field in first


def test_query_falls_back_to_coarser_granularity(monkeypatch):
    """1 分钟拉不到那么远，放粗到 5 分钟重试"""
    calls = _patch_fetch(monkeypatch, [
        _day_rows("2026-10-22", 2),           # klt=1：没有目标日
        _day_rows("2026-10-23", 5),           # klt=5：有目标日
    ])

    result = minute_api.query_minute_data("600000", "stock", "2026-10-23")

    assert result["error"] is None
    assert result["klt"] == 5
    assert len(result["bars"]) == 5
    assert [item[2] for item in calls] == [1, 5]


def test_query_backtracks_to_nearest_trading_day(monkeypatch):
    _patch_fetch(monkeypatch, [_day_rows("2026-10-23", 3)])

    result = minute_api.query_minute_data("600000", "stock", "2026-10-25")

    assert result["error"] is None
    assert result["requested"] == "2026-10-25"
    assert result["date"] == "2026-10-23"
    assert "2026-10-25" in result["note"]
    assert "2026-10-23" in result["note"]


def test_query_errors_when_date_is_out_of_range(monkeypatch):
    _patch_fetch(monkeypatch, [_day_rows("2026-10-23", 3)])

    result = minute_api.query_minute_data("600000", "stock", "2020-01-01")

    assert result["error"] is not None
    assert "2020-01-01" in result["error"]
    assert "2026-10-23" in result["error"]     # 告诉用户能回溯到哪
    assert result["bars"] == []


def test_query_reports_reachable_range(monkeypatch):
    _patch_fetch(monkeypatch, [
        _day_rows("2026-10-22", 2) + _day_rows("2026-10-23", 3)])

    result = minute_api.query_minute_data("600000", "stock", "2026-10-23")

    assert result["earliest_date"] == "2026-10-22"
    assert result["latest_date"] == "2026-10-23"


def test_query_wraps_timeout(monkeypatch):
    _patch_fetch(monkeypatch, [
        requests.exceptions.Timeout("timed out")])

    result = minute_api.query_minute_data("600000", "stock", "2026-10-23")

    assert "请求超时" in result["error"]


def test_query_wraps_connection_error(monkeypatch):
    _patch_fetch(monkeypatch, [
        requests.exceptions.ConnectionError("boom")])

    result = minute_api.query_minute_data("600000", "stock", "2026-10-23")

    assert "网络连接失败" in result["error"]


def test_query_wraps_http_error(monkeypatch):
    _patch_fetch(monkeypatch, [
        requests.exceptions.HTTPError("502 Bad Gateway")])

    result = minute_api.query_minute_data("600000", "stock", "2026-10-23")

    assert "请求失败" in result["error"]


def test_query_wraps_unknown_error(monkeypatch):
    _patch_fetch(monkeypatch, [ValueError("weird")])

    result = minute_api.query_minute_data("600000", "stock", "2026-10-23")

    assert "请求异常" in result["error"]


def test_query_treats_empty_response_as_failure(monkeypatch):
    _patch_fetch(monkeypatch, [[]])

    result = minute_api.query_minute_data("600000", "stock", "2026-10-23")

    assert "返回数据为空" in result["error"]


def test_query_passes_secid_to_fetch(monkeypatch):
    calls = _patch_fetch(monkeypatch, [_day_rows("2026-10-23", 3)])

    minute_api.query_minute_data("399001", "index", "2026-10-23")

    assert calls[0][0] == "0.399001"


def test_query_passes_sina_symbol_to_fetch(monkeypatch):
    calls = _patch_fetch(monkeypatch, [_day_rows("2026-10-23", 3)])

    minute_api.query_minute_data("600000", "stock", "2026-10-23")

    assert calls[0][1] == "sh600000"


def test_query_reports_the_data_source(monkeypatch):
    _patch_fetch(monkeypatch, [_day_rows("2026-10-23", 3)], source="新浪")

    result = minute_api.query_minute_data("600000", "stock", "2026-10-23")

    assert result["error"] is None
    assert result["source"] == "新浪"
    # 主源连不上时得告诉用户数据从哪来、能回溯多远
    assert "数据来自新浪" in result["note"]
    assert minute_api.EM_SOURCE in result["note"]


def test_query_keeps_note_empty_when_primary_source_works(monkeypatch):
    _patch_fetch(monkeypatch, [_day_rows("2026-10-23", 3)])

    result = minute_api.query_minute_data("600000", "stock", "2026-10-23")

    # 主源正常时不解释来源，状态栏只留必要信息
    assert result["source"] == "东方财富"
    assert result["note"] == ""


def test_query_combines_backtrack_and_source_notes(monkeypatch):
    _patch_fetch(monkeypatch, [_day_rows("2026-10-23", 3)], source="新浪")

    result = minute_api.query_minute_data("600000", "stock", "2026-10-25")

    assert result["date"] == "2026-10-23"
    assert "已自动切换到最近交易日" in result["note"]
    assert "数据来自新浪" in result["note"]


def test_query_surfaces_fetch_error_message(monkeypatch):
    _patch_fetch(monkeypatch, [
        minute_api.FetchError("东方财富：网络连接失败；新浪：请求超时（10秒）")])

    result = minute_api.query_minute_data("600000", "stock", "2026-10-23")

    assert result["error"] is not None
    assert minute_api.EM_SOURCE in result["error"]
    assert minute_api.SINA_SOURCE in result["error"]
    assert "网络连接失败" in result["error"]
    assert result["bars"] == []


def test_query_stops_cascading_on_total_network_outage(monkeypatch):
    """网络全断时别为了放粗粒度再跑两遍——每遍都是几轮超时。"""
    calls = []
    def _down(secid, sina_symbol, klt):
        calls.append(klt)
        raise minute_api.FetchError("东方财富：网络连接失败；新浪：网络连接失败",
                                    network=True)
    monkeypatch.setattr(minute_api, "_fetch_klines", _down)

    result = minute_api.query_minute_data("600000", "stock", "2026-10-23")

    assert calls == [1]                          # 只试了一次就收手
    assert result["error"] is not None
    assert result["bars"] == []


def test_query_keeps_cascading_when_data_is_just_empty(monkeypatch):
    """只是没覆盖到那天（不是连不上）时，还得继续放粗粒度找。"""
    calls = _patch_fetch(monkeypatch, [[], []])

    result = minute_api.query_minute_data("600000", "stock", "2026-10-23")

    assert [item[2] for item in calls] == [1, 5, 15]
    assert "返回数据为空" in result["error"]


def test_query_sorts_bars_by_time(monkeypatch):
    rows = _day_rows("2026-10-23", 2)
    rows.reverse()                            # 故意乱序喂进去
    _patch_fetch(monkeypatch, [rows])

    result = minute_api.query_minute_data("600000", "stock", "2026-10-23")

    assert result["bars"][0]["time"] < result["bars"][-1]["time"]


def test_query_skips_unparseable_rows(monkeypatch):
    _patch_fetch(monkeypatch, [
        _day_rows("2026-10-23", 2) +
        [minute_api._parse_kline_row("2026-10-23 09:39,bogus")]])

    result = minute_api.query_minute_data("600000", "stock", "2026-10-23")

    assert result["error"] is None
    assert len(result["bars"]) == 2


# ──────────────── _fetch_klines（网络层） ────────────────

class _FakeResponse:
    """只实现被测代码用到的接口：raise_for_status() 和 content"""

    def __init__(self, body):
        self.content = body

    def raise_for_status(self):
        return None


def _em_body(rows):
    """拼一个东方财富的响应体（名字故意带中文，用来验证 UTF-8 解码）"""
    return json.dumps({"rc": 0, "data": {"code": "600000",
                                         "name": "浦发银行",
                                         "klines": rows}}).encode("utf-8")


def test_fetch_klines_em_parses_and_sorts(monkeypatch):
    monkeypatch.setattr(
        minute_api.requests, "get",
        lambda *a, **k: _FakeResponse(_em_body([
            _row("2026-10-23", "09:32", 9.70, 9.75, 9.85, 9.65, 100),
            _row("2026-10-23", "09:31", 9.60, 9.65, 9.75, 9.55, 100),
        ])))

    rows = minute_api._fetch_klines_em("1.600000", 1)

    assert len(rows) == 2
    assert rows[0]["time"] == "09:31"
    assert rows[1]["time"] == "09:32"


def test_fetch_klines_em_drops_bad_rows(monkeypatch):
    monkeypatch.setattr(
        minute_api.requests, "get",
        lambda *a, **k: _FakeResponse(_em_body([
            _row("2026-10-23", "09:31", 9.60, 9.65, 9.75, 9.55, 100),
            "2026-10-23 09:32,bogus",
            None,
        ])))

    rows = minute_api._fetch_klines_em("1.600000", 1)

    assert len(rows) == 1


def test_fetch_klines_em_retries_once_then_succeeds(monkeypatch):
    attempts = []
    def _flaky(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise requests.exceptions.ConnectionError("抖了一下")
        return _FakeResponse(_em_body([
            _row("2026-10-23", "09:31", 9.60, 9.65, 9.75, 9.55, 100)]))
    monkeypatch.setattr(minute_api.requests, "get", _flaky)

    rows = minute_api._fetch_klines_em("1.600000", 1)

    assert len(attempts) == 2
    assert len(rows) == 1


def test_fetch_klines_em_falls_back_to_direct_when_proxy_rejects(monkeypatch):
    """系统代理对东方财富域名不响应时，改直连重试。

    回归：网络正常却报"网络连接失败"。代理软件掐断请求在 urllib3 层
    可能表现为 ProxyError，也可能只是 ConnectionError（RemoteDisconnected）。
    """
    for first_error in (requests.exceptions.ProxyError("代理掐断连接"),
                        requests.exceptions.ConnectionError("RemoteDisconnected")):
        attempts = []
        def _proxied(*args, **kwargs):
            attempts.append(kwargs.get("proxies"))
            if len(attempts) == 1:
                raise first_error
            return _FakeResponse(_em_body([
                _row("2026-10-23", "09:31", 9.60, 9.65, 9.75, 9.55, 100)]))
        monkeypatch.setattr(minute_api.requests, "get", _proxied)

        rows = minute_api._fetch_klines_em("1.600000", 1)

        assert len(rows) == 1
        # 第一次走系统代理（proxies=None），第二次显式直连
        assert attempts[0] is None
        assert attempts[1] == {"http": None, "https": None}


def test_fetch_klines_em_raises_when_every_attempt_fails(monkeypatch):
    def _down(*args, **kwargs):
        raise requests.exceptions.ConnectionError("网络不通")
    monkeypatch.setattr(minute_api.requests, "get", _down)

    with pytest.raises(requests.exceptions.ConnectionError):
        minute_api._fetch_klines_em("1.600000", 1)


def test_fetch_klines_em_sends_expected_params(monkeypatch):
    captured = {}
    def _spy(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        captured["headers"] = kwargs.get("headers")
        return _FakeResponse(_em_body([]))
    monkeypatch.setattr(minute_api.requests, "get", _spy)

    minute_api._fetch_klines_em("1.600000", 5)

    assert captured["url"] == minute_api.EM_KLINE_URL
    assert captured["params"]["secid"] == "1.600000"
    assert captured["params"]["klt"] == 5
    assert captured["params"]["fqt"] == 0
    assert captured["params"]["lmt"] == minute_api.LMT_BY_KLT[5]
    assert "eastmoney" in captured["headers"]["Referer"]


# ──────────────── 新浪兜底源 ────────────────

def _sina_row(day, open_, high, low, close, volume, amount):
    """按新浪接口的形状造一行（数字都是字符串，和真实返回一致）"""
    return {"day": day, "open": "%.3f" % open_, "high": "%.3f" % high,
            "low": "%.3f" % low, "close": "%.3f" % close,
            "volume": str(volume), "amount": "%.2f" % amount,
            "ma_price5": 9.6}


def _sina_body(rows):
    return json.dumps(rows, ensure_ascii=False).encode("utf-8")


def test_parse_sina_row_maps_fields_without_volume_scaling():
    parsed = minute_api._parse_sina_row(_sina_row(
        "2026-10-23 15:00:00", 9.69, 9.73, 9.64, 9.65, 3229000, 31271508.0))

    assert parsed["date"] == "2026-10-23"
    assert parsed["time"] == "15:00"
    assert parsed["close"] == 9.65
    # 新浪的分钟量本来就是「股」，不能像东方财富那样再乘 100
    assert parsed["volume"] == 3229000
    assert parsed["amount"] == 31271508.0


@pytest.mark.parametrize("row", [None, 42, "not a dict",
                                 {"day": "2026-10-23"},
                                 {"day": "2026-10-23 25:99", "close": "9.65"},
                                 {"day": "2026-99-99 09:31:00"}])
def test_parse_sina_row_rejects_bad_rows(row):
    assert minute_api._parse_sina_row(row) is None


def test_fetch_klines_sina_parses_sorts_and_clamps_datalen(monkeypatch):
    captured = {}
    def _spy(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        captured["headers"] = kwargs.get("headers")
        return _FakeResponse(_sina_body([
            _sina_row("2026-10-23 09:32:00", 9.70, 9.85, 9.65, 9.75, 100, 975.0),
            _sina_row("2026-10-23 09:31:00", 9.60, 9.75, 9.55, 9.65, 100, 965.0),
        ]))
    monkeypatch.setattr(minute_api.requests, "get", _spy)

    rows = minute_api._fetch_klines_sina("sh600000", 5)

    assert captured["url"] == minute_api.SINA_KLINE_URL
    # datalen 给超过 1023 新浪会直接返回 null，所以必须卡在上限
    assert captured["params"] == {"symbol": "sh600000", "scale": 5,
                                  "datalen": minute_api.SINA_MAX_DATALEN}
    assert "sina" in captured["headers"]["Referer"]
    assert len(rows) == 2
    assert rows[0]["time"] == "09:31"
    assert rows[1]["time"] == "09:32"


@pytest.mark.parametrize("payload", [None, [], [None, "bogus", 42]])
def test_fetch_klines_sina_empty_when_no_rows(monkeypatch, payload):
    monkeypatch.setattr(minute_api.requests, "get",
                        lambda *a, **k: _FakeResponse(_sina_body(payload)))

    assert minute_api._fetch_klines_sina("sh600000", 1) == []


def test_fetch_klines_sina_skips_request_for_bad_symbol(monkeypatch):
    calls = []
    monkeypatch.setattr(minute_api.requests, "get",
                        lambda *a, **k: calls.append(1))

    assert minute_api._fetch_klines_sina("", 1) == []
    assert calls == []


def test_fetch_klines_sina_propagates_network_errors(monkeypatch):
    def _down(*args, **kwargs):
        raise requests.exceptions.ConnectionError("网络不通")
    monkeypatch.setattr(minute_api.requests, "get", _down)

    with pytest.raises(requests.exceptions.ConnectionError):
        minute_api._fetch_klines_sina("sh600000", 1)


def test_fetch_klines_sina_retries_once_on_blip(monkeypatch):
    """单次抖动要重试一次：否则它会冒充"全源都连不上"而触发提前收手，
    把本来能拿到的数据丢掉。"""
    attempts = []
    def _flaky(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise requests.exceptions.ConnectionError("抖了一下")
        return _FakeResponse(_sina_body([
            _sina_row("2026-10-23 09:31:00", 9.60, 9.75, 9.55, 9.65, 100, 965.0)]))
    monkeypatch.setattr(minute_api.requests, "get", _flaky)

    rows = minute_api._fetch_klines_sina("sh600000", 1)

    assert len(attempts) == 2
    assert len(rows) == 1


def test_fetch_klines_sina_does_not_retry_bad_json(monkeypatch):
    """坏响应是服务端问题，重试也白搭，直接抛出去让上层报"请求异常"。"""
    attempts = []
    def _broken(*args, **kwargs):
        attempts.append(1)
        return _FakeResponse(b"{not json")
    monkeypatch.setattr(minute_api.requests, "get", _broken)

    with pytest.raises(json.JSONDecodeError):
        minute_api._fetch_klines_sina("sh600000", 1)

    assert len(attempts) == 1


def test_is_network_error_only_matches_connection_problems():
    assert minute_api._is_network_error(requests.exceptions.ConnectionError("x"))
    assert minute_api._is_network_error(requests.exceptions.Timeout("x"))
    assert not minute_api._is_network_error(requests.exceptions.HTTPError("x"))
    assert not minute_api._is_network_error(ValueError("x"))


# ──────────────── 数据源切换 ────────────────

def _one_row():
    return [_day_rows("2026-10-23", 1)[0]]


def test_fetch_klines_uses_eastmoney_when_it_works(monkeypatch):
    calls = []
    def _em(secid, klt):
        calls.append(("em", secid, klt))
        return _one_row()
    def _sina(symbol, klt):
        calls.append(("sina", symbol, klt))
        return _one_row()
    monkeypatch.setattr(minute_api, "_fetch_klines_em", _em)
    monkeypatch.setattr(minute_api, "_fetch_klines_sina", _sina)
    minute_api._reset_source_state()

    rows, source = minute_api._fetch_klines("1.600000", "sh600000", 1)

    assert len(rows) == 1
    assert source == minute_api.EM_SOURCE
    # 主源没问题就别去动兜底源
    assert calls == [("em", "1.600000", 1)]


def test_fetch_klines_falls_back_to_sina_when_eastmoney_is_down(monkeypatch):
    calls = []
    def _em(secid, klt):
        calls.append("em")
        raise requests.exceptions.ConnectionError("RemoteDisconnected")
    def _sina(symbol, klt):
        calls.append("sina")
        return _one_row()
    monkeypatch.setattr(minute_api, "_fetch_klines_em", _em)
    monkeypatch.setattr(minute_api, "_fetch_klines_sina", _sina)
    minute_api._reset_source_state()

    rows, source = minute_api._fetch_klines("1.600000", "sh600000", 1)

    assert len(rows) == 1
    assert source == minute_api.SINA_SOURCE
    assert calls == ["em", "sina"]


def test_fetch_klines_skips_eastmoney_until_it_works_again(monkeypatch):
    """东财连不上会记一笔，冷静期内不再先试它（省掉每次白等两轮超时）；
    冷静期一过又试它，取到数据就把这笔记录清掉。"""
    monkeypatch.setattr(minute_api, "_EM_DOWN_TTL", 0.001)    # 别真等 5 分钟
    calls = []
    def _em(secid, klt):
        calls.append("em")
        if len(calls) == 1:
            raise requests.exceptions.Timeout("超时")
        return _one_row()
    def _sina(symbol, klt):
        calls.append("sina")
        return _one_row()
    monkeypatch.setattr(minute_api, "_fetch_klines_em", _em)
    monkeypatch.setattr(minute_api, "_fetch_klines_sina", _sina)
    minute_api._reset_source_state()

    first = minute_api._fetch_klines("1.600000", "sh600000", 1)
    second = minute_api._fetch_klines("1.600000", "sh600000", 5)
    time.sleep(0.02)                            # 等冷静期过去
    third = minute_api._fetch_klines("1.600000", "sh600000", 15)

    assert calls == ["em", "sina", "sina", "em"]
    assert first[1] == minute_api.SINA_SOURCE
    assert second[1] == minute_api.SINA_SOURCE
    assert third[1] == minute_api.EM_SOURCE


def test_fetch_klines_does_not_cool_off_on_http_error(monkeypatch):
    """HTTP 报错说明接口是通的，不该被记成"连不上"。"""
    calls = []
    def _em(secid, klt):
        calls.append("em")
        raise requests.exceptions.HTTPError("502 Bad Gateway")
    def _sina(symbol, klt):
        calls.append("sina")
        raise requests.exceptions.HTTPError("502 Bad Gateway")
    monkeypatch.setattr(minute_api, "_fetch_klines_em", _em)
    monkeypatch.setattr(minute_api, "_fetch_klines_sina", _sina)
    minute_api._reset_source_state()

    with pytest.raises(minute_api.FetchError):
        minute_api._fetch_klines("1.600000", "sh600000", 1)

    assert not minute_api._eastmoney_down()


def test_fetch_klines_mentions_skipped_source_in_error(monkeypatch):
    """冷静期内跳过主源时，错误信息里还得说一声，别只提新浪。"""
    def _em(secid, klt):
        raise requests.exceptions.ConnectionError("被掐断")
    def _sina(symbol, klt):
        raise requests.exceptions.ConnectionError("被掐断")
    monkeypatch.setattr(minute_api, "_fetch_klines_em", _em)
    monkeypatch.setattr(minute_api, "_fetch_klines_sina", _sina)
    minute_api._reset_source_state()

    try:
        minute_api._fetch_klines("1.600000", "sh600000", 1)
    except minute_api.FetchError:
        pass                                    # 第一次：主源连不上，记进冷静期

    with pytest.raises(minute_api.FetchError) as exc:
        minute_api._fetch_klines("1.600000", "sh600000", 1)

    message = str(exc.value)
    assert "%s：本次未试" % minute_api.EM_SOURCE in message
    assert minute_api.SINA_SOURCE in message


def test_fetch_klines_reports_both_sources_when_all_fail(monkeypatch):
    def _em(secid, klt):
        raise requests.exceptions.ConnectionError("被掐断")
    def _sina(symbol, klt):
        raise requests.exceptions.Timeout("超时")
    monkeypatch.setattr(minute_api, "_fetch_klines_em", _em)
    monkeypatch.setattr(minute_api, "_fetch_klines_sina", _sina)
    minute_api._reset_source_state()

    with pytest.raises(minute_api.FetchError) as exc:
        minute_api._fetch_klines("1.600000", "sh600000", 1)

    message = str(exc.value)
    assert minute_api.EM_SOURCE in message and "网络连接失败" in message
    assert minute_api.SINA_SOURCE in message and "请求超时" in message


def test_fetch_klines_reports_empty_data(monkeypatch):
    monkeypatch.setattr(minute_api, "_fetch_klines_em", lambda s, k: [])
    monkeypatch.setattr(minute_api, "_fetch_klines_sina", lambda s, k: [])
    minute_api._reset_source_state()

    with pytest.raises(minute_api.FetchError) as exc:
        minute_api._fetch_klines("1.600000", "sh600000", 1)

    assert "接口返回数据为空" in str(exc.value)
    # 没数据不等于连不上：放粗粒度可能还有覆盖范围，调用方要继续试
    assert exc.value.network is False


def test_fetch_error_marks_total_network_outage(monkeypatch):
    """全源都是连接错误时打上 network 标记，调用方好提前收手。"""
    def _em(secid, klt):
        raise requests.exceptions.ConnectionError("被掐断")
    def _sina(symbol, klt):
        raise requests.exceptions.Timeout("超时")
    monkeypatch.setattr(minute_api, "_fetch_klines_em", _em)
    monkeypatch.setattr(minute_api, "_fetch_klines_sina", _sina)
    minute_api._reset_source_state()

    with pytest.raises(minute_api.FetchError) as exc:
        minute_api._fetch_klines("1.600000", "sh600000", 1)

    assert exc.value.network is True


def test_fetch_error_is_not_network_when_only_http_fails(monkeypatch):
    """HTTP 报错说明接口是通的，不能当成网络全断。"""
    def _em(secid, klt):
        raise requests.exceptions.HTTPError("502 Bad Gateway")
    def _sina(symbol, klt):
        raise requests.exceptions.HTTPError("502 Bad Gateway")
    monkeypatch.setattr(minute_api, "_fetch_klines_em", _em)
    monkeypatch.setattr(minute_api, "_fetch_klines_sina", _sina)
    minute_api._reset_source_state()

    with pytest.raises(minute_api.FetchError) as exc:
        minute_api._fetch_klines("1.600000", "sh600000", 1)

    assert exc.value.network is False


def test_fetch_klines_survives_a_broken_source_callback(monkeypatch):
    """解析之类的意外不能让整次查询抛出去，得走兜底源。"""
    def _em(secid, klt):
        raise ValueError("解析炸了")
    monkeypatch.setattr(minute_api, "_fetch_klines_em", _em)
    monkeypatch.setattr(minute_api, "_fetch_klines_sina",
                        lambda s, k: _one_row())
    minute_api._reset_source_state()

    rows, source = minute_api._fetch_klines("1.600000", "sh600000", 1)

    assert len(rows) == 1
    assert source == minute_api.SINA_SOURCE

