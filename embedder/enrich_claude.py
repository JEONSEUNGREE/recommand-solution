"""
Claude CLI (Max) 기반 멀티모달 enrich.

PDF 멀티모달 실험 보고서 v1 — 케이스 02(분리 이미지) + 케이스 04(임상 데이터) 패턴 조합.
한 상품의 텍스트 + 이미지를 Claude `-p` (non-interactive)에 전달해 4가지 산출물 추출:
  1) 카테고리 + 신뢰도
  2) 공통 속성 + category_attributes JSONB
  3) 태그 (situation/mood/feature)
  4) 다관점 설명문 (situation/material/style/persona)
  5) 이미지 분류 (main/detail/lifestyle/infographic/...)
  6) 트랩 회피: SET 구성 / 호환 본체 분리

사용: python -m embedder.enrich_claude <rv_product_id> [--no-db]
"""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import psycopg
from PIL import Image


DB_DSN = os.environ.get("DB_DSN", "postgresql://app:app@localhost:5433/recommend")


def _resolve_claude_bin() -> str:
    """claude 실행파일 절대경로. shell 없이 직접 실행하기 위해 .exe 경로를 찾는다.

    shell=True 로 'claude' 를 실행하면 cmd.exe → claude.exe 2단 구조라
    타임아웃 시 cmd.exe 만 죽고 claude.exe 가 고아로 남아 파이프를 막는다.
    """
    from pathlib import Path as _P
    env = os.environ.get("CLAUDE_BIN")
    if env and _P(env).exists():
        return env
    appdata = os.environ.get("APPDATA", "")
    cand = _P(appdata) / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    if cand.exists():
        return str(cand)
    return env or "claude"


CLAUDE_BIN = _resolve_claude_bin()
MAX_IMAGES = int(os.environ.get("ENRICH_MAX_IMAGES", "30"))
IMG_MAX_DIM = int(os.environ.get("ENRICH_IMG_MAX_DIM", "1280"))  # 한 변 최대 px
IMG_QUALITY = int(os.environ.get("ENRICH_IMG_QUALITY", "82"))


class RateLimitError(RuntimeError):
    """claude CLI 사용량 한도 도달 (429). reset_at = 한도 해제 epoch 초 (없으면 None)."""

    def __init__(self, message: str, reset_at: float | None):
        super().__init__(message)
        self.reset_at = reset_at


def _parse_reset_time(text: str) -> float | None:
    """'resets 7:30pm (Asia/Seoul)' 같은 문구 → 다음 해당 시각의 epoch 초."""
    import re as _re
    from datetime import datetime, timedelta
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Seoul")
    except Exception:
        tz = None
    m = _re.search(r'resets?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)', text, _re.IGNORECASE)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    if m.group(3).lower() == "pm" and hour != 12:
        hour += 12
    if m.group(3).lower() == "am" and hour == 12:
        hour = 0
    now = datetime.now(tz) if tz else datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.timestamp()


# JSON Schema — Stage 2 LLM 추출 전략 v1.0 (부록 A 케이스 4 기준)
# extract_product_data tool 스키마 + 우리 트랩 회피 확장
OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["product", "category", "common_attributes", "category_attributes", "tags", "descriptions", "images"],
    "properties": {
        "category": {
            "type": "object",
            "required": ["name", "confidence"],
            "properties": {
                "name": {"type": "string", "description": "leaf 카테고리명 (예: '주얼리 > 14K > 귀걸이/목걸이 SET')"},
                "path": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
        },
        "product": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "description": "정제된 상품명 (마케팅 문구/이모지 제거)"},
                "price": {"type": ["number", "null"]},
                "sale_price": {"type": ["number", "null"]},
                "currency": {"type": "string", "default": "KRW"},
                "options": {"type": "object", "description": "옵션 그룹별 선택지. 예) {color:[...], size:[...]}"},
            },
        },
        "common_attributes": {
            "type": "object",
            "properties": {
                "brand": {"type": ["string", "null"]},
                "brand_tier": {"type": ["string", "null"], "enum": ["럭셔리", "프리미엄", "중가", "저가", None]},
                "price_tier": {"type": ["string", "null"]},
                "target_gender": {"type": ["string", "null"], "enum": ["여성", "남성", "공용", None]},
                "target_age_min": {"type": ["integer", "null"]},
                "target_age_max": {"type": ["integer", "null"]},
                "origin_country": {"type": ["string", "null"]},
                "manufacturer": {"type": ["string", "null"]},
                "is_premium": {"type": ["boolean", "null"]},
                "is_best_seller": {"type": ["boolean", "null"]},
            },
        },
        "category_attributes": {
            "type": "object",
            "description": "카테고리별 동적 속성 JSONB. 예) 주얼리={metal, karat, stones, closure_type, sizes_available, colors_available}",
            "additionalProperties": True,
        },
        "set_components": {
            "type": "array",
            "description": "SET 상품 구성 분해 (예: 귀걸이 + 목걸이)",
            "items": {
                "type": "object",
                "required": ["name", "type"],
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "description": "예) 귀걸이/목걸이/반지"},
                    "attributes": {"type": "object"},
                },
            },
        },
        "related_products": {
            "type": "array",
            "description": "호환/관련 상품 (이미지 트랩 회피 — 본체/추천상품을 본 상품으로 흡수 금지)",
            "items": {
                "type": "object",
                "required": ["relation_type", "target_name"],
                "properties": {
                    "relation_type": {"type": "string", "enum": ["compatible_with", "same_line_variant", "goes_well_with", "shown_in_image_but_not_this"]},
                    "target_name": {"type": "string"},
                },
            },
        },
        "tags": {
            "type": "array",
            "minItems": 10,
            "items": {
                "type": "object",
                "required": ["tag", "tag_category"],
                "properties": {
                    "tag": {"type": "string"},
                    "tag_category": {"type": "string", "enum": ["situation", "mood", "feature"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
        "descriptions": {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "description": "4관점 설명문 (situation/material/style/persona) — 각 30~80자, 핵심만 1~2문장",
            "items": {
                "type": "object",
                "required": ["perspective", "description"],
                "properties": {
                    "perspective": {"type": "string", "enum": ["situation", "material", "style", "persona"]},
                    "description": {"type": "string", "minLength": 20, "maxLength": 120},
                },
            },
        },
        "images": {
            "type": "array",
            "description": "입력된 모든 이미지에 대한 분류 + 내용 설명. 모든 sha1 빠짐없이 포함.",
            "items": {
                "type": "object",
                "required": ["sha1", "image_type", "is_main_product", "used_for_attributes"],
                "properties": {
                    "sha1": {"type": "string"},
                    "image_type": {
                        "type": "string",
                        "enum": [
                            "main", "lifestyle", "detail", "infographic",
                            "size_chart", "color_options", "spec_table",
                            "care_guide", "certification", "packaging",
                            "related_product", "noise", "other",
                        ],
                    },
                    "is_main_product": {"type": "boolean", "description": "true=본 상품의 이미지 / false=다른 상품·배너·안내문 등 본 상품 무관"},
                    "used_for_attributes": {"type": "boolean", "description": "true=이 이미지의 정보를 본 상품의 common/category_attributes/tags/descriptions에 사용함 / false=오염 방지를 위해 사용하지 않음"},
                    "rejection_reason": {"type": "string", "description": "used_for_attributes=false인 경우 사유 (예: '추천 상품 안내', '브랜드 배너', '호환 본체 사진')"},
                },
            },
        },
        "ignored_observations": {
            "type": "array",
            "description": "본 상품과 무관해서 의도적으로 제외한 정보 (오염 방지). 어떤 sha1에서 어떤 정보를 봤지만 왜 제외했는지 기록.",
            "items": {
                "type": "object",
                "required": ["sha1", "observed", "reason"],
                "properties": {
                    "sha1": {"type": "string"},
                    "observed": {"type": "string", "description": "관찰한 내용 (예: 'SPF34/PA++ 표기')"},
                    "reason": {"type": "string", "description": "제외 사유 (예: '본 상품인 퍼프가 아닌 호환 본체 정보')"},
                },
            },
        },
        "trap_detection": {
            "type": "object",
            "description": "이미지 트랩 / SET 케이스 검증 (PDF 케이스 03 헤라 패턴)",
            "properties": {
                "is_trap": {"type": "boolean"},
                "trap_type": {"type": "string"},
                "explanation": {"type": "string"},
                "is_set_or_combo": {"type": "boolean"},
            },
        },
    },
    "additionalProperties": True,
}


SYSTEM_PROMPT = """당신은 한국 쇼핑몰 상품 데이터를 분석하는 전문가입니다.
(참조: "Stage 2 · LLM 추출 전략 v1.0" 프롬프트 2 · 정형 속성 추출)

[중요 규칙]
1. 텍스트 메타 정보를 우선시 (페이지 HTML 텍스트 > 이미지 텍스트). 추측 금지.
2. 이미지가 본 상품과 다를 수 있음 (마케팅 이미지, 호환 본체, 추천 상품 등) — 본 상품 속성으로 흡수 금지.
3. 한국어 인포그래픽의 모든 텍스트를 OCR (사이즈표·임상 데이터 등은 단위까지 정확히).
4. JSON Schema에 정의되지 않은 필드는 추가하지 마라.
5. 불확실하면 null. 환각 금지.
6. SET 상품(여러 구성품)이면 set_components로 분해 + product.name은 SET 자체로.
7. descriptions·tags는 광고 문구가 아니라 사실 묘사. 근거 없는 마케팅 표현 생성 금지 (아래 절대 규칙 참조).

[추출 항목 — 5종]
1. product: name(정제), price, sale_price, currency, options
2. common_attributes: brand/brand_tier(럭셔리·프리미엄·중가·저가)/target_gender(여성·남성·공용)/target_age_min·max/origin_country/manufacturer/is_premium/is_best_seller
3. category_attributes (JSONB · 카테고리별 자유 키):
   - 의류: fit, size_chart, fabric_composition, season, use_case, design_features
   - 주얼리: metal, karat, stones, setting, sizes_available, colors_available, closure_type, length_mm, pendant_size
   - 화장품 도구: tool_type, compatible_products, package_quantity, skin_type
   - 스킨케어: key_ingredients, clinical_results, suitable_skin_types, volume_ml
4. tags: situation(TPO) · mood(분위기) · feature(기능) 3분류, 각 confidence(0~1). 15~30개 목표.
5. descriptions: 4관점 (각 30~80자, 핵심만 1~2문장). 광고 톤 금지 — 사실 묘사 톤.
   - SITUATION: 언제/어디서/어떤 상황에 (계절·활동·라이프스타일)
   - MATERIAL: 무엇으로 만들어졌고 어떤 기능 (소재·성분·기술·인증)
   - STYLE: 어떻게 생겼고 어떤 느낌 (디자인·색감·실루엣·무드)
   - PERSONA: 누구에게 맞는가 (연령·성별·라이프스타일·가치관)

[마케팅·과장 표현 금지 — 절대 규칙]
descriptions와 tags는 텍스트·이미지에서 **실제로 관찰·확인된 사실만** 기술하라.
아래 표현은 원문(텍스트 또는 이미지)에 **명시적으로 적혀 있을 때만** 쓰고, 없으면 절대 생성·추론하지 마라:
- 사회적 증명: "연예인/셀럽/인플루언서가 착용·선호·선택", "○○ 협찬", "방송·드라마 출연", "스타가 찾는"
- 인기·판매 주장: "베스트셀러", "인기 폭발", "완판", "후기 최다", "판매 1위"
- 검증 불가 수식: 근거 없는 "명품", "프리미엄", "트렌디한 스타일을 연예인과 묶는 표현"
- "연예인 스타일", "연예인st" 같은 태그 — 원문 근거 없으면 mood 태그로도 금지
허용: 이미지에서 직접 보이는 디자인·소재·색감·무드 묘사 (예: "로맨틱한 핑크골드 톤", "미니멀한 실루엣").
이유: descriptions는 검색 임베딩 입력이라, 근거 없는 마케팅 문구가 섞이면 검색 정확도가 오염된다.
원문에 연예인 착용 정보가 실제로 있으면 — 그때만 그대로 기술한다 (지어내지 말 것).

[이미지 처리 — 엄격 규칙]
입력된 **모든** 이미지 sha1에 대해 다음 4가지를 빠짐없이 채워라:
1. image_type — main/lifestyle/detail/infographic/size_chart/color_options/spec_table/care_guide/certification/packaging/related_product/noise/other
2. is_main_product — true=본 상품 / false=다른 상품·배너·고객센터 안내·결제안내·추천상품 등
3. used_for_attributes — true=본 상품 정보 추출에 활용 / false=오염 방지 격리. is_main_product=false면 반드시 false.
4. rejection_reason — used_for_attributes=false인 사유 (해당 없으면 "")

[오염 방지 핵심 — 절대 규칙]
- 본 상품과 다른 상품의 정보(예: 호환 본체의 SPF, 추천 상품의 가격, 다른 상품의 사이즈/색상)를
  본 상품의 common_attributes / category_attributes / tags / descriptions에 **절대** 흡수하지 마라.
- 그런 정보는 ignored_observations 또는 related_products로 분리 기록.
- 같은 페이지에 보이더라도 본 상품이 아니면 본 상품 속성에 섞지 마라. (이게 임베딩 품질을 망친다.)
- 다관점 설명문(descriptions)은 used_for_attributes=true인 이미지 정보만 사용해서 작성.

[출력 — 엄격]
응답은 **JSON 한 덩어리만**. 머리말("분석했습니다", "결과를 작성하겠습니다" 등 모든 prelude) 금지.
코드펜스(```) 금지. 첫 글자는 무조건 `{`로 시작. 한국어로.
"""


def make_user_prompt(meta: dict, image_paths: list[Path]) -> str:
    """Claude `-p` 에 넣을 user prompt. 이미지 경로를 인라인으로 박아 넣으면 자동 첨부됨."""
    name = meta.get("title") or meta.get("url") or ""
    extracted_name = meta.get("product_name_extracted") or ""
    url = meta.get("url") or ""
    platform = meta.get("platform") or ""
    body_text = (meta.get("body_text") or "").strip()

    images_block = "\n".join(f"- {p.as_posix()}" for p in image_paths)

    return f"""다음 상품을 분석해 JSON을 출력하라.

[상품 기본 정보]
- 상품명: {name}
- URL: {url}
- 플랫폼: {platform}

[페이지 메타 / 추출 텍스트]
{extracted_name}

[본문 텍스트]
{body_text if body_text else "(텍스트 본문 없음 — 이미지로만 분석)"}

[이미지 파일 경로 — 모두 본 상품 페이지에서 수집된 것]
{images_block}

위 이미지 파일을 직접 열어서 보고, 각 이미지의 sha1(파일명에서 확장자 뺀 부분)을 기준으로 분류하라.
이미지 안에 다른 상품(추천/비교/안내)이 보이면 trap_notes로 따로 메모하고, 본 상품 속성으로 흡수하지 마라.
출력은 JSON 한 덩어리만, 코드펜스 없이.
"""


def resize_images(image_paths: list[Path], workdir: Path) -> list[Path]:
    """원본 이미지를 작업 디렉토리에 리사이즈 복사. 파일명(sha1) 유지."""
    workdir.mkdir(parents=True, exist_ok=True)
    out_paths: list[Path] = []
    for src in image_paths:
        try:
            with Image.open(src) as im:
                im = im.convert("RGB")
                w, h = im.size
                m = max(w, h)
                if m > IMG_MAX_DIM:
                    ratio = IMG_MAX_DIM / m
                    im = im.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
                dst = workdir / (src.stem + ".jpg")
                im.save(dst, "JPEG", quality=IMG_QUALITY, optimize=True)
                out_paths.append(dst)
        except Exception as e:
            print(f"[resize] skip {src.name}: {e}", file=sys.stderr)
    return out_paths


def _run_claude(cmd: list[str], input_text: str, timeout: int) -> subprocess.CompletedProcess:
    """claude.exe 실행 + 안정적 타임아웃.

    [왜 파이프 대신 임시파일을 쓰는가]
    claude.exe 는 내부적으로 자식 claude.exe 를 spawn 하며, 자식은 부모의
    stdout/stderr 파이프 쓰기 핸들을 상속한다. subprocess.communicate() 는
    파이프 EOF 를 기다리는데, 직접 자식이 죽어도 손자가 핸들을 쥐고 있으면
    EOF 가 안 와서 communicate() 가 timeout 과 무관하게 영구 블록된다.
    → stdin/stdout/stderr 를 모두 일반 파일로 연결하면 파이프 EOF 문제가
      사라지고, proc.wait(timeout) 은 순수하게 직접 자식의 종료만 기다리므로
      타임아웃이 확실히 동작한다. 타임아웃 시 taskkill /T 로 트리 전체 종료.
    """
    work = Path(tempfile.mkdtemp(prefix="claude_io_"))
    in_p, out_p, err_p = work / "in.txt", work / "out.txt", work / "err.txt"
    try:
        in_p.write_text(input_text, encoding="utf-8")
        with open(in_p, "r", encoding="utf-8") as fi, \
             open(out_p, "w", encoding="utf-8") as fo, \
             open(err_p, "w", encoding="utf-8") as fe:
            proc = subprocess.Popen(cmd, stdin=fi, stdout=fo, stderr=fe)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, check=False)
                try:
                    proc.wait(timeout=15)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                raise
        out = out_p.read_text(encoding="utf-8", errors="replace")
        err = err_p.read_text(encoding="utf-8", errors="replace")
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def call_claude(prompt: str, system_prompt: str, model: str | None = None) -> tuple[str, dict]:
    """claude -p 호출. JSON 출력은 prompt로만 강제 (cmdline 길이 한계 회피)."""
    # 스키마를 prompt에 인라인으로 박아넣어 모델이 따르도록 — cmdline 초과 방지
    schema_str = json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=2)
    full_payload = (
        f"{system_prompt}\n\n"
        f"=== 반드시 따라야 할 JSON 스키마 ===\n{schema_str}\n\n"
        f"=== INPUT ===\n{prompt}\n\n"
        f"=== 출력 ===\nJSON 한 덩어리만. 코드펜스(```) 없이. 위 스키마 따라."
    )

    chosen_model = model or os.environ.get("CLAUDE_MODEL", "sonnet")
    # shell 없이 claude.exe 직접 실행 — 타임아웃이 claude.exe 를 바로 죽여 고아 방지.
    cmd = [CLAUDE_BIN, "-p", "--model", chosen_model, "--output-format", "json"]

    # 일시적 오류 재시도 (최대 5회). 다음 경우를 transient 로 간주:
    #  - claude 실행파일 path resolution 실패 (영문/한글 OS 메시지)
    #  - 공유 설정파일 .claude.json 동시 읽기/쓰기 경쟁으로 인한 손상 읽힘
    #  - stdout 자체가 비어있음
    import time as _retry_time
    res = None
    _TRANSIENT_MARKERS = (
        "is not recognized", "cannot find",
        "내부 또는 외부 명령", "찾을 수 없",          # claude 명령 인식 실패 (한글)
        "configuration error", "unexpected eof",      # .claude.json 손상 읽힘
        ".claude.json",
    )
    # claude CLI 가 드물게 응답 없이 hang → subprocess 타임아웃으로 끊고 재시도.
    # 정상 enrich 는 3분 이내. 타임아웃 5분, hang 시 최대 3회만 재시도(과도한 대기 방지).
    call_timeout = int(os.environ.get("ENRICH_CALL_TIMEOUT", "300"))
    for attempt in range(5):
        try:
            res = _run_claude(cmd, full_payload, call_timeout)
        except subprocess.TimeoutExpired:
            print(f"[enrich_claude] claude hang/timeout ({call_timeout}s), "
                  f"attempt {attempt+1}", file=sys.stderr)
            if attempt < 2:
                _retry_time.sleep(1.0 * (attempt + 1))
                continue
            raise RuntimeError(f"claude call timed out after 3 attempts ({call_timeout}s each)")
        if res.returncode == 0 and res.stdout and res.stdout.strip():
            break
        err_snippet = (res.stderr or res.stdout or "")[:300]
        low = err_snippet.lower()
        transient = (any(m in low for m in _TRANSIENT_MARKERS)
                     or any(m in err_snippet for m in _TRANSIENT_MARKERS)
                     or not res.stdout)
        # 사용량 한도(429)는 transient 아님 — 즉시 빠져나가 RateLimitError 로.
        if '"api_error_status":429' in err_snippet or "hit your limit" in low:
            break
        if transient and attempt < 4:
            print(f"[enrich_claude] retry {attempt+1}/5 (transient: {err_snippet[:100]})", file=sys.stderr)
            _retry_time.sleep(1.0 * (attempt + 1))
            continue
        break

    # 디버깅용: 항상 stdout/stderr 덤프
    debug_dir = Path(os.environ.get("ENRICH_DEBUG_DIR", "D:/recommand-data/enrich_debug"))
    debug_dir.mkdir(parents=True, exist_ok=True)
    import time as _time
    ts = int(_time.time())
    (debug_dir / f"stdout_{ts}.txt").write_text(res.stdout or "", encoding="utf-8")
    (debug_dir / f"stderr_{ts}.txt").write_text(res.stderr or "", encoding="utf-8")
    (debug_dir / f"payload_{ts}.txt").write_text(full_payload, encoding="utf-8")
    print(f"[enrich_claude] debug saved: {debug_dir} (ts={ts})", file=sys.stderr)
    print(f"[enrich_claude] stdout len={len(res.stdout or '')}, stderr len={len(res.stderr or '')}, exit={res.returncode}", file=sys.stderr)

    # 사용량 한도 (429) 감지 — 배치 스크립트가 reset 시각까지 대기 후 재개
    combined = (res.stdout or "") + (res.stderr or "")
    if '"api_error_status":429' in combined or "hit your limit" in combined.lower():
        raise RateLimitError(
            f"claude usage limit reached: {combined[:200]}",
            _parse_reset_time(combined),
        )

    if res.returncode != 0:
        err_text = (res.stderr or res.stdout or "")[:500]
        raise RuntimeError(f"claude failed (exit {res.returncode}): {err_text}")
    if not res.stdout or not res.stdout.strip():
        raise RuntimeError(f"claude returned empty stdout. stderr: {(res.stderr or '')[:500]}")

    envelope = json.loads(res.stdout)
    raw_result = envelope.get("result") or envelope.get("response") or ""

    if isinstance(raw_result, dict):
        data = raw_result
    else:
        # 모델이 prelude("분석했습니다...") + ```json 코드펜스를 붙일 수 있음.
        # 1) 코드펜스 안의 JSON 우선 추출
        # 2) 없으면 첫 '{' 부터 마지막 '}' 까지 추출 (prelude 무시)
        import re as _re
        text = raw_result.strip()
        m = _re.search(r"```(?:json)?\s*\n?(.+?)\n?```", text, _re.DOTALL)
        if m:
            text = m.group(1).strip()
        elif "{" in text and "}" in text:
            start = text.index("{")
            end = text.rindex("}") + 1
            text = text[start:end]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 모델이 종종 JSON spec 위반 escape를 생성 (\' 같은 거).
            # JSON에서 허용되는 escape는: \" \\ \/ \b \f \n \r \t \uXXXX 뿐.
            # 그 외의 \X는 \X → X 로 정리 (예: \' → ', \a → a).
            cleaned = _re.sub(r'\\([^"\\\\/bfnrtu])', r'\1', text)
            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError as e2:
                try:
                    import json5
                    data = json5.loads(cleaned)
                except Exception:
                    raise RuntimeError(
                        f"JSON parse failed even after cleanup: {e2.msg} at pos {e2.pos}. "
                        f"Context: {cleaned[max(0,e2.pos-80):e2.pos+80]!r}"
                    )

    usage = envelope.get("usage") or {}
    # envelope의 modelUsage에서 실제 사용된 모델명 (claude-sonnet-4-6 등) 우선
    model_usage = envelope.get("modelUsage") or {}
    used_model = ""
    if model_usage:
        # 가장 큰 토큰 소비 모델
        used_model = max(model_usage.items(), key=lambda x: x[1].get("inputTokens", 0))[0]
    meta = {
        "model": used_model or envelope.get("model"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "cost_usd": envelope.get("total_cost_usd"),
        "duration_ms": envelope.get("duration_ms"),
        "raw": res.stdout,
    }
    return data, meta


def call_claude_filter(image_paths: list[Path]) -> set[str]:
    """Pass 1 — 빠른 이미지 필터.

    모든 이미지를 haiku에게 보내 '본 상품 관련 이미지 sha1 배열'만 받아온다.
    출력 토큰이 극히 적어 (이미지 수 × ~20토큰) 비용 미미.
    실패 시 전체 이미지 sha1 집합을 반환해 기존 동작 유지.
    """
    images_block = "\n".join(f"- {p.as_posix()}" for p in image_paths)
    prompt = f"""다음 이미지들을 보고 실제 판매 중인 본 상품에 관한 이미지의 sha1(파일명, 확장자 제외)만 JSON 배열로 출력하라.

포함: 상품 메인샷, 상세컷, 착용컷, 인포그래픽, 사이즈표, 색상옵션
제외: 배너, 결제/배송안내, 고객센터안내, 다른 상품 추천/비교, 브랜드 광고 이미지

이미지 파일 목록:
{images_block}

출력 규칙: JSON 배열만, 설명·머리말 없이. 예) ["abc123", "def456"]"""

    import time as _t
    fallback = {p.stem for p in image_paths}
    filter_timeout = int(os.environ.get("ENRICH_FILTER_TIMEOUT", "180"))
    res = None
    for attempt in range(2):
        try:
            res = _run_claude([CLAUDE_BIN, "-p", "--model", "haiku"], prompt, filter_timeout)
        except subprocess.TimeoutExpired:
            print(f"[enrich_claude] filter hang/timeout ({filter_timeout}s)", file=sys.stderr)
            res = None
        if res is not None and res.returncode == 0 and res.stdout and res.stdout.strip():
            break
        if attempt < 1:
            _t.sleep(0.3)

    if not res or res.returncode != 0 or not res.stdout:
        print("[enrich_claude] filter call failed, using all images", file=sys.stderr)
        return fallback

    import re as _re
    text = res.stdout.strip()
    m = _re.search(r'\[.*?\]', text, _re.DOTALL)
    if m:
        try:
            sha1_list = json.loads(m.group())
            result = {str(s) for s in sha1_list}
            print(f"[enrich_claude] filter: {len(image_paths)} → {len(result)} images kept", file=sys.stderr)
            return result if result else fallback
        except Exception:
            pass
    print("[enrich_claude] filter parse failed, using all images", file=sys.stderr)
    return fallback


def fetch_product(conn, rv_product_id: int) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, advertiser_id, product_code, product_name, html_path, image_dir
                 FROM rv_products WHERE id=%s""",
            (rv_product_id,),
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"rv_products id={rv_product_id} not found")
        cols = ["id", "advertiser_id", "product_code", "product_name", "html_path", "image_dir"]
        return dict(zip(cols, row))


def fetch_excluded_filenames(conn, advertiser_id: int, product_code: str) -> set[str]:
    """rv_product_excluded_images 의 image_filename (sha1.jpg) 집합."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT image_filename FROM rv_product_excluded_images WHERE advertiser_id=%s AND product_code=%s",
            (advertiser_id, product_code),
        )
        return {r[0] for r in cur.fetchall()}


def fetch_blocked_patterns(conn, advertiser_id: int) -> list[str]:
    """advertiser_image_blocks 의 enabled pattern 리스트. URL substring 매칭."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pattern FROM advertiser_image_blocks WHERE advertiser_id=%s AND enabled=true",
            (advertiser_id,),
        )
        return [r[0] for r in cur.fetchall() if r[0]]


def fetch_common_image_srcs(conn, advertiser_id: int, min_products: int = 3) -> set[str]:
    """광고주의 여러 상품(min_products개 이상)에 동일 URL로 등장하는 이미지 src 집합.

    이런 이미지는 사이트 공통 배너·광고·로고일 가능성이 높아 enrich 전 제외한다.
    extracted.json의 image_files[*].src 를 rv_product_images 테이블에서 집계한다.
    테이블이 없으면 빈 집합 반환.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT src_url
                FROM rv_product_images
                WHERE advertiser_id = %s
                GROUP BY src_url
                HAVING COUNT(DISTINCT product_code) >= %s
                """,
                (advertiser_id, min_products),
            )
            return {r[0] for r in cur.fetchall() if r[0]}
    except Exception:
        return set()


def upsert_enriched(conn, rv_product_id: int, prod: dict, data: dict, meta: dict, err: str | None):
    """v1.0 문서 스키마 → product_enriched 테이블 매핑."""
    ca = data.get("common_attributes") or {}
    cat = data.get("category") or {}
    # descriptions는 array → perspective별로 분해
    desc_map = {d.get("perspective"): d.get("description") for d in (data.get("descriptions") or [])}

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO product_enriched(
              rv_product_id, advertiser_id, product_code,
              category, category_confidence,
              brand, brand_tier, price_tier,
              target_gender, target_age_min, target_age_max,
              origin_country, manufacturer,
              category_attributes, compatible_products, set_components,
              tags,
              desc_situation, desc_material, desc_style, desc_persona,
              image_types,
              enrich_method, model_used, input_tokens, output_tokens,
              cache_read_input_tokens, cache_creation_input_tokens,
              cost_usd, duration_ms,
              raw_response, enrich_error
            ) VALUES (%s,%s,%s, %s,%s, %s,%s,%s, %s,%s,%s, %s,%s,
                      %s::jsonb,%s::jsonb,%s::jsonb,
                      %s::jsonb,
                      %s,%s,%s,%s,
                      %s::jsonb,
                      %s,%s,%s,%s,
                      %s,%s,
                      %s,%s,
                      %s,%s)
            ON CONFLICT (rv_product_id) DO UPDATE SET
              category=EXCLUDED.category,
              category_confidence=EXCLUDED.category_confidence,
              brand=EXCLUDED.brand, brand_tier=EXCLUDED.brand_tier, price_tier=EXCLUDED.price_tier,
              target_gender=EXCLUDED.target_gender,
              target_age_min=EXCLUDED.target_age_min, target_age_max=EXCLUDED.target_age_max,
              origin_country=EXCLUDED.origin_country, manufacturer=EXCLUDED.manufacturer,
              category_attributes=EXCLUDED.category_attributes,
              compatible_products=EXCLUDED.compatible_products,
              set_components=EXCLUDED.set_components,
              tags=EXCLUDED.tags,
              desc_situation=EXCLUDED.desc_situation, desc_material=EXCLUDED.desc_material,
              desc_style=EXCLUDED.desc_style, desc_persona=EXCLUDED.desc_persona,
              image_types=EXCLUDED.image_types,
              enrich_method=EXCLUDED.enrich_method, model_used=EXCLUDED.model_used,
              input_tokens=EXCLUDED.input_tokens, output_tokens=EXCLUDED.output_tokens,
              cache_read_input_tokens=EXCLUDED.cache_read_input_tokens,
              cache_creation_input_tokens=EXCLUDED.cache_creation_input_tokens,
              cost_usd=EXCLUDED.cost_usd, duration_ms=EXCLUDED.duration_ms,
              raw_response=EXCLUDED.raw_response, enrich_error=EXCLUDED.enrich_error,
              enriched_at=NOW()
            """,
            (
                rv_product_id, prod["advertiser_id"], prod["product_code"],
                cat.get("name") or cat.get("path"), cat.get("confidence"),
                ca.get("brand"), ca.get("brand_tier"), ca.get("price_tier"),
                ca.get("target_gender"), ca.get("target_age_min"), ca.get("target_age_max"),
                ca.get("origin_country"), ca.get("manufacturer"),
                json.dumps(data.get("category_attributes") or {}, ensure_ascii=False),
                json.dumps(data.get("related_products") or [], ensure_ascii=False),
                json.dumps(data.get("set_components") or [], ensure_ascii=False),
                json.dumps(data.get("tags") or [], ensure_ascii=False),
                desc_map.get("situation"), desc_map.get("material"),
                desc_map.get("style"), desc_map.get("persona"),
                json.dumps(data.get("images") or [], ensure_ascii=False),
                "claude_cli", meta.get("model"),
                meta.get("input_tokens"), meta.get("output_tokens"),
                meta.get("cache_read_input_tokens"), meta.get("cache_creation_input_tokens"),
                meta.get("cost_usd"), meta.get("duration_ms"),
                meta.get("raw"), err,
            ),
        )
    conn.commit()


def run_enrich_one(
    rv_product_id: int,
    *,
    model: str | None = None,
    image_limit: int | None = None,
    image_sha1: str | None = None,
    save_db: bool = True,
    keep_workdir: bool = False,
    conn=None,
) -> dict:
    """단일 상품 enrich 실행 (CLI/HTTP 공용 진입점).

    Returns: 추출된 data dict (DB 저장 여부는 save_db로).
    """
    own_conn = conn is None
    if conn is None:
        conn = psycopg.connect(DB_DSN)
    try:
        prod = fetch_product(conn, rv_product_id)
        extracted_path = Path(prod["image_dir"]).parent / "extracted.json"
        if not extracted_path.exists():
            raise RuntimeError(f"extracted.json not found: {extracted_path}")
        with open(extracted_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        img_files = meta.get("image_files") or []

        # 오염 방지: 세 가지 필터를 LLM에 보내기 전에 미리 제거
        # 1) rv_product_excluded_images — 사용자가 모달에서 [제외] 처리한 개별 이미지
        # 2) advertiser_image_blocks — URL substring 패턴으로 차단된 이미지
        # 3) rv_product_images cross-product — 여러 상품에 공통으로 등장하는 배너·광고 이미지
        excluded_names = fetch_excluded_filenames(conn, prod["advertiser_id"], prod["product_code"])
        blocked_patterns = fetch_blocked_patterns(conn, prod["advertiser_id"])
        common_srcs = fetch_common_image_srcs(conn, prod["advertiser_id"], min_products=3)

        def _is_kept(im: dict) -> tuple[bool, str | None]:
            local = im.get("local_path") or ""
            src = im.get("src") or ""
            fname = Path(local).name
            if fname in excluded_names:
                return False, f"excluded image:{fname}"
            for pat in blocked_patterns:
                if pat and src and pat in src:
                    return False, f"url_block pattern:{pat}"
            if src in common_srcs:
                return False, f"cross-product duplicate:{src[:60]}"
            return True, None

        kept_files: list[Path] = []
        skipped: list[str] = []
        for im in img_files:
            ok, reason = _is_kept(im)
            p = Path(im.get("local_path") or "")
            if not p.exists():
                continue
            if not ok:
                skipped.append(f"{p.name} ({reason})")
                continue
            kept_files.append(p)

        if skipped:
            print(f"[enrich_claude] filtered out {len(skipped)} images (excluded/blocked): {skipped[:5]}{'...' if len(skipped)>5 else ''}", file=sys.stderr)

        img_paths = kept_files
        if image_sha1:
            img_paths = [p for p in img_paths if p.stem == image_sha1]
            if not img_paths:
                raise RuntimeError(f"image with sha1={image_sha1} not found (또는 excluded/blocked)")
        limit = image_limit if image_limit is not None else MAX_IMAGES
        img_paths = img_paths[:limit]

        # 빈 상품 빠른 스킵 — 스크랩된 이미지가 0장이면 claude 호출 자체가 무의미.
        # (이미지·본문 모두 없는 폐기/품절 상품. quota 낭비 방지.)
        if not img_paths:
            raise RuntimeError("스크랩된 이미지 없음 — enrich 불가 (빈 상품, claude 호출 생략)")

        workdir = Path(tempfile.mkdtemp(prefix=f"enrich_{prod['product_code']}_"))
        try:
            resized = resize_images(img_paths, workdir)

            # Pass 1: 빠른 이미지 필터 — 비상품 이미지 미리 제거 (이미지 4장 초과 시만)
            if len(resized) > 4:
                keep_sha1s = call_claude_filter(resized)
                filtered = [p for p in resized if p.stem in keep_sha1s]
                resized = filtered if filtered else resized  # 전부 걸러지면 원본 사용

            # Pass 2: 필터된 이미지로 전체 분석
            prompt = make_user_prompt(meta, resized)
            data, call_meta = call_claude(prompt, SYSTEM_PROMPT, model=model)
            if save_db:
                upsert_enriched(conn, rv_product_id, prod, data, call_meta, err=None)
            return {
                "rv_product_id": rv_product_id,
                "product_code": prod["product_code"],
                "data": data,
                "meta": {k: v for k, v in call_meta.items() if k != "raw"},
                "n_images": len(resized),
            }
        finally:
            if not keep_workdir:
                shutil.rmtree(workdir, ignore_errors=True)
    finally:
        if own_conn:
            conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rv_product_id", type=int)
    ap.add_argument("--no-db", action="store_true", help="DB 적재 생략 (JSON만 출력)")
    ap.add_argument("--keep-workdir", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="이미지 N장만 보내기 (MAX_IMAGES 오버라이드)")
    ap.add_argument("--image-sha1", type=str, default=None, help="특정 sha1 이미지만 (테스트용)")
    ap.add_argument("--model", type=str, default=None, help="claude 모델 (haiku/sonnet/opus)")
    args = ap.parse_args()

    print(f"[enrich_claude] rv_product_id={args.rv_product_id} model={args.model or '(env)'}", file=sys.stderr)
    result = run_enrich_one(
        args.rv_product_id,
        model=args.model,
        image_limit=args.limit,
        image_sha1=args.image_sha1,
        save_db=not args.no_db,
        keep_workdir=args.keep_workdir,
    )
    print(json.dumps(result["data"], ensure_ascii=False, indent=2))
    print(f"[enrich_claude] done (images={result['n_images']}, saved_db={not args.no_db})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
