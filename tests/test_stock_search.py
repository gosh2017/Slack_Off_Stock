"""
stock_search 模块单元测试
=========================
覆盖新浪建议接口返回串的解析与过滤逻辑，以及网络异常分支。
所有网络请求均被 mock，测试不依赖真实网络。
"""

from unittest import mock

import pytest
import requests

from stock_monitor import stock_search
from stock_monitor.config import TIMEOUT


# ──────────────── 测试辅助 ────────────────

class _FakeResponse:
    """模拟 requests.Response，仅实现被测代码使用到的接口。"""

    def __init__(self, text=""):
        self.text = text
        self.encoding = None

    def raise_for_status(self):
        pass


def _record(full_code="sh600000", rec_type="11", code="600000", name="浦发银行"):
    """构造一条建议接口记录（分号分隔字段，11 表示 A 股）。"""
    return f"{full_code},{rec_type},{code},{full_code},{name},,{name},99,1,,,"


def _suggest_text(*records):
    """构造完整的建议接口返回串。"""
    return 'var suggestvalue="%s";' % ";".join(records)


# ──────────────── 解析与过滤 ────────────────

def test_parses_a_share_record():
    with mock.patch.object(stock_search.requests, "get",
                           return_value=_FakeResponse(_suggest_text(_record()))):
        results = stock_search.search_stocks("浦发")

    assert results == [{
        "full_code": "sh600000",
        "code": "600000",
        "name": "浦发银行",
        "market": "sh",
        "type_label": "股票",
    }]


def test_parses_multiple_records_in_order():
    text = _suggest_text(
        _record("sh600000", "11", "600000", "浦发银行"),
        _record("sz000001", "11", "000001", "平安银行"),
    )
    with mock.patch.object(stock_search.requests, "get",
                           return_value=_FakeResponse(text)):
        results = stock_search.search_stocks("银")

    assert [r["code"] for r in results] == ["600000", "000001"]
    assert [r["market"] for r in results] == ["sh", "sz"]


def test_filters_out_non_a_share_records():
    text = _suggest_text(
        _record("sh600000", "11", "600000", "浦发银行"),
        _record("sh000001", "12", "000001", "上证指数"),   # 非 11 → 过滤
    )
    with mock.patch.object(stock_search.requests, "get",
                           return_value=_FakeResponse(text)):
        results = stock_search.search_stocks("浦发")

    assert [r["code"] for r in results] == ["600000"]


def test_respects_limit():
    text = _suggest_text(*[
        _record(f"sh60000{i}", "11", f"60000{i}", f"股票{i}") for i in range(5)
    ])
    with mock.patch.object(stock_search.requests, "get",
                           return_value=_FakeResponse(text)):
        results = stock_search.search_stocks("股票", limit=2)

    assert len(results) == 2


def test_malformed_records_are_skipped():
    text = _suggest_text(
        "sh600000,11",                              # 字段不足 → 跳过
        "",                                         # 空行 → 跳过
        _record("sz000001", "11", "000001", "平安银行"),
    )
    with mock.patch.object(stock_search.requests, "get",
                           return_value=_FakeResponse(text)):
        results = stock_search.search_stocks("银")

    assert [r["code"] for r in results] == ["000001"]


def test_empty_suggestvalue_returns_empty_list():
    with mock.patch.object(stock_search.requests, "get",
                           return_value=_FakeResponse(_suggest_text())):
        assert stock_search.search_stocks("不存在") == []


def test_response_without_suggestvalue_returns_empty_list():
    with mock.patch.object(stock_search.requests, "get",
                           return_value=_FakeResponse("nothing useful here")):
        assert stock_search.search_stocks("浦发") == []


# ──────────────── 请求参数与异常分支 ────────────────

def test_passes_keyword_headers_and_timeout():
    with mock.patch.object(stock_search.requests, "get",
                           return_value=_FakeResponse(_suggest_text())) as get:
        stock_search.search_stocks("浦发")

    get.assert_called_once()
    assert get.call_args.kwargs["params"] == {"key": "浦发"}
    assert get.call_args.kwargs["timeout"] == TIMEOUT
    assert "User-Agent" in get.call_args.kwargs["headers"]


def test_decodes_response_as_gbk():
    response = _FakeResponse(_suggest_text())
    with mock.patch.object(stock_search.requests, "get", return_value=response):
        stock_search.search_stocks("浦发")
    assert response.encoding == "gbk"


@pytest.mark.parametrize("exc", [
    requests.exceptions.Timeout(),
    requests.exceptions.ConnectionError(),
    requests.exceptions.HTTPError("500"),
])
def test_network_errors_return_empty_list(exc):
    with mock.patch.object(stock_search.requests, "get", side_effect=exc):
        assert stock_search.search_stocks("浦发") == []


def test_unexpected_errors_return_empty_list():
    """搜索失败不应影响主界面，任何异常都吞掉并返回空列表。"""
    with mock.patch.object(stock_search.requests, "get",
                           side_effect=RuntimeError("boom")):
        assert stock_search.search_stocks("浦发") == []
