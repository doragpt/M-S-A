import os
import logging
from datetime import datetime, timedelta
import pytz  # タイムゾーン変換用
import traceback
import gc  # ガベージコレクション

from flask import Flask, request, render_template, redirect, url_for, flash, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from sqlalchemy import func, exc
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
@cache.memoize(timeout=60)  # キャッシュ：1分間に短縮して最新データを提供
def api_data():
    """
    各店舗の最新レコードのみを返すエンドポイント（タイムゾーンは JST）。
    集計値（全体平均など）も含める。

    クエリパラメータ:
        page: ページ番号（1から始まる）- 指定しない場合は全データを返す
        per_page: 1ページあたりの項目数（最大100）
    """
    from page_helper import paginate_query_results, format_store_status

    # 各店舗の最新タイムスタンプをサブクエリで取得
    subq = db.session.query(
        StoreStatus.store_name,
        func.max(StoreStatus.timestamp).label("max_time")
    ).group_by(StoreStatus.store_name).subquery()

    query = db.session.query(StoreStatus).join(
        subq,
        (StoreStatus.store_name == subq.c.store_name) &
        (StoreStatus.timestamp == subq.c.max_time)
    ).order_by(StoreStatus.timestamp.desc())

    # ページネーションの有無を確認
    use_pagination = 'page' in request.args
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    # クエリの全結果を取得（集計値の計算用+全データ返却用）
    all_results = query.all()

    # 集計値の計算
    total_store_count = len(all_results)
    total_working_staff = 0
    total_active_staff = 0
    valid_stores = 0  # 勤務中スタッフがいる店舗のカウント
    max_rate = 0

    for r in all_results:
        # 集計用データの収集
        if r.working_staff > 0:
            valid_stores += 1
            total_working_staff += r.working_staff
            total_active_staff += r.active_staff
            rate = ((r.working_staff - r.active_staff) / r.working_staff) * 100
            max_rate = max(max_rate, rate)

    # 全体平均稼働率の計算
    avg_rate = 0
    if valid_stores > 0 and total_working_staff > 0:
        avg_rate = ((total_working_staff - total_active_staff) / total_working_staff) * 100

    # データのフォーマット - JSTタイムゾーンを明示的に設定
    jst = pytz.timezone('Asia/Tokyo')
    # 現在時刻をJSTで取得（API呼び出し時刻）
    now_jst = datetime.now(jst)

    if use_pagination:
        # ページネーション適用
        paginated_result = paginate_query_results(query, page, per_page)
        items = paginated_result['items']
        data = [format_store_status(item, jst) for item in items]

        # ページネーション情報を含むレスポンス
        response = {
            "data": data,
            "meta": {
                "total_count": total_store_count,
                "valid_stores": valid_stores,
                "avg_rate": round(avg_rate, 1),
                "max_rate": round(max_rate, 1),
                "total_working_staff": total_working_staff,
                "total_active_staff": total_active_staff,
                "page": paginated_result['meta']['page'],
                "per_page": paginated_result['meta']['per_page'],
                "total_pages": paginated_result['meta']['total_pages'],
                "has_prev": paginated_result['meta']['has_prev'],
                "has_next": paginated_result['meta']['has_next'],
                "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')  # 現在のJST時間を追加
            }
        }
    else:
        # ページネーションなし - 全データ返却
        data = [format_store_status(item, jst) for item in all_results]

        response = {
            "data": data,
            "meta": {
                "total_count": total_store_count,
                "valid_stores": valid_stores,
                "avg_rate": round(avg_rate, 1),
                "max_rate": round(max_rate, 1),
                "total_working_staff": total_working_staff,
                "total_active_staff": total_active_staff,
                "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')  # 現在のJST時間を追加
            }
        }

    return jsonify(response)


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

    # JSTタイムゾーン
    jst = pytz.timezone('Asia/Tokyo')
    now_jst = datetime.now(jst)

    # ベースクエリ
    query = StoreStatus.query

    # フィルタリング条件適用
    if store := request.args.get('store'):
        query = query.filter(StoreStatus.store_name == store)

    if start_date := request.args.get('start_date'):
        # 日本時間の00:00:00として日付開始時刻を設定
        start_datetime = datetime.strptime(f"{start_date} 00:00:00", "%Y-%m-%d %H:%M:%S")
        start_datetime = jst.localize(start_datetime)
        query = query.filter(StoreStatus.timestamp >= start_datetime)

    if end_date := request.args.get('end_date'):
        # 日本時間の23:59:59として日付終了時刻を設定
        end_datetime = datetime.strptime(f"{end_date} 23:59:59", "%Y-%m-%d %H:%M:%S")
        end_datetime = jst.localize(end_datetime)
        query = query.filter(StoreStatus.timestamp <= end_datetime)

    # データ制限（オプション）
    if limit := request.args.get('limit', type=int):
        query = query.limit(limit)

    # 常に時系列順にソート
    query = query.order_by(StoreStatus.timestamp.asc())

    # ページネーションのパラメータを取得
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 100, type=int)

    # ページネーション適用（limitより優先）
    if 'page' in request.args or 'per_page' in request.args:
        paginated_result = paginate_query_results(query, page, per_page)
        items = paginated_result['items']

        # データのフォーマット
        data = [format_store_status(item, jst) for item in items]

        # メタデータを含むレスポンス
        response = {
            "meta": {
                **paginated_result['meta'],
                "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')  # 現在のJST時間を追加
            }
        }
        return jsonify(data)
    else:
        # 従来通りすべての結果を返す場合
        results = query.all()
        data = [format_store_status(r, jst) for r in results]
        return jsonify(data)


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

        app.logger.info(f"ランキング取得パラメータ: biz_type={biz_type}, limit={limit}, min_samples={min_samples}")

        # サブクエリ: 店舗ごとのグループ化
        subq = db.session.query(
            StoreStatus.store_name,
            func.avg(
                (StoreStatus.working_staff - StoreStatus.active_staff) * 100.0 / 
                func.nullif(StoreStatus.working_staff, 0)
            ).label('avg_rate'),
            func.count().label('sample_count'),
            func.max(StoreStatus.biz_type).label('biz_type'),
            func.max(StoreStatus.genre).label('genre'),
            func.max(StoreStatus.area).label('area')
        ).filter(StoreStatus.working_staff > 0)

        # 業種でフィルタリング（指定があれば）
        if biz_type:
            subq = subq.filter(StoreStatus.biz_type == biz_type)

        # グループ化と最小サンプル数フィルタ（最小値を引数から取得）
        subq = subq.group_by(StoreStatus.store_name).having(func.count() >= min_samples).subquery()

        # メインクエリ: ランキング取得
        query = db.session.query(
            subq.c.store_name,
            subq.c.avg_rate,
            subq.c.sample_count,
            subq.c.biz_type,
            subq.c.genre,
            subq.c.area
        ).order_by(subq.c.avg_rate.desc()).limit(limit)

        # クエリ実行と件数の確認
        results = query.all()
        app.logger.info(f"平均稼働ランキング取得: {len(results)}件のデータを取得")

        # 結果が0件の場合、サンプル数の条件を緩和して再取得
        if len(results) == 0 and min_samples > 1:
            app.logger.warning(f"サンプル数{min_samples}以上の店舗が見つからなかったため、条件を緩和します")
            # サブクエリ再構築（サンプル数条件を1に）
            subq = db.session.query(
                StoreStatus.store_name,
                func.avg(
                    (StoreStatus.working_staff - StoreStatus.active_staff) * 100.0 / 
                    func.nullif(StoreStatus.working_staff, 0)
                ).label('avg_rate'),
                func.count().label('sample_count'),
                func.max(StoreStatus.biz_type).label('biz_type'),
                func.max(StoreStatus.genre).label('genre'),
                func.max(StoreStatus.area).label('area')
            ).filter(StoreStatus.working_staff > 0)
            
            if biz_type:
                subq = subq.filter(StoreStatus.biz_type == biz_type)
                
            subq = subq.group_by(StoreStatus.store_name).having(func.count() >= 1).subquery()
            
            query = db.session.query(
                subq.c.store_name,
                subq.c.avg_rate,
                subq.c.sample_count,
                subq.c.biz_type,
                subq.c.genre,
                subq.c.area
            ).order_by(subq.c.avg_rate.desc()).limit(limit)
            
            results = query.all()
            app.logger.info(f"条件緩和後の取得件数: {len(results)}件")

        # 結果を整形
        data = [{
            'store_name': r.store_name,
            'avg_rate': float(r.avg_rate) if r.avg_rate is not None else 0.0,
            'sample_count': r.sample_count,
            'biz_type': r.biz_type if r.biz_type else '不明',
            'genre': r.genre if r.genre else '不明',
            'area': r.area if r.area else '不明'
        } for r in results]

        return jsonify(data)
    except Exception as e:
        app.logger.error(f"平均稼働ランキング取得エラー: {e}")
        app.logger.error(traceback.format_exc())
        return jsonify({"error": "ランキングの取得中にエラーが発生しました"}), 500

# 業種内ジャンルランキングのAPIエンドポイント
@app.route('/api/ranking/genre')
@cache.memoize(timeout=600)  # キャッシュ：10分間有効
def api_genre_ranking():
    """
    業種内のジャンル別平均稼働率ランキングを返すエンドポイント

    クエリパラメータ:
        biz_type: 業種でフィルタリング（必須）
    """
    biz_type = request.args.get('biz_type')
    if not biz_type:
        return jsonify({"error": "業種(biz_type)の指定が必要です"}), 400

    try:
        # 直接SQLクエリを実行
        # 業種内のジャンル別平均稼働率とサンプル数を計算
        query = db.session.query(
            StoreStatus.genre,
            func.count(func.distinct(StoreStatus.store_name)).label('store_count'),
            func.avg(
                (StoreStatus.working_staff - StoreStatus.active_staff) * 100.0 / 
                func.nullif(StoreStatus.working_staff, 0)
            ).label('avg_rate')
        ).filter(
            StoreStatus.biz_type == biz_type,
            StoreStatus.working_staff > 0
        ).group_by(
            StoreStatus.genre
        ).order_by(
            func.avg(
                (StoreStatus.working_staff - StoreStatus.active_staff) * 100.0 / 
                func.nullif(StoreStatus.working_staff, 0)
            ).desc()
        )

        results = query.all()

        # 結果が空の場合は空のリストを返す
        if not results:
            return jsonify([])

        data = [{
            "genre": r.genre if r.genre else "不明",
            "store_count": r.store_count,
            "avg_rate": round(float(r.avg_rate), 1)
        } for r in results]

        return jsonify(data)
    except Exception as e:
        app.logger.error(f"ジャンルランキング取得エラー: {e}")
        app.logger.error(traceback.format_exc())
        return jsonify({"error": "ジャンルランキングの取得中にエラーが発生しました"}), 500

# 集計済みデータを提供するエンドポイント
@app.route('/api/aggregated')
@cache.memoize(timeout=3600)  # キャッシュ：1時間有効
def api_aggregated_data():
    """
    日付ごとに集計された平均稼働率データを返すエンドポイント
    """
    try:
        # 日付ごとの集計クエリ
        query = db.session.query(
            func.date(StoreStatus.timestamp).label('date'),
            func.avg(
                (StoreStatus.working_staff - StoreStatus.active_staff) * 100.0 / 
                func.nullif(StoreStatus.working_staff, 0)
            ).label('avg_rate'),
            func.count().label('sample_count')
        ).filter(
            StoreStatus.working_staff > 0
        ).group_by(
            func.date(StoreStatus.timestamp)
        ).order_by(
            func.date(StoreStatus.timestamp)
        )

        results = query.all()
        app.logger.info(f"集計クエリ実行結果: {len(results)}件のデータを取得")

        # 結果を整形
        data = []
        skipped_records = 0

        for r in results:
            # 詳細なデバッグログを追加
            app.logger.debug(f"処理中のレコード: date={r.date}, avg_rate={r.avg_rate}, sample_count={r.sample_count}")

            # date属性がNoneの場合はスキップ
            if r.date is None:
                app.logger.warning(f"日付が未設定のレコードをスキップします: {r}")
                skipped_records += 1
                continue

            try:
                # 日付フォーマットのチェック
                if hasattr(r.date, 'isoformat'):
                    date_str = r.date.isoformat()
                    app.logger.debug(f"ISO形式の日付を使用: {date_str}")
                else:
                    date_str = str(r.date)
                    app.logger.warning(f"日付フォーマットが予期しないタイプでした: {type(r.date)}, 値: {date_str}")

                # 平均稼働率と件数のチェック
                avg_rate = 0.0
                if r.avg_rate is not None:
                    try:
                        avg_rate = float(r.avg_rate)
                    except (ValueError, TypeError) as e:
                        app.logger.warning(f"平均稼働率の変換に失敗しました: {r.avg_rate}, エラー: {e}")
                else:
                    app.logger.warning(f"平均稼働率がNullでした: {r}")

                sample_count = r.sample_count or 0

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

        # 空のレスポンス対策
        if not data:
            app.logger.warning("集計データが空でした")

        return jsonify(data)
    except Exception as e:
        app.logger.error(f"API集計データ取得エラー: {e}")
        app.logger.error(traceback.format_exc())
        return jsonify({"error": "集計データの取得中にエラーが発生しました"}), 500

@app.route('/api/averages/daily')
@cache.memoize(timeout=600)  # キャッシュ：10分間有効
def api_daily_averages():
    """
    日次（直近24時間）の平均稼働率データを返すエンドポイント
    """
    from aggregated_data import AggregatedData

    results = AggregatedData.get_daily_averages()

# データ品質・メタデータを提供するAPIエンドポイント
@app.route('/api/data-quality')
@cache.memoize(timeout=300)  # キャッシュ：5分間有効
def api_data_quality():
    """
    データ品質とメタデータ情報を返すエンドポイント
    """
    try:
        # 最終データ更新時刻を取得
        last_update = db.session.query(func.max(StoreStatus.timestamp)).scalar()
        
        # スクレイピング成功率の計算（最新データを基に）
        total_urls = db.session.query(func.count(StoreURL.id)).scalar() or 0
        recent_records = db.session.query(func.count(func.distinct(StoreStatus.store_name))).filter(
            StoreStatus.timestamp >= (datetime.now() - timedelta(hours=2))
        ).scalar() or 0
        
        # スクレイピング成功率
        success_rate = (recent_records / total_urls * 100) if total_urls > 0 else 0
        
        # レコード総数
        total_records = db.session.query(func.count(StoreStatus.id)).scalar() or 0
        
        # 日ごとのデータ完全性 (過去7日間)
        daily_completeness = []
        for i in range(7):
            date = datetime.now() - timedelta(days=i)
            date_start = datetime(date.year, date.month, date.day, 0, 0, 0)
            date_end = datetime(date.year, date.month, date.day, 23, 59, 59)
            
            # その日の店舗カバレッジ
            day_stores = db.session.query(func.count(func.distinct(StoreStatus.store_name))).filter(
                StoreStatus.timestamp.between(date_start, date_end)
            ).scalar() or 0
            
            # カバレッジ率
            coverage_rate = (day_stores / total_urls * 100) if total_urls > 0 else 0
            
            daily_completeness.append({
                'date': date.strftime('%Y-%m-%d'),
                'coverage': round(coverage_rate, 1),
                'store_count': day_stores
            })
        
        # 異常値検出 (稼働率が100%または0%のレコード数)
        extremes_count = db.session.query(func.count(StoreStatus.id)).filter(
            ((StoreStatus.working_staff > 0) & (StoreStatus.active_staff == 0)) |
            ((StoreStatus.working_staff > 0) & (StoreStatus.active_staff == StoreStatus.working_staff))
        ).scalar() or 0
        
        # データベースサイズの取得 (SQLiteの場合)
        db_size = 0
        try:
            import os
            db_path = app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
            if os.path.exists(db_path):
                db_size = os.path.getsize(db_path) / (1024 * 1024)  # MBに変換
        except Exception as e:
            app.logger.warning(f"データベースサイズ取得エラー: {e}")
        
        # データ品質スコア (0-100) の計算
        # 複数の指標を組み合わせて総合的なスコアを算出
        quality_score = (
            (success_rate * 0.4) +                     # スクレイピング成功率 (40%)
            (min(daily_completeness[0]['coverage'], 100) * 0.3) +  # 最新のカバレッジ (30%)
            (max(0, 100 - (extremes_count / total_records * 100)) * 0.3)  # 異常値の少なさ (30%)
        )
        
        return jsonify({
            'last_update': last_update.isoformat() if last_update else None,
            'success_rate': round(success_rate, 1),
            'total_urls': total_urls,
            'total_records': total_records,
            'daily_completeness': daily_completeness,
            'extremes_count': extremes_count,
            'extremes_percentage': round((extremes_count / total_records * 100), 1) if total_records > 0 else 0,
            'db_size_mb': round(db_size, 2),
            'quality_score': round(min(quality_score, 100), 1)
        })
    except Exception as e:
        app.logger.error(f"データ品質API取得エラー: {e}")
        app.logger.error(traceback.format_exc())
        return jsonify({"error": "データ品質情報の取得中にエラーが発生しました"}), 500


    jst = pytz.timezone('Asia/Tokyo')
    data = [{
        'store_name': r.store_name,
        'avg_rate': float(r.avg_rate),
        'sample_count': r.sample_count,
        'start_date': r.start_date.astimezone(jst).isoformat(),
        'end_date': r.end_date.astimezone(jst).isoformat(),
        'biz_type': r.biz_type,
        'genre': r.genre,
        'area': r.area
    } for r in results]

    return jsonify(data)

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
        'start_date': r.start_date.astimezone(jst).isoformat(),
        'end_date': r.end_date.astimezone(jst).isoformat(),
        'biz_type': r.biz_type,
        'genre': r.genre,
        'area': r.area
    } for r in results]

    return jsonify(data)

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
        'start_date': r.start_date.astimezone(jst).isoformat(),
        'end_date': r.end_date.astimezone(jst).isoformat(),
        'biz_type': r.biz_type,
        'genre': r.genre,
        'area': r.area
    } for r in results]

    return jsonify(data)

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
        'start_date': r.start_date.astimezone(jst).isoformat(),
        'end_date': r.end_date.astimezone(jst).isoformat(),
        'biz_type': r.biz_type,
        'genre': r.genre,
        'area': r.area
    } for r in results]

    return jsonify(data)

@app.route('/api/history/optimized')
@cache.memoize(timeout=300)  # キャッシュ：5分間有効
def api_history_optimized():
    """
    最適化されたスクレイピング履歴を返すエンドポイント
    - 大量データの場合は日時でダウンサンプリングする
    - 指定店舗のみの場合は完全なデータを返す
    """
    from page_helper import paginate_query_results, format_store_status

    # 店舗名フィルター（指定された場合は完全なデータを返す）
    store = request.args.get('store')
    if store:
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
        query = query.filter(StoreStatus.timestamp >= f"{start_date} 00:00:00")

    if end_date:
        query = query.filter(StoreStatus.timestamp <= f"{end_date} 23:59:59")

    # 全件数をカウント
    total_count = query.count()

    # 1万件を超える場合はダウンサンプリング
    if total_count > 10000:
        # 各店舗から一定間隔でサンプリング
        store_names = db.session.query(func.distinct(StoreStatus.store_name)).all()
        store_names = [s[0] for s in store_names]

        # ダウンサンプリングされたデータ
        sampled_data = []

        for store_name in store_names:
            # 各店舗のデータを時間順に取得
            store_data = query.filter(StoreStatus.store_name == store_name).order_by(StoreStatus.timestamp.asc()).all()

            # サンプリング率を決定（最大100ポイント/店舗）
            sample_rate = max(1, len(store_data) // 100)

            # サンプリング
            sampled_store_data = store_data[::sample_rate]
            sampled_data.extend(sampled_store_data)

        # 時間順にソート
        sampled_data.sort(key=lambda x: x.timestamp)

        # データのフォーマット
        jst = pytz.timezone('Asia/Tokyo')
        data = [format_store_status(item, jst) for item in sampled_data]

        # メタデータを含むレスポンス
        response = {
            "items": data,
            "meta": {
                "original_count": total_count,
                "sampled_count": len(sampled_data),
                "sampling_rate": sample_rate,
                "is_sampled": True
            }
        }
        return jsonify(response)
    else:
        # 1万件以下の場合は通常のページネーション処理
        return api_history()

# エリア統計データを提供するエンドポイント
@app.route('/api/area-stats')
@cache.memoize(timeout=600)  # キャッシュ：10分間有効
def api_area_stats():
    """
    エリア別の統計情報を返すエンドポイント
    """
    try:
        # エリア別の店舗数と平均稼働率を計算
        query = db.session.query(
            StoreStatus.area,
            func.count(func.distinct(StoreStatus.store_name)).label('store_count'),
            func.avg(
                (StoreStatus.working_staff - StoreStatus.active_staff) * 100.0 / 
                func.nullif(StoreStatus.working_staff, 0)
            ).label('avg_rate')
        ).filter(
            StoreStatus.working_staff > 0
        ).group_by(
            StoreStatus.area
        ).order_by(
            func.avg(
                (StoreStatus.working_staff - StoreStatus.active_staff) * 100.0 / 
                func.nullif(StoreStatus.working_staff, 0)
            ).desc()
        )

        results = query.all()

        data = [{
            "area": r.area if r.area else "不明",
            "store_count": r.store_count,
            "avg_rate": round(float(r.avg_rate), 1)
        } for r in results]

        return jsonify(data)
    except Exception as e:
        app.logger.error(f"エリア統計取得エラー: {e}")
        app.logger.error(traceback.format_exc())
        return jsonify({"error": "エリア統計の取得中にエラーが発生しました"}), 500

# すべての店舗名を取得するAPIエンドポイント
@app.route('/api/store-names')
@cache.memoize(timeout=600)  # キャッシュ：10分間有効
def api_store_names():
    """
    データベースに登録されているすべての店舗名を返すエンドポイント
    """
    try:
        # 重複を除去した店舗名のリストを取得
        store_names = db.session.query(StoreStatus.store_name).distinct().all()
        # タプルのリストからプレーンな文字列のリストに変換
        store_names = [name[0] for name in store_names if name[0]]
        # アルファベット順にソート
        store_names.sort(key=lambda x: x.lower())

        return jsonify(store_names)
    except Exception as e:
        app.logger.error(f"店舗名リスト取得エラー: {e}")
        app.logger.error(traceback.format_exc())
        return jsonify({"error": "店舗名リストの取得中にエラーが発生しました"}), 500

# トップランキングを提供するエンドポイント
@app.route('/api/ranking/top')
@cache.memoize(timeout=600)  # キャッシュ：10分間有効
def api_top_ranking():
    """
    各業種のトップ店舗ランキングを返すエンドポイント

    クエリパラメータ:
        limit: 各業種ごとの上位件数（デフォルト3件）
    """
    try:
        limit = request.args.get('limit', 3, type=int)

        # 業種の一覧を取得
        biz_types = db.session.query(StoreStatus.biz_type).distinct().all()
        biz_types = [bt[0] for bt in biz_types if bt[0]]  # None値を除外

        result = {}

        for biz_type in biz_types:
            # 各業種ごとにトップ店舗を取得
            subq = db.session.query(
                StoreStatus.store_name,
                func.avg(
                    (StoreStatus.working_staff - StoreStatus.active_staff) * 100.0 / 
                    func.nullif(StoreStatus.working_staff, 0)
                ).label('avg_rate'),
                func.count().label('sample_count'),
                func.max(StoreStatus.genre).label('genre'),
                func.max(StoreStatus.area).label('area')
            ).filter(
                StoreStatus.biz_type == biz_type,
                StoreStatus.working_staff > 0
            ).group_by(
                StoreStatus.store_name
            ).having(
                func.count() >= 10
            ).order_by(
                func.avg(
                    (StoreStatus.working_staff - StoreStatus.active_staff) * 100.0 / 
                    func.nullif(StoreStatus.working_staff, 0)
                ).desc()
            ).limit(limit).all()

            if subq:
                result[biz_type] = [{
                    'store_name': r.store_name,
                    'avg_rate': round(float(r.avg_rate), 1),
                    'sample_count': r.sample_count,
                    'genre': r.genre if r.genre else '不明',
                    'area': r.area if r.area else '不明'
                } for r in subq]

        return jsonify(result)
    except Exception as e:
        app.logger.error(f"トップランキング取得エラー: {e}")
        app.logger.error(traceback.format_exc())
        return jsonify({"error": "トップランキングの取得中にエラーが発生しました"}), 500

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
    print(f"サーバーを起動しています: http://0.0.0.0:{port}")
    socketio.run(app, host="0.0.0.0", port=port, debug=True, allow_unsafe_werkzeug=True)