# DB Dump

`recommend.dump`은 `pg_dump -Fc`로 뜬 Postgres custom format. 10k 의류 상품 + bge-m3 임베딩(1024 dim) 포함.

## 복원

```bash
# 1) docker compose로 빈 Postgres 띄우기
docker compose up -d

# 2) 마이그레이션 비활성화한 채 빈 DB로 시작하려면 컨테이너 재생성 후
#    pgvector 확장만 보장한 다음 dump 복원 (custom format은 스키마/데이터 모두 포함)
docker exec -i recommend-pg pg_restore -U app -d recommend --clean --if-exists < db/recommend.dump
```

> `migrations/001_init.sql`이 entrypoint로 자동 실행되므로, 깨끗한 상태에서 복원하려면
> 컨테이너 볼륨(`pgdata`)을 한 번 비우고 다시 올리거나, `--clean --if-exists`로 덮어쓰기.
