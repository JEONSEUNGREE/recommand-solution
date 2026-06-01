$env:CLAUDE_BIN = "C:\Users\pc\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe"
& "D:\WORKSPACE\상품추천\recommand-solution\.venv\Scripts\python.exe" -m uvicorn embedder.server:app --host 0.0.0.0 --port 8001 --app-dir "D:\WORKSPACE\상품추천\recommand-solution"
