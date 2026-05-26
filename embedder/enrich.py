"""상품 1개 단위 enrich 파이프라인 — 단계별 분리.

[1] POST /scrape/fetch
    원본 HTML 다운로드 + body_text 추출 + 이미지 다운로드.
    파일시스템: $RECOMMAND_DATA_DIR/advertisers/{id}/products/{code}/
      ├── source.html
      ├── extracted.json     (body_text, meta, image src 리스트 등)
      └── images/{sha1}.jpg

[2-A] POST /scrape/ocr     — 받아둔 이미지 N장 → EasyOCR → enriched_text
[2-B] POST /scrape/vlm     — 받아둔 이미지 N장 → 비전 LLM 캡셔닝 → enriched_text

OCR/VLM은 한 번에 둘 다 하지 않고, 사용자가 선택해서 한쪽씩 호출.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# scrape.py와 공유 (플랫폼 탐지/컨테이너 선택/노이즈 제거 로직 재사용)
from .scrape import (
    UA,
    _clean,
    _detect_platform,
    _node_label,
    _select_container,
    _strip_noise,
    get_reader,
)

router = APIRouter(prefix="/scrape")

DATA_DIR = Path(os.getenv("RECOMMAND_DATA_DIR", "D:/recommand-data")).resolve()
DB_DSN = os.environ.get("DB_DSN", "postgresql://app:app@localhost:5433/recommend")


def _record_image_srcs(advertiser_id: int, product_code: str, rv_product_id: int | None,
                       image_files: list[dict]) -> None:
    """스크랩된 이미지 src URL을 rv_product_images에 기록 (upsert). 실패해도 무시."""
    rows = [
        (advertiser_id, product_code, rv_product_id,
         im["src"], Path(im.get("local_path") or "").name or None)
        for im in image_files if im.get("src")
    ]
    if not rows:
        return
    try:
        with psycopg.connect(DB_DSN) as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO rv_product_images
                        (advertiser_id, product_code, rv_product_id, src_url, local_filename)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (advertiser_id, product_code, src_url) DO NOTHING
                    """,
                    rows,
                )
    except Exception as e:  # noqa: BLE001
        _log(f"WARN: rv_product_images upsert failed: {e}")


def _product_dir(advertiser_id: int, product_code: str) -> Path:
    safe_code = re.sub(r"[^\w\-.]", "_", product_code)
    p = DATA_DIR / "advertisers" / str(advertiser_id) / "products" / safe_code
    p.mkdir(parents=True, exist_ok=True)
    return p


def _images_dir(advertiser_id: int, product_code: str) -> Path:
    p = _product_dir(advertiser_id, product_code) / "images"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _log(msg: str) -> None:
    print(f"[enrich] {msg}", flush=True, file=sys.stdout)


def _img_filename(url: str) -> str:
    ext = Path(urlparse(url).path).suffix.lower() or ".jpg"
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        ext = ".jpg"
    return hashlib.sha1(url.encode()).hexdigest() + ext


def _download_image(url: str, dest: Path) -> int:
    if dest.exists() and dest.stat().st_size > 0:
        return dest.stat().st_size
    r = requests.get(url, headers={"User-Agent": UA}, timeout=20, stream=True)
    r.raise_for_status()
    size = 0
    with dest.open("wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
            size += len(chunk)
    return size


# ───────────────────────────── [1] FETCH ─────────────────────────────


class FromHtmlRequest(BaseModel):
    advertiser_id: int
    product_code: str
    url: str                            # 이미지 상대경로 resolve용 base URL
    html: str                           # 본문 HTML (이미 받아온 것)
    max_images: int = Field(default=30, ge=0, le=100)
    selector_detail: list[str] | str | None = None
    selector_name: list[str] | str | None = None
    selector_price: list[str] | str | None = None
    image_attrs: str | None = None
    detail_anchor_start: str | None = None
    detail_anchor_end: str | None = None
    image_url_blocks: list[str] = Field(default_factory=list)


class FetchRequest(BaseModel):
    advertiser_id: int
    product_code: str
    url: str
    max_images: int = Field(default=30, ge=0, le=100)
    # 광고주별 selector override — list (priority 순) 또는 단일 str. 비우면 플랫폼 디폴트 폴백.
    selector_detail: list[str] | str | None = None
    selector_name: list[str] | str | None = None
    selector_price: list[str] | str | None = None
    image_attrs: str | None = None  # 콤마구분, 예: "data-frz-src,data-src,src"
    # HTML 주석/문자열 anchor 사이만 슬라이스 (selector보다 우선)
    detail_anchor_start: str | None = None
    detail_anchor_end: str | None = None
    # 이미지 URL 차단 패턴 (substring 매치). src에 포함되면 다운로드 자체 skip.
    image_url_blocks: list[str] = Field(default_factory=list)


def _as_list(v) -> list[str]:
    """list 또는 str을 list[str]로 정규화 (빈 값 제거)."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    return [s for s in v if isinstance(s, str) and s.strip()]


# 메이크샵/카페24 등에서 쓰는 lazy loading 속성 — 우선순위 순
DEFAULT_LAZY_ATTRS = ("data-frz-src", "ec-data-src", "data-src", "data-original", "data-lazy", "data-image", "src")


def _make_pick_img_src(image_attrs: str | None):
    if image_attrs:
        attrs = tuple(s.strip() for s in image_attrs.split(",") if s.strip())
    else:
        attrs = DEFAULT_LAZY_ATTRS

    def pick(img):
        for attr in attrs:
            v = img.get(attr)
            if v and not v.startswith("data:"):
                return v
        return None
    return pick


def _process_html(advertiser_id: int, product_code: str, base_url: str, html_str: str,
                  max_images: int,
                  selector_detail, selector_name, selector_price,
                  image_attrs: str | None,
                  detail_anchor_start: str | None, detail_anchor_end: str | None,
                  image_url_blocks: list[str], log_prefix: str = "FETCH") -> dict:
    """HTML 받아서 본문/이미지 처리. URL 다운로드 부분만 제외하고 fetch와 동일 로직."""
    t0 = time.perf_counter()
    _log(f"{log_prefix} advertiser={advertiser_id} code={product_code} max_images={max_images}")

    pdir = _product_dir(advertiser_id, product_code)
    imgdir = _images_dir(advertiser_id, product_code)
    html_path = pdir / "source.html"
    extracted_path = pdir / "extracted.json"

    html_path.write_text(html_str, encoding="utf-8")
    _log(f"  html saved {len(html_str.encode('utf-8'))}B → {html_path}")

    # 2) 본문/메타 파싱
    soup = BeautifulSoup(html_str, "lxml")
    title = _clean(soup.title.text if soup.title else "")
    meta: dict[str, str] = {}
    for tag in soup.find_all("meta"):
        prop = tag.get("property") or tag.get("name")
        val = tag.get("content")
        if prop and val and (prop.startswith("og:") or prop in {"description", "keywords"}):
            meta[prop] = _clean(val)

    platform, platform_scores = _detect_platform(html_str)

    detail_selectors = _as_list(selector_detail)
    name_selectors = _as_list(selector_name)
    price_selectors = _as_list(selector_price)

    container = None
    used_selector = None
    method = None
    warnings: list[str] = []

    # [0] 최우선: HTML 주석/문자열 anchor 사이로 슬라이스
    if detail_anchor_start and detail_anchor_end:
        try:
            s_idx = html_str.find(detail_anchor_start)
            e_idx = html_str.find(detail_anchor_end, s_idx + len(detail_anchor_start)) if s_idx >= 0 else -1
            if s_idx >= 0 and e_idx > s_idx:
                slice_html = html_str[s_idx + len(detail_anchor_start) : e_idx]
                slice_soup = BeautifulSoup(slice_html, "lxml")
                container = slice_soup.body if slice_soup.body else slice_soup
                used_selector = f"anchor[{detail_anchor_start!r}..{detail_anchor_end!r}]"
                method = "anchor"
                _log(f"  anchor slice: {e_idx - s_idx} chars between anchors")
            else:
                warnings.append(f"anchor not found: start={s_idx} end={e_idx}")
        except Exception as e:
            warnings.append(f"anchor slice failed: {e}")

    # [1] anchor 실패 시 — selector_detail 리스트 시도
    if container is None:
        for sel in detail_selectors:
            try:
                el = soup.select_one(sel)
            except Exception as e:
                warnings.append(f"override selector '{sel}' raised: {e}")
                continue
            if el is None:
                warnings.append(f"override selector '{sel}' not found")
                continue
            if len(el.get_text(strip=True)) < 30 and len(el.find_all("img")) < 1:
                warnings.append(f"override selector '{sel}' matched but empty (label={_node_label(el)})")
                continue
            container = el
            used_selector = sel
            method = "override"
            break

    # [2] 다 실패 시 — 플랫폼 디폴트 폴백
    if container is None:
        if detail_selectors:
            warnings.append(f"all {len(detail_selectors)} override detail selectors failed; falling back to platform default")
        sel_info = _select_container(soup, html_str, platform)
        container = sel_info["container"]
        used_selector = sel_info["used_selector"]
        method = sel_info["method"]
        warnings.extend(sel_info["warnings"])

    stripped = _strip_noise(container) if container else 0
    body_text = _clean(container.get_text(" ")) if container else ""

    # 상품명 — 여러 selector 시도, 매치되는 텍스트들을 합침
    product_name_parts: list[str] = []
    used_name_selectors: list[str] = []
    for sel in name_selectors:
        try:
            for nel in soup.select(sel):  # select_one 대신 select — 같은 클래스 여러 매치 모두 활용
                t = _clean(nel.get_text(" "))
                if t and t not in product_name_parts:
                    product_name_parts.append(t)
                    used_name_selectors.append(sel)
        except Exception as e:
            warnings.append(f"selector_name '{sel}' raised: {e}")
    product_name_extracted = "\n\n".join(product_name_parts) if product_name_parts else None

    _log(f"  platform={platform} detail_used={used_selector} method={method} name_matches={len(product_name_parts)} body_len={len(body_text)} stripped={stripped}")

    # 3) 이미지 src 수집 — 광고주 image_attrs 우선, 없으면 디폴트 lazy 속성. URL 차단 패턴 적용.
    pick = _make_pick_img_src(image_attrs)
    blocks = [b for b in (image_url_blocks or []) if b]
    img_srcs: list[str] = []
    seen: set[str] = set()
    blocked_count = 0
    if container:
        for img in container.find_all("img"):
            if len(img_srcs) >= max_images:
                break
            src = pick(img)
            if not src:
                continue
            full = urljoin(base_url, src)
            if full in seen:
                continue
            seen.add(full)
            if any(b in full for b in blocks):
                blocked_count += 1
                continue
            img_srcs.append(full)
    if blocks:
        _log(f"  url blocks={len(blocks)} blocked={blocked_count} kept={len(img_srcs)}")

    # 4) 이미지 다운로드
    image_files: list[dict] = []
    for idx, src in enumerate(img_srcs, 1):
        fname = _img_filename(src)
        dest = imgdir / fname
        try:
            size = _download_image(src, dest)
            image_files.append({"src": src, "local_path": str(dest), "size": size})
            _log(f"  [{idx}/{len(img_srcs)}] dl ok {size}B {fname}")
        except Exception as e:  # noqa: BLE001
            image_files.append({"src": src, "error": str(e)})
            _log(f"  [{idx}/{len(img_srcs)}] dl FAIL: {e}")

    # 5) extracted.json 저장
    extracted = {
        "url": base_url,
        "title": title,
        "product_name_extracted": product_name_extracted,
        "meta": meta,
        "platform": platform,
        "platform_scores": platform_scores,
        "dom_validation": {
            "used_selector": used_selector,
            "method": method,
            "container_label": _node_label(container),
            "warnings": warnings,
            "stripped_nodes": stripped,
        },
        "body_text": body_text,
        "body_text_len": len(body_text),
        "image_files": image_files,
        "image_count": len([i for i in image_files if "local_path" in i]),
    }
    extracted_path.write_text(json.dumps(extracted, ensure_ascii=False, indent=2), encoding="utf-8")
    _record_image_srcs(advertiser_id, product_code, None, image_files)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    _log(f"DONE {log_prefix} {elapsed_ms:.0f}ms")

    return {
        **extracted,
        "html_path": str(html_path),
        "extracted_path": str(extracted_path),
        "image_dir": str(imgdir),
        "elapsed_ms": elapsed_ms,
    }


@router.post("/fetch")
def fetch(req: FetchRequest):
    try:
        resp = requests.get(req.url, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=400, detail=f"fetch failed: {e}") from e
    resp.encoding = resp.apparent_encoding or "utf-8"
    return _process_html(
        req.advertiser_id, req.product_code, req.url, resp.text,
        req.max_images, req.selector_detail, req.selector_name, req.selector_price,
        req.image_attrs, req.detail_anchor_start, req.detail_anchor_end,
        req.image_url_blocks or [], log_prefix="FETCH",
    )


@router.post("/from-html")
def from_html(req: FromHtmlRequest):
    """이미 받아온 HTML을 그대로 처리. 메이크샵 API의 product_content 같은 데서 받은 본문 처리에 사용."""
    return _process_html(
        req.advertiser_id, req.product_code, req.url, req.html,
        req.max_images, req.selector_detail, req.selector_name, req.selector_price,
        req.image_attrs, req.detail_anchor_start, req.detail_anchor_end,
        req.image_url_blocks or [], log_prefix="FROM_HTML",
    )


# ───────────────────────────── [2-A] OCR ─────────────────────────────


class OcrRequest(BaseModel):
    advertiser_id: int
    product_code: str
    max_images: int = Field(default=8, ge=1, le=50)
    exclude_filenames: list[str] = Field(default_factory=list)


def _ocr_one(path: Path) -> str:
    import numpy as np
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        arr = np.array(im)
    reader = get_reader()
    lines = reader.readtext(arr, detail=0, paragraph=True)
    return "\n".join(lines)


@router.post("/ocr")
def ocr(req: OcrRequest):
    t0 = time.perf_counter()
    _log(f"OCR advertiser={req.advertiser_id} code={req.product_code}")

    pdir = _product_dir(req.advertiser_id, req.product_code)
    imgdir = pdir / "images"
    if not imgdir.exists():
        raise HTTPException(status_code=404, detail="images dir not found; run /scrape/fetch first")

    extracted_path = pdir / "extracted.json"
    extracted = json.loads(extracted_path.read_text(encoding="utf-8")) if extracted_path.exists() else {}
    body_text = extracted.get("body_text", "")

    # 이미지 파일 목록 (제외 파일명 skip + max_images 적용)
    excluded = set(req.exclude_filenames or [])
    img_files = [p for p in sorted(imgdir.glob("*.*")) if p.name not in excluded][: req.max_images]
    if not img_files:
        raise HTTPException(status_code=404, detail="no images downloaded yet (or all excluded)")
    _log(f"OCR advertiser={req.advertiser_id} code={req.product_code} excluded={len(excluded)}")

    ocr_results: list[dict] = []
    for idx, p in enumerate(img_files, 1):
        try:
            t_img = time.perf_counter()
            text = _ocr_one(p)
            ocr_results.append({"file": p.name, "text": text, "ms": int((time.perf_counter() - t_img) * 1000)})
            _log(f"  [{idx}/{len(img_files)}] ocr {len(text)} chars from {p.name}")
        except Exception as e:  # noqa: BLE001
            ocr_results.append({"file": p.name, "error": str(e)})
            _log(f"  [{idx}/{len(img_files)}] ocr FAIL: {e}")

    # enriched_info = 본문 + OCR 텍스트 모두 합쳐서 정제
    parts = [body_text] if body_text else []
    for r in ocr_results:
        if r.get("text"):
            parts.append(r["text"])
    enriched_info = "\n\n".join(p for p in parts if p)

    (pdir / "ocr.json").write_text(
        json.dumps({"results": ocr_results, "enriched_info": enriched_info}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    elapsed_ms = (time.perf_counter() - t0) * 1000
    _log(f"DONE ocr {elapsed_ms:.0f}ms enriched_len={len(enriched_info)}")

    return {
        "method": "ocr",
        "image_count": len(img_files),
        "ocr_results": ocr_results,
        "enriched_info": enriched_info,
        "enriched_info_len": len(enriched_info),
        "elapsed_ms": elapsed_ms,
    }


# ───────────────────────────── [2-B] VLM ─────────────────────────────


class VlmRequest(BaseModel):
    advertiser_id: int
    product_code: str
    max_images: int = Field(default=4, ge=1, le=10)  # 비전 호출은 비싸서 기본 4
    exclude_filenames: list[str] = Field(default_factory=list)


VLM_BACKEND = os.getenv("VLM_BACKEND", "groq")  # 'groq' | 'qwen-local'
VLM_BASE_URL = os.getenv("VLM_BASE_URL", "https://api.groq.com/openai/v1")
VLM_API_KEY = os.getenv("VLM_API_KEY") or os.getenv("GROQ_API_KEY") or ""
VLM_MODEL = os.getenv("VLM_MODEL", "meta-llama/llama-4-maverick-17b-128e-instruct")

VLM_PROMPT = (
    "이 상품 사진을 한국어로 묘사해줘. "
    "패션 검색에서 자주 쓰일 어휘로: 핏, 소재 텍스처, 컬러, 디테일(포켓/단추/패턴/카라/슬리브 길이), "
    "어울리는 스타일/계절/상황(예: 오피스, 데이트, 캠퍼스, 여름 휴가), 페르소나(타겟 연령/성별)를 포함해서. "
    "사진에서 직접 관찰되는 정보만, 군더더기 없이 3~5문장."
)


def _encode_image(p: Path) -> str:
    mime = "image/jpeg"
    suffix = p.suffix.lower()
    if suffix == ".png":
        mime = "image/png"
    elif suffix == ".webp":
        mime = "image/webp"
    elif suffix == ".gif":
        mime = "image/gif"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _vlm_caption_local(image_path: Path) -> str:
    from .qwen_vl import caption as qwen_caption
    return qwen_caption(image_path, VLM_PROMPT)


def _vlm_caption(image_path: Path) -> str:
    if VLM_BACKEND == "qwen-local":
        return _vlm_caption_local(image_path)
    # 기본: Groq HTTP
    if not VLM_API_KEY:
        raise HTTPException(status_code=500, detail="VLM_API_KEY (or GROQ_API_KEY) not set")
    payload = {
        "model": VLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VLM_PROMPT},
                    {"type": "image_url", "image_url": {"url": _encode_image(image_path)}},
                ],
            }
        ],
        "max_tokens": 400,
        "temperature": 0.3,
    }
    r = requests.post(
        f"{VLM_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {VLM_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"VLM HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    return data["choices"][0]["message"]["content"]


@router.post("/vlm")
def vlm(req: VlmRequest):
    t0 = time.perf_counter()
    backend_id = "qwen-local (Qwen2-VL-2B)" if VLM_BACKEND == "qwen-local" else f"groq:{VLM_MODEL}"
    _log(f"VLM advertiser={req.advertiser_id} code={req.product_code} backend={backend_id}")

    pdir = _product_dir(req.advertiser_id, req.product_code)
    imgdir = pdir / "images"
    if not imgdir.exists():
        raise HTTPException(status_code=404, detail="images dir not found; run /scrape/fetch first")

    extracted_path = pdir / "extracted.json"
    extracted = json.loads(extracted_path.read_text(encoding="utf-8")) if extracted_path.exists() else {}
    body_text = extracted.get("body_text", "")

    excluded = set(req.exclude_filenames or [])
    img_files = [p for p in sorted(imgdir.glob("*.*")) if p.name not in excluded][: req.max_images]
    if not img_files:
        raise HTTPException(status_code=404, detail="no images downloaded yet (or all excluded)")
    _log(f"VLM excluded={len(excluded)} processing={len(img_files)}")

    captions: list[dict] = []
    for idx, p in enumerate(img_files, 1):
        try:
            t_img = time.perf_counter()
            cap = _vlm_caption(p)
            captions.append({"file": p.name, "caption": cap, "ms": int((time.perf_counter() - t_img) * 1000)})
            _log(f"  [{idx}/{len(img_files)}] vlm {len(cap)} chars from {p.name}")
        except Exception as e:  # noqa: BLE001
            captions.append({"file": p.name, "error": str(e)})
            _log(f"  [{idx}/{len(img_files)}] vlm FAIL: {e}")

    parts = [body_text] if body_text else []
    for c in captions:
        if c.get("caption"):
            parts.append(c["caption"])
    enriched_info = "\n\n".join(p for p in parts if p)

    (pdir / "vlm.json").write_text(
        json.dumps({"backend": backend_id, "captions": captions, "enriched_info": enriched_info}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    elapsed_ms = (time.perf_counter() - t0) * 1000
    _log(f"DONE vlm {elapsed_ms:.0f}ms enriched_len={len(enriched_info)}")

    return {
        "method": "vlm",
        "backend": backend_id,
        "image_count": len(img_files),
        "captions": captions,
        "enriched_info": enriched_info,
        "enriched_info_len": len(enriched_info),
        "elapsed_ms": elapsed_ms,
    }
