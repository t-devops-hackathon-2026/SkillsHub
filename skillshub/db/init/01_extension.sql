-- 初回起動時（空ボリューム）に一度だけ実行される。
-- pgvector 拡張を有効化するだけ。テーブル定義(DDL)は別 issue（DB スキーマ）で管理する。
CREATE EXTENSION IF NOT EXISTS vector;
