"""
共享配置模块
============
集中管理网络请求的通用配置，避免在多个文件中重复定义。
"""

# ──────────────── 请求头 ────────────────
HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# ──────────────── 超时 ────────────────
TIMEOUT = 10

# ──────────────── API 地址 ────────────────
SINA_BASE = "https://hq.sinajs.cn/list="
SUGGEST_URL = "https://suggest.sinajs.cn/suggest/key="
