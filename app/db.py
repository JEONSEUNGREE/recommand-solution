import os
import psycopg
from pgvector.psycopg import register_vector

DB_DSN = os.getenv("DB_DSN", "postgresql://app:app@localhost:5433/recommend")


def get_conn():
    conn = psycopg.connect(DB_DSN, autocommit=True)
    register_vector(conn)
    return conn
