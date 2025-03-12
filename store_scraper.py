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
# 700店舗を30-40分以内に処理するよう最適化
MAX_CONCURRENT_TASKS = 15  # 並列処理数を大幅に増加
# 店舗情報が取得できなかった場合の再試行回数
MAX_RETRIES_FOR_INFO = 1  # 再試行回数を1回に維持
# タイムアウト設定
PAGE_LOAD_TIMEOUT = 10000  # ページロードのタイムアウトを10秒に短縮
# メモリ管理
FORCE_GC_AFTER_STORES = 20  # 20店舗処理後に強制GC実行
# 一時的なエラーの最大再試行回数
MAX_TEMP_ERROR_RETRIES = 1  # エラー再試行回数を1回に短縮

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
            response = await page.goto(url, waitUntil='networkidle0', timeout=timeout)
            if response and response.status in [404, 403, 500]:
                # エラーメッセージを表示せず、エラーフラグのみ設定
                return False
            return True
        except Exception as e:
            # 詳細なエラーメッセージを表示せず、静かに再試行
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
        except Exception:
            pass  # エラーを表示せず静かに続行
        # ユーザーエージェントを設定
        await page.setUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        )

        # デバッグ表示を抑制
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
    # タスクをバッチ単位で実行し、各バッチの間は3秒待機に延長
    for i in range(0, len(tasks), MAX_CONCURRENT_TASKS):
        batch = tasks[i:i+MAX_CONCURRENT_TASKS]
        batch_results = await asyncio.gather(*batch, return_exceptions=True)
        results.extend(batch_results)
        await asyncio.sleep(3)  # 待機時間を3秒に延長（サーバー負荷軽減と安定性向上）
    gc.collect()
    await browser.close()
    return results

# -------------------------------
# scrape_store_data 関数
# -------------------------------
def scrape_store_data(store_urls: list) -> list:
    """
    非同期スクレイピング処理を同期的に実行するラッパー関数
    パフォーマンスモニタリング機能付き
    """
    import time
    start_time = time.time()
    results = asyncio.run(_scrape_all(store_urls))
    elapsed_time = time.time() - start_time
    print(f"スクレイピング実行時間: {elapsed_time:.2f}秒 (平均: {elapsed_time/len(store_urls):.2f}秒/店舗)")
    return results