"""
监控列表持久化模块
==================
存储用户添加到监控的股票列表，重启后保留。
"""

import json
import os

WATCHLIST_PATH = os.path.join(
    os.path.dirname(__file__), "..", "config", "watchlist.json"
)


def _load() -> list:
    """加载监控列表（文件不存在或损坏时返回空列表）"""
    if not os.path.exists(WATCHLIST_PATH):
        return []
    try:
        with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, IOError):
        return []


def save_watchlist(items: list):
    """保存监控列表到文件"""
    os.makedirs(os.path.dirname(WATCHLIST_PATH), exist_ok=True)
    with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def load_watchlist() -> list:
    """返回当前监控列表"""
    return _load()


def add_to_watchlist(code: str, stock_type: str, name: str = "",
                     price: float = 0.0) -> bool:
    """
    添加股票到监控列表。

    参数:
        code: 股票代码
        stock_type: 类型（stock / index / etf）
        name: 名称（可选，未提供时下次刷新时补全）
        price: 当前价格（可选，仅用于显示，下次刷新会覆盖）

    返回:
        True 表示成功添加，False 表示已存在（跳过）
    """
    items = _load()
    # 去重：同一 (code, type) 只保留一份
    for item in items:
        if item.get("code") == code and item.get("type") == stock_type:
            return False
    items.append({
        "code": code,
        "type": stock_type,
        "name": name or code,
        "price": price,
    })
    save_watchlist(items)
    return True


def remove_from_watchlist(code: str, stock_type: str) -> bool:
    """
    从监控列表移除一只股票。

    返回:
        True 表示找到并移除，False 表示不存在
    """
    items = _load()
    new_items = [
        item for item in items
        if not (item.get("code") == code and item.get("type") == stock_type)
    ]
    if len(new_items) == len(items):
        return False
    save_watchlist(new_items)
    return True


def is_in_watchlist(code: str, stock_type: str) -> bool:
    """检查某只股票是否已在监控列表中"""
    return any(
        item.get("code") == code and item.get("type") == stock_type
        for item in _load()
    )
