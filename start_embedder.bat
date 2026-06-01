@echo off
"D:\WORKSPACE\상품추천\recommand-solution\.venv\Scripts\python.exe" -m uvicorn embedder.server:app --host 0.0.0.0 --port 8001
