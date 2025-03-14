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
        try:
            return dt.isoformat()
        except Exception as e:
            print(f"日時アダプターエラー: {e}, 値: {dt}")
            return str(dt)

    def convert_datetime(s):
        if s is None:
            return None
        try:
            # 文字列に変換
            if isinstance(s, bytes):
                s_decoded = s.decode()
            else:
                s_decoded = str(s) if not isinstance(s, str) else s
            
            # エラー発生を防ぐための簡易処理
            try:
                # 最もシンプルな方法を最初に試みる
                return datetime.datetime.fromisoformat(s_decoded)
            except (ValueError, TypeError):
                try:
                    # 標準的なフォーマットを試す
                    if ' ' in s_decoded:
                        # スペース区切りの日時
                        date_part, time_part = s_decoded.split(' ', 1)
                        if '.' in time_part:
                            time_part = time_part.split('.')[0]  # マイクロ秒は切り捨て
                        return datetime.datetime.strptime(f"{date_part} {time_part}", '%Y-%m-%d %H:%M:%S')
                    elif 'T' in s_decoded:
                        # ISO形式の日時
                        s_simple = s_decoded.split('+')[0] if '+' in s_decoded else s_decoded
                        s_simple = s_simple.split('Z')[0] if 'Z' in s_simple else s_simple
                        if '.' in s_simple:
                            s_simple = s_simple.split('.')[0]  # マイクロ秒は切り捨て
                        return datetime.datetime.strptime(s_simple, '%Y-%m-%dT%H:%M:%S')
                    else:
                        # 日付のみ
                        return datetime.datetime.strptime(s_decoded, '%Y-%m-%d')
                except (ValueError, TypeError):
                    # すべての方法が失敗した場合は現在時刻を返す（重大なエラーを防ぐため）
                    print(f"日付変換に失敗。現在時刻を代用: {s_decoded}")
                    return datetime.datetime.now()
        except Exception as e:
            print(f"予期しない日付変換エラー: {e}, 入力値: {repr(s)}")
            return datetime.datetime.now()

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