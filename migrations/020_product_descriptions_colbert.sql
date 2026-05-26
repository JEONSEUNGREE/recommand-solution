-- ColBERT 토큰 벡터 컬럼 추가
-- BGE-M3 ColBERT 128d 기준: 행당 ~30KB, 4,224행 전체 ~127MB
-- jsonb로 저장: [[tok1_f1,...,tok1_f128], [tok2_f1,...], ...]

ALTER TABLE product_descriptions
    ADD COLUMN IF NOT EXISTS colbert_vecs jsonb;

-- 인덱스 없음 — colbert_vecs는 top-K 후보에만 접근하므로 풀스캔 불필요.
-- 검색 흐름: dense ANN → top-50 → colbert_vecs 포인트 조회(PK) → MaxSim 계산
