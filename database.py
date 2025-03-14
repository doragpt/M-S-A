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
            # 文字列に変換
            if isinstance(s, bytes):
                s_decoded = s.decode()
            else:
                s_decoded = str(s) if not isinstance(s, str) else s
                
            # 日付文字列の標準化
            if ' ' in s_decoded and 'T' not in s_decoded:
                parts = s_decoded.split(' ')
                if len(parts) >= 2:
                    s_decoded = f"{parts[0]}T{parts[1]}"
                    
            # マイクロ秒の処理（6桁に制限）
            if '.' in s_decoded:
                date_part, fraction_part = s_decoded.split('.', 1)
                # マイクロ秒部分とそれ以降（タイムゾーン情報など）を分離
                microsec_part = fraction_part
                timezone_part = ""
                if '+' in fraction_part:
                    microsec_part, timezone_part = fraction_part.split('+', 1)
                    timezone_part = '+' + timezone_part
                elif '-' in fraction_part and fraction_part.index('-') > 0:  # 日付の区切りではなくタイムゾーンの場合
                    microsec_part, timezone_part = fraction_part.split('-', 1)
                    timezone_part = '-' + timezone_part
                elif 'Z' in fraction_part:
                    microsec_part, timezone_part = fraction_part.split('Z', 1)
                    timezone_part = 'Z' + timezone_part
                
                # マイクロ秒を6桁に制限
                if len(microsec_part) > 6:
                    microsec_part = microsec_part[:6]
                
                # 再構築
                s_decoded = f"{date_part}.{microsec_part}{timezone_part}"
                
            # 実際の日時オブジェクト作成
            try:
                return datetime.datetime.fromisoformat(s_decoded)
            except ValueError:
                # fromisoformatがサポートしていない形式の場合、別の方法を試す
                import dateutil.parser
                return dateutil.parser.parse(s_decoded)
            
            return s_decoded
        except (ValueError, TypeError) as e:
            # エラーの詳細をログに出力
            print(f"日付変換エラー: {e}, 入力値: {repr(s)}")
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