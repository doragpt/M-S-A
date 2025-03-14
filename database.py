import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import sqlite3
import datetime

# 環境変数から DATABASE_URL を取得、なければ SQLite を使用
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///store_data.db')

# SQLAlchemy のエンジンを作成
engine = create_engine(DATABASE_URL, echo=True)

# セッションの作成
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# データベース接続を取得する関数
def get_db_connection():
    """データベース接続を取得する関数"""
    # detect_types=sqlite3.PARSE_DECLTYPESを追加してdatetimeを適切に処理
    conn = sqlite3.connect('store_data.db', detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES)
    conn.row_factory = sqlite3.Row

    # datetime型のアダプターとコンバーターを登録
    def adapt_datetime(dt):
        if dt is None:
            return None
        return dt.isoformat()

    def convert_datetime(s):
        if s is None:
            return None
        try:
            if isinstance(s, bytes):
                return datetime.datetime.fromisoformat(s.decode())
            elif isinstance(s, str):
                return datetime.datetime.fromisoformat(s)
            return s
        except (ValueError, TypeError):
            # ISO形式でない場合は文字列として返す
            if isinstance(s, bytes):
                return s.decode()
            return s

    # SQLiteにカスタムの変換関数を登録
    sqlite3.register_adapter(datetime.datetime, adapt_datetime)
    sqlite3.register_converter("timestamp", convert_datetime)
    sqlite3.register_converter("datetime", convert_datetime)

    # 外部キー制約を有効化
    conn.execute("PRAGMA foreign_keys = ON")
    # ジャーナルモードの最適化
    conn.execute("PRAGMA journal_mode = WAL")
    # 同期モードの最適化
    conn.execute("PRAGMA synchronous = NORMAL")

    return conn

# テスト用に接続を確立して簡単なクエリを実行する関数
def test_connection():
    with engine.connect() as connection:
        result = connection.execute(text("SELECT version();"))
        for row in result:
            print(row)

if __name__ == '__main__':
    test_connection()