"""Andar 상품 상세 페이지에서 텍스트/이미지 자산을 분리해 뽑아본다.

쇼핑몰 상세 페이지의 전형적인 구조 확인용:
- 상품명/가격/메타: <head> + 상단 .xans-product-detail 류 (HTML 텍스트로 존재)
- 상품 상세 본문: 보통 #prdDetail 안에 <img> 잔뜩 (이미지로 박힌 본문 — OCR 대상)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

HTML_PATH = Path(__file__).with_name("andar_17828.html")
BASE_URL = "https://andar.co.kr/product/detail.html?product_no=17828"


def text_clean(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip()


def main() -> None:
    raw = HTML_PATH.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(raw, "lxml")

    # --- 1) <head> 메타: OG/트위터 태그가 보통 가장 깔끔한 상품 요약을 갖고 있음 ---
    meta: dict[str, str] = {}
    for tag in soup.find_all("meta"):
        prop = tag.get("property") or tag.get("name")
        val = tag.get("content")
        if prop and val and (prop.startswith("og:") or prop.startswith("twitter:") or prop in {"description", "keywords"}):
            meta[prop] = text_clean(val)

    title = text_clean(soup.title.text if soup.title else "")

    # --- 2) 가격/상품번호/브랜드 — Andar 쇼핑몰은 카페24 기반: .xans-product-detail / [data-product-no] 류 ---
    # 일단 단순히 가격 표기 패턴으로 잡아본다
    price_candidates = []
    for el in soup.select("[id*=price], [class*=price], [class*=Price]"):
        t = text_clean(el.get_text(" "))
        if not t:
            continue
        if re.search(r"\d{1,3}(,\d{3})+\s*원", t) or re.search(r"₩\s*\d", t):
            price_candidates.append(t[:200])

    # --- 3) 상품 상세 본문 영역 — 카페24 표준 id는 #prdDetail ---
    detail_area = soup.find(id="prdDetail") or soup.select_one(".xans-product-detail") or soup.body
    detail_text = text_clean(detail_area.get_text(" ")) if detail_area else ""

    # 본문에 박힌 이미지들 (= 우리가 OCR 돌릴 대상)
    detail_imgs = []
    if detail_area:
        for img in detail_area.find_all("img"):
            src = img.get("ec-data-src") or img.get("data-src") or img.get("src")
            if not src:
                continue
            url = urljoin(BASE_URL, src)
            detail_imgs.append({
                "src": url,
                "alt": text_clean(img.get("alt") or ""),
            })

    # 메인 상품 사진(썸네일 영역) — 보통 .keyImg / .bigImage 류
    hero_imgs = []
    for sel in [".bigImage img", ".thumbnail img", "[class*=BigImage] img", "[class*=keyImg] img", "[class*=ThumbnailImage] img"]:
        for img in soup.select(sel):
            src = img.get("ec-data-src") or img.get("data-src") or img.get("src")
            if src:
                hero_imgs.append(urljoin(BASE_URL, src))
    hero_imgs = list(dict.fromkeys(hero_imgs))  # dedupe, keep order

    out = {
        "title": title,
        "meta": meta,
        "price_candidates": list(dict.fromkeys(price_candidates))[:10],
        "detail_text_len": len(detail_text),
        "detail_text_preview": detail_text[:800],
        "detail_imgs_count": len(detail_imgs),
        "detail_imgs_sample": detail_imgs[:8],
        "hero_imgs_count": len(hero_imgs),
        "hero_imgs_sample": hero_imgs[:8],
    }

    out_path = Path(__file__).with_name("andar_17828.extracted.json")
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written: {out_path}")


if __name__ == "__main__":
    main()
