import time, traceback

OUT = r"D:\WORKSPACE\상품추천\recommand-solution\bench.txt"

def log(msg):
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(str(msg) + "\n")

open(OUT, "w", encoding="utf-8").close()
try:
    log("step: importing FlagEmbedding")
    from FlagEmbedding import BGEM3FlagModel
    log("step: loading model")
    t0 = time.time()
    m = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False)
    log(f"load_s={round(time.time()-t0,1)}")

    texts = ["봄 데이트에 어울리는 14k 하트 드롭 귀걸이로 우아한 분위기를 더합니다"] * 50
    t2 = time.time()
    out = m.encode(texts, batch_size=16, return_dense=True, return_sparse=True, return_colbert_vecs=False)
    dt = time.time() - t2
    rate = 50 / dt
    lw = out["lexical_weights"][0]
    log(f"rate_per_s={round(rate,1)}")
    log(f"est_4224_min={round(4224/rate/60,1)}")
    log(f"dense_shape={out['dense_vecs'].shape}")
    log(f"sparse_nonzero={len(lw)}")
    log("OK_DONE")
except Exception:
    log("EXC:\n" + traceback.format_exc())
