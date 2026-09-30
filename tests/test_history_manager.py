"""
history_manager 模块单元测试
============================
所有用例均把历史文件重定向到临时目录，不会读写项目真实的
config/query_history.json。
"""

import json
import re
from unittest import mock

import pytest

from stock_monitor import history_manager

# "YYYY-MM-DD HH:MM:SS"
TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

SAMPLE_DATA = {
    "name": "浦发银行",
    "price": 19.02,
    "change_percent": 1.17,
}


@pytest.fixture
def history_path(tmp_path):
    """将 _history_path() 重定向到临时文件，返回该文件路径。"""
    path = tmp_path / "config" / "query_history.json"
    with mock.patch.object(history_manager, "_history_path",
                           return_value=str(path)):
        yield path


# ──────────────── load_history ────────────────

def test_load_missing_file_returns_empty(history_path):
    assert not history_path.exists()
    assert history_manager.load_history() == []


def test_save_then_load_roundtrip(history_path):
    records = [{"code": "600000", "type": "stock", "name": "浦发银行",
                "price": 19.02, "change_percent": 1.17,
                "time": "2026-07-30 09:30:15"}]
    history_manager.save_history(records)
    assert history_manager.load_history() == records


def test_load_corrupt_json_returns_empty(history_path):
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text("{{{ not json", encoding="utf-8")
    assert history_manager.load_history() == []


def test_load_non_list_json_returns_empty(history_path):
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text('{"code": "600000"}', encoding="utf-8")
    assert history_manager.load_history() == []


# ──────────────── save_query ────────────────

def test_save_query_builds_record(history_path):
    history = history_manager.save_query("600000", "stock", SAMPLE_DATA)

    assert len(history) == 1
    record = history[0]
    assert record["code"] == "600000"
    assert record["type"] == "stock"
    assert record["name"] == "浦发银行"
    assert record["price"] == 19.02
    assert record["change_percent"] == 1.17
    assert TIME_PATTERN.match(record["time"])


def test_save_query_persists_to_disk(history_path):
    history_manager.save_query("600000", "stock", SAMPLE_DATA)
    assert history_manager.load_history() == history_manager.load_history()
    assert history_path.exists()


def test_save_query_missing_fields_use_defaults(history_path):
    history = history_manager.save_query("600000", "stock", {})
    assert history[0]["name"] == "--"
    assert history[0]["price"] == 0.0
    assert history[0]["change_percent"] == 0.0


def test_save_query_newest_record_first(history_path):
    history_manager.save_query("600000", "stock", SAMPLE_DATA)
    history = history_manager.save_query("000001", "stock", {"name": "平安银行"})
    assert [r["code"] for r in history] == ["000001", "600000"]


def test_save_query_dedupes_same_code_and_type(history_path):
    history_manager.save_query("600000", "stock", SAMPLE_DATA)
    history = history_manager.save_query(
        "600000", "stock", {"name": "浦发银行", "price": 20.00})

    assert len(history) == 1
    assert history[0]["price"] == 20.00      # 保留最新一次查询的数据
    assert len(history_manager.load_history()) == 1


def test_save_query_keeps_same_code_with_different_type(history_path):
    history_manager.save_query("000001", "stock", {"name": "平安银行"})
    history = history_manager.save_query("000001", "index", {"name": "上证指数"})

    assert len(history) == 2
    assert {(r["code"], r["type"]) for r in history} == {
        ("000001", "stock"), ("000001", "index")}


def test_save_query_truncates_to_max_history(history_path):
    total = history_manager.MAX_HISTORY + 10
    for i in range(total):
        history_manager.save_query(f"6000{i:02d}", "stock", SAMPLE_DATA)

    history = history_manager.load_history()
    assert len(history) == history_manager.MAX_HISTORY
    # 最新的记录仍在最前面
    assert history[0]["code"] == f"6000{total - 1:02d}"


def test_save_history_writes_utf8_without_escaping(history_path):
    history_manager.save_history([{"code": "600000", "type": "stock",
                                   "name": "浦发银行"}])
    raw = history_path.read_text(encoding="utf-8")
    assert "浦发银行" in raw            # ensure_ascii=False
    assert json.loads(raw)[0]["code"] == "600000"


# ──────────────── remove_history_record ────────────────

def test_remove_history_record(history_path):
    history_manager.save_query("600000", "stock", SAMPLE_DATA)
    history_manager.save_query("000001", "stock", {"name": "平安银行"})

    history = history_manager.remove_history_record("600000", "stock")
    assert [r["code"] for r in history] == ["000001"]
    assert history_manager.load_history() == history


def test_remove_history_record_ignores_other_type(history_path):
    history_manager.save_query("000001", "stock", {"name": "平安银行"})
    history_manager.save_query("000001", "index", {"name": "上证指数"})

    history = history_manager.remove_history_record("000001", "stock")
    assert len(history) == 1
    assert history[0]["type"] == "index"


def test_remove_history_record_not_found_keeps_history(history_path):
    history_manager.save_query("600000", "stock", SAMPLE_DATA)
    history = history_manager.remove_history_record("999999", "stock")
    assert len(history) == 1


def test_remove_history_record_on_empty_history(history_path):
    assert history_manager.remove_history_record("600000", "stock") == []


# ──────────────── clear_history ────────────────

def test_clear_history_removes_file(history_path):
    history_manager.save_query("600000", "stock", SAMPLE_DATA)
    assert history_path.exists()

    history_manager.clear_history()
    assert not history_path.exists()
    assert history_manager.load_history() == []


def test_clear_history_when_file_missing_is_noop(history_path):
    history_manager.clear_history()      # 不应抛出异常
    assert history_manager.load_history() == []
