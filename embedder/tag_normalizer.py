"""
태그 정규화 규칙 단일 모듈.

모든 태그 정규화는 이 파일에서 관리한다.
enrich_claude.py, normalize_tags_db.py, API 엔드포인트 모두 여기서 임포트.

규칙 (순서 보장):
  1. 앞뒤 공백 제거
  2. 내부 공백 제거   → "큐빅 지르코니아" → "큐빅지르코니아"
  3. 소문자 통일      → "14K", "2WAY", "K-Fashion" → "14k", "2way", "k-fashion"
  4. 불필요한 괄호 제거 → "귀걸이(set)" → "귀걸이(set)" → "귀걸이set"
  5. 앞뒤 특수문자 strip → "-실버-" → "실버"
  6. 빈 문자열 필터링

규칙 추가 시 _apply_rules() 안에만 추가하면 된다.
"""

import re


def _apply_rules(s: str) -> str:
    s = s.strip()
    s = re.sub(r"\s+", "", s)        # 내부 공백 전부 제거
    s = s.lower()                     # ASCII 소문자 통일 (한글은 영향 없음)
    s = re.sub(r"[()[\]{}]", "", s)  # 괄호류 제거
    s = s.strip("-._,!?/|")          # 앞뒤 특수문자 제거
    return s


def normalize_tag_str(s: str) -> str:
    """태그 문자열 하나를 정규화. 빈 결과면 빈 문자열 반환."""
    if not isinstance(s, str):
        return ""
    return _apply_rules(s)


def normalize_tags(tags: list) -> list:
    """
    LLM이 반환한 tags 배열 정규화.
    - 문자열 태그, 객체 태그({"tag": ..., ...}) 모두 처리
    - 정규화 후 중복 제거 (먼저 나온 것 유지)
    """
    seen: set[str] = set()
    result = []
    for t in tags:
        if isinstance(t, str):
            norm = normalize_tag_str(t)
            if norm and norm not in seen:
                seen.add(norm)
                result.append(norm)
        elif isinstance(t, dict) and "tag" in t:
            norm = normalize_tag_str(t["tag"])
            if norm and norm not in seen:
                seen.add(norm)
                result.append({**t, "tag": norm})
    return result
