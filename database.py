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
    import sqlite3.dbapi2 as sqlite
    sqlite.encode = lambda x: x.encode('utf-8', 'ignore')
    sqlite.decode = lambda x: x.decode('utf-8', 'ignore')

    try:
        conn = sqlite3.connect(
            'store_data.db',
            detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES,
            timeout=20,
            isolation_level=None  # 自動コミットモード
        )
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
            import logging
            logger = logging.getLogger('app')

            if s is None:
                return None
            try:
                # 既に datetime オブジェクトの場合はそのまま返す
                if isinstance(s, datetime.datetime):
                    return s

                # バイト列から文字列に変換
                if isinstance(s, bytes):
                    s = s.decode()
                elif not isinstance(s, str):
                    s = str(s)

                # 具体的なエラー例の対応: 2025-02-19T03:03:42.281587
                if 'T' in s and '.' in s and len(s.split('.')[1]) > 6:
                    # マイクロ秒が6桁以上ある場合は6桁に切り詰める
                    date_part, time_part = s.split('T')
                    time_main, micro_part = time_part.split('.')

                    # マイクロ秒またはその他の部分が長すぎる場合、6桁に切り詰める
                    if '+' in micro_part:
                        micro, tz = micro_part.split('+', 1)
                        micro = micro[:6]
                        s = f"{date_part}T{time_main}.{micro}+{tz}"
                    elif '-' in micro_part[10:]:  # タイムゾーン情報を持つマイナスの場合
                        parts = micro_part.split('-', 1)
                        micro = parts[0][:6]
                        s = f"{date_part}T{time_main}.{micro}-{parts[1]}"
                    else:
                        micro = micro_part[:6]
                        s = f"{date_part}T{time_main}.{micro}"

                # シンプルな処理フロー
                try:
                    # ISOフォーマットの処理 (Python 3.7+)
                    if 'T' in s:
                        # Zを+00:00に置換してタイムゾーン対応
                        s = s.replace('Z', '+00:00')

                        # タイムゾーン情報がない場合はUTCとみなす
                        if '+' not in s and '-' not in s[10:]:
                            s = s + '+00:00'

                        # Python 3.7+ のfromisoformatを使用
                        if hasattr(datetime.datetime, 'fromisoformat'):
                            try:
                                return datetime.datetime.fromisoformat(s)
                            except ValueError as e:
                                logger.warning(f"fromisoformat失敗: {e}, 入力値: {s}")
                                # 続行してフォールバック方法を試す

                        # マイクロ秒対応のstrptimeを使用（フォールバック）
                        try:
                            if '.' in s:
                                # マイクロ秒あり
                                main_part = s.split('+')[0] if '+' in s else s
                                # タイムゾーン情報を取り除く
                                if '-' in main_part[10:]:
                                    main_part = main_part.split('-')[0]
                                return datetime.datetime.strptime(main_part, '%Y-%m-%dT%H:%M:%S.%f')
                            else:
                                # マイクロ秒なし
                                main_part = s.split('+')[0] if '+' in s else s
                                # タイムゾーン情報を取り除く
                                if '-' in main_part[10:]:
                                    main_part = main_part.split('-')[0]
                                return datetime.datetime.strptime(main_part, '%Y-%m-%dT%H:%M:%S')
                        except ValueError as e:
                            logger.warning(f"ISO strptime失敗: {e}, 入力値: {s}")

                    # スペース区切りの日時
                    elif ' ' in s:
                        try:
                            if '.' in s:
                                return datetime.datetime.strptime(s, '%Y-%m-%d %H:%M:%S.%f')
                            else:
                                return datetime.datetime.strptime(s, '%Y-%m-%d %H:%M:%S')
                        except ValueError:
                            # 日付部分だけ解析
                            date_part = s.split(' ')[0]
                            return datetime.datetime.strptime(date_part, '%Y-%m-%d')

                    # 日付のみ
                    else:
                        return datetime.datetime.strptime(s, '%Y-%m-%d')

                except Exception as parse_error:
                    logger.warning(f"日付変換エラー: {parse_error}, 入力値: {s}")
                    # フォールバック: 現在時刻を返す
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