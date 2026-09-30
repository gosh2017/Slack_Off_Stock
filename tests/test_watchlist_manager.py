"""
watchlist_manager 模块单元测试
==============================
所有用例均把监控列表文件重定向到临时目录，不会读写项目真实的
config/watchlist.json。
"""

import json
from unittest import mock

import pytest

from stock_monitor import watchlist_manager


@pytest.fixture
def watchlist_path(tmp_path):
    """将 WATCHLIST_PATH 指向临时文件，返回该文件路径。"""
    path = tmp_path / "config" / "watchlist.json"
    with mock.patch.object(watchlist_manager, "WATCHLIST_PATH", str(path)):
        yield path


# ──────────────── load_watchlist ────────────────

def test_load_missing_file_returns_empty(watchlist_path):
    assert not watchlist_path.exists()
    assert watchlist_manager.load_watchlist() == []


def test_load_corrupt_json_returns_empty(watchlist_path):
    watchlist_path.parent.mkdir(parents=True, exist_ok=True)
    watchlist_path.write_text("{{{ not json", encoding="utf-8")
    assert watchlist_manager.load_watchlist() == []


def test_load_non_list_json_returns_empty(watchlist_path):
    watchlist_path.parent.mkdir(parents=True, exist_ok=True)
    watchlist_path.write_text('{"code": "600000"}', encoding="utf-8")
    assert watchlist_manager.load_watchlist() == []


def test_save_watchlist_writes_utf8(watchlist_path):
    watchlist_manager.save_watchlist(
        [{"code": "600000", "type": "stock", "name": "浦发银行", "price": 19.02}])

    raw = watchlist_path.read_text(encoding="utf-8")
    assert "浦发银行" in raw
    assert json.loads(raw)[0]["code"] == "600000"


def test_save_watchlist_creates_missing_directory(watchlist_path):
    assert not watchlist_path.parent.exists()
    watchlist_manager.save_watchlist([])
    assert watchlist_path.parent.exists()


# ──────────────── add_to_watchlist ────────────────

def test_add_returns_true_and_persists(watchlist_path):
    assert watchlist_manager.add_to_watchlist("600000", "stock", "浦发银行", 19.02) is True
    assert watchlist_manager.load_watchlist() == [
        {"code": "600000", "type": "stock", "name": "浦发银行", "price": 19.02}]


def test_add_without_name_falls_back_to_code(watchlist_path):
    watchlist_manager.add_to_watchlist("600000", "stock")
    item = watchlist_manager.load_watchlist()[0]
    assert item["name"] == "600000"
    assert item["price"] == 0.0


def test_add_empty_name_falls_back_to_code(watchlist_path):
    watchlist_manager.add_to_watchlist("600000", "stock", "")
    assert watchlist_manager.load_watchlist()[0]["name"] == "600000"


def test_add_duplicate_returns_false(watchlist_path):
    assert watchlist_manager.add_to_watchlist("600000", "stock") is True
    assert watchlist_manager.add_to_watchlist("600000", "stock", "浦发银行") is False
    assert len(watchlist_manager.load_watchlist()) == 1


def test_add_same_code_different_type_is_allowed(watchlist_path):
    assert watchlist_manager.add_to_watchlist("000001", "stock") is True
    assert watchlist_manager.add_to_watchlist("000001", "index") is True
    assert len(watchlist_manager.load_watchlist()) == 2


def test_add_preserves_insertion_order(watchlist_path):
    watchlist_manager.add_to_watchlist("600000", "stock")
    watchlist_manager.add_to_watchlist("000001", "stock")
    watchlist_manager.add_to_watchlist("399001", "index")
    assert [i["code"] for i in watchlist_manager.load_watchlist()] == [
        "600000", "000001", "399001"]


# ──────────────── remove_from_watchlist ────────────────

def test_remove_existing_returns_true(watchlist_path):
    watchlist_manager.add_to_watchlist("600000", "stock")
    watchlist_manager.add_to_watchlist("000001", "stock")

    assert watchlist_manager.remove_from_watchlist("600000", "stock") is True
    assert [i["code"] for i in watchlist_manager.load_watchlist()] == ["000001"]


def test_remove_missing_returns_false(watchlist_path):
    assert watchlist_manager.remove_from_watchlist("600000", "stock") is False


def test_remove_wrong_type_returns_false(watchlist_path):
    watchlist_manager.add_to_watchlist("600000", "stock")
    assert watchlist_manager.remove_from_watchlist("600000", "index") is False
    assert len(watchlist_manager.load_watchlist()) == 1


# ──────────────── is_in_watchlist ────────────────

def test_is_in_watchlist_matches_code_and_type(watchlist_path):
    watchlist_manager.add_to_watchlist("600000", "stock")

    assert watchlist_manager.is_in_watchlist("600000", "stock") is True
    assert watchlist_manager.is_in_watchlist("600000", "index") is False
    assert watchlist_manager.is_in_watchlist("000001", "stock") is False


def test_is_in_watchlist_on_empty_list(watchlist_path):
    assert watchlist_manager.is_in_watchlist("600000", "stock") is False
