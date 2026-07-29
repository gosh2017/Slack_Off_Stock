"""
股票数据获取模块
================
封装调用新浪财经免费 API，解析 A 股行情数据。
不依赖任何 GUI 组件，可独立测试。
"""

import requests
import re

# 新浪财经接口 URL（无需 API Key）
SINA_API_URL = "https://hq.sinajs.cn/list="

# 请求头，模拟浏览器访问
HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# 请求超时时间（秒）
TIMEOUT = 10

# ──────────────── 已知指数代码映射（新浪接口所需前缀）───────────────
# 部分指数不按普通前缀规则，需单独指定。
INDEX_CODES = {
    "000001": "sh",   # 上证指数
    "000300": "sh",   # 沪深300
    "000688": "sh",   # 科创50
    "000905": "sh",   # 中证500
    "000922": "sh",   # 中证1000
    "399001": "sz",   # 深证成指
    "399005": "sz",   # 中小板指
    "399006": "sz",   # 创业板指
}


def _is_etf_code(code: str) -> bool:
    """
    判断是否为场内 ETF / LOF 代码。

    ETF 代码特征：
      - 51xxxx、56xxxx → 上海 ETF（如 510300 沪深300ETF）
      - 159xxx、160xxx → 深圳 ETF / LOF
    """
    if code.startswith(("51", "56")):
        return True
    if code.startswith(("159", "160")):
        return True
    return False


def _build_code(stock_code: str, stock_type: str = "stock") -> str:
    """
    根据代码和类型，返回新浪接口所需的完整代码（带交易所前缀）。

    支持的 stock_type：
      - "stock" — 股票（默认）
      - "index" — 指数
      - "etf"   — 场内 ETF / LOF

    股票前缀规则：
      - 以 6 开头 → 上海（sh）
      - 以 0、3 开头 → 深圳（sz）
      - 以 4 开头 → 北京（bj）

    ETF 前缀规则：
      - 51xxxx、56xxxx → 上海（sh）
      - 159xxx、160xxx → 深圳（sz）

    指数前缀规则：
      - 优先查 INDEX_CODES 映射表
      - 以 399 开头 → 深圳（sz）
      - 其余 → 上海（sh）

    参数：
        stock_code: 纯数字代码
        stock_type: 查询类型

    返回：
        带交易所前缀的代码，如 "sh600000"
    """
    code = stock_code.strip()

    if stock_type == "index":
        if code in INDEX_CODES:
            return f"{INDEX_CODES[code]}{code}"
        if code.startswith("399"):
            return f"sz{code}"
        return f"sh{code}"

    if stock_type == "etf":
        if code.startswith(("51", "56")):
            return f"sh{code}"
        else:
            return f"sz{code}"

    # 默认按股票处理
    if code.startswith("6"):
        return f"sh{code}"
    elif code.startswith(("0", "3")):
        return f"sz{code}"
    elif code.startswith("4"):
        return f"bj{code}"
    else:
        return f"sh{code}"


def _parse_sina_data(data_str: str) -> dict:
    """
    解析新浪财经返回的 CSV 格式数据。

    返回格式示例：
        var hq_str_sh600000="浦发银行,18.88,18.80,19.02,19.10,18.80,...";

    字段说明（前 10 个重要字段）：
        0: 股票名称
        1: 今开价格
        2: 昨日收盘价
        3: 当前价格（现价）
        4: 今日最高价
        5: 今日最低价
        6: 买入价
        7: 卖出价
        8: 成交量（手）
        9: 成交额（万）
        30: 日期
        31: 时间

    参数：
        data_str: 新浪 API 返回的原始字符串

    返回：
        结构化字典，包含股票行情数据
    """
    # 提取引号内的 CSV 数据部分
    match = re.search(r'"([^"]+)"', data_str)
    if not match:
        raise ValueError("无法解析API返回数据：未找到数据内容")

    fields = match.group(1).split(",")

    if len(fields) < 32:
        raise ValueError(f"API返回数据字段不足，实际字段数: {len(fields)}")

    name = fields[0]
    if not name:
        raise ValueError("股票代码无效或不存在")

    try:
        open_price = float(fields[1]) if fields[1] else 0.0
        yesterday_close = float(fields[2]) if fields[2] else 0.0
        current_price = float(fields[3]) if fields[3] else 0.0
        high_price = float(fields[4]) if fields[4] else 0.0
        low_price = float(fields[5]) if fields[5] else 0.0
        date_str = fields[30]
        time_str = fields[31]
    except (ValueError, IndexError) as e:
        raise ValueError(f"数据解析失败：{e}")

    # 计算涨跌幅
    if yesterday_close > 0:
        change_percent = (current_price - yesterday_close) / yesterday_close * 100
    else:
        change_percent = 0.0

    return {
        "name": name,
        "code": fields,  # 保留原始字段以便调试
        "open": open_price,
        "yesterday_close": yesterday_close,
        "price": current_price,
        "high": high_price,
        "low": low_price,
        "change_percent": round(change_percent, 2),
        "date": date_str,
        "time": time_str,
    }


def query_stock(stock_code: str, stock_type: str = "stock") -> dict:
    """
    查询单只 A 股股票 / 指数 / 场内 ETF 的实时行情数据。

    支持的代码类型：
        - 股票：如 "600000"（浦发银行）、"000001"（平安银行）
        - 指数：如 "000001"（上证指数）、"399001"（深证成指）
        - ETF：  如 "510300"（沪深300ETF）、"518880"（黄金ETF）

    参数：
        stock_code: 代码（纯数字）
        stock_type: 查询类型，"stock" / "index" / "etf"（默认 "stock"）

    返回：
        字典，包含以下键：
            - name:           名称（字符串）
            - price:          当前价格（浮点数）
            - change_percent: 涨跌幅（百分比，浮点数）
            - yesterday_close: 昨日收盘价（浮点数）
            - high:           今日最高价（浮点数）
            - low:            今日最低价（浮点数）
            - open:           今开价格（浮点数）
            - time:           数据时间（字符串）
            - error:          错误信息（成功时为 None）

    异常情况：
        网络错误、超时、解析失败等均捕获并返回 error 字段，
        不会抛出异常。
    """
    result = {
        "name": "--",
        "price": 0.0,
        "change_percent": 0.0,
        "yesterday_close": 0.0,
        "high": 0.0,
        "low": 0.0,
        "open": 0.0,
        "time": "--:--:--",
        "error": None,
    }

    try:
        full_code = _build_code(stock_code, stock_type)
        url = f"{SINA_API_URL}{full_code}"

        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        response.encoding = "gbk"  # 新浪返回 GBK 编码
        response.raise_for_status()

        data = _parse_sina_data(response.text)
        result.update(data)
        result["error"] = None

    except requests.exceptions.Timeout:
        result["error"] = f"请求超时（{TIMEOUT}秒），请检查网络连接"
    except requests.exceptions.ConnectionError:
        result["error"] = "网络连接失败，请检查网络"
    except requests.exceptions.RequestException as e:
        result["error"] = f"网络请求异常：{e}"
    except ValueError as e:
        result["error"] = f"数据解析错误：{e}"
    except Exception as e:
        result["error"] = f"未知错误：{e}"

    return result


# 测试入口（直接运行此文件时执行）
if __name__ == "__main__":
    import sys

    test_code = sys.argv[1] if len(sys.argv) > 1 else "600000"
    print(f"正在查询股票代码: {test_code} ...")
    data = query_stock(test_code)
    if data["error"]:
        print(f"❌ 查询失败: {data['error']}")
    else:
        print(f"✅ 查询成功")
        print(f"   股票名称: {data['name']}")
        print(f"   最新价:   {data['price']}")
        print(f"   涨跌幅:   {data['change_percent']:+.2f}%")
        print(f"   最高:     {data['high']}")
        print(f"   最低:     {data['low']}")
        print(f"   昨收:     {data['yesterday_close']}")
        print(f"   时间:     {data['time']}")