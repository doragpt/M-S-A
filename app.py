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
    ・重複チェックは「店舗名＋エリア＋スクレイピング実行時刻（分単位切り捨て）」で行い、
      既に同じ複合キーが存在する場合はそのレコードを更新する（上書き）。
    ・古いデータ（2年以上前）を削除してパフォーマンスを維持する。
    ・データ更新後にキャッシュをクリアして最新情報を反映する。
    ・処理完了後、Socket.IO を利用してクライアントへ更新通知を送信する。
    """
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
            results = scrape_store_data(store_urls)
        except Exception as e:
            app.logger.error("スクレイピング中のエラー: %s", e)
            return

        # 結果をDBに保存（既存レコードがあれば更新、なければ新規追加）
        for record in results:
            if record:  # 空の結果はスキップ
                # 重複チェック：店舗名とエリアと実行時刻（分単位）でチェック
                existing = StoreStatus.query.filter(
                    StoreStatus.store_name == record.get('store_name'),
                    StoreStatus.area == record.get('area'),
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
                    # 新規の場合は新たにレコードを追加
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
        # キャッシュクリア：特に/api/historyのキャッシュを削除
        cache.clear()

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
# ジョブIDを 'scrape_job' として登録
executors = {'default': ProcessPoolExecutor(max_workers=1)}
scheduler = BackgroundScheduler(executors=executors)
scheduler.add_job(scheduled_scrape, 'interval', hours=1, id='scrape_job')
# 毎日午前3時にメンテナンスタスクを実行
scheduler.add_job(maintenance_task, 'cron', hour=3, minute=0, id='maintenance_job')
scheduler.start()

# ---------------------------------------------------------------------
# 5. API エンドポイント
# ---------------------------------------------------------------------
@app.route('/api/data')
@cache.cached(timeout=300)  # キャッシュ：5分間有効
def api_data():
    """
    各店舗の最新レコードのみを返すエンドポイント（タイムゾーンは JST）。
    集計値（全体平均など）も含める。

    クエリパラメータ:
        page: ページ番号（1から始まる）
        per_page: 1ページあたりの項目数（最大100）
        biz_type: 業種でフィルタリング
        area: エリアでフィルタリング
        format: 'compact'を指定すると必要最小限のフィールドだけを返す
    """
    """
    各店舗の最新レコードのみを返すエンドポイント（タイムゾーンは JST）。
    集計値（全体平均など）も含める。

    クエリパラメータ:
        page: ページ番号（1から始まる）
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

    # ページネーションのパラメータを取得
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    # クエリの全結果を取得（集計値の計算用）
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

    # ページネーション適用
    paginated_result = paginate_query_results(query, page, per_page)
    items = paginated_result['items']

    # データのフォーマット
    jst = pytz.timezone('Asia/Tokyo')
    data = [format_store_status(item, jst) for item in items]

    # レスポンスの組み立て（データ、集計値、ページネーション情報を含む）
    response = {
        "data": data,
        "meta": {
            "total_count": total_store_count,
            "valid_stores": valid_stores,
            "avg_rate": round(avg_rate, 1),
            "max_rate": round(max_rate,1), # Added max_rate to meta
            "total_working_staff": total_working_staff,
            "total_active_staff": total_active_staff,
            "page": paginated_result['meta']['page'],
            "per_page": paginated_result['meta']['per_page'],
            "total_pages": paginated_result['meta']['total_pages'],
            "has_prev": paginated_result['meta']['has_prev'],
            "has_next": paginated_result['meta']['has_next']
        }
    }

    return jsonify(response)


@app.route('/api/history')
@cache.cached(timeout=300)  # キャッシュ：5分間有効（DB負荷軽減）
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

    # ベースクエリ
    query = StoreStatus.query

    # フィルタリング条件適用
    if store := request.args.get('store'):
        query = query.filter(StoreStatus.store_name == store)

    if start_date := request.args.get('start_date'):
        query = query.filter(StoreStatus.timestamp >= f"{start_date} 00:00:00")

    if end_date := request.args.get('end_date'):
        query = query.filter(StoreStatus.timestamp <= f"{end_date} 23:59:59")

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
        jst = pytz.timezone('Asia/Tokyo')
        data = [format_store_status(item, jst) for item in items]

        # メタデータを含むレスポンス
        response = {
            "items": data,
            "meta": paginated_result['meta']
        }
        return jsonify(response)
    else:
        # 従来通りすべての結果を返す場合
        results = query.all()
        jst = pytz.timezone('Asia/Tokyo')
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
# 9. メイン実行部
# ---------------------------------------------------------------------
if __name__ == '__main__':
    # ローカル環境用の設定
    import os
    port = int(os.environ.get("PORT", 5000))

    # サーバー起動 - シンプルな設定
    print(f"サーバーを起動しています: http://0.0.0.0:{port}")
    socketio.run(app, host="0.0.0.0", port=port, debug=True, allow_unsafe_werkzeug=True)

# 新規エンドポイント: 平均稼働ランキング
@app.route('/api/ranking/average')
@cache.cached(timeout=600)  # キャッシュ：10分間有効
def api_average_ranking():
    """
    店舗の平均稼働率ランキングを返すエンドポイント

    クエリパラメータ:
        biz_type: 業種でフィルタリング
        limit: 上位何件を返すか（デフォルト20件）
    """
    # フィルタリング条件
    biz_type = request.args.get('biz_type')
    limit = request.args.get('limit', 20, type=int)

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

    # グループ化と最小サンプル数フィルタ
    subq = subq.group_by(StoreStatus.store_name).having(func.count() >= 10).subquery()

    # メインクエリ: ランキング取得
    query = db.session.query(
        subq.c.store_name,
        subq.c.avg_rate,
        subq.c.sample_count,
        subq.c.biz_type,
        subq.c.genre,
        subq.c.area
    ).order_by(subq.c.avg_rate.desc()).limit(limit)

    results = query.all()

    # 結果を整形
    data = [{
        'store_name': r.store_name,
        'avg_rate': float(r.avg_rate),
        'sample_count': r.sample_count,
        'biz_type': r.biz_type,
        'genre': r.genre,
        'area': r.area
    } for r in results]

    return jsonify(data)

# 集計済みデータを提供するエンドポイント（日付ごとの平均稼働率など）
@app.route('/api/aggregated')
@cache.cached(timeout=3600)  # キャッシュ：1時間有効
def api_aggregated_data():
    """
    日付ごとに集計された平均稼働率データを返すエンドポイント
    """
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

    # 結果を整形
    data = [{
        'date': r.date.isoformat(),
        'avg_rate': float(r.avg_rate),
        'sample_count': r.sample_count
    } for r in results]

    return jsonify(data)
