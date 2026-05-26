"""HTML 한 파일 분석 — 플랫폼 추정 + 상품상세 컨테이너 후보 찾기.

usage: python -m scripts.scrape.inspect <html_path>
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup, Tag

# 플랫폼별 시그니처 (HTML 어디든 보이면 강한 증거)
PLATFORM_SIGNATURES: dict[str, list[str]] = {
    "cafe24": [
        r"xans-product-detail",
        r"prdDetail",
        r"xans-product",
        r"/cafe24",
        r"poxo\.com",
        r"\.cafe24\.com",
    ],
    "makeshop": [
        r"makeshop",
        r"/shopdetail\.html",
        r"branduid=",
        r"_mkc=",
        r"M_default",
        r"shop1\.makeshop\.co\.kr",
        r"index\.makeshop\.co\.kr",
    ],
    "godo": [
        r"godo",
        r"_godo",
        r"goods/goods_view",
        r"gd_basic_options",
    ],
    "naver_smartstore": [
        r"smartstore\.naver\.com",
        r"shopping\.naver",
    ],
    "imweb": [
        r"imweb",
        r"site\.imweb\.me",
    ],
    "shopify": [
        r"shopify",
        r"cdn\.shopify",
    ],
}

# 상품 상세 본문 컨테이너 후보 (id 또는 class 부분 매치). 위에 가까울수록 우선.
DETAIL_CANDIDATES_BY_PLATFORM: dict[str, list[str]] = {
    "cafe24": ["#prdDetail", ".xans-product-detail", "#productDetail"],
    "makeshop": ["#productDetail", ".prd_detail", "#detailpageWrap", ".product-detail", ".item-detail", "[id*=detail]"],
    "godo": [".goods_detail", ".detail_pic", "#contents .goods_detail_view"],
    "naver_smartstore": [".product_detail_area", "[class*=detail]"],
    "imweb": [".product_detail", ".product-detail-cont"],
}

# 무조건 본문이 아닐 영역 (블랙리스트)
BLACKLIST_PATTERNS = [
    r"footer", r"header", r"^nav$", r"aside",
    r"recommend", r"related", r"banner", r"advertis",
    r"review-list", r"qna", r"snb", r"gnb",
    r"cart", r"login", r"search",
]


def detect_platform(html: str) -> tuple[str, dict[str, int]]:
    scores: dict[str, int] = {}
    for plat, patterns in PLATFORM_SIGNATURES.items():
        s = sum(len(re.findall(p, html, re.I)) for p in patterns)
        if s > 0:
            scores[plat] = s
    if not scores:
        return ("unknown", {})
    top = max(scores, key=scores.get)
    return (top, scores)


def _node_label(el: Tag) -> str:
    parts = [el.name]
    if el.get("id"):
        parts.append(f"#{el.get('id')}")
    cls = el.get("class")
    if cls:
        parts.append("." + ".".join(cls[:3]))
    return "".join(parts)


def _is_blacklisted(el: Tag) -> bool:
    ident = " ".join(filter(None, [el.get("id", ""), *(el.get("class") or [])]))
    if not ident:
        return False
    return any(re.search(p, ident, re.I) for p in BLACKLIST_PATTERNS)


def find_detail_candidates(soup: BeautifulSoup) -> list[dict]:
    """이미지 밀집도 + 텍스트 길이로 본문 후보 div를 점수화."""
    cands: list[dict] = []
    for el in soup.find_all(["div", "section", "article"]):
        if _is_blacklisted(el):
            continue
        imgs = el.find_all("img", recursive=True)
        # 직계 자식들만 보면 너무 깊은 wrapper도 다 잡혀서, 노드 자체 텍스트 길이로도 거름
        text = el.get_text(" ", strip=True)
        if len(text) < 50 and len(imgs) < 2:
            continue
        cands.append({
            "label": _node_label(el),
            "img_count": len(imgs),
            "text_len": len(text),
            "score": len(imgs) * 200 + len(text),  # 이미지 1장 ≈ 200자 가중치
        })
    cands.sort(key=lambda c: -c["score"])
    return cands[:8]


def main(path: str) -> None:
    html = Path(path).read_bytes().decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")

    platform, scores = detect_platform(html)

    # 플랫폼별 사전 정의 셀렉터가 잡히는지
    matched_selectors: list[dict] = []
    for sel in DETAIL_CANDIDATES_BY_PLATFORM.get(platform, []):
        try:
            el = soup.select_one(sel)
        except Exception:
            el = None
        if el:
            matched_selectors.append({
                "selector": sel,
                "label": _node_label(el),
                "img_count": len(el.find_all("img", recursive=True)),
                "text_len": len(el.get_text(" ", strip=True)),
            })

    cands = find_detail_candidates(soup)

    out = {
        "file": path,
        "platform_guess": platform,
        "platform_scores": scores,
        "title": soup.title.text.strip() if soup.title else "",
        "meta_charset": soup.find("meta", charset=True).get("charset", "") if soup.find("meta", charset=True) else "",
        "matched_known_selectors": matched_selectors,
        "heuristic_top_candidates": cands,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "scripts/scrape/samples/pippin.html")
