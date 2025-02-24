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
# 環境変数 REDIS_URL が設定されていなければローカルの Redis を使用
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

# APScheduler の設定：1時間ごとに scheduled_scrape を実行
# ジョブIDを 'scrape_job' として登録
executors = {'default': ProcessPoolExecutor(max_workers=1)}
scheduler = BackgroundScheduler(executors=executors)
scheduler.add_job(scheduled_scrape, 'interval', hours=1, id='scrape_job')
scheduler.start()

# ---------------------------------------------------------------------
# 5. API エンドポイント
# ---------------------------------------------------------------------
@app.route('/api/data')
def api_data():
    """
    各店舗の最新レコードのみを返すエンドポイント（タイムゾーンは JST）。
    """
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
@cache.cached(timeout=300)  # キャッシュ：5分間有効（DB負荷軽減）
def api_history():
    """
    全スクレイピング履歴を時系列昇順で返すエンドポイント（タイムゾーンは JST）。
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
    # 開発用デバッグモード (本番環境では不要)
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)

②store_scraper.py
import asyncio
from pyppeteer import launch
from pyppeteer_stealth import stealth
from bs4 import BeautifulSoup
import re
from datetime import datetime, timedelta
import pytz
import gc

# -------------------------------
# 定数設定
# -------------------------------
# 並列処理する店舗数の上限（同時に処理するタスク数）
MAX_CONCURRENT_TASKS = 10
# 店舗情報が取得できなかった場合の再試行回数
MAX_RETRIES_FOR_INFO = 3

# -------------------------------
# fetch_page 関数
# -------------------------------
async def fetch_page(page, url, retries=3, timeout=20000):
    """
    指定されたページでURLにアクセスし、ネットワークアイドル状態になるまで待機する関数
    
    Parameters:
      - page: pyppeteer のページオブジェクト
      - url: アクセスするURL
      - retries: リトライ回数（デフォルト3回）
      - timeout: タイムアウト（ミリ秒単位、デフォルト20000ms=20秒）
    
    Returns:
      - True: ページの読み込みに成功した場合
      - False: 全てのリトライで失敗した場合
    """
    for attempt in range(retries):
        try:
            await page.goto(url, waitUntil='networkidle0', timeout=timeout)
            return True
        except Exception as e:
            print(f"ページロード失敗（リトライ {attempt+1}/{retries}）: {url} - {e}")
            await asyncio.sleep(5)
    return False

# -------------------------------
# scrape_store 関数
# -------------------------------
async def scrape_store(browser, url: str, semaphore) -> dict:
    """
    単一店舗の「本日出勤」情報をスクレイピングする関数
    - ヘッドレスブラウザを用いて対象ページにアクセス
    - BeautifulSoup でHTMLをパースして店舗情報とシフト情報を取得
    - ページ再利用により再取得時に新規ページ作成のオーバーヘッドを削減
    
    Parameters:
      - browser: pyppeteer のブラウザオブジェクト
      - url: 店舗の基本URL（必要に応じて末尾に "/" を追加）
      - semaphore: 並列実行制御用のセマフォ
    
    Returns:
      - dict: 取得した店舗情報およびシフト情報の集計結果
    """
    async with semaphore:
        # URLの末尾に "/" がなければ追加し、出勤情報ページのURLを作成
        if not url.endswith("/"):
            url += "/"
        attend_url = url + "attend/soon/"

        # 新規ページを作成
        page = await browser.newPage()
        try:
            await stealth(page)  # 検出回避のため stealth を適用
        except Exception as e:
            print("stealth 適用エラー:", e)
        # ユーザーエージェントを設定
        await page.setUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        )

        print("初回アクセス中:", attend_url)
        # 指定URLにアクセス。タイムアウト20秒、リトライ3回
        success = await fetch_page(page, attend_url, retries=3, timeout=20000)
        if not success:
            await page.close()
            return {}
        # ページ読み込み後、1秒待機
        await asyncio.sleep(1)
        # ページコンテンツを取得し、BeautifulSoupでパース
        content = await page.content()
        soup = BeautifulSoup(content, "html.parser")

        # 店舗情報の初期値を「不明」とする
        store_name, biz_type, genre, area = "不明", "不明", "不明", "不明"
        # エリア情報の取得（現在のエリアを示す <li> 要素から）
        current_area_elem = soup.find("li", class_="area_menu_item current")
        if current_area_elem:
            a_elem = current_area_elem.find("a")
            if a_elem:
                area = a_elem.get_text(strip=True)

        # 店舗名、業種、ジャンルの情報取得（再取得時は同じページを再利用）
        attempt = 0
        while attempt < MAX_RETRIES_FOR_INFO:
            menushop_div = soup.find("div", class_="menushopname none")
            if menushop_div:
                h1_elem = menushop_div.find("h1")
                if h1_elem:
                    store_name = h1_elem.get_text(strip=True)
                    # h1 の次の要素から店舗情報を取得する
                    store_info = h1_elem.next_sibling.strip() if h1_elem.next_sibling else ""
                    match = re.search(r"(.+?)\((.+?)/(.+?)\)", store_info)
                    if match:
                        biz_type, genre, extracted_area = match.groups()
                        if area == "不明":
                            area = extracted_area
            # 情報が取得できたらループ終了
            if store_name != "不明":
                break
            attempt += 1
            print(f"店舗情報再取得試行 {attempt} 回目: {url}")
            # 同じページを再利用して再度アクセス
            success = await fetch_page(page, attend_url, retries=3, timeout=20000)
            if not success:
                await page.close()
                return {}
            await asyncio.sleep(1)
            content = await page.content()
            soup = BeautifulSoup(content, "html.parser")
            current_area_elem = soup.find("li", class_="area_menu_item current")
            if current_area_elem:
                a_elem = current_area_elem.find("a")
                if a_elem:
                    area = a_elem.get_text(strip=True)
        if store_name == "不明":
            print("再取得に失敗: ", url)
            await page.close()
            return {}

        # 出勤情報の取得
        container = soup.find("div", class_="shukkin-list-container")
        if not container:
            await page.close()
            return {
                "store_name": store_name,
                "biz_type": biz_type,
                "genre": genre,
                "area": area,
                "total_staff": 0,
                "working_staff": 0,
                "active_staff": 0,
                "url": url,
                "shift_time": ""
            }
        # 本日の出勤情報は "item-0" 部分にある（明日の情報は対象外）
        today_tab = container.find("div", class_="item-0")
        wrappers = today_tab.find_all("div", class_="sugunavi_wrapper") if today_tab else []
        print("シフト件数:", len(wrappers))

        total_staff = 0     # 総出勤数
        working_staff = 0   # 勤務中の人数
        active_staff = 0    # 「即ヒメ」（待機中）の人数

        jst = pytz.timezone('Asia/Tokyo')
        # 各シフト（wrapper）ごとにループ処理
        for wrapper in wrappers:
            p_elems = wrapper.find_all("p", class_="time_font_size shadow shukkin_detail_time")
            for p_elem in p_elems:
                text = p_elem.get_text(strip=True)
                if not text:
                    continue
                # 「明日」「次回」「出勤予定」「お休み」などが含まれている場合はスキップ
                if any(kw in text for kw in ["明日", "次回", "出勤予定", "お休み"]):
                    continue
                # 「完売」の場合は出勤数のみをカウント
                if "完売" in text:
                    total_staff += 1
                    continue
                # シフト時間（例： "10:00～18:00"）のパターンを抽出
                match = re.search(r"(\d{1,2}):(\d{2})～(\d{1,2}):(\d{2})", text)
                if match:
                    start_h, start_m, end_h, end_m = map(int, match.groups())
                    current_time = datetime.now(jst)
                    parsed_start = datetime.strptime(f"{start_h}:{start_m}", "%H:%M").time()
                    parsed_end = datetime.strptime(f"{end_h}:{end_m}", "%H:%M").time()
                    # シフトが日を跨ぐ場合の処理
                    if parsed_end < parsed_start:
                        if current_time.time() < parsed_end:
                            start_time = datetime.combine(current_time.date() - timedelta(days=1), parsed_start)
                            end_time = datetime.combine(current_time.date(), parsed_end)
                        else:
                            start_time = datetime.combine(current_time.date(), parsed_start)
                            end_time = datetime.combine(current_time.date() + timedelta(days=1), parsed_end)
                    else:
                        start_time = datetime.combine(current_time.date(), parsed_start)
                        end_time = datetime.combine(current_time.date(), parsed_end)
                    # タイムゾーンを適用
                    start_time = jst.localize(start_time)
                    end_time = jst.localize(end_time)
                    total_staff += 1
                    # 現在の時刻がシフト内にある場合は勤務中とカウント
                    if start_time <= current_time <= end_time:
                        working_staff += 1
                        status_container = wrapper.find("div", class_="sugunavi_spacer_1line")
                        if not status_container:
                            status_container = wrapper.find("div", class_="sugunavi_spacer_2line")
                        if status_container:
                            sugunavibox = status_container.find("div", class_="sugunavibox")
                            if sugunavibox:
                                status_text = sugunavibox.get_text(strip=True)
                            else:
                                status_text = ""
                        else:
                            status_text = ""
                        if ("待機中" in status_text) or (status_text == ""):
                            active_staff += 1
        await page.close()
        return {
            "store_name": store_name,
            "biz_type": biz_type,
            "genre": genre,
            "area": area,
            "total_staff": total_staff,
            "working_staff": working_staff,
            "active_staff": active_staff,
            "url": url,
            "shift_time": ""
        }

# -------------------------------
# _scrape_all 関数
# -------------------------------
async def _scrape_all(store_urls: list) -> list:
    """
    複数店舗のスクレイピングを並列実行数制限付きで実行する関数
    - バッチ処理間の待機時間を3秒から2秒に短縮
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
    executable_path = '/usr/bin/google-chrome'
    browser = await launch(
        headless=True,
        executablePath=executable_path,
        ignoreHTTPSErrors=True,
        defaultViewport=None,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--single-process",
            "--headless=new",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-infobars"
        ]
    )
    # 各店舗URLに対するスクレイピングタスクを作成
    tasks = [scrape_store(browser, url, semaphore) for url in store_urls]
    results = []
    # タスクをバッチ単位で実行し、各バッチの間は2秒待機
    for i in range(0, len(tasks), MAX_CONCURRENT_TASKS):
        batch = tasks[i:i+MAX_CONCURRENT_TASKS]
        batch_results = await asyncio.gather(*batch, return_exceptions=True)
        results.extend(batch_results)
        await asyncio.sleep(2)  # 待機時間を2秒に変更
    gc.collect()
    await browser.close()
    return results

# -------------------------------
# scrape_store_data 関数
# -------------------------------
def scrape_store_data(store_urls: list) -> list:
    """
    非同期スクレイピング処理を同期的に実行するラッパー関数
    """
    return asyncio.run(_scrape_all(store_urls))

③integrated_dashbord.html
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>統合ダッシュボード</title>
  <!-- Bootstrap CSS -->
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet" />
  <!-- DataTables CSS -->
  <link rel="stylesheet" type="text/css" href="https://cdn.datatables.net/1.13.4/css/jquery.dataTables.min.css" />
  <!-- Chart.js -->
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <!-- Chart.js Zoom Plugin -->
  <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@1.2.1/dist/chartjs-plugin-zoom.min.js"></script>
  <style>
    /* 共通スタイル */
    body {
      background-color: #f7f7f7;
      font-family: 'Roboto', Arial, sans-serif;
    }
    .header {
      background-color: #007bff;
      color: #fff;
      padding: 15px;
      text-align: center;
      box-shadow: 0 2px 4px rgba(0,0,0,0.1);
      margin-bottom: 20px;
    }
    .header.dark {
      background-color: #444;
    }
    .card {
      border: none;
      border-radius: 8px;
      box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .card-header {
      font-weight: bold;
      background-color: rgba(0,123,255,0.85);
    }
    .card-body {
      font-size: 1.2rem;
    }
    .tab-pane {
      margin-top: 20px;
    }
    .chart-container {
      position: relative;
      margin: auto;
      height: 400px;
    }
    table.dataTable tbody tr {
      background-color: transparent;
    }
    .store-name.clickable {
      cursor: pointer;
      color: #007bff;
      text-decoration: underline;
    }
    .badge {
      font-size: 0.9rem;
    }
    /* ダークモード */
    body.dark {
      background-color: #333;
      color: #eee;
    }
    body.dark .header {
      background-color: #444;
    }
    body.dark .card {
      background-color: #555;
      color: #fff;
    }
    body.dark table.dataTable tbody tr {
      color: #fff;
    }
    .modal-header {
      background-color: #007bff;
      color: #fff;
      border-bottom: none;
    }
    /* Toast 用スタイル */
    .toast-container {
      position: fixed;
      top: 20px;
      right: 20px;
      z-index: 1055;
    }
  </style>
</head>
<body>
  <!-- ヘッダー -->
  <div class="header" id="pageHeader">
    <h1>統合ダッシュボード</h1>
    <div class="btn-group">
      <button id="darkModeToggle" class="btn btn-light btn-sm">ダークモード切替</button>
      <button id="favoritesToggle" class="btn btn-light btn-sm">お気に入りのみ表示</button>
    </div>
  </div>

  <div class="container-fluid my-3">
    <!-- メインタブ -->
    <ul class="nav nav-tabs" id="dashboardTabs" role="tablist">
      <li class="nav-item" role="presentation">
        <a class="nav-link active" id="dashboard-tab" data-bs-toggle="tab" href="#dashboard" role="tab">
          店舗稼働状況
        </a>
      </li>
      <li class="nav-item" role="presentation">
        <a class="nav-link" id="history-tab" data-bs-toggle="tab" href="#history" role="tab">
          店舗履歴グラフ（期間指定）
        </a>
      </li>
      <li class="nav-item" role="presentation">
        <a class="nav-link" id="hourly-tab" data-bs-toggle="tab" href="#hourly" role="tab">
          時間帯別分析
        </a>
      </li>
      <li class="nav-item" role="presentation">
        <a class="nav-link" id="area-tab" data-bs-toggle="tab" href="#area" role="tab">
          エリア統計
        </a>
      </li>
      <li class="nav-item" role="presentation">
        <a class="nav-link" id="type-tab" data-bs-toggle="tab" href="#type" role="tab">
          業種内ジャンルランキング
        </a>
      </li>
      <!-- 新規追加：平均稼働ランキングタブ -->
      <li class="nav-item" role="presentation">
        <a class="nav-link" id="ranking-tab" data-bs-toggle="tab" href="#ranking" role="tab">
          平均稼働ランキング
        </a>
      </li>
    </ul>

    <!-- タブコンテンツ -->
    <div class="tab-content" id="dashboardTabsContent">
      <!-- タブ1: 店舗稼働状況 -->
      <div class="tab-pane fade show active" id="dashboard" role="tabpanel" aria-labelledby="dashboard-tab">
        <div class="row my-4">
          <!-- 各カード：店舗数、平均稼働率、最高稼働率 -->
          <div class="col-md-4 mb-3">
            <div class="card text-white bg-primary">
              <div class="card-header">店舗数</div>
              <div class="card-body">
                <h5 id="totalStores" class="card-title">--</h5>
                <p class="card-text">現在データがある店舗数</p>
              </div>
            </div>
          </div>
          <div class="col-md-4 mb-3">
            <div class="card text-white bg-success">
              <div class="card-header">平均稼働率</div>
              <div class="card-body">
                <h5 id="avgRate" class="card-title">--</h5>
                <p class="card-text">勤務中キャストに対する実働率</p>
              </div>
            </div>
          </div>
          <div class="col-md-4 mb-3">
            <div class="card text-white bg-warning">
              <div class="card-header">最高稼働率</div>
              <div class="card-body">
                <h5 id="maxRate" class="card-title">--</h5>
                <p class="card-text">最高の稼働率店舗の稼働率</p>
              </div>
            </div>
          </div>
        </div>

        <!-- 店舗情報テーブル -->
        <div class="row">
          <div class="col">
            <div class="table-responsive">
              <table id="storeTable" class="table table-striped table-bordered">
                <thead>
                  <tr>
                    <th>選択</th>
                    <th>店舗名</th>
                    <th>業種</th>
                    <th>ジャンル</th>
                    <th>エリア</th>
                    <th>総出勤</th>
                    <th>勤務中人数</th>
                    <th>即ヒメ人数</th>
                    <th>現在稼働率 (%)</th>
                    <th>状態</th>
                    <th>直近1週間の稼働率 (%)</th>
                    <th>直近1か月の稼働率 (%)</th>
                  </tr>
                </thead>
                <tbody id="dataTable"></tbody>
              </table>
            </div>
            <!-- CSVエクスポートボタン追加 -->
            <button id="exportCSV" class="btn btn-secondary mt-2">CSVエクスポート</button>
            <button id="compareBtn" class="btn btn-info mt-2">比較分析</button>
          </div>
        </div>

        <!-- トップ10店舗グラフ -->
        <div class="row my-3">
          <div class="col">
            <canvas id="currentRateChart"></canvas>
          </div>
        </div>
      </div><!-- /タブ1 -->

      <!-- タブ2: 店舗履歴グラフ（期間指定） -->
      <div class="tab-pane fade" id="history" role="tabpanel" aria-labelledby="history-tab">
        <div class="my-4">
          <h3>店舗履歴グラフ（期間指定）</h3>
          <div class="row mb-3">
            <div class="col-md-3">
              <label for="storeSelectHistory" class="form-label">店舗選択</label>
              <select id="storeSelectHistory" class="form-select">
                <option value="">全店舗</option>
              </select>
            </div>
            <div class="col-md-3">
              <label for="startDate" class="form-label">開始日</label>
              <input type="date" id="startDate" class="form-control" />
            </div>
            <div class="col-md-3">
              <label for="endDate" class="form-label">終了日</label>
              <input type="date" id="endDate" class="form-control" />
            </div>
            <div class="col-md-3 d-flex align-items-end">
              <button id="updateHistoryBtn" class="btn btn-primary w-100">更新</button>
            </div>
          </div>
          <div class="card mb-3">
            <div class="card-body">
              <canvas id="historyChart"></canvas>
            </div>
          </div>
          <div class="card">
            <div class="card-body">
              <h5 class="card-title">集計結果</h5>
              <p id="historyStats" class="mb-0">--</p>
            </div>
          </div>
        </div>
      </div><!-- /タブ2 -->

      <!-- タブ3: 時間帯別分析 -->
      <div class="tab-pane fade" id="hourly" role="tabpanel" aria-labelledby="hourly-tab">
        <div class="my-4">
          <h3>時間帯別分析</h3>
          <div class="row mb-3">
            <div class="col-md-4">
              <label for="hourlyStoreSelect" class="form-label">店舗選択</label>
              <select id="hourlyStoreSelect" class="form-select">
                <option value="">全店舗</option>
              </select>
            </div>
            <div class="col-md-4 d-flex align-items-end">
              <button id="updateHourlyBtn" class="btn btn-primary w-100">更新</button>
            </div>
          </div>
          <div class="chart-container">
            <canvas id="hourlyChart"></canvas>
          </div>
        </div>
      </div><!-- /タブ3 -->

      <!-- タブ4: エリア統計 -->
      <div class="tab-pane fade" id="area" role="tabpanel" aria-labelledby="area-tab">
        <div class="my-4">
          <h2 class="mb-4 text-center">エリア統計</h2>
          <div class="table-responsive mb-3">
            <table class="table table-bordered table-striped">
              <thead>
                <tr>
                  <th>エリア</th>
                  <th>店舗数</th>
                  <th>平均稼働率 (%)</th>
                </tr>
              </thead>
              <tbody id="areaTable"></tbody>
            </table>
          </div>
          <div class="card">
            <div class="card-body">
              <canvas id="areaChart"></canvas>
            </div>
          </div>
        </div>
      </div><!-- /タブ4 -->

      <!-- タブ5: 業種内ジャンルランキング -->
      <div class="tab-pane fade" id="type" role="tabpanel" aria-labelledby="type-tab">
        <div class="my-4">
          <h2 class="mb-4 text-center">業種内ジャンルランキング</h2>
          <div class="row mb-3">
            <div class="col-md-6">
              <label for="industrySelectType" class="form-label">業種を選択</label>
              <select id="industrySelectType" class="form-select"></select>
            </div>
          </div>
          <div class="table-responsive mb-3">
            <table id="typeTable" class="table table-bordered table-striped">
              <thead>
                <tr>
                  <th>ジャンル</th>
                  <th>店舗数</th>
                  <th>平均稼働率 (%)</th>
                </tr>
              </thead>
              <tbody id="typeTable"></tbody>
            </table>
          </div>
          <div class="card">
            <div class="card-body">
              <canvas id="typeChart"></canvas>
            </div>
          </div>
        </div>
      </div><!-- /タブ5 -->

      <!-- タブ6: 平均稼働ランキング -->
      <div class="tab-pane fade" id="ranking" role="tabpanel" aria-labelledby="ranking-tab">
        <div class="my-4">
          <h2 class="mb-4 text-center">平均稼働ランキング</h2>
          <div class="row mb-3">
            <div class="col-md-6">
              <label for="rankingTypeSelect" class="form-label">ランキングタイプ</label>
              <select id="rankingTypeSelect" class="form-select">
                <option value="all" selected>全店舗</option>
                <option value="industry">業種別</option>
              </select>
            </div>
            <div class="col-md-6" id="industryFilterDiv" style="display: none;">
              <label for="industrySelectRanking" class="form-label">業種を選択</label>
              <select id="industrySelectRanking" class="form-select">
                <!-- 業種リストはJSで動的に追加 -->
              </select>
            </div>
          </div>
          <button id="updateRankingBtn" class="btn btn-primary mb-3">ランキング更新</button>
          <div class="table-responsive">
            <table id="rankingTable" class="table table-bordered table-striped">
              <thead>
                <tr>
                  <th>順位</th>
                  <th>店舗名</th>
                  <th>業種</th>
                  <th>ジャンル</th>
                  <th>エリア</th>
                  <th>平均稼働率 (%)</th>
                </tr>
              </thead>
              <tbody id="rankingTableBody"></tbody>
            </table>
          </div>
        </div>
      </div><!-- /タブ6 -->

    </div> <!-- /tab-content -->
  </div> <!-- /container-fluid -->

  <!-- 店舗詳細モーダル -->
  <div class="modal fade" id="storeDetailModal" tabindex="-1" aria-labelledby="storeDetailModalLabel" aria-hidden="true">
    <div class="modal-dialog modal-lg">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" id="storeDetailModalLabel">店舗詳細</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="閉じる"></button>
        </div>
        <div class="modal-body">
          <div id="storeDetailBasic"></div>
          <hr />
          <!-- モーダル内タブ：履歴グラフ・時間帯別分析 -->
          <ul class="nav nav-tabs" id="detailTabs" role="tablist">
            <li class="nav-item" role="presentation">
              <a class="nav-link active" id="detail-history-tab" data-bs-toggle="tab" href="#detailHistory" role="tab">履歴グラフ</a>
            </li>
            <li class="nav-item" role="presentation">
              <a class="nav-link" id="detail-hourly-tab" data-bs-toggle="tab" href="#detailHourly" role="tab">時間帯分析</a>
            </li>
          </ul>
          <div class="tab-content" id="detailTabsContent">
            <div class="tab-pane fade show active" id="detailHistory" role="tabpanel" aria-labelledby="detail-history-tab">
              <div class="chart-container">
                <canvas id="detailHistoryChart"></canvas>
              </div>
            </div>
            <div class="tab-pane fade" id="detailHourly" role="tabpanel" aria-labelledby="detail-hourly-tab">
              <div class="chart-container">
                <canvas id="detailHourlyChart"></canvas>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- 比較分析モーダル -->
  <div class="modal fade" id="compareModal" tabindex="-1" aria-labelledby="compareModalLabel" aria-hidden="true">
    <div class="modal-dialog modal-lg">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title" id="compareModalLabel">店舗比較分析</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="閉じる"></button>
        </div>
        <div class="modal-body">
          <canvas id="compareChart"></canvas>
        </div>
      </div>
    </div>
  </div>

  <!-- Toast通知用コンテナ -->
  <div class="toast-container">
    <div id="updateToast" class="toast align-items-center text-bg-info border-0" role="alert" aria-live="assertive" aria-atomic="true">
      <div class="d-flex">
        <div class="toast-body">
          新しいデータに更新しました！
        </div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
      </div>
    </div>
  </div>

  <!-- jQuery, DataTables, Bootstrap JS -->
  <script src="https://code.jquery.com/jquery-3.6.4.min.js"></script>
  <script src="https://cdn.datatables.net/1.13.4/js/jquery.dataTables.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
  <!-- Socket.IO クライアントライブラリ -->
  <script src="https://cdn.socket.io/3.1.3/socket.io.min.js"></script>

  <script>
    (function () {
      document.addEventListener("DOMContentLoaded", async () => {

        /***********************************************
         * fetchWithRetry: API呼び出しのリトライ処理
         ***********************************************/
        async function fetchWithRetry(url, options = {}, retries = 3, delay = 2000) {
          for (let i = 0; i < retries; i++) {
            try {
              const response = await fetch(url, options);
              if (!response.ok) throw new Error("HTTP error " + response.status);
              return response;
            } catch (err) {
              console.error(Fetch error (${url}), attempt ${i + 1}:, err);
              if (i < retries - 1) await new Promise((resolve) => setTimeout(resolve, delay));
              else throw err;
            }
          }
        }

        /***********************************************
         * グローバル変数と初期設定
         ***********************************************/
        const charts = {
          currentRate: null,
          history: null,
          hourly: null,
          area: null,
          type: null,
          detailHistory: null,
          detailHourly: null,
          compare: null,
        };
        let favorites = JSON.parse(localStorage.getItem("favorites") || "[]");
        let favoritesFilterActive = false;

        // ユーザー設定（フィルタ値）の復元
        const savedIndustry = localStorage.getItem("industryFilter") || "";
        const savedHistoryStore = localStorage.getItem("historyStoreFilter") || "";
        const savedHourlyStore = localStorage.getItem("hourlyStoreFilter") || "";

        /***********************************************
         * ダークモード & お気に入りフィルタ
         ***********************************************/
        document.getElementById("darkModeToggle")?.addEventListener("click", () => {
          document.body.classList.toggle("dark");
          document.getElementById("pageHeader")?.classList.toggle("dark");
        });
        document.getElementById("favoritesToggle")?.addEventListener("click", () => {
          const rows = document.querySelectorAll("#storeTable tbody tr");
          favoritesFilterActive = !favoritesFilterActive;
          rows.forEach((row) => {
            const storeName = row.cells[1].innerText.trim();
            row.style.display = (favoritesFilterActive && !favorites.includes(storeName)) ? "none" : "";
          });
          document.getElementById("favoritesToggle").innerText = favoritesFilterActive
            ? "すべて表示"
            : "お気に入りのみ表示";
        });

        /***********************************************
         * CSVエクスポート機能：店舗テーブルの内容をCSV形式でダウンロード
         ***********************************************/
        document.getElementById("exportCSV")?.addEventListener("click", () => {
          const rows = Array.from(document.querySelectorAll("#storeTable tr"));
          const csvContent = rows.map(row => {
            return Array.from(row.cells)
                        .map(cell => "${cell.innerText.trim().replace(/"/g, '""')}")
                        .join(",");
          }).join("\n");
          const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = "store_data.csv";
          a.click();
          URL.revokeObjectURL(url);
        });

        /***********************************************
         * 店舗稼働状況更新 (/api/data)
         * 最新データから店舗数、平均/最高稼働率を計算しテーブル更新
         ***********************************************/
        async function updateDashboard() {
          try {
            const response = await fetchWithRetry("/api/data", {}, 3, 2000);
            const data = await response.json();

            // 上部カード更新
            document.getElementById("totalStores").innerText = data.length;
            const avgRate = data.reduce((sum, store) => {
              if (store.working_staff > 0) {
                return sum + ((store.working_staff - store.active_staff) / store.working_staff * 100);
              }
              return sum;
            }, 0) / (data.length || 1);
            document.getElementById("avgRate").innerText = avgRate.toFixed(1) + "%";
            const maxRate = data.reduce((max, store) => {
              if (store.working_staff > 0) {
                return Math.max(max, ((store.working_staff - store.active_staff) / store.working_staff * 100));
              }
              return max;
            }, 0);
            document.getElementById("maxRate").innerText = maxRate.toFixed(1) + "%";

            // テーブル更新
            const tableBody = document.getElementById("dataTable");
            tableBody.innerHTML = "";
            data.forEach((store) => {
              const rate = store.working_staff > 0 ? ((store.working_staff - store.active_staff) / store.working_staff * 100) : 0;
              const rateStr = rate.toFixed(1);
              const tagHtml = rate >= 50 
                              ? '<span class="badge bg-danger">高稼働</span>' 
                              : '<span class="badge bg-secondary">低稼働</span>';
              const isFav = favorites.includes(store.store_name);
              const rowHtml = 
                <tr>
                  <td>
                    <button class="favorite-btn btn btn-sm ${isFav ? "btn-warning" : "btn-outline-warning"}" data-store-name="${store.store_name}">★</button>
                    <input type="checkbox" class="compare-checkbox" data-store-name="${store.store_name}">
                  </td>
                  <td class="store-name clickable" data-store='${encodeURIComponent(JSON.stringify(store))}'>${store.store_name}</td>
                  <td>${store.biz_type || ""}</td>
                  <td>${store.genre || ""}</td>
                  <td>${store.area || ""}</td>
                  <td>${store.total_staff}</td>
                  <td>${store.working_staff}</td>
                  <td>${store.active_staff}</td>
                  <td>${rateStr}</td>
                  <td>${tagHtml}</td>
                  <td>--</td>
                  <td>--</td>
                </tr>;
              tableBody.insertAdjacentHTML("beforeend", rowHtml);
            });

            // DataTables 初期化
            if ($.fn.dataTable.isDataTable("#storeTable")) {
              $("#storeTable").DataTable().destroy();
            }
            $("#storeTable").DataTable({
              responsive: true,
              order: [[8, "desc"]],
              language: { search: "検索:" }
            });

            // お気に入りボタンイベント
            $("#storeTable tbody").off("click", ".favorite-btn").on("click", ".favorite-btn", function (e) {
              e.stopPropagation();
              const storeName = $(this).data("store-name");
              if (favorites.includes(storeName)) {
                favorites = favorites.filter(n => n !== storeName);
                $(this).removeClass("btn-warning").addClass("btn-outline-warning");
              } else {
                favorites.push(storeName);
                $(this).removeClass("btn-outline-warning").addClass("btn-warning");
              }
              localStorage.setItem("favorites", JSON.stringify(favorites));
            });

            // 店舗詳細モーダル表示
            $("#storeTable tbody").off("click", ".store-name.clickable").on("click", ".store-name.clickable", function () {
              const store = JSON.parse(decodeURIComponent($(this).data("store")));
              const content = 
                <strong>店舗名:</strong> ${store.store_name}<br>
                <strong>業種:</strong> ${store.biz_type || "不明"}<br>
                <strong>ジャンル:</strong> ${store.genre || "不明"}<br>
                <strong>エリア:</strong> ${store.area || "不明"}<br>
                <strong>総出勤:</strong> ${store.total_staff}<br>
                <strong>勤務中人数:</strong> ${store.working_staff}<br>
                <strong>即ヒメ人数:</strong> ${store.active_staff}<br>
                <strong>店舗URL:</strong> <a href="${store.url}" target="_blank">${store.url}</a>
              ;
              $("#storeDetailBasic").html(content);
              updateDetailHistoryChart(store.store_name);
              updateDetailHourlyChart(store.store_name);
              new bootstrap.Modal(document.getElementById("storeDetailModal")).show();
            });
          } catch (error) {
            console.error("APIデータ取得エラー:", error);
          }
        }

        /***********************************************
         * トップ10店舗グラフ更新 (/api/data)
         * ズーム／パン機能を有効化
         ***********************************************/
        async function updateTop10Chart() {
          try {
            const response = await fetchWithRetry("/api/data", {}, 3, 2000);
            const data = await response.json();
            data.forEach(store => {
              store.currentRate = store.working_staff > 0 ? ((store.working_staff - store.active_staff) / store.working_staff * 100).toFixed(1) : 0;
            });
            const topData = data.sort((a, b) => b.currentRate - a.currentRate).slice(0, 10);
            const labels = topData.map(store => store.store_name);
            const rates = topData.map(store => store.currentRate);
            const ctx = document.getElementById("currentRateChart").getContext("2d");
            if (charts.currentRate) charts.currentRate.destroy();
            charts.currentRate = new Chart(ctx, {
              type: "bar",
              data: {
                labels: labels,
                datasets: [{
                  label: "現在稼働率 (%)",
                  data: rates,
                  backgroundColor: "rgba(75, 192, 192, 0.7)",
                  borderColor: "rgba(75, 192, 192, 1)",
                  borderWidth: 1
                }]
              },
              options: {
                indexAxis: "y",
                responsive: true,
                plugins: {
                  title: {
                    display: true,
                    text: "トップ10店舗の現在稼働率",
                    font: { size: 20 }
                  },
                  tooltip: {
                    callbacks: { label: context => ${context.parsed.x}% }
                  },
                  zoom: {
                    pan: { enabled: true, mode: 'xy' },
                    zoom: { enabled: true, mode: 'xy' }
                  },
                  legend: { display: false }
                },
                scales: {
                  x: {
                    beginAtZero: true,
                    max: 100,
                    title: { display: true, text: "稼働率(%)" }
                  },
                  y: { ticks: { font: { size: 12 } } }
                },
                animation: { duration: 1000 }
              }
            });
          } catch (error) {
            console.error("トップ10店舗グラフエラー:", error);
          }
        }

        /***********************************************
         * 比較分析モーダル (/api/data)
         ***********************************************/
        document.getElementById("compareBtn")?.addEventListener("click", async () => {
          const selectedStores = Array.from(document.querySelectorAll(".compare-checkbox:checked"))
                                        .map(cb => cb.getAttribute("data-store-name"));
          if (selectedStores.length < 2) {
            alert("比較分析は2店舗以上選択してください。");
            return;
          }
          try {
            const response = await fetchWithRetry("/api/data", {}, 3, 2000);
            const data = await response.json();
            const compareData = data.filter(store => selectedStores.includes(store.store_name));
            const labels = compareData.map(s => s.store_name);
            const rates = compareData.map(s => s.working_staff > 0 ? ((s.working_staff - s.active_staff) / s.working_staff * 100).toFixed(1) : 0);
            const ctx = document.getElementById("compareChart").getContext("2d");
            if (charts.compare) charts.compare.destroy();
            charts.compare = new Chart(ctx, {
              type: "bar",
              data: {
                labels: labels,
                datasets: [{
                  label: "現在稼働率 (%)",
                  data: rates,
                  backgroundColor: "rgba(54, 162, 235, 0.6)",
                  borderColor: "rgba(54, 162, 235, 1)",
                  borderWidth: 1
                }]
              },
              options: {
                responsive: true,
                plugins: {
                  title: {
                    display: true,
                    text: "比較分析",
                    font: { size: 20 }
                  },
                  zoom: {
                    pan: { enabled: true, mode: 'xy' },
                    zoom: { enabled: true, mode: 'xy' }
                  }
                }
              }
            });
            new bootstrap.Modal(document.getElementById("compareModal")).show();
          } catch (error) {
            console.error("比較分析エラー:", error);
          }
        });

        /***********************************************
         * 店舗履歴グラフ（期間指定） (/api/history)
         ***********************************************/
        async function updateHistoryChart() {
          try {
            const response = await fetchWithRetry("/api/history", {}, 3, 2000);
            const data = await response.json();
            const storeSelect = document.getElementById("storeSelectHistory");
            // 復元したフィルタ値をセット
            if(storeSelect) {
              storeSelect.value = savedHistoryStore;
            }
            const selectedStore = storeSelect ? storeSelect.value : "";
            const startDate = document.getElementById("startDate")?.value || "";
            const endDate = document.getElementById("endDate")?.value || "";
            let filtered = data;
            if (startDate) filtered = filtered.filter(record => new Date(record.timestamp) >= new Date(startDate));
            if (endDate) filtered = filtered.filter(record => new Date(record.timestamp) <= new Date(endDate));
            filtered.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
            let labels = [];
            let effectiveRates = [];
            if (!selectedStore) {
              const aggregator = {};
              filtered.forEach(record => {
                const d = new Date(record.timestamp);
                const dateStr = d.toLocaleDateString() + " " + d.toTimeString().substring(0, 5);
                if (!aggregator[dateStr]) aggregator[dateStr] = { sum: 0, count: 0 };
                if (record.working_staff > 0) {
                  aggregator[dateStr].sum += ((record.working_staff - record.active_staff) / record.working_staff * 100);
                }
                aggregator[dateStr].count += 1;
              });
              const dateKeys = Object.keys(aggregator).sort((a, b) => new Date(a) - new Date(b));
              labels = dateKeys;
              effectiveRates = dateKeys.map(key => (aggregator[key].count > 0 ? (aggregator[key].sum / aggregator[key].count).toFixed(1) : "0.0"));
            } else {
              filtered = filtered.filter(record => record.store_name === selectedStore);
              labels = filtered.map(record => {
                const d = new Date(record.timestamp);
                return d.toLocaleDateString() + " " + d.toTimeString().substring(0, 5);
              });
              effectiveRates = filtered.map(record => record.working_staff > 0 ? ((record.working_staff - record.active_staff) / record.working_staff * 100).toFixed(1) : 0);
            }
            const ctx = document.getElementById("historyChart").getContext("2d");
            if (charts.history) charts.history.destroy();
            charts.history = new Chart(ctx, {
              type: "line",
              data: {
                labels: labels,
                datasets: [{
                  label: "実働率 (%)",
                  data: effectiveRates,
                  borderColor: "rgba(255, 99, 132, 1)",
                  backgroundColor: "rgba(255, 99, 132, 0.2)",
                  fill: false,
                  tension: 0.3,
                  pointRadius: 3,
                  pointHoverRadius: 5
                }]
              },
              options: {
                responsive: true,
                plugins: {
                  title: {
                    display: true,
                    text: (selectedStore === "") 
                          ? "全店舗の平均稼働率（期間指定）"
                          : 店舗(${selectedStore})の履歴グラフ,
                    font: { size: 20 }
                  },
                  tooltip: { callbacks: { label: context => ${context.parsed.y}% } },
                  zoom: {
                    pan: { enabled: true, mode: 'xy' },
                    zoom: { enabled: true, mode: 'xy' }
                  }
                },
                scales: {
                  y: { beginAtZero: true, max: 100, title: { display: true, text: "実働率 (%)" } },
                  x: { title: { display: true, text: "日時" } }
                },
                animation: { duration: 1000 }
              }
            });
            // 統計結果表示
            const historyStats = document.getElementById("historyStats");
            if (effectiveRates.length > 0) {
              const numericRates = effectiveRates.map(Number);
              const overallAvg = numericRates.reduce((a, b) => a + b, 0) / numericRates.length;
              historyStats.textContent = 期間内サンプル数: ${numericRates.length} / 平均稼働率: ${overallAvg.toFixed(1)}%;
            } else {
              historyStats.textContent = "該当データなし";
            }
          } catch (error) {
            console.error("履歴グラフエラー:", error);
          }
        }
        document.getElementById("updateHistoryBtn")?.addEventListener("click", updateHistoryChart);

        // 履歴グラフ用店舗ドロップダウン更新（フィルタ値保存）
        async function fillHistoryStoreDropdown() {
          try {
            const response = await fetchWithRetry("/api/history", {}, 3, 2000);
            const data = await response.json();
            const storeSelect = document.getElementById("storeSelectHistory");
            if (!storeSelect) return;
            const stores = new Set();
            data.forEach(item => { if (item.store_name) stores.add(item.store_name); });
            storeSelect.innerHTML = '<option value="">全店舗</option>';
            stores.forEach(store => {
              storeSelect.innerHTML += <option value="${store}">${store}</option>;
            });
            // 復元済みのフィルタを反映
            storeSelect.value = savedHistoryStore;
            // 保存イベント
            storeSelect.addEventListener("change", () => {
              localStorage.setItem("historyStoreFilter", storeSelect.value);
            });
          } catch (err) {
            console.error("履歴グラフ店舗リスト取得エラー:", err);
          }
        }

        /***********************************************
         * 時間帯別分析 (/api/history)
         ***********************************************/
        async function updateHourlyAnalysis() {
          try {
            const response = await fetchWithRetry("/api/history", {}, 3, 2000);
            const data = await response.json();
            const hourlyStoreSelect = document.getElementById("hourlyStoreSelect");
            if(hourlyStoreSelect) {
              hourlyStoreSelect.value = savedHourlyStore;
              hourlyStoreSelect.addEventListener("change", () => {
                localStorage.setItem("hourlyStoreFilter", hourlyStoreSelect.value);
              });
            }
            const selectedStore = hourlyStoreSelect ? hourlyStoreSelect.value : "";
            let filtered = selectedStore ? data.filter(record => record.store_name === selectedStore) : data;
            const hourly = {};
            filtered.forEach(record => {
              if (record.working_staff > 0) {
                const d = new Date(record.timestamp);
                const hour = d.getHours();
                const rate = ((record.working_staff - record.active_staff) / record.working_staff) * 100;
                if (!hourly[hour]) hourly[hour] = { sum: 0, count: 0 };
                hourly[hour].sum += rate;
                hourly[hour].count += 1;
              }
            });
            const labels = [];
            const avgRates = [];
            for (let h = 0; h < 24; h++) {
              labels.push(${h}:00);
              avgRates.push(hourly[h] && hourly[h].count > 0 ? (hourly[h].sum / hourly[h].count).toFixed(1) : "0");
            }
            const ctx = document.getElementById("hourlyChart").getContext("2d");
            if (charts.hourly) charts.hourly.destroy();
            charts.hourly = new Chart(ctx, {
              type: "line",
              data: {
                labels: labels,
                datasets: [{
                  label: "平均実働率 (%)",
                  data: avgRates,
                  borderColor: "rgba(255, 159, 64, 1)",
                  backgroundColor: "rgba(255, 159, 64, 0.2)",
                  fill: false,
                  tension: 0.3,
                  pointRadius: 3,
                  pointHoverRadius: 5
                }]
              },
              options: {
                responsive: true,
                plugins: {
                  title: { display: true, text: "時間帯別分析", font: { size: 20 } },
                  tooltip: { callbacks: { label: context => ${context.parsed.y}% } },
                  zoom: {
                    pan: { enabled: true, mode: 'xy' },
                    zoom: { enabled: true, mode: 'xy' }
                  }
                },
                scales: {
                  y: { beginAtZero: true, max: 100, title: { display: true, text: "実働率 (%)" } },
                  x: { title: { display: true, text: "時間帯" } }
                },
                animation: { duration: 1000 }
              }
            });
          } catch (err) {
            console.error("時間帯別分析エラー:", err);
          }
        }
        document.getElementById("updateHourlyBtn")?.addEventListener("click", updateHourlyAnalysis);

        // 時間帯分析用店舗ドロップダウン更新
        async function fillHourlyStoreDropdown() {
          try {
            const response = await fetchWithRetry("/api/history", {}, 3, 2000);
            const data = await response.json();
            const hourlyStoreSelect = document.getElementById("hourlyStoreSelect");
            if (!hourlyStoreSelect) return;
            const stores = new Set();
            data.forEach(item => { if (item.store_name) stores.add(item.store_name); });
            hourlyStoreSelect.innerHTML = '<option value="">全店舗</option>';
            stores.forEach(store => {
              hourlyStoreSelect.innerHTML += <option value="${store}">${store}</option>;
            });
            hourlyStoreSelect.value = savedHourlyStore;
          } catch (err) {
            console.error("時間帯分析店舗リスト取得エラー:", err);
          }
        }

        /***********************************************
         * エリア統計 (/api/history) の更新（最新店舗のみ集計）
         ***********************************************/
        async function updateAreaStatistics() {
          try {
            const responseLatest = await fetchWithRetry("/api/data", {}, 3, 2000);
            const dataLatest = await responseLatest.json();
            const latestStoreNames = new Set(dataLatest.map(store => store.store_name));
            const responseHistory = await fetchWithRetry("/api/history", {}, 3, 2000);
            const dataHistory = await responseHistory.json();
            const filteredHistory = dataHistory.filter(record => latestStoreNames.has(record.store_name));
            const areaData = {};
            filteredHistory.forEach(record => {
              const area = record.area || "不明";
              if (!areaData[area]) {
                areaData[area] = { stores: new Set(), totalRate: 0, rateCount: 0 };
              }
              areaData[area].stores.add(record.id);
              if (record.working_staff > 0) {
                areaData[area].totalRate += ((record.working_staff - record.active_staff) / record.working_staff * 100);
                areaData[area].rateCount += 1;
              }
            });
            const areaTable = document.getElementById("areaTable");
            areaTable.innerHTML = "";
            const labels = [];
            const avgRates = [];
            for (const area in areaData) {
              const { stores, totalRate, rateCount } = areaData[area];
              const storeCount = stores.size;
              const avgRate = rateCount > 0 ? (totalRate / rateCount).toFixed(1) : "0.0";
              areaTable.insertAdjacentHTML("beforeend", 
                <tr>
                  <td>${area}</td>
                  <td>${storeCount}</td>
                  <td>${avgRate}</td>
                </tr>
              );
              labels.push(area);
              avgRates.push(parseFloat(avgRate));
            }
            const ctx = document.getElementById("areaChart").getContext("2d");
            if (charts.area) charts.area.destroy();
            charts.area = new Chart(ctx, {
              type: "bar",
              data: {
                labels: labels,
                datasets: [{
                  label: "平均稼働率 (%)",
                  data: avgRates,
                  backgroundColor: "rgba(153, 102, 255, 0.7)",
                  borderColor: "rgba(153, 102, 255, 1)",
                  borderWidth: 1
                }]
              },
              options: {
                responsive: true,
                scales: {
                  y: { beginAtZero: true, max: 100, title: { display: true, text: "平均稼働率 (%)" } }
                }
              }
            });
          } catch (error) {
            console.error("エリア統計エラー:", error);
          }
        }

        /***********************************************
         * 業種内ジャンルランキング (/api/history) の更新（最新店舗のみ集計）
         ***********************************************/
        async function fillIndustryDropdown() {
          try {
            const responseLatest = await fetchWithRetry("/api/data", {}, 3, 2000);
            const dataLatest = await responseLatest.json();
            const industries = new Set();
            dataLatest.forEach(item => { if(item.biz_type && item.biz_type !== "不明") industries.add(item.biz_type); });
            const industrySelect = document.getElementById("industrySelectType");
            if (!industrySelect) return;
            industrySelect.innerHTML = "";
            industries.forEach(ind => {
              industrySelect.innerHTML += <option value="${ind}">${ind}</option>;
            });
            // 復元済みの値を反映
            industrySelect.value = savedIndustry;
            industrySelect.addEventListener("change", () => {
              localStorage.setItem("industryFilter", industrySelect.value);
              updateIndustryRanking();
            });
          } catch (err) {
            console.error("業種ドロップダウン取得エラー:", err);
          }
        }

        async function updateIndustryRanking() {
          try {
            const industrySelect = document.getElementById("industrySelectType");
            const selectedIndustry = industrySelect ? industrySelect.value : "";
            const responseLatest = await fetchWithRetry("/api/data", {}, 3, 2000);
            const dataLatest = await responseLatest.json();
            const latestStoreNames = new Set(dataLatest.map(store => store.store_name));
            const responseHistory = await fetchWithRetry("/api/history", {}, 3, 2000);
            const dataHistory = await responseHistory.json();
            const filtered = dataHistory.filter(record => latestStoreNames.has(record.store_name) &&
              (selectedIndustry ? record.biz_type === selectedIndustry : true));
            const genreData = {};
            filtered.forEach(record => {
              const genre = record.genre || "不明";
              if (!genreData[genre]) {
                genreData[genre] = { stores: new Set(), totalRate: 0, rateCount: 0 };
              }
              genreData[genre].stores.add(record.store_name);
              if (record.working_staff > 0) {
                genreData[genre].totalRate += ((record.working_staff - record.active_staff) / record.working_staff * 100);
                genreData[genre].rateCount += 1;
              }
            });
            const typeTable = document.getElementById("typeTable");
            typeTable.innerHTML = "";
            const labels = [];
            const avgRates = [];
            for (const g in genreData) {
              const { stores, totalRate, rateCount } = genreData[g];
              const storeCount = stores.size;
              const avgRate = rateCount > 0 ? (totalRate / rateCount).toFixed(1) : "0.0";
              typeTable.insertAdjacentHTML("beforeend", 
                <tr>
                  <td>${g}</td>
                  <td>${storeCount}</td>
                  <td>${avgRate}</td>
                </tr>
              );
              labels.push(g);
              avgRates.push(parseFloat(avgRate));
            }
            const ctx = document.getElementById("typeChart").getContext("2d");
            if (charts.type) charts.type.destroy();
            charts.type = new Chart(ctx, {
              type: "bar",
              data: {
                labels: labels,
                datasets: [{
                  label: "平均稼働率 (%)",
                  data: avgRates,
                  backgroundColor: "rgba(255, 206, 86, 0.7)",
                  borderColor: "rgba(255, 206, 86, 1)",
                  borderWidth: 1
                }]
              },
              options: {
                responsive: true,
                scales: {
                  y: { beginAtZero: true, max: 100, title: { display: true, text: "平均稼働率 (%)" } }
                }
              }
            });
          } catch (error) {
            console.error("業種内ジャンルランキングエラー:", error);
          }
        }

        /***********************************************
         * 店舗詳細モーダル内の履歴グラフ (/api/history)
         ***********************************************/
        async function updateDetailHistoryChart(storeName) {
          try {
            const response = await fetchWithRetry("/api/history", {}, 3, 2000);
            const data = await response.json();
            const filtered = data.filter(record => record.store_name === storeName)
                                 .sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
            const labels = filtered.map(record => {
              const d = new Date(record.timestamp);
              return d.toLocaleDateString() + " " + d.toTimeString().substring(0, 5);
            });
            const effectiveRates = filtered.map(record => record.working_staff > 0 ? ((record.working_staff - record.active_staff) / record.working_staff * 100).toFixed(1) : 0);
            const ctx = document.getElementById("detailHistoryChart").getContext("2d");
            if (charts.detailHistory) charts.detailHistory.destroy();
            charts.detailHistory = new Chart(ctx, {
              type: "line",
              data: {
                labels: labels,
                datasets: [{
                  label: "実働率 (%)",
                  data: effectiveRates,
                  borderColor: "rgba(255, 99, 132, 1)",
                  backgroundColor: "rgba(255, 99, 132, 0.2)",
                  fill: false,
                  tension: 0.3,
                  pointRadius: 3,
                  pointHoverRadius: 5
                }]
              },
              options: {
                responsive: true,
                plugins: {
                  title: { display: true, text: "店舗履歴グラフ", font: { size: 20 } },
                  tooltip: { callbacks: { label: context => ${context.parsed.y}% } },
                  zoom: {
                    pan: { enabled: true, mode: 'xy' },
                    zoom: { enabled: true, mode: 'xy' }
                  }
                },
                scales: {
                  y: { beginAtZero: true, max: 100, title: { display: true, text: "実働率 (%)" } },
                  x: { title: { display: true, text: "日時" } }
                },
                animation: { duration: 1000 }
              }
            });
          } catch (error) {
            console.error("店舗詳細履歴グラフエラー:", error);
          }
        }

        /***********************************************
         * 店舗詳細モーダル内の時間帯別分析 (/api/history)
         ***********************************************/
        async function updateDetailHourlyChart(storeName) {
          try {
            const response = await fetchWithRetry("/api/history", {}, 3, 2000);
            const data = await response.json();
            const filtered = data.filter(record => record.store_name === storeName);
            const hourly = {};
            filtered.forEach(record => {
              if (record.working_staff > 0) {
                const d = new Date(record.timestamp);
                const hour = d.getHours();
                const rate = ((record.working_staff - record.active_staff) / record.working_staff) * 100;
                if (!hourly[hour]) hourly[hour] = { sum: 0, count: 0 };
                hourly[hour].sum += rate;
                hourly[hour].count += 1;
              }
            });
            const labels = [];
            const avgRates = [];
            for (let h = 0; h < 24; h++) {
              labels.push(${h}:00);
              avgRates.push(hourly[h] && hourly[h].count > 0 ? (hourly[h].sum / hourly[h].count).toFixed(1) : "0");
            }
            const ctx = document.getElementById("detailHourlyChart").getContext("2d");
            if (charts.detailHourly) charts.detailHourly.destroy();
            charts.detailHourly = new Chart(ctx, {
              type: "line",
              data: {
                labels: labels,
                datasets: [{
                  label: "平均実働率 (%)",
                  data: avgRates,
                  borderColor: "rgba(54, 162, 235, 1)",
                  backgroundColor: "rgba(54, 162, 235, 0.2)",
                  fill: false,
                  tension: 0.3,
                  pointRadius: 3,
                  pointHoverRadius: 5
                }]
              },
              options: {
                responsive: true,
                plugins: {
                  title: { display: true, text: "店舗詳細 時間帯別分析", font: { size: 20 } },
                  tooltip: { callbacks: { label: context => ${context.parsed.y}% } },
                  zoom: {
                    pan: { enabled: true, mode: 'xy' },
                    zoom: { enabled: true, mode: 'xy' }
                  }
                },
                scales: {
                  y: { beginAtZero: true, max: 100, title: { display: true, text: "実働率 (%)" } },
                  x: { title: { display: true, text: "時間帯" } }
                },
                animation: { duration: 1000 }
              }
            });
          } catch (err) {
            console.error("店舗詳細時間帯分析エラー:", err);
          }
        }

        /***********************************************
         * 平均稼働ランキングタブの更新 (/api/history)
         ***********************************************/
        async function updateAverageRanking() {
          try {
            const response = await fetchWithRetry("/api/history", {}, 3, 2000);
            const data = await response.json();

            // 各店舗ごとに平均稼働率を算出
            const storeStats = {};
            data.forEach(record => {
              if (record.working_staff > 0) {
                const rate = ((record.working_staff - record.active_staff) / record.working_staff * 100);
                if (!storeStats[record.store_name]) {
                  storeStats[record.store_name] = {
                    store_name: record.store_name,
                    biz_type: record.biz_type || "",
                    genre: record.genre || "",
                    area: record.area || "",
                    totalRate: 0,
                    count: 0
                  };
                }
                storeStats[record.store_name].totalRate += rate;
                storeStats[record.store_name].count += 1;
              }
            });
            const rankingArray = Object.values(storeStats).map(store => {
              return { ...store, avgRate: store.count > 0 ? (store.totalRate / store.count) : 0 };
            });

            // ランキングタイプによってフィルタ
            const rankingType = document.getElementById("rankingTypeSelect").value;
            let filteredArray = rankingArray;
            if (rankingType === "industry") {
              const selectedIndustry = document.getElementById("industrySelectRanking").value;
              filteredArray = rankingArray.filter(store => store.biz_type === selectedIndustry);
            }

            // 平均稼働率の降順にソート
            filteredArray.sort((a, b) => b.avgRate - a.avgRate);

            // テーブルに結果を表示
            const tbody = document.getElementById("rankingTableBody");
            tbody.innerHTML = "";
            filteredArray.forEach((store, index) => {
              const rowHtml = 
                <tr>
                  <td>${index + 1}</td>
                  <td>${store.store_name}</td>
                  <td>${store.biz_type}</td>
                  <td>${store.genre}</td>
                  <td>${store.area}</td>
                  <td>${store.avgRate.toFixed(1)}</td>
                </tr>
              ;
              tbody.insertAdjacentHTML("beforeend", rowHtml);
            });
          } catch (error) {
            console.error("平均稼働ランキングエラー:", error);
          }
        }

        /***********************************************
         * 業種別ランキング用の業種ドロップダウンを更新
         ***********************************************/
        async function fillIndustryDropdownForRanking() {
          try {
            const response = await fetchWithRetry("/api/history", {}, 3, 2000);
            const data = await response.json();
            const industries = new Set();
            data.forEach(record => {
              if (record.biz_type) industries.add(record.biz_type);
            });
            const industrySelect = document.getElementById("industrySelectRanking");
            industrySelect.innerHTML = "";
            industries.forEach(ind => {
              industrySelect.innerHTML += <option value="${ind}">${ind}</option>;
            });
          } catch (err) {
            console.error("ランキング業種ドロップダウン取得エラー:", err);
          }
        }

        // ランキングタイプの変更イベント
        document.getElementById("rankingTypeSelect").addEventListener("change", async function() {
          if (this.value === "industry") {
            document.getElementById("industryFilterDiv").style.display = "";
            await fillIndustryDropdownForRanking();
          } else {
            document.getElementById("industryFilterDiv").style.display = "none";
          }
        });

        // ランキング更新ボタンのイベント登録
        document.getElementById("updateRankingBtn").addEventListener("click", updateAverageRanking);

        /***********************************************
         * Socket.IOによるリアルタイム更新とトースト通知
         ***********************************************/
        const socket = io();
        socket.on("update", (msg) => {
          console.log("Socket update received:", msg.data);
          updateDashboard();
          updateTop10Chart();
          updateAreaStatistics();
          updateIndustryRanking();
          // トースト通知表示
          const toastEl = document.getElementById("updateToast");
          const toast = new bootstrap.Toast(toastEl);
          toast.show();
        });

        /***********************************************
         * 初期ロード処理
         ***********************************************/
        updateDashboard();
        updateTop10Chart();
        updateAreaStatistics();
        await fillIndustryDropdown();
        updateIndustryRanking();
        await fillHistoryStoreDropdown();
        updateHistoryChart();
        await fillHourlyStoreDropdown();
        updateHourlyAnalysis();
      });
    })();
  </script>
</body>
</html>
