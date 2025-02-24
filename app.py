import os
from datetime import datetime, timedelta
import pytz  # タイムゾーン変換用
from functools import wraps
from flask import Flask, request, render_template, redirect, url_for, flash, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from sqlalchemy import func, Index
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ProcessPoolExecutor
from flask_caching import Cache

# ---------------------------------------------------------------------
# 1. Flaskアプリ & DB接続設定
# ---------------------------------------------------------------------
app = Flask(__name__, template_folder='templates', static_folder='static')
# 秘密鍵は環境変数で設定（デフォルト値はあくまでサンプルです）
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your_secret_key_here')

DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://postgres:saru1111@localhost:5432/store_data')
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# キャッシュ設定（Redis利用）
app.config['CACHE_TYPE'] = 'RedisCache'
app.config['CACHE_REDIS_URL'] = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
cache = Cache(app)

db = SQLAlchemy(app)
socketio = SocketIO(app)

# ---------------------------------------------------------------------
# 2. モデル定義
# ---------------------------------------------------------------------
class StoreStatus(db.Model):
    """
    店舗ごとのスクレイピング結果を保存するテーブル
    ※各レコードは、ある時点での店舗稼働情報を表す
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
    __table_args__ = (Index('ix_store_status_name_area_timestamp', 'store_name', 'area', 'timestamp'),)

class StoreURL(db.Model):
    """
    スクレイピング対象の店舗URLを保存するテーブル
    ※エラー発生時に error_flag と last_error を記録
    """
    __tablename__ = 'store_urls'
    id = db.Column(db.Integer, primary_key=True)
    store_url = db.Column(db.Text, unique=True, nullable=False)
    error_flag = db.Column(db.Integer, default=0)
    last_error = db.Column(db.Text, nullable=True)

# ---------------------------------------------------------------------
# 3. テーブル作成
# ---------------------------------------------------------------------
with app.app_context():
    db.create_all()

# ---------------------------------------------------------------------
# 4. 簡易Basic認証デコレータの定義
# ---------------------------------------------------------------------
def check_auth(username, password):
    return username == os.environ.get('ADMIN_USER', 'admin') and password == os.environ.get('ADMIN_PASS', 'password')

def authenticate():
    return Response('認証が必要です', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# ---------------------------------------------------------------------
# 5. スクレイピング処理 & 定期実行
# ---------------------------------------------------------------------
from store_scraper import scrape_store_data

def scheduled_scrape():
    """
    APScheduler により1時間おきに実行されるスクレイピングジョブ
    ・StoreURLテーブルの各店舗URLに対してスクレイピングを実施し、
      結果をStoreStatusテーブルに保存する。
    ・同一店舗・エリア・分単位の重複は更新する形にする。
    ・スクレイピング失敗時は、StoreURLのerror_flagとlast_errorに記録する。
    ・古いデータ（2年以上前）は削除し、キャッシュをクリア後、Socket.IOで更新通知する。
    """
    with app.app_context():
        scrape_time = datetime.now()
        retention_date = datetime.now() - timedelta(days=730)
        store_url_objs = StoreURL.query.all()
        store_urls = [s.store_url for s in store_url_objs]
        if not store_urls:
            app.logger.info("店舗URLが1件も登録されていません。")
            return
        app.logger.info("【スクレイピング開始】対象店舗数: %d", len(store_urls))
        try:
            results = scrape_store_data(store_urls)
        except Exception as e:
            app.logger.error("スクレイピング中のエラー: %s", e)
            return
        for url_obj, record in zip(store_url_objs, results):
            if record:
                url_obj.error_flag = 0
                url_obj.last_error = None
                existing = StoreStatus.query.filter(
                    StoreStatus.store_name == record.get('store_name'),
                    StoreStatus.area == record.get('area'),
                    func.date_trunc('minute', StoreStatus.timestamp) == scrape_time.replace(second=0, microsecond=0)
                ).first()
                if existing:
                    existing.biz_type = record.get('biz_type')
                    existing.genre = record.get('genre')
                    existing.area = record.get('area')
                    existing.total_staff = record.get('total_staff')
                    existing.working_staff = record.get('working_staff')
                    existing.active_staff = record.get('active_staff')
                    existing.url = record.get('url')
                    existing.shift_time = record.get('shift_time')
                else:
                    new_status = StoreStatus(
                        timestamp=scrape_time,
                        store_name=record.get('store_name'),
                        biz_type=record.get('biz_type'),
                        genre=record.get('genre'),
                        area=record.get('area'),
                        total_staff=record.get('total_staff'),
                        working_staff=record.get('working_staff'),
                        active_staff=record.get('active_staff'),
                        url=record.get('url'),
                        shift_time=record.get('shift_time')
                    )
                    db.session.add(new_status)
            else:
                url_obj.error_flag = 1
                url_obj.last_error = "スクレイピング失敗"
        db.session.commit()

        db.session.query(StoreStatus).filter(StoreStatus.timestamp < retention_date).delete()
        db.session.commit()

        app.logger.info("スクレイピング完了＆古いデータ削除完了。")
        cache.clear()
        socketio.emit('update', {'data': 'Dashboard updated'})

# 定期スクレイピングジョブを1時間間隔で登録（ジョブID 'scrape_job'）
executors = {'default': ProcessPoolExecutor(max_workers=1)}
scheduler = BackgroundScheduler(executors=executors)
scheduler.add_job(scheduled_scrape, 'interval', hours=1, id='scrape_job')
scheduler.start()

# ---------------------------------------------------------------------
# 6. APIエンドポイント
# ---------------------------------------------------------------------
@app.route('/api/data')
def api_data():
    """
    各店舗の最新レコードのみを返すエンドポイント（JST）
    """
    subq = db.session.query(
        StoreStatus.store_name,
        func.max(StoreStatus.timestamp).label("max_time")
    ).group_by(StoreStatus.store_name).subquery()

    query = db.session.query(StoreStatus).join(
        subq,
        (StoreStatus.store_name == subq.c.store_name) &
        (StoreStatus.timestamp == subq.c.max_time)
    ).order_by(StoreStatus.timestamp.desc())

    results = query.all()
    data = []
    jst = pytz.timezone('Asia/Tokyo')
    for r in results:
        data.append({
            "id": r.id,
            "timestamp": r.timestamp.astimezone(jst).isoformat(),
            "store_name": r.store_name,
            "biz_type": r.biz_type,
            "genre": r.genre,
            "area": r.area,
            "total_staff": r.total_staff,
            "working_staff": r.working_staff,
            "active_staff": r.active_staff,
            "url": r.url,
            "shift_time": r.shift_time
        })
    return jsonify(data)

@app.route('/api/history')
@cache.cached(timeout=300)
def api_history():
    """
    全スクレイピング履歴をページネーション対応で返すエンドポイント（JST）
    クエリパラメータ: page, per_page
    """
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    pagination = StoreStatus.query.order_by(StoreStatus.timestamp.asc()).paginate(page, per_page, error_out=False)
    results = pagination.items
    data = []
    jst = pytz.timezone('Asia/Tokyo')
    for r in results:
        data.append({
            "id": r.id,
            "timestamp": r.timestamp.astimezone(jst).isoformat(),
            "store_name": r.store_name,
            "biz_type": r.biz_type,
            "genre": r.genre,
            "area": r.area,
            "total_staff": r.total_staff,
            "working_staff": r.working_staff,
            "active_staff": r.active_staff,
            "url": r.url,
            "shift_time": r.shift_time
        })
    return jsonify({
        "data": data,
        "total": pagination.total,
        "page": pagination.page,
        "per_page": pagination.per_page,
        "pages": pagination.pages
    })

# ---------------------------------------------------------------------
# 7. 管理画面（店舗URL管理） ※認証付き
# ---------------------------------------------------------------------
@app.route('/admin/manage', methods=['GET', 'POST'])
@requires_auth
def manage_store_urls():
    """
    管理画面：店舗URLの追加・編集・削除を行うページ
    """
    if request.method == 'POST':
        store_url = request.form.get('store_url', '').strip()
        if not store_url:
            flash("URLを入力してください。", "warning")
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
@requires_auth
def delete_store_url(id):
    """
    店舗URLの削除処理
    """
    url_obj = StoreURL.query.get_or_404(id)
    db.session.delete(url_obj)
    db.session.commit()
    flash("店舗URLを削除しました。", "success")
    return redirect(url_for('manage_store_urls'))

@app.route('/admin/edit/<int:id>', methods=['GET', 'POST'])
@requires_auth
def edit_store_url(id):
    """
    店舗URLの編集ページ
    """
    url_obj = StoreURL.query.get_or_404(id)
    if request.method == 'POST':
        new_url = request.form.get('store_url', '').strip()
        if not new_url:
            flash("URLを入力してください。", "warning")
            return redirect(url_for('edit_store_url', id=id))
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
# 8. 統合ダッシュボードルート ※認証付き（トップページにも認証を追加）
# ---------------------------------------------------------------------
@app.route('/')
@requires_auth
def index():
    """
    統合ダッシュボードのHTMLを返すルート
    """
    return render_template('integrated_dashboard.html')

# ---------------------------------------------------------------------
# 9. 手動スクレイピング実行ルート ※認証付き
# ---------------------------------------------------------------------
@app.route('/admin/manual_scrape', methods=['POST'])
@requires_auth
def manual_scrape():
    """
    管理画面から手動でスクレイピングを実行し、次回定期スクレイピング実行時刻を現在時刻＋1時間に更新する
    """
    scheduled_scrape()
    next_time = datetime.now() + timedelta(hours=1)
    try:
        scheduler.modify_job('scrape_job', next_run_time=next_time)
        flash("手動スクレイピングを実行しました。次回は {} に実行されます。".format(next_time.strftime("%Y-%m-%d %H:%M:%S")), "success")
    except Exception as e:
        flash("スクレイピングジョブの次回実行時刻更新に失敗しました: " + str(e), "warning")
    return redirect(url_for('manage_store_urls'))

# ---------------------------------------------------------------------
# 10. メイン実行部
# ---------------------------------------------------------------------
if __name__ == '__main__':
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
