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
    import logging
    logger = logging.getLogger('app')
    
    try:
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
                logger.error(f"日時アダプターエラー: {e}, 値: {dt}")
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
                
                # エラー発生を防ぐための改善された処理
                try:
                    # ISOフォーマットを最初に試す
                    if 'T' in s_decoded:
                        # マイクロ秒の処理を改善
                        if '.' in s_decoded:
                            # マイクロ秒付きのISOフォーマット
                            main_part = s_decoded.split('+')[0] if '+' in s_decoded else s_decoded
                            main_part = main_part.split('Z')[0] if 'Z' in main_part else main_part
                            date_part, time_part = main_part.split('T')
                            time_components = time_part.split('.')
                            time_base = time_components[0]
                            return datetime.datetime.strptime(f"{date_part}T{time_base}", '%Y-%m-%dT%H:%M:%S')
                        else:
                            # マイクロ秒なしのISOフォーマット
                            s_simple = s_decoded.split('+')[0] if '+' in s_decoded else s_decoded
                            s_simple = s_simple.split('Z')[0] if 'Z' in s_simple else s_simple
                            return datetime.datetime.strptime(s_simple, '%Y-%m-%dT%H:%M:%S')
                    elif ' ' in s_decoded:
                        # スペース区切りの日時
                        date_part, time_part = s_decoded.split(' ', 1)
                        if '.' in time_part:
                            time_components = time_part.split('.')
                            time_base = time_components[0]
                            return datetime.datetime.strptime(f"{date_part} {time_base}", '%Y-%m-%d %H:%M:%S')
                        else:
                            return datetime.datetime.strptime(s_decoded, '%Y-%m-%d %H:%M:%S')
                    else:
                        # 日付のみ
                        return datetime.datetime.strptime(s_decoded, '%Y-%m-%d')
                except (ValueError, TypeError) as parse_error:
                    logger.warning(f"標準フォーマットでの日付変換に失敗: {parse_error}, 入力値: {s_decoded}")
                    
                    # 最後の手段：文字列の切り捨てと整形
                    try:
                        if 'T' in s_decoded:
                            date_part, time_part = s_decoded.split('T', 1)
                            time_part = time_part.split('.')[0] if '.' in time_part else time_part
                            time_part = time_part.split('+')[0] if '+' in time_part else time_part
                            time_part = time_part.split('Z')[0] if 'Z' in time_part else time_part
                            # 時間が6文字以下であることを確認 (HH:MM:SS)
                            if len(time_part) > 8:
                                time_part = time_part[:8]
                            return datetime.datetime.strptime(f"{date_part}T{time_part}", '%Y-%m-%dT%H:%M:%S')
                        else:
                            # 現在時刻を返す（最終的なフォールバック）
                            logger.warning(f"日付変換に最終的に失敗。現在時刻を代用: {s_decoded}")
                            return datetime.datetime.now()
                    except Exception:
                        # すべての方法が失敗した場合は現在時刻を返す
                        logger.warning(f"すべての日付変換方法に失敗。現在時刻を代用: {s_decoded}")
                        return datetime.datetime.now()
            except Exception as e:
                logger.error(f"予期しない日付変換エラー: {e}, 入力値: {repr(s)}")
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
        
        # 接続テスト
        test_query = "SELECT COUNT(*) FROM store_status"
        result = conn.execute(test_query).fetchone()
        logger.info(f"データベース接続成功: store_statusテーブルのレコード数 = {result[0]}")

        return conn
    except Exception as e:
        logger.error(f"データベース接続エラー: {e}")
        # バックアップとしてデフォルトのsqlite3接続を試す
        try:
            basic_conn = sqlite3.connect('store_data.db')
            basic_conn.row_factory = sqlite3.Row
            logger.warning("基本的なデータベース接続にフォールバックしました")
            return basic_conn
        except Exception as fallback_err:
            logger.critical(f"フォールバック接続も失敗: {fallback_err}")
            raise

# テスト用に接続を確立して簡単なクエリを実行する関数
def test_connection():
    with engine.connect() as connection:
        result = connection.execute(text("SELECT version();"))
        for row in result:
            print(row)

if __name__ == '__main__':
    test_connection()