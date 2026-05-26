"""특정 source.html에서 이미지가 어디에 몰려있는지 진단.
어떤 lazy 속성이 쓰이는지, 어느 컨테이너에 진짜 상세가 있는지 확인."""
from __future__ import annotations
import sys
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup, Tag


def main(path: str) -> None:
    raw = Path(path).read_text(encoding="utf-8")
    soup = BeautifulSoup(raw, "lxml")

    # 1) 전체 img 통계
    all_imgs = soup.find_all("img")
    print(f"전체 <img> 태그 수: {len(all_imgs)}")

    # 2) src 속성 종류 (lazy loading 패턴)
    src_attrs = Counter()
    for img in all_imgs:
        for attr in img.attrs:
            if "src" in attr or "data" in attr or "lazy" in attr or "original" in attr:
                if img.get(attr):
                    src_attrs[attr] += 1
    print(f"\nimg에 쓰인 src류 속성:")
    for k, v in src_attrs.most_common():
        print(f"  {k}: {v}")

    # 3) 컨테이너별 이미지 수 (큰 div 위주)
    print(f"\n이미지가 5장 이상 있는 div/section/article:")
    for el in soup.find_all(["div", "section", "article", "table"]):
        imgs = el.find_all("img", recursive=False)  # 직계만
        if len(imgs) >= 5:
            label = el.name
            if el.get("id"): label += f"#{el.get('id')}"
            cls = el.get("class")
            if cls: label += "." + ".".join(cls[:2])
            text_len = len(el.get_text(" ", strip=True))
            print(f"  {label}: 직계 img={len(imgs)}, 전체 img={len(el.find_all('img'))}, text_len={text_len}")

    # 4) #productDetail vs 다른 컨테이너 비교
    print(f"\n알려진 컨테이너별 이미지 수:")
    for sel in ["#productDetail", "#prd_detail", "#detailpageWrap", ".prd_detail", "#contents", ".productDetail"]:
        el = soup.select_one(sel)
        if el is None:
            print(f"  {sel}: NOT FOUND")
            continue
        imgs = el.find_all("img")
        print(f"  {sel}: total_img={len(imgs)}, text_len={len(el.get_text(' ', strip=True))}")

    # 5) 우리 코드가 src로 뽑는 속성 시뮬레이션
    print(f"\n우리 코드가 뽑는 src (ec-data-src | data-src | src) 기준:")
    extracted = 0
    no_src = 0
    examples = []
    pd = soup.select_one("#productDetail")
    if pd:
        for img in pd.find_all("img"):
            src = img.get("ec-data-src") or img.get("data-src") or img.get("src")
            if src and not src.startswith("data:"):
                extracted += 1
                if len(examples) < 5:
                    examples.append((list(img.attrs.keys()), src[:120]))
            else:
                no_src += 1
        print(f"  #productDetail 안에서: 추출가능={extracted}, src없음={no_src}")
        print(f"  예시 (속성목록 / src):")
        for attrs, src in examples:
            print(f"    {attrs}\n      → {src}")

    # 6) lazy 패턴이 잡힌 img들의 실제 src 확인
    print(f"\n다른 lazy 속성을 가진 img 샘플:")
    extra_attrs = ["data-original", "data-lazy", "data-srcset", "data-image", "_src", "lazyload"]
    for attr in extra_attrs:
        with_attr = [img for img in all_imgs if img.get(attr)]
        if with_attr:
            print(f"  {attr}: {len(with_attr)} 개, 예시 src='{with_attr[0].get(attr)[:120]}'")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "D:/recommand-data/advertisers/1/products/2280472/source.html")
