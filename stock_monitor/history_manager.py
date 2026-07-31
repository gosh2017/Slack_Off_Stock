"""
查询历史管理模块
================
记录查询过的股票（含指数 / ETF），以 JSON 配置文件形式持久化到本地。

配置文件路径：程序目录 / config / query_history.json
"""

import json
import os
from datetime import datetime
from pathlib import Path

# ──────────────── 常量 ────────────────

# 配置文件相对路径
_CONFIG_DIR = "config"
_HISTORY_FILE = os.path.join(_CONFIG_DIR, "query_history.json")

# 单条记录最大数量（防止无限膨胀）
MAX_HISTORY = 50

# 类型中英文映射
_TYPE_LABEL = {
    "stock": "股票",
    "index": "指数",
    "etf": "ETF",
}


def _history_path() -> str:
    """返回查询历史配置文件绝对路径"""
    return str(Path(__file__).resolve().parent.parent / _HISTORY_FILE)


def load_history() -> list[dict]:
    """
    加载查询历史记录。

    返回：
        记录列表（按时间倒序，最新在前）。
        每项格式：
            {
                "code": "600000",
                "type": "stock",
                "name": "浦发银行",
                "price": 19.02,
                "change_percent": 1.23,
                "time": "2026-07-30 09:30:15"
            }
    """
    path = _history_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except (json.JSONDecodeError, IOError):
        return []
    return []


def save_history(history: list[dict]) -> None:
    """
    保存查询历史到本地 JSON 文件。

    参数：
        history: 记录列表
    """
    path = _history_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def save_query(code: str, stock_type: str, data: dict) -> list[dict]:
    """
    保存一次查询到历史记录。

    新增记录时自动去重（同一 code + type 只保留最新一条）。

    参数：
        code:      股票代码
        stock_type: 类型（stock / index / etf）
        data:      query_stock() 返回的数据字典

    返回：
        更新后的完整历史记录列表
    """
    history = load_history()

    # 构建新记录
    new_record = {
        "code": code,
        "type": stock_type,
        "name": data.get("name", "--"),
        "price": data.get("price", 0.0),
        "change_percent": data.get("change_percent", 0.0),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # 去重：同一 code + type 只保留最新
    deduped = [
        r for r in history
        if not (r["code"] == code and r["type"] == stock_type)
    ]

    # 新记录插在最前面
    deduped.insert(0, new_record)

    # 限制最大条数
    deduped = deduped[:MAX_HISTORY]

    save_history(deduped)
    return deduped


def clear_history() -> None:
    """清空查询历史"""
    path = _history_path()
    if os.path.exists(path):
        os.remove(path)
