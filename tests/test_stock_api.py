"""
stock_api 模块单元测试
======================
覆盖交易所前缀构造、新浪行情数据解析、K线日线解析与周/月聚合，
以及各网络异常分支。所有网络请求均被 mock，测试不依赖真实网络。
"""

from unittest import mock

import pytest
import requests

from stock_monitor import stock_api
from stock_monitor.config import SINA_BASE, TIMEOUT


# ──────────────── 测试辅助 ────────────────

class _FakeResponse:
    """模拟 requests.Response，仅实现被测代码使用到的接口。"""

    def __init__(self, text="", payload=None, status_error=None):
        self.text = text
        self.encoding = None
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        return self._payload


def _sina_text(overrides=None):
    """
    构造一条完整的新浪行情返回串（33 个字段，与真实接口一致）。

    参数：
        overrides: {字段下标: 值}，用于按需替换某个字段。
    """
    fields = [""] * 33
    fields[0] = "浦发银行"
    fields[1] = "18.88"      # 今开
    fields[2] = "18.80"      # 昨收
    fields[3] = "19.02"      # 现价
    fields[4] = "19.10"      # 最高
    fields[5] = "18.80"      # 最低
    fields[6] = "19.01"      # 买一
    fields[7] = "19.02"      # 卖一
    fields[8] = "123456"     # 成交量（手）
    fields[9] = "234567"     # 成交额（万）
    fields[30] = "2026-07-30"
    fields[31] = "09:30:15"
    fields[32] = "00"
    for index, value in (overrides or {}).items():
        fields[index] = value
    return 'var hq_str_sh600000="%s";' % ",".join(fields)


def _kline_item(day, open_, close, high, low, volume):
    """构造一条新浪日K线记录（字段均为字符串，与真实接口一致）。"""
    return {
        "day": day,
        "open": str(open_),
        "close": str(close),
        "high": str(high),
        "low": str(low),
        "volume": str(volume),
    }


def _daily_klines():
    """
    新浪日K接口的原始返回（键为 day，数值为字符串），日期升序。

    跨周：2026-01-05~07 属 2026-W01，2026-01-12~13 属 2026-W02。
    2026-01-05 是周一，因此两个自然周分别落在 %W 的第 1、2 周。
    """
    return [
        _kline_item("2026-01-05", 10.0, 10.5, 10.8, 9.9, 100),
        _kline_item("2026-01-06", 10.5, 10.2, 11.0, 10.0, 200),
        _kline_item("2026-01-07", 10.2, 10.9, 11.2, 9.8, 300),
        _kline_item("2026-01-12", 11.0, 11.5, 11.6, 10.9, 400),
        _kline_item("2026-01-13", 11.5, 11.3, 11.7, 11.1, 500),
    ]


def _parsed_klines():
    """query_kline_data 解析后的日K数据（键为 date，数值为 float/int）。"""
    return [
        {"date": "2026-01-05", "open": 10.0, "close": 10.5, "high": 10.8, "low": 9.9, "volume": 100},
        {"date": "2026-01-06", "open": 10.5, "close": 10.2, "high": 11.0, "low": 10.0, "volume": 200},
        {"date": "2026-01-07", "open": 10.2, "close": 10.9, "high": 11.2, "low": 9.8, "volume": 300},
        {"date": "2026-01-12", "open": 11.0, "close": 11.5, "high": 11.6, "low": 10.9, "volume": 400},
        {"date": "2026-01-13", "open": 11.5, "close": 11.3, "high": 11.7, "low": 11.1, "volume": 500},
    ]


# ──────────────── _is_etf_code ────────────────

class TestIsEtfCode:
    @pytest.mark.parametrize("code", ["510300", "512880", "560000", "159915", "160000"])
    def test_etf_codes(self, code):
        assert stock_api._is_etf_code(code) is True

    @pytest.mark.parametrize("code", ["600000", "000001", "300750", "430090", "399001"])
    def test_non_etf_codes(self, code):
        assert stock_api._is_etf_code(code) is False


# ──────────────── _build_code ────────────────

BUILD_CODE_CASES = [
    # (代码, 类型, 期望结果)
    ("600000", "stock", "sh600000"),      # 上海主板
    ("601398", "stock", "sh601398"),
    ("000002", "stock", "sz000002"),      # 深圳主板
    ("300750", "stock", "sz300750"),      # 创业板
    ("430090", "stock", "bj430090"),      # 北交所
    ("999999", "stock", "sh999999"),      # 未知前缀 → 回退上海
    ("000001", "index", "sh000001"),      # 上证指数（映射表）
    ("000300", "index", "sh000300"),      # 沪深300（映射表）
    ("000688", "index", "sh000688"),      # 科创50（映射表）
    ("000905", "index", "sh000905"),      # 中证500（映射表）
    ("000922", "index", "sh000922"),      # 中证1000（映射表）
    ("399001", "index", "sz399001"),      # 深证成指（映射表）
    ("399005", "index", "sz399005"),      # 中小板指（映射表）
    ("399006", "index", "sz399006"),      # 创业板指（映射表）
    ("399999", "index", "sz399999"),      # 399 开头 → 深圳
    ("123456", "index", "sh123456"),      # 其余 → 上海
    ("510300", "etf", "sh510300"),        # 沪市 ETF
    ("512880", "etf", "sh512880"),
    ("560000", "etf", "sh560000"),
    ("159915", "etf", "sz159915"),        # 深市 ETF
    ("160000", "etf", "sz160000"),        # 深市 LOF
]


@pytest.mark.parametrize("code,stock_type,expected", BUILD_CODE_CASES)
def test_build_code(code, stock_type, expected):
    assert stock_api._build_code(code, stock_type) == expected


def test_build_code_default_type_is_stock():
    assert stock_api._build_code("600000") == "sh600000"


def test_build_code_strips_whitespace():
    assert stock_api._build_code("  600000  ") == "sh600000"


def test_build_code_unknown_type_falls_back_to_stock():
    assert stock_api._build_code("600000", "unknown") == "sh600000"


# ──────────────── _parse_sina_data ────────────────

class TestParseSinaData:
    def test_parses_all_fields(self):
        data = stock_api._parse_sina_data(_sina_text())
        assert data["name"] == "浦发银行"
        assert data["open"] == 18.88
        assert data["yesterday_close"] == 18.80
        assert data["price"] == 19.02
        assert data["high"] == 19.10
        assert data["low"] == 18.80
        assert data["date"] == "2026-07-30"
        assert data["time"] == "09:30:15"

    def test_change_percent_rounds_to_two_decimals(self):
        # (19.02 - 18.80) / 18.80 * 100 = 1.1702...
        data = stock_api._parse_sina_data(_sina_text())
        assert data["change_percent"] == 1.17

    def test_change_percent_negative(self):
        data = stock_api._parse_sina_data(_sina_text({3: "17.86"}))
        # (17.86 - 18.80) / 18.80 * 100 = -5.0
        assert data["change_percent"] == -5.0

    def test_change_percent_zero_when_yesterday_close_missing(self):
        data = stock_api._parse_sina_data(_sina_text({2: ""}))
        assert data["yesterday_close"] == 0.0
        assert data["change_percent"] == 0.0

    def test_empty_numeric_fields_default_to_zero(self):
        data = stock_api._parse_sina_data(_sina_text({1: "", 3: "", 4: "", 5: ""}))
        assert data["open"] == 0.0
        assert data["price"] == 0.0
        assert data["high"] == 0.0
        assert data["low"] == 0.0

    def test_raw_fields_preserved_for_debugging(self):
        data = stock_api._parse_sina_data(_sina_text())
        assert isinstance(data["code"], list)
        assert len(data["code"]) == 33
        assert data["code"][0] == "浦发银行"

    def test_missing_quoted_content_raises(self):
        with pytest.raises(ValueError, match="未找到数据内容"):
            stock_api._parse_sina_data("hq_str_sh600000=;")

    def test_empty_quoted_content_raises(self):
        with pytest.raises(ValueError, match="未找到数据内容"):
            stock_api._parse_sina_data('var hq_str_sh600000="";')

    def test_too_few_fields_raises(self):
        with pytest.raises(ValueError, match="字段不足"):
            stock_api._parse_sina_data('var hq_str_sh600000="浦发银行,18.88";')

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="股票代码无效或不存在"):
            stock_api._parse_sina_data(_sina_text({0: ""}))

    def test_invalid_numeric_field_raises(self):
        with pytest.raises(ValueError, match="数据解析失败"):
            stock_api._parse_sina_data(_sina_text({1: "abc"}))


# ──────────────── _aggregate_klines ────────────────

class TestAggregateKlines:
    def test_empty_input_returns_empty(self):
        assert stock_api._aggregate_klines([], "weekly") == []

    def test_daily_returns_input_unchanged(self):
        klines = _parsed_klines()
        assert stock_api._aggregate_klines(klines, "daily") == klines

    def test_weekly_groups_by_calendar_week(self):
        result = stock_api._aggregate_klines(_parsed_klines(), "weekly")
        assert len(result) == 2
        assert [k["date"] for k in result] == ["2026-01-07", "2026-01-13"]

    def test_weekly_first_group_ohlcv(self):
        group = stock_api._aggregate_klines(_parsed_klines(), "weekly")[0]
        assert group == {
            "date": "2026-01-07",    # 该周最后交易日
            "open": 10.0,            # 该周第一个交易日开盘价
            "close": 10.9,           # 该周最后一个交易日收盘价
            "high": 11.2,            # 周内最高
            "low": 9.8,              # 周内最低
            "volume": 600,           # 周内成交量之和
        }

    def test_weekly_second_group_ohlcv(self):
        group = stock_api._aggregate_klines(_parsed_klines(), "weekly")[1]
        assert group == {
            "date": "2026-01-13",
            "open": 11.0,
            "close": 11.3,
            "high": 11.7,
            "low": 10.9,             # min(10.9, 11.1)
            "volume": 900,
        }

    def test_monthly_merges_whole_month(self):
        result = stock_api._aggregate_klines(_parsed_klines(), "monthly")
        assert result == [{
            "date": "2026-01-13",
            "open": 10.0,
            "close": 11.3,
            "high": 11.7,
            "low": 9.8,
            "volume": 1500,
        }]

    def test_invalid_dates_are_skipped(self):
        klines = _parsed_klines()
        klines.insert(2, {"date": "bad-date", "open": 1.0, "close": 1.0,
                          "high": 1.0, "low": 1.0, "volume": 1})
        result = stock_api._aggregate_klines(klines, "weekly")
        assert len(result) == 2
        assert result[0]["volume"] == 600   # 非法日期未计入

    def test_all_dates_invalid_returns_empty(self):
        klines = [{"date": "x", "open": 1.0, "close": 1.0,
                   "high": 1.0, "low": 1.0, "volume": 1}]
        assert stock_api._aggregate_klines(klines, "weekly") == []


# ──────────────── query_stock ────────────────

class TestQueryStock:
    def test_success_populates_all_fields(self):
        with mock.patch.object(stock_api.requests, "get",
                               return_value=_FakeResponse(text=_sina_text())):
            result = stock_api.query_stock("600000")

        assert result["error"] is None
        assert result["name"] == "浦发银行"
        assert result["price"] == 19.02
        assert result["open"] == 18.88
        assert result["yesterday_close"] == 18.80
        assert result["high"] == 19.10
        assert result["low"] == 18.80
        assert result["change_percent"] == 1.17
        assert result["date"] == "2026-07-30"
        assert result["time"] == "09:30:15"

    def test_sends_headers_and_timeout(self):
        with mock.patch.object(stock_api.requests, "get",
                               return_value=_FakeResponse(text=_sina_text())) as get:
            stock_api.query_stock("600000")

        get.assert_called_once()
        assert get.call_args.kwargs["timeout"] == TIMEOUT
        assert "User-Agent" in get.call_args.kwargs["headers"]

    def test_decodes_response_as_gbk(self):
        response = _FakeResponse(text=_sina_text())
        with mock.patch.object(stock_api.requests, "get", return_value=response):
            stock_api.query_stock("600000")
        assert response.encoding == "gbk"

    @pytest.mark.parametrize("code,stock_type,full_code", [
        ("600000", "stock", "sh600000"),
        ("000001", "stock", "sz000001"),
        ("000001", "index", "sh000001"),   # 与上一行同代码、不同类型
        ("399001", "index", "sz399001"),
        ("510300", "etf", "sh510300"),
        ("159915", "etf", "sz159915"),
    ])
    def test_builds_expected_request_url(self, code, stock_type, full_code):
        with mock.patch.object(stock_api.requests, "get",
                               return_value=_FakeResponse(text=_sina_text())) as get:
            stock_api.query_stock(code, stock_type)
        assert get.call_args.args[0] == f"{SINA_BASE}{full_code}"

    def test_default_type_is_stock(self):
        with mock.patch.object(stock_api.requests, "get",
                               return_value=_FakeResponse(text=_sina_text())) as get:
            stock_api.query_stock("000001")
        assert get.call_args.args[0] == f"{SINA_BASE}sz000001"

    @pytest.mark.parametrize("exc,prefix", [
        (requests.exceptions.Timeout(), "请求超时"),
        (requests.exceptions.ConnectionError(), "网络连接失败"),
        (requests.exceptions.HTTPError("500"), "网络请求异常"),
        (RuntimeError("boom"), "未知错误"),
    ])
    def test_error_branches_return_default_result(self, exc, prefix):
        with mock.patch.object(stock_api.requests, "get", side_effect=exc):
            result = stock_api.query_stock("600000")

        assert result["error"].startswith(prefix)
        # 出错时其余字段保持默认值
        assert result["name"] == "--"
        assert result["price"] == 0.0
        assert result["change_percent"] == 0.0
        assert result["time"] == "--:--:--"

    def test_unparsable_body_reports_parse_error(self):
        with mock.patch.object(stock_api.requests, "get",
                               return_value=_FakeResponse(text="no quotes here")):
            result = stock_api.query_stock("600000")

        assert result["error"].startswith("数据解析错误")
        assert "无法解析API返回数据" in result["error"]

    def test_invalid_code_reports_parse_error(self):
        with mock.patch.object(stock_api.requests, "get",
                               return_value=_FakeResponse(text=_sina_text({0: ""}))):
            result = stock_api.query_stock("600000")

        assert result["error"] == "数据解析错误：股票代码无效或不存在"

    def test_never_raises(self):
        """任何异常都应被捕获并写入 error 字段，不向调用方抛出。"""
        with mock.patch.object(stock_api.requests, "get",
                               side_effect=Exception("unexpected")):
            result = stock_api.query_stock("600000")
        assert result["error"] == "未知错误：unexpected"


# ──────────────── query_kline_data ────────────────

class TestQueryKlineData:
    def test_daily_klines_parsed_and_typed(self):
        payload = _daily_klines()
        with mock.patch.object(stock_api.requests, "get",
                               return_value=_FakeResponse(payload=payload)):
            result = stock_api.query_kline_data("600000")

        assert result["error"] is None
        assert len(result["klines"]) == 5
        first = result["klines"][0]
        assert first == {
            "date": "2026-01-05", "open": 10.0, "close": 10.5,
            "high": 10.8, "low": 9.9, "volume": 100,
        }
        assert isinstance(first["open"], float)
        assert isinstance(first["volume"], int)

    def test_klines_sorted_ascending_by_date(self):
        payload = list(reversed(_daily_klines()))
        with mock.patch.object(stock_api.requests, "get",
                               return_value=_FakeResponse(payload=payload)):
            result = stock_api.query_kline_data("600000")

        dates = [k["date"] for k in result["klines"]]
        assert dates == sorted(dates)
        assert dates[0] == "2026-01-05"

    def test_weekly_period_aggregates_daily_data(self):
        with mock.patch.object(stock_api.requests, "get",
                               return_value=_FakeResponse(payload=_daily_klines())):
            result = stock_api.query_kline_data("600000", period="weekly")

        assert result["error"] is None
        assert [k["date"] for k in result["klines"]] == ["2026-01-07", "2026-01-13"]

    @pytest.mark.parametrize("period,datalen", [
        ("daily", 120),
        ("weekly", 400),
        ("monthly", 800),
    ])
    def test_request_url_uses_period_specific_datalen(self, period, datalen):
        with mock.patch.object(stock_api.requests, "get",
                               return_value=_FakeResponse(payload=_daily_klines())) as get:
            stock_api.query_kline_data("600000", period=period)

        url = get.call_args.args[0]
        assert "symbol=sh600000" in url
        assert "scale=240" in url
        assert f"datalen={datalen}" in url

    def test_unknown_period_falls_back_to_daily_fetch_count(self):
        with mock.patch.object(stock_api.requests, "get",
                               return_value=_FakeResponse(payload=_daily_klines())) as get:
            result = stock_api.query_kline_data("600000", period="yearly")

        assert "datalen=120" in get.call_args.args[0]
        assert result["error"] is None

    def test_index_code_uses_index_prefix(self):
        with mock.patch.object(stock_api.requests, "get",
                               return_value=_FakeResponse(payload=_daily_klines())) as get:
            stock_api.query_kline_data("000001", stock_type="index")
        assert "symbol=sh000001" in get.call_args.args[0]

    def test_empty_payload_reports_error(self):
        with mock.patch.object(stock_api.requests, "get",
                               return_value=_FakeResponse(payload=[])):
            result = stock_api.query_kline_data("600000")

        assert result["error"] == "数据解析错误：API 返回数据为空"
        assert result["klines"] == []

    def test_non_list_payload_reports_error(self):
        with mock.patch.object(stock_api.requests, "get",
                               return_value=_FakeResponse(payload={"day": "2026-01-05"})):
            result = stock_api.query_kline_data("600000")
        assert result["error"] == "数据解析错误：API 返回数据为空"

    def test_non_dict_items_skipped(self):
        payload = ["garbage"] + _daily_klines()
        with mock.patch.object(stock_api.requests, "get",
                               return_value=_FakeResponse(payload=payload)):
            result = stock_api.query_kline_data("600000")

        assert result["error"] is None
        assert len(result["klines"]) == 5

    def test_invalid_numbers_skip_item(self):
        payload = [{"day": "2026-01-05", "open": "abc", "close": "1",
                    "high": "1", "low": "1", "volume": "1"}]
        with mock.patch.object(stock_api.requests, "get",
                               return_value=_FakeResponse(payload=payload)):
            result = stock_api.query_kline_data("600000")
        assert result["error"] == "数据解析错误：K线数据解析失败"

    @pytest.mark.parametrize("exc,prefix", [
        (requests.exceptions.Timeout(), "请求超时"),
        (requests.exceptions.ConnectionError(), "网络连接失败"),
        (RuntimeError("boom"), "未知错误"),
    ])
    def test_error_branches(self, exc, prefix):
        with mock.patch.object(stock_api.requests, "get", side_effect=exc):
            result = stock_api.query_kline_data("600000")

        assert result["error"].startswith(prefix)
        assert result["klines"] == []
