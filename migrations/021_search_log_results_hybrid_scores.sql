-- 하이브리드 검색 결과의 신호별 점수를 검색이력에 저장.
-- 이력 클릭 시 사유 팝업에서 dense/sparse/형태소/융합점수를 "미저장" 없이 그대로 표시.
-- (매칭 단어 상세는 query+doc 로 실시간 재계산하므로 별도 저장 불필요.)

ALTER TABLE search_log_results
    ADD COLUMN IF NOT EXISTS dense_dist   real,   -- dense 코사인거리 (<=>)
    ADD COLUMN IF NOT EXISTS sparse_ip    real,   -- BGE sparse 내적 (<#>)
    ADD COLUMN IF NOT EXISTS morph_ip     real,   -- 형태소 sparse 내적 (<#>)
    ADD COLUMN IF NOT EXISTS fusion_score real,   -- 최종 융합 점수
    ADD COLUMN IF NOT EXISTS colbert_score real,  -- ColBERT MaxSim 점수 (rerank 시)
    ADD COLUMN IF NOT EXISTS rank_dense   integer,
    ADD COLUMN IF NOT EXISTS rank_sparse  integer,
    ADD COLUMN IF NOT EXISTS rank_morph   integer;
