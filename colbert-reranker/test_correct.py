import sys
sys.stdout.reconfigure(encoding="utf-8")
import requests

for q in ["겨울에 따듯하게 입을 니투", "데이트할때 입기조은 원피스", "귀걸이"]:
    r = requests.post("http://localhost:8001/search-rv-llm",
                      json={"query": q, "k": 3, "backend": "bge", "llm_provider": "openai"},
                      timeout=120)
    j = r.json()
    print(f"원문    : {j.get('raw_query')}")
    print(f"보정    : {j.get('corrected_query')}")
    print(f"semantic: {j.get('parsed', {}).get('semantic_query')}")
    print(f"notes   : {j.get('parsed', {}).get('notes')}")
    print(f"provider: {j.get('parsed', {}).get('provider')}")
    print(f"결과    : {len(j.get('items', []))}건")
    print("-" * 50)
