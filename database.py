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
                
                # 既に datetime オブジェクトの場合はそのまま返す
                if isinstance(s, datetime.datetime):
                    return s
                
                # エラー発生を防ぐための改善された処理
                try:
                    # ISOフォーマットを最初に試す
                    if 'T' in s_decoded:
                        # マイクロ秒付きフォーマットの完全対応
                        try:
                            # Python 3.7+用の簡易解析（可能な場合）
                            if hasattr(datetime.datetime, 'fromisoformat'):
                                # マイクロ秒の処理を行うため、Z/+を適切に処理
                                iso_str = s_decoded.replace('Z', '+00:00')
                                if '+' not in iso_str and '-' not in iso_str[10:]:  # タイムゾーン情報がない場合
                                    iso_str = iso_str + '+00:00'
                                try:
                                    return datetime.datetime.fromisoformat(iso_str)
                                except ValueError:
                                    # fromisoformatが失敗した場合は従来の方法を試す
                                    pass
                            
                            # 従来の方法（フォールバック）
                            main_part = s_decoded.split('+')[0] if '+' in s_decoded else s_decoded
                            main_part = main_part.split('Z')[0] if 'Z' in main_part else main_part
                            main_part = main_part.split('-')[0] if '-' in main_part and main_part.count('-') > 2 else main_part
                            
                            if '.' in main_part:
                                # マイクロ秒を含む場合、マイクロ秒を除去
                                date_part, time_part = main_part.split('T')
                                time_base = time_part.split('.')[0]
                                return datetime.datetime.strptime(f"{date_part}T{time_base}", '%Y-%m-%dT%H:%M:%S')
                            else:
                                # マイクロ秒なしの場合
                                return datetime.datetime.strptime(main_part, '%Y-%m-%dT%H:%M:%S')
                        except Exception as e:
                            logger.warning(f"ISO日付パース失敗（フォールバックします）: {e}")
                            # フォールバック: 日付部分と時間部分を分離してみる
                            try:
                                date_part = s_decoded.split('T')[0]
                                return datetime.datetime.strptime(date_part, '%Y-%m-%d')
                            except Exception:
                                pass
                    
                    elif ' ' in s_decoded:
                        # スペース区切りの日時
                        try:
                            date_part, time_part = s_decoded.split(' ', 1)
                            if '.' in time_part:
                                time_base = time_part.split('.')[0]
                                return datetime.datetime.strptime(f"{date_part} {time_base}", '%Y-%m-%d %H:%M:%S')
                            else:
                                return datetime.datetime.strptime(s_decoded, '%Y-%m-%d %H:%M:%S')
                        except Exception:
                            # 日付部分だけ解析を試みる
                            try:
                                date_part = s_decoded.split(' ')[0]
                                return datetime.datetime.strptime(date_part, '%Y-%m-%d')
                            except Exception:
                                pass
                    else:
                        # 日付のみと仮定
                        return datetime.datetime.strptime(s_decoded, '%Y-%m-%d')
                except (ValueError, TypeError) as parse_error:
                    logger.warning(f"標準フォーマットでの日付変換に失敗: {parse_error}, 入力値: {s_decoded}")
                    
                    # 最後の手段：現在時刻を返す
                    logger.warning(f"日付変換に最終的に失敗。現在時刻を代用: {s_decoded}")
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