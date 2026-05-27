import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "AIRA · 로딩 모션 갤러리",
  description: "검색 대기 화면용 로딩/스켈레톤 모션 컨셉 모음 (게이지·요리사·탐정 등)",
};

// 정적 퍼블(public/pub/loading/index.html)을 깔끔한 /pub/loading URL로 노출.
// 갤러리 마크업은 정적 HTML을 단일 출처로 유지하고, 이 라우트는 풀스크린 iframe으로 감싼다.
export default function LoadingGalleryPage() {
  return (
    <iframe
      src="/pub/loading/index.html"
      title="로딩 모션 갤러리"
      style={{ position: "fixed", inset: 0, width: "100vw", height: "100vh", border: 0 }}
    />
  );
}
