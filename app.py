import os
from datetime import datetime, timedelta
import pytz  # タイムゾーン変換用

from flask import Flask, request, render_template, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from sqlalchemy import func
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ProcessPoolExecutor

# 追加：Flask-Caching のインポート
from flask_caching import Cache

# ---------------------------------------------------------------------
# 1. Flaskアプリ & DB接続設定
# ---------------------------------------------------------------------
app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your_secret_key_here')

# DATABASE_URL: 環境変数が設定されていない場合はローカルの設定を使用
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://postgres:saru1111@localhost:5432/store_data')
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 追加：キャッシュ設定（Redisを利用）
app.config['CACHE_TYPE'] = 'RedisCache'
# 環境変数REDIS_URLが設定されていなければローカルのRedisを使用
app.config['CACHE_REDIS_URL'] = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
cache = Cache(app)

db = SQLAlchemy(app)
socketio = SocketIO(app)

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


# ---------------------------------------------------------------------
# 3. テーブル作成
# ---------------------------------------------------------------------
with app.app_context():
    db.create_all()


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
    ・同一店舗＋同一タイムスタンプ（分単位切り捨て）が既に存在する場合は上書きする。
    ・処理完了後、Socket.IO を利用してクライアントに更新通知を送信する。
    ・また、古いデータ（2年以上前）を削除してパフォーマンスを維持する。
    ・さらに、データ更新後にキャッシュをクリアして最新データを反映する。
    """
    with app.app_context():
        # スクレイピング実行時刻（全レコード共通）
        scrape_time = datetime.now()
        # 古いデータ保持期間（過去2年以上前を削除）
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

        for record in results:
            if record:  # 空の結果はスキップ
                # 重複チェック：同じ店舗名かつ同じスクレイピング実行時刻（分単位切り捨て）で既存のレコードを確認
                existing = StoreStatus.query.filter(
                    StoreStatus.store_name == record.get('store_name'),
                    func.date_trunc('minute', StoreStatus.timestamp) == scrape_time.replace(second=0, microsecond=0)
                ).first()
                if existing:
                    # 既存レコードがあれば更新
                    existing.biz_type = record.get('biz_type')
                    existing.genre = record.get('genre')
                    existing.area = record.get('area')
                    existing.total_staff = record.get('total_staff')
                    existing.working_staff = record.get('working_staff')
                    existing.active_staff = record.get('active_staff')
                    existing.url = record.get('url')
                    existing.shift_time = record.get('shift_time')
                else:
                    # 新規レコード作成
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
        db.session.commit()

        # 古いデータ（2年以上前）の削除
        db.session.query(StoreStatus).filter(StoreStatus.timestamp < retention_date).delete()
        db.session.commit()

        app.logger.info("スクレイピング完了＆古いデータ削除完了。")
        # キャッシュクリア
        cache.clear()
        socketio.emit('update', {'data': 'Dashboard updated'})

# APScheduler の設定：1時間ごとに scheduled_scrape を実行
executors = {'default': ProcessPoolExecutor(max_workers=1)}
scheduler = BackgroundScheduler(executors=executors)
scheduler.add_job(scheduled_scrape, 'interval', hours=1)
scheduler.start()


# ---------------------------------------------------------------------
# 5. API エンドポイント
# ---------------------------------------------------------------------
@app.route('/api/data')
def api_data():
    """
    各店舗ごとの最新レコードのみを返すエンドポイント。
    ※同じ店舗名でも、エリアが異なれば別店舗としてグループ化するように変更。
    タイムゾーンは JST (Asia/Tokyo) に変換して返します。
    """
    # 店舗名とエリアの複合キーで最新の timestamp を取得するサブクエリ
    subq = db.session.query(
        StoreStatus.store_name,
        StoreStatus.area,
        func.max(StoreStatus.timestamp).label("max_time")
    ).group_by(StoreStatus.store_name, StoreStatus.area).subquery()

    query = db.session.query(StoreStatus).join(
        subq,
        (StoreStatus.store_name == subq.c.store_name) &
        (StoreStatus.area == subq.c.area) &
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
@cache.cached(timeout=300)  # キャッシュ：5分間有効
def api_history():
    """
    すべてのスクレイピング履歴を時系列昇順で返すエンドポイント。
    タイムゾーンは JST (Asia/Tokyo) に変換して返します。
    """
    results = StoreStatus.query.order_by(StoreStatus.timestamp.asc()).all()
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


# ---------------------------------------------------------------------
# 6. 管理画面 (店舗URL管理)
# ---------------------------------------------------------------------
@app.route('/admin/manage', methods=['GET', 'POST'])
def manage_store_urls():
    """
    管理画面：店舗URL の追加・編集・削除を行うページ
    - GET: 登録済みURL一覧を表示
    - POST: 新規URLを追加
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
    店舗URL の編集ページ
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
# 7. 統合ダッシュボードルート
# ---------------------------------------------------------------------
@app.route('/')
def index():
    """
    統合ダッシュボードの HTML を返すルート
    """
    return render_template('integrated_dashboard.html')


# ---------------------------------------------------------------------
# 8. メイン実行部
# ---------------------------------------------------------------------
if __name__ == '__main__':
    # 開発用デバッグモード (本番環境では不要)
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
