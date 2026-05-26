-- BGE-M3 하이브리드(dense + sparse) 검색을 위한 sparse 임베딩 컬럼.
-- BGE-M3는 dense(1024d)·sparse(lexical weights)·colbert(multi-vector) 세 표현을
-- 단일 forward pass로 출력한다. 여기서는 sparse(어휘 가중치)를 추가 보관한다.
--
-- sparse 차원 = BGE-M3 토크나이저(XLM-RoBERTa) vocab 크기 = 250002.
-- 대부분 0이고 등장 토큰 자리에만 가중치가 있는 희소 벡터(sparsevec).
-- 검색은 내적(<#>)으로 어휘 중첩 점수를 구한 뒤 dense 코사인과 가중합한다.

ALTER TABLE product_descriptions
    ADD COLUMN IF NOT EXISTS embedding_sparse sparsevec(250002);

-- 행 수가 수천 단위라 brute-force 스캔으로 충분. 별도 인덱스는 두지 않는다.
-- (sparsevec HNSW는 pgvector 0.8에서 지원하나 이 규모에선 불필요.)
