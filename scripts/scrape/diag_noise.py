"""광고주 한 상품의 source.html에서 추천상품/배너/노이즈 영역의 ID·class 식별."""
from __future__ import annotations
import sys
from collections import Counter
from pathlib import Path
from bs4 import BeautifulSoup, Tag


def main(path: str) -> None:
    soup = BeautifulSoup(Path(path).read_text(encoding="utf-8"), "lxml")
    pd = soup.select_one("#productDetail")
    if pd is None:
        print("#productDetail 없음"); return

    print("=== #productDetail 직계 자식들 (img/text 분포) ===")
    for i, child in enumerate(pd.children, 1):
        if not isinstance(child, Tag): continue
        label = child.name
        if child.get("id"): label += f"#{child.get('id')}"
        cls = child.get("class")
        if cls: label += "." + ".".join(cls[:3])
        imgs = len(child.find_all("img"))
        text_len = len(child.get_text(" ", strip=True))
        if imgs >= 2 or text_len >= 50:
            print(f"  [{i:2}] {label}  img={imgs}  text_len={text_len}")
            print(f"        text preview: {child.get_text(' ', strip=True)[:100]}")

    print()
    print("=== 추천/관련 의심 영역 (id/class에 키워드 매치) ===")
    keywords = ["related", "recommend", "relation", "another", "etc",
                "추천", "관련", "같이", "이상품", "함께",
                "ItemDetail", "itemDetail", "M_default_relation"]
    seen = set()
    for el in pd.find_all(True):
        ident_id = el.get("id", "")
        ident_cls = " ".join(el.get("class") or [])
        ident = ident_id + " " + ident_cls
        if not ident.strip(): continue
        for kw in keywords:
            if kw.lower() in ident.lower():
                key = (el.name, ident_id, ident_cls)
                if key in seen: continue
                seen.add(key)
                label = el.name
                if ident_id: label += f"#{ident_id}"
                if ident_cls: label += "." + ".".join((el.get("class") or [])[:3])
                imgs = len(el.find_all("img"))
                text_len = len(el.get_text(" ", strip=True))
                print(f"  {label}  img={imgs} text={text_len}  matched='{kw}'")
                break

    print()
    print("=== div의 id/class 빈도 상위 30 (전체 페이지) ===")
    cnt = Counter()
    for el in soup.find_all("div"):
        ident_id = el.get("id", "")
        ident_cls = " ".join(el.get("class") or [])
        if ident_id or ident_cls:
            key = (ident_id, ident_cls)
            cnt[key] += 1
    for (i, c), n in cnt.most_common(30):
        print(f"  {n:>3}  id='{i}' class='{c}'")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "D:/recommand-data/advertisers/1/products/2280472/source.html")
