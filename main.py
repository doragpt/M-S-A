import os
import logging
import pytz
from datetime import datetime, timedelta

from flask import Flask, render_template, redirect, url_for

# Monkey patch for werkzeug issue
import werkzeug
from urllib.parse import quote as url_quote
werkzeug.urls.url_quote = url_quote
from flask_caching import Cache
from flask_socketio import SocketIO
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ProcessPoolExecutor

from models import db
from api_routes import api_bp
from api_endpoints import init_cache
from store_scraper import scrape_store_data
from aggregated_data import AggregatedData

# ロギング設定
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Flaskアプリの初期化
import os
app_dir = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(app_dir, 'templates')
static_dir = os.path.join(app_dir, 'static')
app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
print(f"テンプレートディレクトリ: {template_dir}")
print(f"静的ファイルディレクトリ: {static_dir}")
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your_secret_key_here')
app.config.update(
    SESSION_COOKIE_NAME='msa_session',
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(days=31),
    SESSION_TYPE='filesystem',
    SESSION_FILE_DIR='./flask_session',
    SESSION_FILE_THRESHOLD=500,
    SESSION_FILE_MODE=384,  # 0o600
    SESSION_COOKIE_PATH='/',
    # エンコーディング設定を追加
    SESSION_USE_SIGNER=True,
    SESSION_ENCODING='utf-8'
)

# Werkzeugのエンコーディング設定を上書き
from urllib.parse import quote
def patched_url_quote(string, charset='utf-8', safe='/', errors=None):
    if isinstance(string, bytes):
        string = string.decode(charset)
    return quote(string, safe=safe)
werkzeug.urls.url_quote = patched_url_quote

# セッションディレクトリの作成
os.makedirs('./flask_session', exist_ok=True)

# データベース設定
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///store_data.db')
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# SQLiteかPostgreSQLかによって最適なオプションを設定
if DATABASE_URL.startswith('sqlite'):
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True  
    }
else:
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'pool_timeout': 30,
        'pool_size': 10,
        'max_overflow': 20
    }

# キャッシュ設定
if os.environ.get('REDIS_URL'):
    # 本番環境: Redis利用
    app.config['CACHE_TYPE'] = 'RedisCache'
    app.config['CACHE_REDIS_URL'] = os.environ.get('REDIS_URL')
    app.config['CACHE_OPTIONS'] = {'socket_timeout': 3, 'socket_connect_timeout': 3}
else:
    # 開発環境: SimpleCache利用
    app.config['CACHE_TYPE'] = 'SimpleCache'
    app.config['CACHE_DEFAULT_TIMEOUT'] = 300

app.config['CACHE_KEY_PREFIX'] = 'msa_v1_'
app.config['CACHE_THRESHOLD'] = 1000

cache = Cache(app)
init_cache(cache)  # API設定にキャッシュインスタンスを渡す

# SocketIO
socketio = SocketIO(app, async_mode='threading')

# データベース初期化
db.init_app(app)
with app.app_context():
    db.create_all()

# API Blueprint登録
app.register_blueprint(api_bp, url_prefix='/api')

# スケジューリング用の設定
jst = pytz.timezone('Asia/Tokyo')
executors = {'default': ProcessPoolExecutor(max_workers=1)}
scheduler = BackgroundScheduler(executors=executors, timezone=jst)

# 定期スクレイピング処理
def scheduled_scrape():
    """定期的に実行されるスクレイピングジョブ"""
    from models import StoreURL, StoreStatus
    from database import get_db_connection

    with app.app_context():
        logger.info("定期スクレイピングを開始します")

        # スクレイピング実行時刻（JSTタイムゾーン）
        jst = pytz.timezone('Asia/Tokyo')
        scrape_time = datetime.now(jst)

        # 対象URLを取得
        store_url_objs = StoreURL.query.all()
        store_urls = [s.store_url for s in store_url_objs]

        if not store_urls:
            logger.info("店舗URLが登録されていません。")
            return

        logger.info(f"スクレイピング開始: 対象店舗数 {len(store_urls)}")

        try:
            # スクレイピング実行
            results = scrape_store_data(store_urls)
            logger.info(f"スクレイピング完了: 取得件数 {len(results)}")

            # 結果をDBに保存
            record_update_count = 0
            record_insert_count = 0

            for record in results:
                if not record:
                    continue

                store_name = record.get('store_name', '')
                area = record.get('area', '')

                if not store_name or not area:
                    continue

                # 重複チェック
                formatted_time = scrape_time.strftime('%Y-%m-%d %H:%M')

                conn = get_db_connection()
                existing_query = """
                SELECT id FROM store_status 
                WHERE store_name = ? AND area = ? 
                AND strftime('%Y-%m-%d %H:%M', timestamp) = ?
                """
                existing = conn.execute(existing_query, 
                                      [store_name, area, formatted_time]).fetchone()
                conn.close()

                if existing:
                    # 既存レコードを更新
                    stmt = """
                    UPDATE store_status SET
                    biz_type = ?, genre = ?, area = ?, 
                    total_staff = ?, working_staff = ?, active_staff = ?,
                    url = ?, shift_time = ?
                    WHERE id = ?
                    """
                    conn = get_db_connection()
                    conn.execute(stmt, [
                        record.get('biz_type'),
                        record.get('genre'),
                        area,
                        record.get('total_staff', 0),
                        record.get('working_staff', 0),
                        record.get('active_staff', 0),
                        record.get('url', ''),
                        record.get('shift_time', ''),
                        existing['id']
                    ])
                    conn.commit()
                    conn.close()
                    record_update_count += 1
                else:
                    # 新規レコードを追加
                    stmt = """
                    INSERT INTO store_status
                    (timestamp, store_name, biz_type, genre, area, 
                     total_staff, working_staff, active_staff, url, shift_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """
                    conn = get_db_connection()
                    conn.execute(stmt, [
                        scrape_time,
                        store_name,
                        record.get('biz_type'),
                        record.get('genre'),
                        area,
                        record.get('total_staff', 0),
                        record.get('working_staff', 0),
                        record.get('active_staff', 0),
                        record.get('url', ''),
                        record.get('shift_time', '')
                    ])
                    conn.commit()
                    conn.close()
                    record_insert_count += 1

            logger.info(f"DB処理完了: 更新={record_update_count}件, 新規追加={record_insert_count}件")

            # 集計データの更新
            AggregatedData.calculate_and_save_aggregated_data()

            # キャッシュをクリア（キャッシュミスを防ぐためにエラーを捕捉）
            try:
                cache.clear()
                logger.info("キャッシュをクリアしました")
            except Exception as cache_err:
                logger.error(f"キャッシュクリア中にエラーが発生しました: {cache_err}")

            # Socket.IO で更新通知
            socketio.emit('update', {'data': 'Dashboard updated'})

        except Exception as e:
            logger.error(f"スクレイピング処理中にエラーが発生しました: {e}")

# スケジューラーにジョブを登録
scheduler.add_job(scheduled_scrape, 'interval', hours=1, id='scrape_job')
scheduler.add_job(lambda: cache.clear(), 'cron', hour=3, minute=0, id='cache_clear_job')
scheduler.start()

# ルート設定
@app.route('/')
def index():
    """統合ダッシュボードを表示"""
    return render_template('integrated_dashboard.html')

@app.route('/hourly')
def hourly_analysis():
    """時間帯別分析を表示"""
    return render_template('hourly_analysis.html')

@app.route('/admin')
def admin():
    """管理画面へリダイレクト"""
    return redirect(url_for('manage_store_urls'))

@app.route('/admin/manage', methods=['GET', 'POST'])
def manage_store_urls():
    """店舗URL管理画面"""
    from models import StoreURL
    from flask import request, flash

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
    """店舗URLの削除処理"""
    from models import StoreURL
    from flask import flash

    url_obj = StoreURL.query.get_or_404(id)
    db.session.delete(url_obj)
    db.session.commit()
    flash("店舗URLを削除しました。", "success")
    return redirect(url_for('manage_store_urls'))

@app.route('/admin/edit/<int:id>', methods=['GET', 'POST'])
def edit_store_url(id):
    """店舗URLの編集ページ"""
    from models import StoreURL
    from flask import request, flash

    url_obj = StoreURL.query.get_or_404(id)

    if request.method == 'POST':
        new_url = request.form.get('store_url', '').strip()
        if not new_url:
            flash("URLを入力してください。", "warning")
            return redirect(url_for('edit_store_url', id=id))

        # 重複チェック
        conflict = StoreURL.query.filter(StoreURL.store_url == new_url, StoreURL.id != id).first()
        if conflict:
            flash("そのURLは既に他の店舗として登録されています。", "warning")
            return redirect(url_for('edit_store_url', id=id))

        url_obj.store_url = new_url
        db.session.commit()
        flash("店舗URLを更新しました。", "success")
        return redirect(url_for('manage_store_urls'))

    return render_template('edit_store_url.html', url_data=url_obj)

@app.route('/admin/manual_scrape', methods=['POST'])
def manual_scrape():
    """手動スクレイピング実行"""
    from flask import flash
    from datetime import timedelta

    # 手動スクレイピング実行
    scheduled_scrape()

    # 次回スケジュール設定
    next_time = datetime.now() + timedelta(hours=1)
    try:
        scheduler.modify_job('scrape_job', next_run_time=next_time)
        flash(f"手動スクレイピングを実行しました。次回は {next_time.strftime('%Y-%m-%d %H:%M:%S')} に実行されます。", "success")
    except Exception as e:
        flash(f"スクレイピングを実行しましたが、スケジュール更新に失敗しました: {e}", "warning")

    return redirect(url_for('manage_store_urls'))

@app.route('/bulk_add_store_urls', methods=['POST'])
def bulk_add_store_urls():
    """複数のURL一括追加処理"""
    from models import StoreURL
    from flask import request, flash

    bulk_urls = request.form.get('bulk_urls', '')
    urls = [url.strip() for url in bulk_urls.splitlines() if url.strip()]

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
            continue

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

# 初期データ収集を遅延実行するための関数
def delayed_initial_setup():
    """サーバー起動後に初期設定を行う関数"""
    with app.app_context():
        try:
            # 既存データとURLの確認
            from models import StoreStatus, StoreURL
            status_count = StoreStatus.query.count()
            url_count = StoreURL.query.count()

            logger.info(f"データベース状態確認: ステータス={status_count}件, URL={url_count}件")

            if url_count == 0:
                # 本番環境用：実際の店舗URLを登録してください
                # サンプルURLの追加
                sample_urls = [
                    "https://example1.com/store1",
                    "https://example2.com/store2"
                ]
                for url in sample_urls:
                    new_url = StoreURL(store_url=url)
                    db.session.add(new_url)
                db.session.commit()
                logger.info("サンプルURLを追加しました")

            if status_count == 0:
                logger.info("初期データ収集を開始します...")
                scheduled_scrape()

        except Exception as e:
            logger.error(f"初期化エラー: {e}")

# メイン実行部分
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    jst = pytz.timezone('Asia/Tokyo')
    now_jst = datetime.now(jst)
    logger.info(f"サーバー起動時刻（JST）: {now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
    
    # 開発環境の場合のみ:
    if os.environ.get('FLASK_ENV') == 'development':
        logger.info(f"開発サーバーを起動しています: http://0.0.0.0:{port}")
        
        # サーバー起動後に初期データ収集を遅延実行
        scheduler.add_job(
            delayed_initial_setup, 
            trigger='date', 
            run_date=datetime.now(jst) + timedelta(seconds=10),
            id='initial_setup'
        )
        
        # 開発サーバー起動
        socketio.run(app, host="0.0.0.0", port=port, debug=True, allow_unsafe_werkzeug=True)
    else:
        # 本番環境では Gunicorn で起動するため、ここでは何もしない
        # 代わりに gunicorn -c gunicorn_config.py main:app コマンドを使用する
        
        # 初期データ収集を即時実行
        logger.info("本番モードで初期化を行います")
        delayed_initial_setup()
        
        # Gunicorn では socketio.run() を呼び出さない
        # Gunicorn と eventlet ワーカーが WebSocket 接続を処理する
        logger.info("本番環境ではgunicornコマンドでサーバーを起動してください: gunicorn -c gunicorn_config.py main:app")
