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
    conn = sqlite3.connect('store_data.db', detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row

    # datetime型のアダプターとコンバーターを登録
    def adapt_datetime(dt):
        return dt.isoformat()

    def convert_datetime(s):
        try:
            return datetime.datetime.fromisoformat(s.decode())
        except ValueError:
            # ISO形式でない場合は文字列として返す
            return s.decode()

    sqlite3.register_adapter(datetime.datetime, adapt_datetime)
    sqlite3.register_converter("timestamp", convert_datetime)

    return conn

# テスト用に接続を確立して簡単なクエリを実行する関数
def test_connection():
    with engine.connect() as connection:
        result = connection.execute(text("SELECT version();"))
        for row in result:
            print(row)

if __name__ == '__main__':
    test_connection()