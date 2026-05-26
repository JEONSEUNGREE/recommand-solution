"""상품 상세 페이지 스크랩 + 이미지 OCR.

POST /scrape
{ "url": "https://...", "max_images": 10, "ocr": true }

→ {
    "url": "...",
    "title": "...",
    "meta": {...},
    "body_text": "...",
    "images": [
      { "src": "...", "local_path": "...", "ocr_text": "...", "alt": "..." }
    ]
  }

이미지는 IMAGE_DIR 밑에 sha1(url) 파일명으로 저장.
첫 실행시 EasyOCR 가중치(~65MB)를 ~/.EasyOCR/ 에 자동 다운로드.
"""
from __future__ import annotations

import hashlib
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

# --- 플랫폼 시그니처: HTML 어디든 매치되면 점수 가산 ---
PLATFORM_SIGNATURES: dict[str, list[str]] = {
    "cafe24": [
        r"xans-product-detail", r"prdDetail", r"xans-product",
        r"poxo\.com", r"\.cafe24\.com", r"/cafe24",
    ],
    "makeshop": [
        r"makeshop", r"/shopdetail\.html", r"branduid=",
        r"_mkc=", r"M_default", r"makeshop\.co\.kr",
    ],
    "godo": [r"godo", r"_godo", r"goods/goods_view", r"gd_basic_options"],
    "naver_smartstore": [r"smartstore\.naver\.com", r"shopping\.naver"],
    "imweb": [r"imweb", r"site\.imweb\.me"],
    "shopify": [r"shopify", r"cdn\.shopify"],
}

# --- 플랫폼별 상품 상세 컨테이너 화이트리스트 (우선순위 순) ---
DETAIL_SELECTORS_BY_PLATFORM: dict[str, list[str]] = {
    "cafe24": ["#prdDetail", ".xans-product-detail", "#productDetail"],
    "makeshop": ["#productDetail", ".prd_detail", "#detailpageWrap", ".product-detail"],
    "godo": [".goods_detail", ".detail_pic", "#contents .goods_detail_view"],
    "naver_smartstore": [".product_detail_area"],
    "imweb": [".product_detail", ".product-detail-cont"],
}

# --- 본문 안에서도 무조건 제거할 노이즈 영역 ---
BLACKLIST_NODE_NAMES = ("script", "style", "noscript", "nav", "aside", "header", "footer", "iframe")
BLACKLIST_ID_CLASS = [
    r"recommend", r"related", r"banner", r"advertis",
    r"review[-_]?list", r"qna", r"snb", r"gnb",
    r"\bcart\b", r"\blogin\b", r"\bsearch\b",
    r"\bfooter\b", r"\bheader\b", r"^nav$",
]

# --- 휴리스틱 폴백 시 너무 넓은 wrapper로 잡혀버리는 ID 패턴 ---
WRAPPER_ID_PATTERNS = [r"^wrap$", r"^wrapper$", r"^body$", r"^main$", r"^content$", r"^contentWrap", r"^contentWrapper"]

# --- 이미지 URL에 이 패턴이 포함되면 광고/인트로 이미지로 간주하고 제외 ---
IMAGE_URL_BLOCKLIST = [
    r"/intro/", r"/banner/", r"/event/", r"/popup/",
    r"/notice/", r"/advertis", r"/promotion/",
]

IMAGE_DIR = Path(__file__).resolve().parent.parent / "scripts" / "scrape" / "images"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# EasyOCR Reader — 모듈 로딩 시 한 번만 생성 (~5초 + 첫 실행 시 모델 다운로드)
_reader = None


def get_reader():
    global _reader
    if _reader is None:
        import easyocr  # 무거운 import, 실제 OCR 요청 들어올 때만
        print("Loading EasyOCR (ko+en, CPU)...")
        _reader = easyocr.Reader(["ko", "en"], gpu=False, verbose=False)
        print("  → ready")
    return _reader


class ScrapeRequest(BaseModel):
    url: str
    max_images: int = Field(default=8, ge=0, le=50)
    ocr: bool = True


@dataclass
class ImgInfo:
    src: str
    alt: str
    local_path: str | None = None
    ocr_text: str | None = None
    error: str | None = None


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s).strip() if s else ""


def _safe_filename(url: str) -> str:
    ext = Path(urlparse(url).path).suffix.lower() or ".jpg"
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        ext = ".jpg"
    return hashlib.sha1(url.encode()).hexdigest() + ext


def _download_image(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        return
    r = requests.get(url, headers={"User-Agent": UA}, timeout=15, stream=True)
    r.raise_for_status()
    with dest.open("wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)


def _ocr_image(path: Path) -> str:
    # Windows + OpenCV는 path에 한글 들어있으면 cv2.imread가 None 반환.
    # PIL로 직접 읽어서 ndarray로 EasyOCR에 넘긴다.
    import numpy as np
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        arr = np.array(im)

    reader = get_reader()
    lines = reader.readtext(arr, detail=0, paragraph=True)
    return "\n".join(lines)


def _log(msg: str) -> None:
    # uvicorn 기본 로거에 끼지 않고 바로 stdout flush — tail -f 친화적
    print(f"[scrape] {msg}", flush=True, file=sys.stdout)


def _node_label(el: Tag | None) -> str:
    if el is None:
        return "(none)"
    parts = [el.name or "?"]
    if el.get("id"):
        parts.append(f"#{el.get('id')}")
    cls = el.get("class")
    if cls:
        parts.append("." + ".".join(cls[:3]))
    return "".join(parts)


def _detect_platform(html: str) -> tuple[str, dict[str, int]]:
    scores: dict[str, int] = {}
    for plat, patterns in PLATFORM_SIGNATURES.items():
        s = sum(len(re.findall(p, html, re.I)) for p in patterns)
        if s > 0:
            scores[plat] = s
    if not scores:
        return ("unknown", {})
    return (max(scores, key=scores.get), scores)


def _is_blacklisted(el: Tag) -> bool:
    ident = " ".join(filter(None, [el.get("id", ""), *(el.get("class") or [])]))
    return bool(ident) and any(re.search(p, ident, re.I) for p in BLACKLIST_ID_CLASS)


def _is_wrapper(el: Tag) -> bool:
    ident = el.get("id", "")
    return bool(ident) and any(re.search(p, ident, re.I) for p in WRAPPER_ID_PATTERNS)


def _strip_noise(container: Tag) -> int:
    """블랙리스트 자손 노드들을 본문에서 제거. 제거된 개수 반환."""
    removed = 0
    for name in BLACKLIST_NODE_NAMES:
        for el in container.find_all(name):
            el.decompose()
            removed += 1
    for el in list(container.find_all(True)):
        # decompose된 부모를 따라간 dead ref 방어
        if el.parent is None:
            continue
        if _is_blacklisted(el):
            el.decompose()
            removed += 1
    return removed


def _heuristic_container(soup: BeautifulSoup) -> Tag | None:
    """알려진 셀렉터 다 실패 시 — img 밀집도 + 텍스트 길이 기반.
    wrapper ID로 잡히는 너무 넓은 영역은 제외."""
    best: tuple[int, Tag] | None = None
    for el in soup.find_all(["div", "section", "article"]):
        if _is_blacklisted(el) or _is_wrapper(el):
            continue
        imgs = len(el.find_all("img", recursive=True))
        text_len = len(el.get_text(" ", strip=True))
        if text_len < 100 and imgs < 3:
            continue
        score = imgs * 200 + text_len
        if best is None or score > best[0]:
            best = (score, el)
    return best[1] if best else None


def _select_container(soup: BeautifulSoup, html: str, platform: str) -> dict:
    """본문 컨테이너 결정 + 진단 정보."""
    warnings: list[str] = []
    selectors = DETAIL_SELECTORS_BY_PLATFORM.get(platform, [])

    # 1) 화이트리스트 시도
    for sel in selectors:
        try:
            el = soup.select_one(sel)
        except Exception as e:  # noqa: BLE001 — 잘못된 셀렉터 방어
            warnings.append(f"selector '{sel}' raised: {e}")
            continue
        if el is None:
            continue
        # 비어있는 매칭은 의심 — 텍스트도 이미지도 없으면 스킵
        if len(el.get_text(strip=True)) < 30 and len(el.find_all("img")) < 1:
            warnings.append(f"selector '{sel}' matched but empty (label={_node_label(el)})")
            continue
        return {
            "container": el,
            "used_selector": sel,
            "method": "whitelist",
            "warnings": warnings,
        }

    if selectors:
        warnings.append(f"platform={platform} but none of {selectors} matched usable container")

    # 2) 휴리스틱 폴백
    h = _heuristic_container(soup)
    if h is not None:
        warnings.append(f"falling back to heuristic ({_node_label(h)})")
        return {
            "container": h,
            "used_selector": _node_label(h),
            "method": "heuristic",
            "warnings": warnings,
        }

    # 3) 최후 — body
    warnings.append("no detail container found, using <body>")
    return {
        "container": soup.body,
        "used_selector": "body",
        "method": "fallback",
        "warnings": warnings,
    }


@router.post("/scrape")
def scrape(req: ScrapeRequest):
    t_start = time.perf_counter()
    _log(f"START url={req.url} max_images={req.max_images} ocr={req.ocr}")

    try:
        t0 = time.perf_counter()
        resp = requests.get(req.url, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
        _log(f"  fetched html  {(time.perf_counter()-t0)*1000:.0f}ms  {len(resp.content)}B")
    except requests.RequestException as e:
        raise HTTPException(status_code=400, detail=f"fetch failed: {e}") from e

    # 응답 인코딩 자동 추정 (cafe24/한국 쇼핑몰은 보통 utf-8)
    resp.encoding = resp.apparent_encoding or "utf-8"
    html_str = resp.text
    soup = BeautifulSoup(html_str, "lxml")

    title = _clean(soup.title.text if soup.title else "")
    meta: dict[str, str] = {}
    for tag in soup.find_all("meta"):
        prop = tag.get("property") or tag.get("name")
        val = tag.get("content")
        if prop and val and (prop.startswith("og:") or prop in {"description", "keywords"}):
            meta[prop] = _clean(val)

    # --- 플랫폼 진단 + 컨테이너 선택 ---
    platform, platform_scores = _detect_platform(html_str)
    sel_info = _select_container(soup, html_str, platform)
    detail: Tag | None = sel_info["container"]
    removed = _strip_noise(detail) if detail else 0
    _log(f"  platform={platform} scores={platform_scores} selector={sel_info['used_selector']} method={sel_info['method']} stripped={removed}")
    for w in sel_info["warnings"]:
        _log(f"  WARN: {w}")

    body_text = _clean(detail.get_text(" ")) if detail else ""

    # 이미지 수집 — 본문 영역 안에서만 (블랙리스트는 이미 _strip_noise로 제거됨)
    seen: set[str] = set()
    imgs: list[ImgInfo] = []
    candidates = list(detail.find_all("img")) if detail else []

    for img in candidates:
        if len(imgs) >= req.max_images:
            break
        src = img.get("ec-data-src") or img.get("data-src") or img.get("src")
        if not src or src.startswith("data:"):
            continue
        full = urljoin(req.url, src)
        if full in seen:
            continue
        seen.add(full)
        if any(re.search(p, full, re.I) for p in IMAGE_URL_BLOCKLIST):
            _log(f"  skip blocked image url: {full}")
            continue
        imgs.append(ImgInfo(src=full, alt=_clean(img.get("alt") or "")))

    _log(f"  found {len(imgs)} images")

    # 다운로드 + OCR
    for idx, info in enumerate(imgs, 1):
        try:
            t0 = time.perf_counter()
            fname = _safe_filename(info.src)
            dest = IMAGE_DIR / fname
            cached = dest.exists() and dest.stat().st_size > 0
            _download_image(info.src, dest)
            info.local_path = str(dest)
            dl_ms = (time.perf_counter() - t0) * 1000
            _log(f"  [{idx}/{len(imgs)}] dl  {dl_ms:6.0f}ms  {'(cache)' if cached else f'{dest.stat().st_size}B'}  {info.src.rsplit('/', 1)[-1]}")
        except Exception as e:  # noqa: BLE001 — 한 장 실패가 전체를 죽이지 않게
            info.error = f"download: {e}"
            _log(f"  [{idx}/{len(imgs)}] dl FAIL: {e}")
            continue

        if req.ocr:
            try:
                t0 = time.perf_counter()
                info.ocr_text = _ocr_image(dest)
                ocr_ms = (time.perf_counter() - t0) * 1000
                _log(f"  [{idx}/{len(imgs)}] ocr {ocr_ms:6.0f}ms  → {len(info.ocr_text or '')} chars")
            except Exception as e:  # noqa: BLE001
                info.ocr_text = None
                info.error = f"ocr: {e}"
                _log(f"  [{idx}/{len(imgs)}] ocr FAIL: {e}")

    total_ms = (time.perf_counter() - t_start) * 1000
    _log(f"DONE  {total_ms:.0f}ms total  ({len(imgs)} imgs)")

    return {
        "url": req.url,
        "title": title,
        "meta": meta,
        "platform": platform,
        "platform_scores": platform_scores,
        "dom_validation": {
            "used_selector": sel_info["used_selector"],
            "method": sel_info["method"],  # "whitelist" | "heuristic" | "fallback"
            "container_label": _node_label(detail),
            "warnings": sel_info["warnings"],
            "stripped_nodes": removed,
        },
        "body_text": body_text,
        "body_text_len": len(body_text),
        "image_count": len(imgs),
        "images": [vars(i) for i in imgs],
    }
