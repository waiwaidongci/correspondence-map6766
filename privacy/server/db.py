"""SQLite 持久化层（仅标准库）。

所有写操作串行化：进程内写锁 + SQLite 事务，保证并发发布时
后一个发布基于前一个已提交的公开信息合并判断，而不是各自安全、合起来泄露。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(os.environ.get("PRIVACY_DB_PATH",
                              Path(__file__).resolve().parent.parent / "data" / "privacy.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_write_lock = threading.RLock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS studies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT '',
  row_labels TEXT NOT NULL,          -- JSON list[str]
  col_labels TEXT NOT NULL,          -- JSON list[str]
  base_table TEXT NOT NULL,          -- JSON list[list[int]]
  threshold INTEGER NOT NULL CHECK(threshold >= 1),
  status TEXT NOT NULL DEFAULT 'active',   -- active | retired
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  revised_at TEXT
);

CREATE TABLE IF NOT EXISTS publications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  study_id INTEGER NOT NULL REFERENCES studies(id),
  seq INTEGER NOT NULL,               -- 研究内发布序号
  title TEXT NOT NULL,
  row_groups TEXT NOT NULL,           -- JSON list[list[int]]
  col_groups TEXT NOT NULL,
  suppressed_blocks TEXT NOT NULL,    -- JSON [[ri,cj],...] 分组块坐标
  protected_cells TEXT NOT NULL,      -- JSON [[i,j],...] 底表保护格
  released_table TEXT NOT NULL,       -- JSON 脱敏分组计数（null 表示隐去）
  row_marginals TEXT NOT NULL,        -- JSON 各组行合计
  col_marginals TEXT NOT NULL,        -- JSON 各组列合计
  grand_total INTEGER NOT NULL,
  ranges_json TEXT NOT NULL,          -- JSON 逐保护格可行整数范围（含本次）
  safe INTEGER NOT NULL,              -- 1=可发布 0=拒绝
  reject_reasons TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pub_study ON publications(study_id, seq);
"""


def init_db() -> None:
    with _write_lock, _connect() as conn:
        conn.executescript(SCHEMA)


def transaction():
    """返回一个已加进程写锁的连接上下文；异常回滚。"""
    return _Tx()


class _Tx:
    def __enter__(self) -> sqlite3.Connection:
        _write_lock.acquire()
        self.conn = _connect()
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()
            _write_lock.release()


class _ReadTx:
    """只读事务：不占写锁，写事务在 BEGIN IMMEDIATE 下互斥，读到的都是已提交快照。"""
    def __enter__(self) -> sqlite3.Connection:
        self.conn = _connect()
        self.conn.execute("BEGIN")
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        self.conn.rollback()
        self.conn.close()


def read_transaction():
    return _ReadTx()


# ---------- 序列化助手 ----------
def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def loads(text: str):
    return json.loads(text)


def study_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "row_labels": loads(row["row_labels"]),
        "col_labels": loads(row["col_labels"]),
        "base_table": loads(row["base_table"]),
        "threshold": row["threshold"],
        "status": row["status"],
        "created_at": row["created_at"],
        "revised_at": row["revised_at"],
    }


def pub_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "study_id": row["study_id"],
        "seq": row["seq"],
        "title": row["title"],
        "row_groups": loads(row["row_groups"]),
        "col_groups": loads(row["col_groups"]),
        "suppressed_blocks": loads(row["suppressed_blocks"]),
        "protected_cells": loads(row["protected_cells"]),
        "released_table": loads(row["released_table"]),
        "row_marginals": loads(row["row_marginals"]),
        "col_marginals": loads(row["col_marginals"]),
        "grand_total": row["grand_total"],
        "ranges": loads(row["ranges_json"]),
        "safe": bool(row["safe"]),
        "reject_reasons": loads(row["reject_reasons"]),
        "created_at": row["created_at"],
    }
