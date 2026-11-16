import httpx
from bs4 import BeautifulSoup
from loguru import logger

_CLIENT = httpx.Client(
    headers={
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10; SM-G981B) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Mobile Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://m.baidu.com/",
    },
    timeout=8.0,
    follow_redirects=True,
)


def web_search(query: str, max_results: int = 5) -> str:
    """通过百度移动搜索获取实时网页摘要，直连免代理，适配国内网络环境。"""
    if not query or not query.strip():
        return "搜索词为空"

    logger.info(f"🌐 触发联网搜索: {query}")

    for attempt in range(2):
        try:
            resp = _CLIENT.get("https://m.baidu.com/s", params={"word": query.strip()})
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            results = []
            seen = set()
            for card in soup.select("div.c-result, div.c-container"):
                if card.get("data-tpl") in ("recommend_list", "adv_wenku"):
                    continue
                text = " ".join(card.get_text(" ", strip=True).split())
                if not text or len(text) < 20 or "大家还在搜" in text:
                    continue
                snippet = text[:260]
                if snippet in seen:
                    continue
                seen.add(snippet)
                results.append(f"- {snippet}")
                if len(results) >= max_results:
                    break

            if results:
                joined = "\n".join(results)
                logger.success(f"🌐 联网搜索成功 ({len(results)} 条摘要)")
                return joined

            logger.warning(f"联网搜索第 {attempt+1} 次无有效结果: {query}")
        except Exception as e:
            logger.warning(f"联网搜索第 {attempt+1} 次失败: {e}")

    return "未检索到相关有效信息"
