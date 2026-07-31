"""
股票名称搜索模块
================
通过新浪财经建议接口，根据股票名称/关键词搜索匹配的股票代码。

API: https://suggest.sinajs.cn/suggest/key=<关键词>
返回格式（GBK 编码，分号分隔）：
    sh600000,11,600000,sh600000,浦发银行,,浦发银行,99,1,,,...
    ┌─字段─────────┬─类型─┬─代码─┬─完整代码─┬─名称─┬─...
"""

import requests
import re

from stock_monitor.config import HEADERS, TIMEOUT, SUGGEST_URL

# 类型代码 → 类型标签（用于过滤，仅返回 A 股）
A_SHARE_TYPES = {"11"}  # 11 = A 股


def search_stocks(keyword: str, limit: int = 10) -> list[dict]:
    """
    根据关键词搜索股票。

    参数：
        keyword: 股票名称或关键词，如 "浦发"、"平安"
        limit:   最多返回结果数

    返回：
        股票列表，每项为字典：
            {
                "full_code": "sh600000",   # 完整代码（含交易所前缀）
                "code":      "600000",     # 纯数字代码
                "name":      "浦发银行",   # 股票名称
                "market":    "sh",         # 交易所
                "type_label": "股票"       # 类型标签
            }

    异常：
        网络错误、解析失败等均捕获，返回空列表。
    """
    results = []
    try:
        response = requests.get(
            SUGGEST_URL,
            params={"key": keyword},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        response.encoding = "gbk"
        response.raise_for_status()

        text = response.text
        # 提取 suggestvalue="..." 内的内容
        match = re.search(r'suggestvalue="(.*)"', text, re.DOTALL)
        if not match:
            return results

        # 按分号分割每条记录
        raw = match.group(1)
        for line in raw.split(";"):
            if not line.strip():
                continue
            fields = [f.strip() for f in line.split(",")]
            if len(fields) < 5:
                continue

            full_code = fields[0]
            rec_type = fields[1]

            # 仅保留 A 股
            if rec_type not in A_SHARE_TYPES:
                continue

            code = fields[2]
            name = fields[4]
            market = fields[0][:2]

            results.append({
                "full_code": full_code,
                "code": code,
                "name": name,
                "market": market,
                "type_label": "股票",
            })

            if len(results) >= limit:
                break

    except requests.exceptions.RequestException:
        pass
    except Exception:
        pass

    return results
