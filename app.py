import os
import logging
from datetime import datetime, timedelta
import pytz  # タイムゾーン変換用
import traceback
import gc  # ガベージコレクション

from flask import Flask, request, render_template, redirect, url_for, flash, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from sqlalchemy import func, exc, and_
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ProcessPoolExecutor

# 追加：Flask-Caching のインポート
from flask_caching import Cache
import json
from database import get_db_connection # Added

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# 1. Flaskアプリ & DB接続設定
# ---------------------------------------------------------------------
app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your_secret_key_here')

# ローカル環境専用：SQLiteデータベースを使用
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///store_data.db')
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# データベースがSQLiteの場合は最小限のオプションを使用し、
# PostgreSQLなどの場合は完全なプールオプションを適用する
if DATABASE_URL.startswith('sqlite'):
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True  # 接続が生きているか確認（SQLiteでも動作）
    }
else:
    # PostgreSQLなどの本番環境用DB向けの完全なオプション
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,  # 接続が生きているか確認
        'pool_recycle': 300,    # 5分ごとに接続をリサイクル
        'pool_timeout': 30,     # 接続タイムアウト30秒
        'pool_size': 10,        # コネクションプールサイズ
        'max_overflow': 20      # 最大オーバーフロー接続数
    }

# キャッシュ設定（環境に応じて自動選択）
if os.environ.get('REDIS_URL'):
    # 本番環境: Redis利用
    app.config['CACHE_TYPE'] = 'RedisCache'
    app.config['CACHE_REDIS_URL'] = os.environ.get('REDIS_URL')
    app.config['CACHE_OPTIONS'] = {'socket_timeout': 3, 'socket_connect_timeout': 3}
else:
    # 開発環境: SimpleCache利用（Redis不要）
    app.config['CACHE_TYPE'] = 'SimpleCache'
    app.config['CACHE_DEFAULT_TIMEOUT'] = 300

# キャッシュ設定の追加
app.config['CACHE_KEY_PREFIX'] = 'm_s_a_v1_'  # キャッシュキーのプレフィックス
app.config['CACHE_THRESHOLD'] = 1000  # SimpleCache使用時の最大アイテム数

cache = Cache(app)

# リクエスト制限の設定 (簡易版)
request_limits = {}  # IPアドレスごとのリクエスト回数記録用

db = SQLAlchemy(app)
socketio = SocketIO(app, async_mode='threading')

# ---------------------------------------------------------------------
# 2. モデル定義
# ---------------------------------------------------------------------
class StoreStatus(db.Model):
    """
    店舗ごとのスクレイピング結果を保存するテーブル。
    各レコードは、ある時点での店舗の稼働情報を表します。
    """
    __tablename__ = 'store_status'
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, server_default=func.now(), index=True)
    store_name = db.Column(db.Text)
    biz_type = db.Column(db.Text)
    genre = db.Column(db.Text)
    area = db.Column(db.Text)
    total_staff = db.Column(db.Integer)
    working_staff = db.Column(db.Integer)
    active_staff = db.Column(db.Integer)
    url = db.Column(db.Text)
    shift_time = db.Column(db.Text)


class StoreURL(db.Model):
    """
    スクレイピング対象の店舗URLを保存するテーブル。
    """
    __tablename__ = 'store_urls'
    id = db.Column(db.Integer, primary_key=True)
    store_url = db.Column(db.Text, unique=True, nullable=False)
    error_flag = db.Column(db.Integer, default=0)

# 集計データテーブル
class DailyAverage(db.Model):
    """日次の平均稼働率（直近24時間）"""
    __tablename__ = 'daily_averages'
    id = db.Column(db.Integer, primary_key=True)
    store_name = db.Column(db.Text, index=True)
    avg_rate = db.Column(db.Float)
    sample_count = db.Column(db.Integer)
    start_date = db.Column(db.DateTime)
    end_date = db.Column(db.DateTime)
    biz_type = db.Column(db.Text)
    genre = db.Column(db.Text)
    area = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=func.now())

class WeeklyAverage(db.Model):
    """週次の平均稼働率（直近7日間）"""
    __tablename__ = 'weekly_averages'
    id = db.Column(db.Integer, primary_key=True)
    store_name = db.Column(db.Text, index=True)
    avg_rate = db.Column(db.Float)
    sample_count = db.Column(db.Integer)
    start_date = db.Column(db.DateTime)
    end_date = db.Column(db.DateTime)
    biz_type = db.Column(db.Text)
    genre = db.Column(db.Text)
    area = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=func.now())

class MonthlyAverage(db.Model):
    """月次の平均稼働率（直近30日間）"""
    __tablename__ = 'monthly_averages'
    id = db.Column(db.Integer, primary_key=True)
    store_name = db.Column(db.Text, index=True)
    avg_rate = db.Column(db.Float)
    sample_count = db.Column(db.Integer)
    start_date = db.Column(db.DateTime)
    end_date = db.Column(db.DateTime)
    biz_type = db.Column(db.Text)
    genre = db.Column(db.Text)
    area = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=func.now())

class StoreAverage(db.Model):
    """店舗ごとの全期間平均稼働率（2年以内）"""
    __tablename__ = 'store_averages'
    id = db.Column(db.Integer, primary_key=True)
    store_name = db.Column(db.Text, index=True)
    avg_rate = db.Column(db.Float)
    sample_count = db.Column(db.Integer)
    start_date = db.Column(db.DateTime)
    end_date = db.Column(db.DateTime)
    biz_type = db.Column(db.Text)
    genre = db.Column(db.Text)
    area = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=func.now())

# ---------------------------------------------------------------------
# 3. テーブル作成
# ---------------------------------------------------------------------
# 明示的にモデルをインポートして、db.create_all()がすべてのテーブルを作成するようにする
with app.app_context():
    try:
        # 各モデルを明示的に参照して、SQLAlchemyに認識させる
        app.logger.info("データベーステーブルを作成しています...")
        app.logger.info(f"モデル: StoreStatus, StoreURL, DailyAverage, WeeklyAverage, MonthlyAverage, StoreAverage")

        # 明示的にモデルクラスを参照して、SQLAlchemyが認識できるようにする
        tables = [StoreStatus, StoreURL, DailyAverage, WeeklyAverage, MonthlyAverage, StoreAverage]
        app.logger.info(f"登録テーブル数: {len(tables)}")

        # テーブル作成
        db.create_all()

        # テーブルが作成されたか確認
        table_names = db.inspect(db.engine).get_table_names()
        app.logger.info(f"作成されたテーブル: {', '.join(table_names)}")

        app.logger.info("データベーステーブルの作成が完了しました")
    except Exception as e:
        app.logger.error(f"テーブル作成中にエラーが発生しました: {e}")
        app.logger.error(traceback.format_exc())

# ---------------------------------------------------------------------
# 4. スクレイピング処理 & 定期実行
# ---------------------------------------------------------------------
# ※スクレイピング処理は外部ファイル (store_scraper.py) に定義してあると想定
from store_scraper import scrape_store_data

def scheduled_scrape():
    """
    APScheduler で1時間おきに実行されるスクレイピングジョブ。
    ・StoreURL テーブルに登録された各店舗URLに対してスクレイピングを実行し、
      得られた結果を StoreStatus テーブルに保存する。
    ・重複チェックは「店舗名＋エリア＋スクレイピング実行時刻（分単位切り捨て）」で行い、
      既に同じ複合キーが存在する場合はそのレコードを更新する（上書き）。
    ・古いデータ（2年以上前）を削除してパフォーマンスを維持する。
    ・データ更新後に集計データを計算し、集計用テーブルに保存する。
    ・処理完了後、Socket.IO を利用してクライアントへ更新通知を送信する。
    """
    from aggregated_data import AggregatedData

    with app.app_context():
        # スクレイピング実行時刻（全レコード共通のタイムスタンプ）
        scrape_time = datetime.now()
        # 古いデータ削除：過去2年以上前
        retention_date = datetime.now() - timedelta(days=730)

        store_url_objs = StoreURL.query.all()
        store_urls = [s.store_url for s in store_url_objs]
        if not store_urls:
            app.logger.info("店舗URLが1件も登録されていません。")
            return

        app.logger.info("【スクレイピング開始】対象店舗数: %d", len(store_urls))
        try:
            app.logger.info("スクレイピング処理を開始します...")
            try:
                results = scrape_store_data(store_urls)
                app.logger.info("スクレイピング処理が完了しました。結果件数: %d", len(results))
            except Exception as e:
                if "signal only works in main thread" in str(e):
                    app.logger.info("メインスレッドエラーを検出しました。マルチプロセス実行に切り替えます。")
                    import multiprocessing
                    ctx = multiprocessing.get_context('spawn')
                    with ctx.Pool(1) as pool:
                        results = pool.apply(scrape_store_data, (store_urls,))
                    app.logger.info("マルチプロセス実行が完了しました。結果件数: %d", len(results))
                else:
                    raise
        except Exception as e:
            app.logger.error("スクレイピング中のエラー: %s", e)
            app.logger.error(traceback.format_exc())
            return

        # 結果をDBに保存（既存レコードがあれば更新、なければ新規追加）
        record_update_count = 0
        record_insert_count = 0

        try:
            app.logger.info(f"スクレイピング結果: {len(results)}件のレコードを処理開始")

            for record in results:
                if not record:  # 空の結果はスキップ
                    app.logger.warning("空のレコードをスキップしました")
                    continue

                try:
                    # レコードの内容確認（デバッグ用）
                    store_name = record.get('store_name', '')
                    area = record.get('area', '')
                    total_staff = record.get('total_staff', 0)
                    working_staff = record.get('working_staff', 0)
                    active_staff = record.get('active_staff', 0)

                    app.logger.debug(f"処理中のレコード: {store_name} ({area}) - 総:{total_staff}, 勤務:{working_staff}, 待機:{active_staff}")

                    if not store_name or not area:
                        app.logger.warning(f"必須データ不足のためスキップ: 店舗名={store_name}, エリア={area}")
                        continue

                    # タイムスタンプ文字列をログ出力（デバッグ用）
                    formatted_time = scrape_time.strftime('%Y-%m-%d %H:%M')
                    app.logger.debug(f"重複チェック用タイムスタンプ: {formatted_time}")

                    # 重複チェック: 店舗名とエリアと実行時刻（分単位）でチェック
                    # SQLクエリをログ出力
                    timestamp_query = f"strftime('%Y-%m-%d %H:%M', timestamp) = '{formatted_time}'"
                    query_desc = f"StoreStatus.store_name='{store_name}' AND StoreStatus.area='{area}' AND {timestamp_query}"
                    app.logger.debug(f"重複チェッククエリ: {query_desc}")

                    existing = StoreStatus.query.filter(
                        StoreStatus.store_name == store_name,
                        StoreStatus.area == area,
                        func.strftime('%Y-%m-%d %H:%M', StoreStatus.timestamp) == formatted_time
                    ).first()

                    if existing:
                        # 既存レコードがあれば更新
                        app.logger.info(f"既存レコードを更新: {store_name} (ID: {existing.id})")
                        existing.biz_type = record.get('biz_type')
                        existing.genre = record.get('genre')
                        existing.area = area
                        existing.total_staff = total_staff
                        existing.working_staff = working_staff
                        existing.active_staff = active_staff
                        existing.url = record.get('url', '')
                        existing.shift_time = record.get('shift_time', '')
                        record_update_count += 1
                    else:
                        # 新規の場合は新たにレコードを追加
                        app.logger.info(f"新規レコードを追加: {store_name}")
                        new_status = StoreStatus(
                            timestamp=scrape_time,
                            store_name=store_name,
                            biz_type=record.get('biz_type'),
                            genre=record.get('genre'),
                            area=area,
                            total_staff=total_staff,
                            working_staff=working_staff,
                            active_staff=active_staff,
                            url=record.get('url', ''),
                            shift_time=record.get('shift_time', '')
                        )
                        db.session.add(new_status)
                        record_insert_count += 1

                except Exception as e:
                    app.logger.error(f"レコード処理中の例外発生: {record.get('store_name', 'unknown')} - {str(e)}")
                    app.logger.error(traceback.format_exc())

            # セッションの変更をコミット
            db.session.commit()
            app.logger.info(f"DB処理完了: 更新={record_update_count}件, 新規追加={record_insert_count}件")

        except Exception as e:
            db.session.rollback()
            app.logger.error(f"DB操作中に例外が発生しました: {str(e)}")
            app.logger.error(traceback.format_exc())

        # 古いデータ（2年以上前）の削除
        db.session.query(StoreStatus).filter(StoreStatus.timestamp < retention_date).delete()
        db.session.commit()

        # 集計データの計算と保存
        app.logger.info("集計データの計算開始")
        aggregation_result = AggregatedData.calculate_and_save_aggregated_data()
        app.logger.info(f"集計データの計算完了: {aggregation_result}")

        # 特定のキャッシュのみをクリア（集計データは更新済みなのでそのまま）
        try:
            app.logger.info("キャッシュをクリアしています...")

            # すべてのAPIエンドポイントのキャッシュをクリア
            cache.delete_memoized(api_data)
            cache.delete_memoized(api_history)
            cache.delete_memoized(api_history_optimized)
            cache.delete_memoized(api_daily_averages)
            cache.delete_memoized(api_weekly_averages)
            cache.delete_memoized(api_monthly_averages)
            cache.delete_memoized(api_store_averages)
            cache.delete_memoized(api_average_ranking)
            cache.delete_memoized(api_aggregated_data)
            cache.delete_memoized(api_genre_ranking) # Added

            # 古いキャッシュキーも念のためクリア
            legacy_keys = [
                'view/api_data', 
                'view/api_history',
                'api_data',
                'api_history'
            ]
            for key in legacy_keys:
                cache.delete(key)

            app.logger.info("キャッシュのクリアが完了しました")
        except Exception as e:
            app.logger.error(f"キャッシュクリア中にエラーが発生しました: {str(e)}")
            app.logger.error(traceback.format_exc())

        # Socket.IO でクライアントへ更新通知
        socketio.emit('update', {'data': 'Dashboard updated'})

# 深夜にメモリ解放とキャッシュクリアを行う関数
def maintenance_task():
    """深夜のメンテナンス処理: キャッシュクリア、メモリ解放など"""
    with app.app_context():
        app.logger.info("メンテナンスタスク実行中: キャッシュクリア、メモリ解放")
        # キャッシュ完全クリア
        cache.clear()
        # 明示的にガベージコレクションを実行
        collected = gc.collect()
        app.logger.info(f"ガベージコレクション完了: {collected}オブジェクト解放")

# APScheduler の設定：1時間ごとに scheduled_scrape を実行
# ジョブIDを 'scrape_job' として登録（日本時間を基準に）
executors = {'default': ProcessPoolExecutor(max_workers=1)}
jst = pytz.timezone('Asia/Tokyo')
scheduler = BackgroundScheduler(executors=executors, timezone=jst)
scheduler.add_job(scheduled_scrape, 'interval', hours=1, id='scrape_job')
# 毎日午前3時（日本時間）にメンテナンスタスクを実行
scheduler.add_job(maintenance_task, 'cron', hour=3, minute=0, id='maintenance_job')
scheduler.start()

# ---------------------------------------------------------------------
# 5. API エンドポイント
# ---------------------------------------------------------------------
@app.route('/api/data')
@cache.memoize(timeout=60)  # キャッシュ：60秒間有効（パフォーマンス向上のため）
def api_data():
    """
    各店舗の最新レコードのみを返すエンドポイント（タイムゾーンは JST）。
    集計値（全体平均など）も含める。

    クエリパラメータ:
        page: ページ番号（1から始まる）- 指定しない場合は全データを返す
        per_page: 1ページあたりの項目数（最大100）
    """
    from page_helper import paginate_query_results, format_store_status
    from database import get_db_connection

    # JSTタイムゾーン設定（エラー時も必要なので最初に定義）
    jst = pytz.timezone('Asia/Tokyo')
    now_jst = datetime.now(jst)

    try:
        app.logger.info("API /api/data へのリクエスト開始")

        # キャッシュをクリアするパラメータ
        refresh = request.args.get('refresh', '0') == '1'
        if refresh:
            cache.delete_memoized(api_data)
            app.logger.info("キャッシュをクリアしました")

        # ページネーションの有無を確認
        use_pagination = 'page' in request.args
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)  # 最大100件に制限

        # データベース接続エラーを受け取った場合のフォールバックレスポンス
        fallback_response = {
            "data": [],
            "meta": {
                "total_count": 0,
                "message": "データベース接続エラーが発生しました",
                "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
            }
        }

        # SQLAlchemyを使用してデータ取得（SQLite変換エラー回避のため）
        try:
            # 直接SQLiteを使用して最新データを取得
            app.logger.info("直接SQLiteを使用してデータを取得します")
            conn = get_db_connection()

            if conn is None:
                app.logger.error("データベース接続が取得できませんでした")
                return jsonify(fallback_response), 200

            # 最新レコードを取得するクエリ
            query = """
            WITH LatestTimestamps AS (
                SELECT store_name, MAX(timestamp) as max_timestamp
                FROM store_status
                GROUP BY store_name
            )
            SELECT s.*
            FROM store_status s
            JOIN LatestTimestamps lt ON s.store_name = lt.store_name AND s.timestamp = lt.max_timestamp
            """

            try:
                all_results = list(conn.execute(query).fetchall())
                conn.close()
                app.logger.info(f"SQLite直接取得結果: {len(all_results)}件のレコードを取得")
            except Exception as query_err:
                app.logger.error(f"クエリ実行エラー: {str(query_err)}")
                app.logger.error(traceback.format_exc())

                # 最後の手段：単純に最新の100件を取得
                try:
                    conn = get_db_connection()
                    query = """
                    SELECT * FROM store_status
                    ORDER BY timestamp DESC
                    LIMIT 100
                    """
                    all_results = list(conn.execute(query).fetchall())
                    conn.close()
                    app.logger.info(f"最終フォールバック結果: {len(all_results)}件のレコードを取得")
                except Exception as final_err:
                    app.logger.error(f"最終フォールバック取得エラー: {str(final_err)}")
                    return jsonify(fallback_response), 200


            # データが0件の場合
            if len(all_results) == 0:
                app.logger.warning("データが0件です。データベースが空か、クエリが正しくありません。")
                return jsonify({
                    "data": [],
                    "meta": {
                        "total_count": 0,
                        "message": "データが見つかりません",
                        "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
                    }
                }), 200

            # 集計値の計算
            total_store_count = len(all_results)
            total_working_staff = 0
            total_active_staff = 0
            valid_stores = 0
            max_rate = 0

            app.logger.debug("集計値の計算開始")
            for item in all_results:
                # NULL値チェック
                try:
                    # SQLAlchemyモデルかSQLiteのRowかを確認して適切に値を取得
                    if hasattr(item, 'working_staff'):
                        # SQLAlchemyモデルの場合
                        working_staff = item.working_staff if item.working_staff is not None else 0
                        active_staff = item.active_staff if item.active_staff is not None else 0
                    else:
                        # SQLiteのRowオブジェクトの場合
                        working_staff = item['working_staff'] if item['working_staff'] is not None else 0
                        active_staff = item['active_staff'] if item['active_staff'] is not None else 0

                    # 数値型チェック
                    working_staff = int(working_staff)
                    active_staff = int(active_staff)

                    # 集計用データの収集
                    if working_staff > 0:
                        valid_stores += 1
                        total_working_staff += working_staff
                        total_active_staff += active_staff

                        # 稼働率計算
                        rate = ((working_staff - active_staff) / working_staff) * 100
                        max_rate = max(max_rate, rate)
                except (ValueError, TypeError, ZeroDivisionError, KeyError, AttributeError) as e:
                    app.logger.debug(f"集計スキップ: {str(e)}")
                    continue

            # 全体平均稼働率の計算
            avg_rate = 0
            if valid_stores > 0 and total_working_staff > 0:
                avg_rate = ((total_working_staff - total_active_staff) / total_working_staff) * 100

            # データをフォーマット
            app.logger.debug("データフォーマット開始")
            formatted_data = []
            format_errors = 0

            for item in all_results:
                try:
                    formatted_item = format_store_status(item, jst)
                    if formatted_item:  # Noneでない場合のみ追加
                        formatted_data.append(formatted_item)
                except Exception as fmt_err:
                    app.logger.warning(f"フォーマットエラー: {str(fmt_err)}")
                    app.logger.warning(traceback.format_exc())
                    format_errors += 1
                    # エラーでもformat_store_statusが辞書を返すようになったのでエラーは発生しない

            # ページネーション処理
            if use_pagination:
                try:
                    # 手動ページネーション（クエリではなく結果に対して）
                    start_idx = (page - 1) * per_page
                    end_idx = start_idx + per_page

                    # インデックス範囲チェック
                    if start_idx >= len(formatted_data):
                        start_idx = 0
                        page = 1

                    paged_data = formatted_data[start_idx:end_idx]

                    # ページネーション情報
                    total_pages = (len(formatted_data) + per_page - 1) // per_page if per_page > 0 else 1
                    has_next = page < total_pages
                    has_prev = page > 1

                    response = {
                        "data": paged_data,
                        "meta": {
                            "total_count": total_store_count,
                            "valid_stores": valid_stores,
                            "avg_rate": round(avg_rate, 1),
                            "max_rate": round(max_rate, 1),
                            "total_working_staff": total_working_staff,
                            "total_active_staff": total_active_staff,
                            "format_errors": format_errors,
                            "page": page,
                            "per_page": per_page,
                            "total_pages": total_pages,
                            "has_prev": has_prev,
                            "has_next": has_next,
                            "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
                        }
                    }
                except Exception as page_err:
                    app.logger.error(f"ページネーション処理エラー: {str(page_err)}")
                    app.logger.error(traceback.format_exc())
                    # エラー時は全データを返す
                    response = {
                        "data": formatted_data,
                        "meta": {
                            "total_count": total_store_count,
                            "valid_stores": valid_stores,
                            "avg_rate": round(avg_rate, 1),
                            "max_rate": round(max_rate, 1),
                            "total_working_staff": total_working_staff,
                            "total_active_staff": total_active_staff,
                            "format_errors": format_errors,
                            "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z'),
                            "pagination_error": str(page_err)
                        }
                    }
            else:
                # ページネーションなし - 全データ返却
                response = {
                    "data": formatted_data,
                    "meta": {
                        "total_count": total_store_count,
                        "valid_stores": valid_stores,
                        "avg_rate": round(avg_rate, 1),
                        "max_rate": round(max_rate, 1),
                        "total_working_staff": total_working_staff,
                        "total_active_staff": total_active_staff,
                        "format_errors": format_errors,
                        "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
                    }
                }

            app.logger.info(f"API /api/data レスポンス準備完了: {len(response['data'])}件のデータ")
            return jsonify(response), 200

        except Exception as e:
            app.logger.error(f"クエリ/データ処理中の例外: {str(e)}")
            app.logger.error(traceback.format_exc())

            # 統一されたエラーレスポンス形式
            return jsonify({
                "data": [],
                "meta": {
                    "error": str(e),
                    "message": "クエリ実行中にエラーが発生しました",
                    "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
                }
            }), 200

    except Exception as e:
        app.logger.error(f"API /api/data 全体での例外: {str(e)}")
        app.logger.error(traceback.format_exc())

        # 統一されたエラーレスポンス形式
        return jsonify({
            "data": [],
            "meta": {
                "error": str(e),
                "message": "APIの処理中にエラーが発生しました",
                "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
            }
        }), 200


@app.route('/bulk_add_store_urls', methods=['POST'])
def bulk_add_store_urls():
    """複数のURL一括追加処理"""
    bulk_urls = request.form.get('bulk_urls', '')

    # 空白行や重複を除去
    urls = [url.strip() for url in bulk_urls.splitlines() if url.strip()]

    # 成功・エラーカウント
    success_count = 0
    error_count = 0
    invalid_urls = []

    from urllib.parse import urlparse

    for url in urls:
        # URL形式の検証
        try:
            result = urlparse(url)
            if not all([result.scheme, result.netloc]):
                invalid_urls.append(url)
                error_count += 1
                continue
        except Exception:
            invalid_urls.append(url)
            error_count += 1
            continue

        # 重複チェック
        existing = StoreURL.query.filter_by(store_url=url).first()
        if existing:
            continue  # 既に存在する場合はスキップ

        # 新規URL追加
        try:
            new_url = StoreURL(store_url=url)
            db.session.add(new_url)
            success_count += 1
        except Exception:
            error_count += 1
            invalid_urls.append(url)

    # コミット
    try:
        db.session.commit()
        if invalid_urls:
            flash(f'{success_count}件のURLを追加しました。{error_count}件は失敗しました。無効なURL: {", ".join(invalid_urls[:5])}{"..." if len(invalid_urls) > 5 else ""}', 'warning')
        else:
            flash(f'{success_count}件のURLを追加しました。', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'エラーが発生しました: {e}', 'danger')

    return redirect(url_for('manage_store_urls'))

@app.route('/api/history')
@cache.memoize(timeout=300)  # キャッシュ：5分間有効（DB負荷軽減）
def api_history():
    """
    スクレイピング履歴を検索・フィルタリングして返すエンドポイント（タイムゾーンは JST）。

    クエリパラメータ:
        store: 店舗名で絞り込み
        start_date: 指定日以降のデータ (YYYY-MM-DD形式)
        end_date: 指定日以前のデータ (YYYY-MM-DD形式)
        limit: 返す最大レコード数
        page: ページ番号（1から始まる）
        per_page: 1ページあたりの項目数（最大100）
    """
    from page_helper import paginate_query_results, format_store_status
    from database import get_db_connection

    # JSTタイムゾーン
    jst = pytz.timezone('Asia/Tokyo')
    now_jst = datetime.now(jst)

    try:
        # クエリパラメータの取得
        store = request.args.get('store')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        limit = request.args.get('limit', type=int)
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 100, type=int), 100)  # 最大100件に制限

        # SQLAlchemyを使用してデータを取得する
        try:
            # クエリ条件を構築
            query = StoreStatus.query

            # フィルタリング条件の適用
            if store:
                query = query.filter(StoreStatus.store_name == store)

            if start_date:
                try:
                    # 日本時間の00:00:00として日付開始時刻を設定
                    start_datetime = datetime.strptime(f"{start_date} 00:00:00", "%Y-%m-%d %H:%M:%S")
                    start_datetime = jst.localize(start_datetime)
                    query = query.filter(StoreStatus.timestamp >= start_datetime)
                except Exception as e:
                    app.logger.error(f"開始日付変換エラー: {e}")
                    # エラー時は日付フィルタを適用しない

            if end_date:
                try:
                    # 日本時間の23:59:59として日付終了時刻を設定
                    end_datetime = datetime.strptime(f"{end_date} 23:59:59", "%Y-%m-%d %H:%M:%S")
                    end_datetime = jst.localize(end_datetime)
                    query = query.filter(StoreStatus.timestamp <= end_datetime)
                except Exception as e:
                    app.logger.error(f"終了日付変換エラー: {e}")
                    # エラー時は日付フィルタを適用しない

            # ソート順
            query = query.order_by(StoreStatus.timestamp.asc())

            # 総件数を取得
            total_count = query.count()

            # ページネーションまたは制限を適用
            if 'page' in request.args or 'per_page' in request.args:
                offset = (page - 1) * per_page
                query = query.limit(per_page).offset(offset)
            elif limit:
                query = query.limit(limit)

            try:
                # データ取得
                results = query.all()
            except Exception as query_error:
                app.logger.error(f"SQLAlchemyクエリエラー: {query_error}")
                app.logger.error(traceback.format_exc())

                # SQLiteに直接接続して取得を試みる
                try:
                    app.logger.info("SQLiteを直接使用して再試行します")
                    conn = get_db_connection()

                    # SQLクエリの基本部分
                    sql_query = "SELECT * FROM store_status WHERE 1=1"
                    params = []

                    # フィルタリング条件の適用
                    if store:
                        sql_query += " AND store_name = ?"
                        params.append(store)

                    # 日付フィルタは単純化（SQLite文字列比較を使用）
                    if start_date:
                        sql_query += " AND date(timestamp) >= date(?)"
                        params.append(start_date)

                    if end_date:
                        sql_query += " AND date(timestamp) <= date(?)"
                        params.append(end_date)

                    # ソート順
                    sql_query += " ORDER BY timestamp ASC"

                    # 制限
                    if 'page' in request.args or 'per_page' in request.args:
                        offset = (page - 1) * per_page
                        sql_query += f" LIMIT {per_page} OFFSET {offset}"
                    elif limit:
                        sql_query += f" LIMIT {limit}"

                    # 総件数の取得（単純化した近似値）
                    count_sql = "SELECT COUNT(*) FROM store_status"
                    total_count = conn.execute(count_sql).fetchone()[0]

                    # データ取得
                    results = list(conn.execute(sql_query, params).fetchall())
                    conn.close()
                except Exception as sqlite_err:
                    app.logger.error(f"SQLite直接取得エラー: {sqlite_err}")
                    # エラーレスポンスを返す
                    return jsonify({
                        "data": [],
                        "meta": {
                            "error": str(sqlite_err),
                            "message": "データベース処理中にエラーが発生しました",
                            "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
                        }
                    }), 200  # 200を返す（統一化）

            # データのフォーマット
            data = []
            for item in results:
                try:
                    formatted_item = format_store_status(item, jst)
                    if formatted_item:  # Noneでない場合のみ追加
                        data.append(formatted_item)
                except Exception as fmt_err:
                    app.logger.warning(f"フォーマットエラー: {fmt_err}")
                    # format_store_statusが改善されているのでエラーは発生しにくい

            # ページネーション情報
            if 'page' in request.args or 'per_page' in request.args:
                total_pages = (total_count + per_page - 1) // per_page if per_page > 0 else 0
                has_next = page < total_pages
                has_prev = page > 1

                meta = {
                    "page": page,
                    "per_page": per_page,
                    "total_count": total_count,
                    "total_pages": total_pages,
                    "has_next": has_next,
                    "has_prev": has_prev,
                    "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z'),
                    "filter": {
                        "store": store if store else None,
                        "start_date": start_date if start_date else None,
                        "end_date": end_date if end_date else None
                    }
                }
            else:
                # ページネーションなしの場合
                meta = {
                    "total_count": total_count,
                    "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z'),
                    "filter": {
                        "store": store if store else None,
                        "start_date": start_date if start_date else None,
                        "end_date": end_date if end_date else None
                    }
                }

            # レスポンス返却
            return jsonify({
                "data": data,
                "meta": meta
            })

        except Exception as db_error:
            app.logger.error(f"データベース処理エラー: {db_error}")
            app.logger.error(traceback.format_exc())

            # 統一されたエラーレスポンス形式
            return jsonify({
                "data": [],
                "meta": {
                    "error": str(db_error),
                    "message": "データベース処理中にエラーが発生しました",
                    "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
                }
            }), 200  # 200を返す（統一化）

    except Exception as e:
        app.logger.error(f"API /api/history 全体での例外: {e}")
        app.logger.error(traceback.format_exc())

        # 統一されたエラーレスポンス形式
        return jsonify({
            "data": [],
            "meta": {
                "error": str(e),
                "message": "履歴APIの処理中にエラーが発生しました",
                "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
            }
        }), 200  # 200を返す（統一化）


# ---------------------------------------------------------------------
# 6. 管理画面 (店舗URL管理)
# ---------------------------------------------------------------------
@app.route('/admin/manage', methods=['GET', 'POST'])
def manage_store_urls():
    """
    管理画面：店舗URL の追加・編集・削除を行うページ
    """
    if request.method == 'POST':
        store_url = request.form.get('store_url', '').strip()
        if not store_url:
            flash("URLを入力してください。", "warning")
            return redirect(url_for('manage_store_urls'))

        # URLの基本検証
        try:
            from urllib.parse import urlparse
            result = urlparse(store_url)
            if not all([result.scheme, result.netloc]):
                raise ValueError("URLの形式が正しくありません")
        except Exception as e:
            flash(f"無効なURL形式です: {str(e)}", "danger")
            return redirect(url_for('manage_store_urls'))

        existing = StoreURL.query.filter_by(store_url=store_url).first()
        if existing:
            flash("その店舗URLは既に登録されています。", "warning")
        else:
            new_url = StoreURL(store_url=store_url)
            db.session.add(new_url)
            db.session.commit()
            flash("店舗URLを追加しました。", "success")
        return redirect(url_for('manage_store_urls'))

    urls = StoreURL.query.order_by(StoreURL.id.asc()).all()
    return render_template('management.html', urls=urls)


@app.route('/admin/delete/<int:id>', methods=['POST'])
def delete_store_url(id):
    """
    店舗URL の削除処理
    """
    url_obj = StoreURL.query.get_or_404(id)
    db.session.delete(url_obj)
    db.session.commit()
    flash("店舗URLを削除しました。", "success")
    return redirect(url_for('manage_store_urls'))


@app.route('/admin/edit/<int:id>', methods=['GET', 'POST'])
def edit_store_url(id):
    """
    店舗URL の編集ページ（編集機能追加）
    """
    url_obj = StoreURL.query.get_or_404(id)
    if request.method == 'POST':
        new_url = request.form.get('store_url', '').strip()
        if not new_url:
            flash("URLを入力してください。", "warning")
            return redirect(url_for('edit_store_url', id=id))

        # 重複チェック（他のレコードと重複していないか）
        conflict = StoreURL.query.filter(StoreURL.store_url == new_url, StoreURL.id != id).first()
        if conflict:
            flash("そのURLは既に他の店舗として登録されています。", "warning")
            return redirect(url_for('edit_store_url', id=id))

        url_obj.store_url = new_url
        db.session.commit()
        flash("店舗URLを更新しました。", "success")
        return redirect(url_for('manage_store_urls'))

    return render_template('edit_store_url.html', url_data=url_obj)


# ---------------------------------------------------------------------
# 7. 統合ダッシュボードルート
# ---------------------------------------------------------------------
@app.route('/')
def index():
    """
    統合ダッシュボードの HTML を返すルート
    """
    return render_template('integrated_dashboard.html')


# ---------------------------------------------------------------------
# 8. 手動スクレイピング実行ルート（定期スクレイピングの次回実行時刻を更新）
# ---------------------------------------------------------------------
@app.route('/admin/manual_scrape', methods=['POST'])
def manual_scrape():
    """
    管理画面などから手動でスクレイピングを実行し、その完了時刻を基準に
    次回定期スクレイピングの実行時刻を現在時刻＋1時間に更新するルート
    """
    # 手動実行
    scheduled_scrape()
    # 次回実行時刻を現在時刻＋1時間に設定
    next_time = datetime.now() + timedelta(hours=1)
    try:
        scheduler.modify_job('scrape_job', next_run_time=next_time)
        flash("手動スクレイピングを実行しました。次回は {} に実行されます。".format(next_time.strftime("%Y-%m-%d %H:%M:%S")), "success")
    except Exception as e:
        flash("スクレイピングジョブの次回実行時刻更新に失敗しました: " + str(e), "warning")
    return redirect(url_for('manage_store_urls'))


# ---------------------------------------------------------------------
# 9. API エンドポイント (移動済み)
# ---------------------------------------------------------------------

# 新規エンドポイント: 平均稼働ランキング
@app.route('/api/ranking/average')
@cache.memoize(timeout=600)  # キャッシュ：10分間間有効
def api_average_ranking():
    """
    店舗の平均稼働率ランキングを返すエンドポイント

    クエリパラメータ:
        biz_type: 業種でフィルタリング
        limit: 上位何件を返すか（デフォルト5000件）
        min_samples: 最小サンプル数（デフォルト1件）
    """
    try:
        # フィルタリング条件
        biz_type = request.args.get('biz_type')
        # デフォルト値を5000に増加（通常は全店舗データを取得できるよう）
        limit = request.args.get('limit', 5000, type=int)
        min_samples = request.args.get('min_samples', 1, type=int)  # サンプル数の最小値（デフォルト1）

        # 最大値を制限（これも10000に引き上げ）
        if limit > 10000:
            limit = 10000

        # JSTタイムゾーン設定
        jst = pytz.timezone('Asia/Tokyo')
        now_jst = datetime.now(jst)

        app.logger.info(f"ランキング取得パラメータ: biz_type={biz_type}, limit={limit}, min_samples={min_samples}")

        # SQLiteを直接使用する方法に変更
        conn = get_db_connection()

        try:
            # フィルタリング条件を構築
            conditions = ["working_staff > 0"]
            params = []

            if biz_type:
                conditions.append("biz_type = ?")
                params.append(biz_type)

            # 条件文字列
            where_clause = " AND ".join(conditions)

            # 平均稼働率の計算とランキングクエリ
            query = f"""
            SELECT 
                store_name,
                AVG((working_staff - active_staff) * 100.0 / working_staff) as avg_rate,
                COUNT(*) as sample_count,
                MAX(biz_type) as biz_type,
                MAX(genre) as genre,
                MAX(area) as area
            FROM store_status
            WHERE {where_clause}
            GROUP BY store_name
            HAVING COUNT(*) >= ?
            ORDER BY avg_rate DESC
            LIMIT ?
            """

            # パラメータの追加
            params.append(min_samples)
            params.append(limit)

            # クエリ実行
            app.logger.debug(f"実行SQL: {query}")
            app.logger.debug(f"パラメータ: {params}")

            cursor = conn.execute(query, params)
            results = cursor.fetchall()

            # 結果件数ログ出力
            app.logger.info(f"平均稼働ランキング取得: {len(results)}件のデータを取得")

            # 結果が0件の場合、サンプル数を緩和して再取得
            if len(results) == 0 and min_samples > 1:
                app.logger.warning(f"サンプル数{min_samples}以上の店舗が見つからなかったため、条件を緩和します")

                # パラメータを更新（サンプル数条件を1に）
                params[-2] = 1

                # 再度クエリ実行
                cursor = conn.execute(query, params)
                results = cursor.fetchall()
                app.logger.info(f"条件緩和後の取得件数: {len(results)}件")

            # 結果を整形
            data = []
            for r in results:
                avg_rate = r['avg_rate']
                avg_rate_float = 0.0
                if avg_rate is not None:
                    try:
                        avg_rate_float = float(avg_rate)
                    except (ValueError, TypeError):
                        avg_rate_float = 0.0

                data.append({
                    'store_name': r['store_name'],
                    'avg_rate': round(avg_rate_float, 1),
                    'sample_count': r['sample_count'],
                    'biz_type': r['biz_type'] if r['biz_type'] else '不明',
                    'genre': r['genre'] if r['genre'] else '不明',
                    'area': r['area'] if r['area'] else '不明'
                })

            # 接続クローズ
            conn.close()

            # 統一されたレスポンス形式で返す
            return jsonify({
                "data": data,
                "meta": {
                    "count": len(data),
                    "biz_type": biz_type if biz_type else "全業種",
                    "min_samples": min_samples,
                    "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
                }
            })

        except Exception as query_error:
            if conn:
                conn.close()

            app.logger.error(f"ランキングクエリエラー: {query_error}")
            app.logger.error(traceback.format_exc())

            # 統一されたエラーレスポンス形式
            return jsonify({
                "data": [],
                "meta": {
                    "error": str(query_error),
                    "message": "ランキングクエリの実行中にエラーが発生しました",
                    "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
                }
            }), 200  # 200を返す（統一化）

    except Exception as e:
        app.logger.error(f"平均稼働ランキング取得エラー: {e}")
        app.logger.error(traceback.format_exc())

        # JSTタイムゾーン (エラー処理のためにここでも定義)
        try:
            jst = pytz.timezone('Asia/Tokyo')
            now_jst = datetime.now(jst)
            current_time = now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
        except Exception:
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 統一されたエラーレスポンス形式
        return jsonify({
            "data": [],
            "meta": {
                "error": str(e),
                "message": "ランキングの取得中にエラーが発生しました",
                "current_time": current_time
            }
        }), 200  # 200を返す（統一化）

# 業種内ジャンルランキングのAPIエンドポイント
@app.route('/api/ranking/genre')
@cache.memoize(timeout=600)  # キャッシュ：10分間有効
def api_genre_ranking():
    """
    業種内のジャンル別平均稼働率ランキングを返すエンドポイント

    クエリパラメータ:
        biz_type: 業種でフィルタリング（指定なしの場合は全業種）
    """
    biz_type = request.args.get('biz_type')

    # JSTタイムゾーン設定
    jst = pytz.timezone('Asia/Tokyo')
    now_jst = datetime.now(jst)

    # 業種が指定されていない場合は全業種のジャンル集計を返す
    if not biz_type:
        app.logger.info("業種が指定されていません。全業種のジャンルランキングを返します。")
        # SQLiteを直接使用
        try:
            conn = get_db_connection()

            # 全業種のジャンル別平均稼働率を計算するクエリ
            query = """
            SELECT 
                genre,
                COUNT(DISTINCT store_name) as store_count,
                AVG((working_staff - active_staff) * 100.0 / working_staff) as avg_rate
            FROM store_status
            WHERE working_staff > 0 AND genre IS NOT NULL AND genre != ''
            GROUP BY genre
            ORDER BY avg_rate DESC
            """

            cursor = conn.execute(query)
            results = cursor.fetchall()
            conn.close()

            # 結果を整形
            data = []
            for result in results:
                genre = result['genre'] if result['genre'] else "不明"
                store_count = result['store_count'] if result['store_count'] else 0

                # avg_rate の安全な取得と変換
                avg_rate = 0
                try:
                    if result['avg_rate'] is not None:
                        avg_rate = float(result['avg_rate'])
                except (ValueError, TypeError) as e:
                    app.logger.warning(f"平均値の変換エラー: {e}, genre: {genre}")

                data.append({
                    "genre": genre,
                    "store_count": store_count,
                    "avg_rate": round(avg_rate, 1)
                })

            # 結果が空の場合はダミーデータを作成（フロントエンドのエラー回避）
            if not data:
                data = [{"genre": "データなし", "store_count": 0, "avg_rate": 0.0}]

            return jsonify({
                "data": data,
                "meta": {
                    "biz_type": "全業種",
                    "count": len(data),
                    "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
                }
            }), 200

        except Exception as e:
            app.logger.error(f"全業種ジャンルランキング取得エラー: {e}")
            app.logger.error(traceback.format_exc())

            # ダミーデータを返す（エラー情報付き）
            return jsonify({
                "data": [
                    {"genre": "データ取得エラー", "store_count": 0, "avg_rate": 0.0}
                ],
                "meta": {
                    "error": str(e),
                    "message": "全業種ジャンルランキングの取得中にエラーが発生しました",
                    "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
                }
            }), 200

    # 特定業種のジャンルランキングを取得
    try:
        # SQLiteを直接使用する方法に変更
        conn = get_db_connection()

        try:
            # ジャンル別の平均稼働率を計算するクエリ
            query = """
            SELECT 
                genre,
                COUNT(DISTINCT store_name) as store_count,
                AVG((working_staff - active_staff) * 100.0 / working_staff) as avg_rate
            FROM store_status
            WHERE biz_type = ? AND working_staff > 0
            GROUP BY genre
            ORDER BY avg_rate DESC
            """

            # クエリ実行
            app.logger.debug(f"実行SQL: {query}")
            app.logger.debug(f"パラメータ: {biz_type}")

            cursor = conn.execute(query, [biz_type])
            results = cursor.fetchall()

            # 接続クローズ
            conn.close()

            # 結果が空の場合でもダミーデータを生成（フロントエンドのエラー回避のため）
            if not results:
                app.logger.warning(f"業種「{biz_type}」のジャンルデータがありません。ダミーデータを返します。")
                return jsonify({
                    "data": [
                        {"genre": "該当データなし", "store_count": 0, "avg_rate": 0.0}
                    ],
                    "meta": {
                        "biz_type": biz_type,
                        "count": 1,
                        "message": "該当するジャンルデータがありません",
                        "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
                    }
                }), 200

            # 結果を整形
            data = []
            for result in results:
                genre = result['genre'] if result['genre'] else "不明"
                store_count = result['store_count'] if result['store_count'] else 0

                # avg_rate の安全な取得と変換
                avg_rate = 0
                try:
                    if result['avg_rate'] is not None:
                        avg_rate = float(result['avg_rate'])
                except (ValueError, TypeError) as e:
                    app.logger.warning(f"平均値の変換エラー: {e}, genre: {genre}")

                data.append({
                    "genre": genre,
                    "store_count": store_count,
                    "avg_rate": round(avg_rate, 1)
                })

            # 統一されたレスポンス形式で返す
            return jsonify({
                "data": data,
                "meta": {
                    "biz_type": biz_type,
                    "count": len(data),
                    "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
                }
            }), 200

        except Exception as query_error:
            if conn:
                conn.close()

            app.logger.error(f"ジャンルランキングクエリエラー: {query_error}")
            app.logger.error(traceback.format_exc())

            # ダミーデータを返す（エラー情報付き）
            return jsonify({
                "data": [
                    {"genre": "エラー発生", "store_count": 0, "avg_rate": 0.0}
                ],
                "meta": {
                    "error": str(query_error),
                    "message": "ジャンルランキングクエリの実行中にエラーが発生しました",
                    "biz_type": biz_type,
                    "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
                }
            })

# 時間帯別稼働率分析のAPIエンドポイント
@app.route('/api/hourly-analysis')
@cache.memoize(timeout=600)  # キャッシュ：10分間有効
def api_hourly_analysis():
    """
    時間帯別の平均稼働率を返すエンドポイント

    クエリパラメータ:
        store: 特定の店舗名でフィルタリング（省略時は全店舗の平均）
    """
    # JSTタイムゾーン設定
    jst = pytz.timezone('Asia/Tokyo')
    now_jst = datetime.now(jst)

    try:
        # クエリパラメータの取得
        store = request.args.get('store')

        # クエリの構築
        query = """
        SELECT 
            strftime('%H', timestamp) AS hour,
            AVG((working_staff - active_staff) * 100.0 / working_staff) AS avg_rate,
            COUNT(*) AS sample_count
        FROM store_status
        WHERE working_staff > 0
        """

        params = []
        if store:
            query += " AND store_name = ?"
            params.append(store)

        query += """
        GROUP BY strftime('%H', timestamp)
        ORDER BY hour
        """

        # SQLiteに直接接続
        conn = get_db_connection()
        results = list(conn.execute(query, params).fetchall())
        conn.close()

        # 結果をフォーマット
        hourly_data = []
        for hour in range(24):  # 0-23時まで全時間帯のデータを用意
            hour_str = f"{hour:02d}"

            # 該当する時間帯のデータを検索
            found = False
            for row in results:
                if row['hour'] == hour_str:
                    try:
                        avg_rate = float(row['avg_rate']) if row['avg_rate'] is not None else 0
                    except (ValueError, TypeError):
                        avg_rate = 0

                    hourly_data.append({
                        'hour': hour,
                        'hour_str': f"{hour}:00",
                        'avg_rate': round(avg_rate, 1),
                        'sample_count': row['sample_count']
                    })
                    found = True
                    break

            # データがない時間帯は0で埋める
            if not found:
                hourly_data.append({
                    'hour': hour,
                    'hour_str': f"{hour}:00",
                    'avg_rate': 0,
                    'sample_count': 0
                })

        # 統一されたレスポンス形式で返す
        return jsonify({
            "data": hourly_data,
            "meta": {
                "store": store if store else "全店舗",
                "count": len(hourly_data),
                "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
            }
        })

    except Exception as e:
        app.logger.error(f"時間帯別分析取得エラー: {e}")
        app.logger.error(traceback.format_exc())

        # 統一されたエラーレスポンス形式
        return jsonify({
            "data": [],
            "meta": {
                "error": str(e),
                "message": "時間帯別分析の取得中にエラーが発生しました",
                "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
            }
        }), 200  # 統一したレスポンスコード

# 人気店舗ランキングのAPIエンドポイント
@app.route('/api/ranking/popular')
@cache.memoize(timeout=600)  # キャッシュ：10分間有効
def api_popular_ranking():
    """
    人気店舗ランキングを返すエンドポイント

    クエリパラメータ:
        biz_type: 業種でフィルタリング
        period: ranking_period - 'daily', 'weekly', 'monthly', 'all'（デフォルトは'monthly'）
        limit: 上位何件を返すか（デフォルト20件）
    """
    try:
        # クエリパラメータの取得
        biz_type = request.args.get('biz_type')
        period = request.args.get('period', 'monthly')
        limit = request.args.get('limit', 20, type=int)

        # JSTタイムゾーン設定
        jst = pytz.timezone('Asia/Tokyo')
        now_jst = datetime.now(jst)

        # 期間に基づいてテーブル選択
        if period == 'daily':
            table_name = 'daily_averages'
        elif period == 'weekly':
            table_name = 'weekly_averages'
        elif period == 'monthly':
            table_name = 'monthly_averages'
        else:  # 'all'
            table_name = 'store_averages'

        # クエリの構築
        query = f"""
        SELECT 
            store_name,
            avg_rate,
            sample_count,
            biz_type,
            genre,
            area
        FROM {table_name}
        WHERE sample_count > 0
        """

        params = []
        if biz_type:
            query += " AND biz_type = ?"
            params.append(biz_type)

        query += " ORDER BY avg_rate DESC LIMIT ?"
        params.append(limit)

        # SQLiteに直接接続
        conn = get_db_connection()
        results = list(conn.execute(query, params).fetchall())
        conn.close()

        # 結果をフォーマット
        ranking_data = []
        for idx, row in enumerate(results, 1):
            try:
                avg_rate = float(row['avg_rate']) if row['avg_rate'] is not None else 0
            except (ValueError, TypeError):
                avg_rate = 0

            ranking_data.append({
                'rank': idx,
                'store_name': row['store_name'],
                'avg_rate': round(avg_rate, 1),
                'sample_count': row['sample_count'],
                'biz_type': row['biz_type'] if row['biz_type'] else '不明',
                'genre': row['genre'] if row['genre'] else '不明',
                'area': row['area'] if row['area'] else '不明'
            })

        # 統一されたレスポンス形式で返す
        return jsonify({
            "data": ranking_data,
            "meta": {
                "period": period,
                "biz_type": biz_type if biz_type else "全業種",
                "count": len(ranking_data),
                "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
            }
        })

    except Exception as e:
        app.logger.error(f"人気店舗ランキング取得エラー: {e}")
        app.logger.error(traceback.format_exc())

        # 統一されたエラーレスポンス形式
        return jsonify({
            "data": [],
            "meta": {
                "error": str(e),
                "message": "人気店舗ランキングの取得中にエラーが発生しました",
                "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
            }
        }), 200  # 統一したレスポンスコード

                    "biz_type": biz_type,
                    "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
                }
            }), 200

    except Exception as e:
        app.logger.error(f"ジャンルランキング取得エラー: {e}")
        app.logger.error(traceback.format_exc())

        # ダミーデータを返す（エラー情報付き）
        return jsonify({
            "data": [
                {"genre": "サーバーエラー", "store_count": 0, "avg_rate": 0.0}
            ],
            "meta": {
                "error": str(e),
                "message": "ジャンルランキングの取得中にエラーが発生しました",
                "biz_type": biz_type,
                "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
            }
        }), 200

# 集計済みデータを提供するエンドポイント
@app.route('/api/aggregated')
@cache.memoize(timeout=3600)  # キャッシュ：1時間有効
def api_aggregated_data():
    """
    日付ごとに集計された平均稼働率データを返すエンドポイント
    """
    # JSTタイムゾーン設定
    jst = pytz.timezone('Asia/Tokyo')
    now_jst = datetime.now(jst)

    try:
        # SQLiteを直接使用する方法に変更
        conn = get_db_connection()

        try:
            # 日付ごとの平均稼働率を計算するクエリ
            query = """
            SELECT 
                date(timestamp) as date,
                AVG((working_staff - active_staff) * 100.0 / working_staff) as avg_rate,
                COUNT(*) as sample_count
            FROM store_status
            WHERE working_staff > 0
            GROUP BY date(timestamp)
            ORDER BY date(timestamp)
            """

            # クエリ実行
            app.logger.debug(f"実行SQL: {query}")

            cursor = conn.execute(query)
            results = cursor.fetchall()

            # 接続クローズ
            conn.close()

            app.logger.info(f"集計クエリ実行結果: {len(results)}件のデータを取得")

            # 結果を整形
            data = []
            skipped_records = 0

            for r in results:
                # 詳細なデバッグログを追加
                app.logger.debug(f"処理中のレコード: date={r['date']}, avg_rate={r['avg_rate']}, sample_count={r['sample_count']}")

                # date属性がNoneの場合はスキップ
                if r['date'] is None:
                    app.logger.warning(f"日付が未設定のレコードをスキップします: {r}")
                    skipped_records += 1
                    continue

                try:
                    # 日付文字列の取得
                    date_str = r['date']

                    # 平均稼働率と件数のチェック
                    avg_rate = 0.0
                    if r['avg_rate'] is not None:
                        try:
                            avg_rate = float(r['avg_rate'])
                        except (ValueError, TypeError) as e:
                            app.logger.warning(f"平均稼働率の変換に失敗しました: {r['avg_rate']}, エラー: {e}")

                    sample_count = r['sample_count'] or 0

                    data.append({
                        'date': date_str,
                        'avg_rate': round(avg_rate, 1),  # 丸める処理を追加
                        'sample_count': sample_count
                    })
                    app.logger.debug(f"データに追加: date={date_str}, avg_rate={avg_rate}, sample_count={sample_count}")
                except (AttributeError, TypeError, ValueError) as e:
                    app.logger.error(f"データ変換エラー: {e}, レコード: {r}")
                    app.logger.error(traceback.format_exc())
                    skipped_records += 1
                    continue

            # 集計情報のログ出力
            app.logger.info(f"集計データ処理完了: 有効={len(data)}件, スキップ={skipped_records}件")

            # 統一されたレスポンス形式で返す
            return jsonify({
                "data": data,
                "meta": {
                    "count": len(data),
                    "skipped_records": skipped_records,
                    "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
                }
            }), 200

        except Exception as query_error:
            if conn:
                conn.close()

            app.logger.error(f"集計データクエリエラー: {query_error}")
            app.logger.error(traceback.format_exc())

            # 統一されたエラーレスポンス形式
            return jsonify({
                "data": [],
                "meta": {
                    "error": str(query_error),
                    "message": "集計データクエリの実行中にエラーが発生しました",
                    "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
                }
            }), 200  # 統一したレスポンスコード

    except Exception as e:
        app.logger.error(f"API集計データ取得エラー: {e}")
        app.logger.error(traceback.format_exc())

        # 統一されたエラーレスポンス形式
        return jsonify({
            "data": [],
            "meta": {
                "error": str(e),
                "message": "集計データの取得中にエラーが発生しました",
                "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
            }
        }), 200  # 統一したレスポンスコード

@app.route('/api/averages/daily')
@cache.memoize(timeout=600)  # キャッシュ：10分間有効
def api_daily_averages():
    """
    日次（直近24時間）の平均稼働率データを返すエンドポイント
    """
    from aggregated_data import AggregatedData

    results = AggregatedData.get_daily_averages()

    jst = pytz.timezone('Asia/Tokyo')
    data = [{
        'store_name': r.store_name,
        'avg_rate': float(r.avg_rate),
        'sample_count': r.sample_count,
        'start_date': r.start_date.astimezone(jst).isoformat() if r.start_date else None,
        'end_date': r.end_date.astimezone(jst).isoformat() if r.end_date else None,
        'biz_type': r.biz_type,
        'genre': r.genre,
        'area': r.area
    } for r in results]

    # フロントエンド側の前提に合わせてデータを包む
    return jsonify({"data": data})

@app.route('/api/averages/weekly')
@cache.memoize(timeout=1800)  # キャッシュ：30分間有効
def api_weekly_averages():
    """
    週次（直近7日間）の平均稼働率データを返すエンドポイント
    """
    from aggregated_data import AggregatedData

    results = AggregatedData.get_weekly_averages()

    jst = pytz.timezone('Asia/Tokyo')
    data = [{
        'store_name': r.store_name,
        'avg_rate': float(r.avg_rate),
        'sample_count': r.sample_count,
        'start_date': r.start_date.astimezone(jst).isoformat() if r.start_date else None,
        'end_date': r.end_date.astimezone(jst).isoformat() if r.end_date else None,
        'biz_type': r.biz_type,
        'genre': r.genre,
        'area': r.area
    } for r in results]

    # フロントエンド側の前提に合わせてデータを包む
    return jsonify({"data": data})

@app.route('/api/averages/monthly')
@cache.memoize(timeout=1800)  # キャッシュ：30分間有効
def api_monthly_averages():
    """
    月次（直近30日間）の平均稼働率データを返すエンドポイント
    """
    from aggregated_data import AggregatedData

    results = AggregatedData.get_monthly_averages()

    jst = pytz.timezone('Asia/Tokyo')
    data = [{
        'store_name': r.store_name,
        'avg_rate': float(r.avg_rate),
        'sample_count': r.sample_count,
        'start_date': r.start_date.astimezone(jst).isoformat() if r.start_date else None,
        'end_date': r.end_date.astimezone(jst).isoformat() if r.end_date else None,
        'biz_type': r.biz_type,
        'genre': r.genre,
        'area': r.area
    } for r in results]

    # フロントエンド側の前提に合わせてデータを包む
    return jsonify({"data": data})

@app.route('/api/averages/stores')
@cache.memoize(timeout=3600)  # キャッシュ：1時間有効
def api_store_averages():
    """
    店舗ごとの全期間平均稼働率データを返すエンドポイント
    """
    from aggregated_data import AggregatedData

    results = AggregatedData.get_store_averages()

    jst = pytz.timezone('Asia/Tokyo')
    data = [{
        'store_name': r.store_name,
        'avg_rate': float(r.avg_rate),
        'sample_count': r.sample_count,
        'start_date': r.start_date.astimezone(jst).isoformat() if r.start_date else None,
        'end_date': r.end_date.astimezone(jst).isoformat() if r.end_date else None,
        'biz_type': r.biz_type,
        'genre': r.genre,
        'area': r.area
    } for r in results]

    # フロントエンド側の前提に合わせてデータを包む
    return jsonify({"data": data})

@app.route('/api/history/optimized')
@cache.memoize(timeout=300)  # キャッシュ：5分間有効
def api_history_optimized():
    """
    最適化されたスクレイピング履歴を返すエンドポイント
    - 大量データの場合は日時でダウンサンプリングする
    - 指定店舗のみの場合は完全なデータを返す
    """
    from page_helper import paginate_query_results, format_store_status

    # JSTタイムゾーン
    jst = pytz.timezone('Asia/Tokyo')
    now_jst = datetime.now(jst)

    try:
        # 店舗名フィルター（指定された場合は完全なデータを返す）
        store = request.args.get('store')
        if store:
            # api_history()を直接呼び出さず、リダイレクトさせる
            return api_history()

        # 日付範囲フィルター
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')

        # 期間指定なし、かつ店舗指定なしの場合はダウンサンプリングを行う
        if not start_date and not end_date:
            # 期間指定なしの場合、過去7日間のデータだけに限定
            start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')

        # 基本クエリ
        query = StoreStatus.query

        if start_date:
            try:
                # 日本時間の00:00:00として日付開始時刻を設定
                start_datetime = datetime.strptime(f"{start_date} 00:00:00", "%Y-%m-%d %H:%M:%S")
                start_datetime = jst.localize(start_datetime)
                query = query.filter(StoreStatus.timestamp >= start_datetime)
            except Exception as e:
                app.logger.error(f"開始日付変換エラー: {e}")
                # エラー時は期間を7日間に設定
                start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
                try:
                    start_datetime = datetime.strptime(f"{start_date} 00:00:00", "%Y-%m-%d %H:%M:%S")
                    start_datetime = jst.localize(start_datetime)
                    query = query.filter(StoreStatus.timestamp >= start_datetime)
                except Exception:
                    # 再度失敗した場合はフィルタを適用しない
                    pass

        if end_date:
            try:
                # 日本時間の23:59:59として日付終了時刻を設定
                end_datetime = datetime.strptime(f"{end_date} 23:59:59", "%Y-%m-%d %H:%M:%S")
                end_datetime = jst.localize(end_datetime)
                query = query.filter(StoreStatus.timestamp <= end_datetime)
            except Exception as e:
                app.logger.error(f"終了日付変換エラー: {e}")
                # エラー時は日付フィルタを適用しない

        try:
            # 全件数をカウント
            total_count = query.count()
            app.logger.info(f"検索条件に該当するレコード数: {total_count}件")
        except Exception as count_err:
            app.logger.error(f"レコード数カウントエラー: {count_err}")
            total_count = 10001  # エラー時は多いと仮定してダウンサンプリング実行

        # 1万件を超える場合はダウンサンプリング
        if total_count > 10000:
            app.logger.info("データ量が多いためダウンサンプリングを実行します")
            try:
                # 各店舗から一定間隔でサンプリング
                store_names_query = db.session.query(func.distinct(StoreStatus.store_name))
                store_names = [s[0] for s in store_names_query.all() if s[0]]

                app.logger.info(f"サンプリング対象の店舗数: {len(store_names)}店舗")

                # ダウンサンプリングされたデータ
                sampled_data = []

                # サンプリング処理でエラーが発生した店舗数
                error_stores = 0

                for store_name in store_names:
                    try:
                        # 各店舗のデータを時間順に取得
                        store_data = query.filter(StoreStatus.store_name == store_name).order_by(StoreStatus.timestamp.asc()).all()

                        # サンプリング率を決定（最大100ポイント/店舗）
                        data_len = len(store_data)
                        sample_rate = max(1, data_len // 100)

                        # サンプリング
                        sampled_store_data = store_data[::sample_rate]
                        sampled_data.extend(sampled_store_data)

                        app.logger.debug(f"店舗 '{store_name}' から {len(sampled_store_data)}/{data_len} のデータをサンプリング")
                    except Exception as store_err:
                        app.logger.error(f"店舗 '{store_name}' のサンプリング中にエラー: {store_err}")
                        error_stores += 1
                        continue

                if not sampled_data:
                    app.logger.warning("サンプリング結果が0件です。通常のapi_historyにフォールバックします。")
                    return api_history()

                # 時間順にソート
                sampled_data.sort(key=lambda x: x.timestamp if hasattr(x, 'timestamp') and x.timestamp else datetime.min)

                # データのフォーマット - Noneを返す可能性を考慮
                data = []
                for item in sampled_data:
                    formatted_item = format_store_status(item, jst)
                    if formatted_item:  # Noneでない場合のみ追加
                        data.append(formatted_item)

                # サンプリング率をエラーが発生した店舗がない場合のみ計算
                sample_rate_info = sample_rate if error_stores == 0 else "可変"

                # メタデータを含むレスポンス（統一形式）
                response = {
                    "data": data,
                    "meta": {
                        "original_count": total_count,
                        "sampled_count": len(data),
                        "sampling_rate": sample_rate_info,
                        "is_sampled": True,
                        "error_stores": error_stores,
                        "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z'),
                        "filter": {
                            "store": store if store else None,
                            "start_date": start_date if start_date else None,
                            "end_date": end_date if end_date else None
                        }
                    }
                }
                return jsonify(response)
            except Exception as sample_err:
                app.logger.error(f"ダウンサンプリング中のエラー: {sample_err}")
                app.logger.error(traceback.format_exc())
                # エラー時はapi_historyにフォールバック
                return api_history()
        else:
            # 1万件以下の場合は通常のページネーション処理
            app.logger.info(f"データ量が{total_count}件のため、通常のapi_historyを使用します")
            return api_history()
    except Exception as e:
        app.logger.error(f"API /api/history/optimized 全体での例外: {e}")
        app.logger.error(traceback.format_exc())

        # 統一されたエラーレスポンス形式
        return jsonify({
            "data": [],
            "meta": {
                "error": str(e),
                "message": "最適化された履歴APIの処理中にエラーが発生しました",
                "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
            }
        }), 500

# エリア統計データを提供するエンドポイント
@app.route('/api/area-stats')
@cache.memoize(timeout=600)  # キャッシュ：10分間有効
def api_area_stats():
    """
    エリア別の統計情報を返すエンドポイント
    """
    # JSTタイムゾーン設定
    jst = pytz.timezone('Asia/Tokyo')
    now_jst = datetime.now(jst)

    try:
        # 最新の各店舗データを取得するサブクエリ
        subq = db.session.query(
            StoreStatus.store_name,
            func.max(StoreStatus.timestamp).label('max_timestamp')
        ).group_by(StoreStatus.store_name).subquery()

        # 各エリアの店舗数と平均稼働率を計算
        query = db.session.query(
            StoreStatus.area,
            func.count(func.distinct(StoreStatus.store_name)).label('store_count'),
            func.avg(
                (StoreStatus.working_staff - StoreStatus.active_staff) * 100.0 / 
                func.nullif(StoreStatus.working_staff, 0)
            ).label('avg_rate')
        ).join(
            subq,
            and_(
                StoreStatus.store_name == subq.c.store_name,
                StoreStatus.timestamp == subq.c.max_timestamp
            )
        ).filter(
            StoreStatus.working_staff > 0  # 稼働中のスタッフがいる店舗のみで計算
        ).group_by(StoreStatus.area)

        results = query.all()

        # 結果をフォーマット
        formatted_results = []
        for result in results:
            # 結果のアンパック - NULL安全対応
            area = getattr(result, 'area', None) or '不明'
            store_count = getattr(result, 'store_count', 0)

            # avg_rate の安全な取得と変換
            avg_rate = 0
            try:
                raw_avg_rate = getattr(result, 'avg_rate', None)
                if raw_avg_rate is not None:
                    avg_rate = float(raw_avg_rate)
            except (ValueError, TypeError) as e:
                app.logger.warning(f"平均値の変換エラー: {e}, raw_value: {getattr(result, 'avg_rate', None)}")

            formatted_results.append({
                'area': area,
                'store_count': store_count,
                'avg_rate': round(avg_rate, 2)
            })

        # 統一されたレスポンス形式で返す
        return jsonify({
            "data": formatted_results,
            "meta": {
                "count": len(formatted_results),
                "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
            }
        })
    except Exception as e:
        app.logger.error(f"エリア統計取得エラー: {e}")
        app.logger.error(traceback.format_exc())

        # 統一されたエラーレスポンス形式
        return jsonify({
            "data": [],
            "meta": {
                "error": str(e),
                "message": "エリア統計の取得中にエラーが発生しました",
                "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
            }
        }), 500

# すべての店舗名を取得するAPIエンドポイント
@app.route('/api/store-names')
@cache.memoize(timeout=600)  # キャッシュ：10分間有効
def api_store_names():
    """
    データベースに登録されているすべての店舗名を返すエンドポイント
    """
    # JSTタイムゾーン設定
    jst = pytz.timezone('Asia/Tokyo')
    now_jst = datetime.now(jst)

    try:
        # 重複を除去した店舗名のリストを取得
        store_names = db.session.query(StoreStatus.store_name).distinct().all()
        # タプルのリストからプレーンな文字列のリストに変換
        store_names = [name[0] for name in store_names if name[0]]
        # アルファベット順にソート
        store_names.sort(key=lambda x: x.lower())

        # 統一されたレスポンス形式で返す
        return jsonify({
            "data": store_names,
            "meta": {
                "count": len(store_names),
                "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
            }
        })
    except Exception as e:
        app.logger.error(f"店舗名リスト取得エラー: {e}")
        app.logger.error(traceback.format_exc())

        # 統一されたエラーレスポンス形式
        return jsonify({
            "data": [],
            "meta": {
                "error": str(e),
                "message": "店舗名リストの取得中にエラーが発生しました",
                "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
            }
        }), 500

# トップランキングを提供するエンドポイント
@app.route('/api/ranking/top')
@cache.memoize(timeout=600)  # キャッシュ：10分間有効
def api_top_ranking():
    """
    各業種のトップ店舗ランキングを返すエンドポイント

    クエリパラメータ:
        limit: 各業種ごとの上位件数（デフォルト3件）
        min_samples: 最小サンプル数（デフォルト1件）
    """
    # JSTタイムゾーン設定
    jst = pytz.timezone('Asia/Tokyo')
    now_jst = datetime.now(jst)

    try:
        limit = request.args.get('limit', 3, type=int)
        min_samples = request.args.get('min_samples', 1, type=int)  # サンプル数条件を緩和（デフォルト1）

        # 業種の一覧を取得
        try:
            biz_types = db.session.query(StoreStatus.biz_type).distinct().all()
            biz_types = [bt[0] for bt in biz_types if bt[0]]  # None値を除外
        except Exception as bt_err:
            app.logger.error(f"業種リスト取得エラー: {bt_err}")

            # SQLiteを直接使用して取得を試みる
            try:
                conn = get_db_connection()
                cursor = conn.execute("SELECT DISTINCT biz_type FROM store_status WHERE biz_type IS NOT NULL AND biz_type != ''")
                biz_types = [row['biz_type'] for row in cursor.fetchall()]
                conn.close()
            except Exception as sqlite_err:
                app.logger.error(f"SQLite業種リスト取得エラー: {sqlite_err}")
                # 代表的な業種を手動で設定
                biz_types = ['キャバクラ', 'ガールズバー', 'セクキャバ', '風俗店', 'メンズエステ']
                app.logger.warning(f"デフォルト業種リストを使用します: {biz_types}")

        # 業種リストが空の場合
        if not biz_types:
            biz_types = ['キャバクラ', 'ガールズバー', 'セクキャバ', '風俗店', 'メンズエステ']
            app.logger.warning(f"業種リストが空のため、デフォルト業種リストを使用します: {biz_types}")

        result = {}
        biz_type_counts = {}

        # SQLiteを直接使用
        conn = get_db_connection()

        for biz_type in biz_types:
            try:
                # 各業種ごとにトップ店舗を取得（サンプル数条件を変数化）
                query = """
                SELECT 
                    store_name,
                    AVG((working_staff - active_staff) * 100.0 / working_staff) as avg_rate,
                    COUNT(*) as sample_count,
                    MAX(genre) as genre,
                    MAX(area) as area
                FROM store_status
                WHERE biz_type = ? AND working_staff > 0
                GROUP BY store_name
                HAVING COUNT(*) >= ?
                ORDER BY avg_rate DESC
                LIMIT ?
                """

                cursor = conn.execute(query, [biz_type, min_samples, limit])
                subq = cursor.fetchall()

                # データがない場合、サンプル数条件を1に緩和して再試行
                if not subq and min_samples > 1:
                    app.logger.warning(f"業種「{biz_type}」でデータが見つからなかったため、サンプル数条件を緩和します")
                    cursor = conn.execute(query, [biz_type, 1, limit])
                    subq = cursor.fetchall()

                if subq:
                    result[biz_type] = []
                    for r in subq:
                        # avg_rate の安全な取得と変換
                        avg_rate = 0
                        try:
                            if r['avg_rate'] is not None:
                                avg_rate = float(r['avg_rate'])
                        except (ValueError, TypeError) as e:
                            app.logger.warning(f"平均値の変換エラー: {e}, store: {r['store_name']}")

                        result[biz_type].append({
                            'store_name': r['store_name'],
                            'avg_rate': round(avg_rate, 1),
                            'sample_count': r['sample_count'],
                            'genre': r['genre'] if r['genre'] else '不明',
                            'area': r['area'] if r['area'] else '不明'
                        })
                    biz_type_counts[biz_type] = len(result[biz_type])
                else:
                    # 2回目の試行でも結果がない場合のみダミーデータを生成
                    app.logger.warning(f"業種「{biz_type}」ではデータが見つかりませんでした")
                    result[biz_type] = [{
                        'store_name': f'{biz_type}データなし',
                        'avg_rate': 0.0,
                        'sample_count': 0,
                        'genre': '不明',
                        'area': '不明'
                    }]
                    biz_type_counts[biz_type] = 1
            except Exception as biz_err:
                app.logger.error(f"業種「{biz_type}」のランキング取得エラー: {biz_err}")
                # エラー時もダミーデータを用意
                result[biz_type] = [{
                    'store_name': f'{biz_type}データエラー',
                    'avg_rate': 0.0,
                    'sample_count': 0,
                    'genre': 'エラー',
                    'area': 'エラー'
                }]
                biz_type_counts[biz_type] = 1

        # 接続をクローズ
        conn.close()

        # 結果が空の場合（すべての業種で取得失敗）
        if not result:
            # デフォルトのダミーデータを生成
            for biz_type in biz_types:
                result[biz_type] = [{
                    'store_name': f'{biz_type}サンプル店舗',
                    'avg_rate': 0.0,
                    'sample_count': 0,
                    'genre': '不明',
                    'area': '不明'
                }]
                biz_type_counts[biz_type] = 1

        # 統一されたレスポンス形式で返す - このAPIは特殊な構造なのでdataにオブジェクトを設定
        return jsonify({
            "data": result,
            "meta": {
                "biz_types": len(biz_types),
                "biz_type_counts": biz_type_counts,
                "limit": limit,
                "min_samples": min_samples,
                "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
            }
        }), 200
    except Exception as e:
        app.logger.error(f"トップランキング取得エラー: {e}")
        app.logger.error(traceback.format_exc())

        # エラー時もダミーデータを返す（フロントエンドのエラー回避）
        dummy_data = {
            'キャバクラ': [
                {'store_name': 'サンプル店舗1', 'avg_rate': 50.0, 'sample_count': 0, 'genre': '不明', 'area': '不明'},
                {'store_name': 'サンプル店舗2', 'avg_rate': 40.0, 'sample_count': 0, 'genre': '不明', 'area': '不明'},
                {'store_name': 'サンプル店舗3', 'avg_rate': 30.0, 'sample_count': 0, 'genre': '不明', 'area': '不明'}
            ]
        }

        # 統一されたエラーレスポンス形式（ダミーデータ付き）
        return jsonify({
            "data": dummy_data,
            "meta": {
                "error": str(e),
                "message": "トップランキングの取得中にエラーが発生しました（ダミーデータを表示）",
                "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
            }
        }), 200  # 統一したレスポンスコード

# ---------------------------------------------------------------------
# 9. メイン実行部
# ---------------------------------------------------------------------
if __name__ == '__main__':
    # ローカル環境用の設定
    import os
    port = int(os.environ.get("PORT", 5000))

    # タイムゾーン設定を確認
    jst = pytz.timezone('Asia/Tokyo')
    now_jst = datetime.now(jst)
    print(f"サーバー起動時刻（JST）: {now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")

    # サーバー起動 - シンプルな設定
    print(f"アプリケーションを起動しています: http://0.0.0.0:{port}")
    socketio.run(app, host="0.0.0.0", port=port, debug=True, allow_unsafe_werkzeug=True)