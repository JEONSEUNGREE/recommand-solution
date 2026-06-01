# 로컬 실행 명령어 모음

각 서비스를 native(Postgres만 Docker)로 띄우는 명령어. PowerShell 기준.

| 서비스 | 포트 | 비고 |
|--------|------|------|
| Postgres | 5433 | Docker (`recommend-pg`) |
| Backend (Spring) | 8090 | gradle bootRun |
| Embedder (BGE-M3) | 8001 | uvicorn .venv |
| Sparse Embedder | 8002 | uvicorn |
| ColBERT Reranker | 8003 | uvicorn |
| Frontend | 5500 | 정적 서버 |

기동 순서: **Postgres → Embedder/Sparse/ColBERT → Backend → Frontend**

---

## 0. Postgres (Docker)

```powershell
docker start recommend-pg
# 헬스체크
docker exec recommend-pg pg_isready -U app -d recommend
```

---

## 1. Backend (Spring Boot)

> ⚠️ `C:\Users\pc\.gradle` 경로에서 transforms 버그가 나서 **반드시 프로젝트-로컬 GRADLE_USER_HOME** 사용.

### 실행 (백그라운드 + 로그 파일)

```powershell
$env:GRADLE_USER_HOME = "D:\WORKSPACE\상품추천\recommand-solution\.gradle-home"
Set-Location "D:\WORKSPACE\상품추천\recommand-solution\backend"
Start-Process -FilePath ".\gradlew.bat" `
  -ArgumentList "bootRun","--no-daemon" `
  -RedirectStandardOutput "D:\WORKSPACE\상품추천\recommand-solution\backend-run.log" `
  -RedirectStandardError  "D:\WORKSPACE\상품추천\recommand-solution\backend-run.err.log" `
  -PassThru -NoNewWindow | Select-Object Id, ProcessName, StartTime
```

### 부팅 완료 확인

```powershell
# 로그에서 startup 시그널 대기 (sh / Git Bash)
until grep -qE "Started .*Application|APPLICATION FAILED|BUILD FAILED" backend-run.log; do sleep 2; done
tail -20 backend-run.log
```

성공 시 로그:
```
Tomcat started on port 8090 (http)
Started RecommendApiApplication in 3.6 seconds
```

### 헬스체크
```powershell
curl http://localhost:8090/actuator/health
```

### 종료
```powershell
# PID 확인
Get-Process java | Where-Object { $_.MainWindowTitle -like "*bootRun*" -or $_.Path -like "*gradle*" }
# 또는 8090 점유 프로세스
Get-NetTCPConnection -LocalPort 8090 | Select-Object OwningProcess
Stop-Process -Id <PID> -Force
```

---

## 2. Embedder (BGE-M3, FastAPI)

> ⚠️ **반드시 `.venv\Scripts\python.exe` 사용** (시스템 python으로는 uvicorn 모듈 못 찾음).
> ⚠️ **`--host 0.0.0.0`** — 127.0.0.1로 띄우면 브라우저(192.168.x)에서 호출 실패.

```powershell
Set-Location "D:\WORKSPACE\상품추천\recommand-solution"
.\embedder\.venv\Scripts\python.exe -m uvicorn embedder.server:app `
  --host 0.0.0.0 --port 8001
```

### 백그라운드 + 로그
```powershell
Set-Location "D:\WORKSPACE\상품추천\recommand-solution"
Start-Process -FilePath ".\embedder\.venv\Scripts\python.exe" `
  -ArgumentList "-m","uvicorn","embedder.server:app","--host","0.0.0.0","--port","8001" `
  -RedirectStandardOutput "embedder-run.log" `
  -RedirectStandardError  "embedder-run.err.log" `
  -PassThru -NoNewWindow
```

### 헬스체크
```powershell
curl http://localhost:8001/healthz
```

---

## 3. Sparse Embedder (BGE-M3 sparse)

```powershell
Set-Location "D:\WORKSPACE\상품추천\recommand-solution"
.\sparse-embedder\.venv\Scripts\python.exe -m uvicorn sparse-embedder.server:app `
  --host 0.0.0.0 --port 8002
```

---

## 4. ColBERT Reranker

```powershell
Set-Location "D:\WORKSPACE\상품추천\recommand-solution"
.\colbert-reranker\.venv\Scripts\python.exe -m uvicorn colbert-reranker.server:app `
  --host 0.0.0.0 --port 8003
```

---

## 5. Frontend (정적 서버)

```powershell
Set-Location "D:\WORKSPACE\상품추천\recommand-solution\frontend\public"
python -m http.server 5500
```

접속:
- 일반: http://localhost:5500/admin/chat2.html
- LAN: http://192.168.101.27:5500/admin/chat2.html

---

## 트러블슈팅

### "No module named uvicorn"
→ 시스템 python으로 실행했을 때. `.venv\Scripts\python.exe`로 다시.

### 백엔드가 Flyway 마이그레이션에서 죽음
→ `flyway_schema_history`에 010~021을 applied로 직접 INSERT (memory: flyway_dump_history_mismatch.md 참고)

### gradle transforms 에러 (`C:\Users\pc\.gradle`)
→ 반드시 `$env:GRADLE_USER_HOME` 프로젝트-로컬 경로 설정 후 실행.

### 임베더가 LAN에서 안 보임
→ `--host 0.0.0.0` 인지 확인. `127.0.0.1`이면 LAN 차단됨.

---

## DB 백업/복원

### 백업 (커스텀 포맷, 압축)
```powershell
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
docker exec recommend-pg pg_dump -U app -d recommend -F c -f /tmp/recommend_backup.dump
docker cp recommend-pg:/tmp/recommend_backup.dump "D:/recommand-data/backups/recommend_$ts.dump"
```

### 복원
```powershell
docker cp "D:/recommand-data/backups/recommend_XXXX.dump" recommend-pg:/tmp/restore.dump
docker exec recommend-pg pg_restore -U app -d recommend --clean --if-exists /tmp/restore.dump
```
