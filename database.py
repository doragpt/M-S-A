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
                
            # デバッグログ
            print(f"変換前の日時文字列: {s_decoded}")
                
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
                
                # タイムゾーン部分の処理を改善
                zone_marker_index = -1
                for marker in ['+', 'Z']:
                    if marker in microsec_part:
                        zone_marker_index = microsec_part.find(marker)
                        break
                
                # '-'については特殊処理（日付の区切りではなくタイムゾーンの場合のみ）
                if zone_marker_index == -1 and '-' in microsec_part:
                    # 最初の'-'が日付の区切りではなくタイムゾーンの場合
                    if microsec_part.index('-') > 0:
                        zone_marker_index = microsec_part.index('-')
                
                # タイムゾーン部分を分離
                if zone_marker_index > -1:
                    timezone_part = microsec_part[zone_marker_index:]
                    microsec_part = microsec_part[:zone_marker_index]
                
                # マイクロ秒を6桁に制限
                if len(microsec_part) > 6:
                    microsec_part = microsec_part[:6]
                
                # 再構築
                s_decoded = f"{date_part}.{microsec_part}{timezone_part}"
            
            print(f"処理後の日時文字列: {s_decoded}")
                
            # 実際の日時オブジェクト作成 - 複数の方法で試行
            try:
                # 方法1: Python標準のfromisoformat
                return datetime.datetime.fromisoformat(s_decoded)
            except ValueError as e1:
                print(f"fromisoformat失敗: {e1}")
                try:
                    # 方法2: dateutil (より寛容なパーサー)
                    import dateutil.parser
                    return dateutil.parser.parse(s_decoded)
                except Exception as e2:
                    print(f"dateutil.parse失敗: {e2}")
                    try:
                        # 方法3: strptimeを試す (タイムゾーン情報を除去)
                        if 'T' in s_decoded:
                            # ISOフォーマットのパターン
                            dt_part = s_decoded.split('.')[0] if '.' in s_decoded else s_decoded
                            dt_part = dt_part.split('+')[0] if '+' in dt_part else dt_part
                            dt_part = dt_part.split('-')[0] if '-' in dt_part and dt_part.rindex('-') > 10 else dt_part
                            dt_part = dt_part.split('Z')[0] if 'Z' in dt_part else dt_part
                            return datetime.datetime.strptime(dt_part, '%Y-%m-%dT%H:%M:%S')
                        else:
                            # 標準的な日時フォーマット
                            return datetime.datetime.strptime(s_decoded.split('.')[0], '%Y-%m-%d %H:%M:%S')
                    except Exception as e3:
                        print(f"strptime失敗: {e3}")
                        # どの方法も失敗した場合は文字列をそのまま返す
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