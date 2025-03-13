import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# 環境変数から DATABASE_URL を取得、なければ SQLite を使用
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///store_data.db')

# SQLAlchemy のエンジンを作成
engine = create_engine(DATABASE_URL, echo=True)

# セッションの作成
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# データベース接続を取得する関数
def get_db_connection():
    return engine.connect()

# テスト用に接続を確立して簡単なクエリを実行する関数
def test_connection():
    with engine.connect() as connection:
        result = connection.execute(text("SELECT version();"))
        for row in result:
            print(row)

if __name__ == '__main__':
    test_connection()
