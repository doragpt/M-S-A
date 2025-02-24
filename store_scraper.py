import asyncio
from pyppeteer import launch
from pyppeteer_stealth import stealth
from bs4 import BeautifulSoup
import re
from datetime import datetime, timedelta
import pytz
import gc
import os

# -------------------------------
# 定数設定（環境変数で動的に設定可能）
# -------------------------------
MAX_CONCURRENT_TASKS = int(os.environ.get('MAX_CONCURRENT_TASKS', 20))
MAX_RETRIES_FOR_INFO = 3

# -------------------------------
# fetch_page 関数
# -------------------------------
async def fetch_page(page, url, retries=3, timeout=20000):
    """
    指定されたページでURLにアクセスし、ネットワークアイドル状態になるまで待機する
    Parameters:
      - page: pyppeteer のページオブジェクト
      - url: アクセスするURL
      - retries: リトライ回数（デフォルト3回）
      - timeout: タイムアウト（ミリ秒単位、デフォルト20000ms）
    Returns:
      - True: 成功
      - False: 全てのリトライで失敗
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
    - ヘッドレスブラウザで対象ページにアクセス
    - BeautifulSoupでHTMLをパースし、店舗情報・シフト情報を取得
    - 同一ページを再利用して再取得を試み、作成オーバーヘッドを削減
    Parameters:
      - browser: pyppeteer のブラウザオブジェクト
      - url: 店舗基本URL（末尾に "/" を追加）
      - semaphore: 並列実行制御用セマフォ
    Returns:
      - dict: 店舗情報とシフト情報の集計結果（失敗時は空dict）
    """
    async with semaphore:
        if not url.endswith("/"):
            url += "/"
        attend_url = url + "attend/soon/"

        page = await browser.newPage()
        try:
            await stealth(page)
        except Exception as e:
            print("stealth 適用エラー:", e)
        await page.setUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        )

        print("初回アクセス中:", attend_url)
        success = await fetch_page(page, attend_url, retries=3, timeout=20000)
        if not success:
            await page.close()
            return {}
        await asyncio.sleep(1)
        content = await page.content()
        soup = BeautifulSoup(content, "html.parser")

        # 初期値設定
        store_name, biz_type, genre, area = "不明", "不明", "不明", "不明"
        current_area_elem = soup.find("li", class_="area_menu_item current")
        if current_area_elem:
            a_elem = current_area_elem.find("a")
            if a_elem:
                area = a_elem.get_text(strip=True)

        attempt = 0
        while attempt < MAX_RETRIES_FOR_INFO:
            menushop_div = soup.find("div", class_="menushopname none")
            if menushop_div:
                h1_elem = menushop_div.find("h1")
                if h1_elem:
                    store_name = h1_elem.get_text(strip=True)
                    store_info = h1_elem.next_sibling.strip() if h1_elem.next_sibling else ""
                    match = re.search(r"(.+?)\((.+?)/(.+?)\)", store_info)
                    if match:
                        biz_type, genre, extracted_area = match.groups()
                        if area == "不明":
                            area = extracted_area
            if store_name != "不明":
                break
            attempt += 1
            print(f"店舗情報再取得試行 {attempt} 回目: {url}")
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
            print("再取得に失敗:", url)
            await page.close()
            return {}

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
        today_tab = container.find("div", class_="item-0")
        wrappers = today_tab.find_all("div", class_="sugunavi_wrapper") if today_tab else []
        print("シフト件数:", len(wrappers))

        total_staff = 0
        working_staff = 0
        active_staff = 0
        jst = pytz.timezone('Asia/Tokyo')
        for wrapper in wrappers:
            p_elems = wrapper.find_all("p", class_="time_font_size shadow shukkin_detail_time")
            for p_elem in p_elems:
                text = p_elem.get_text(strip=True)
                if not text:
                    continue
                if any(kw in text for kw in ["明日", "次回", "出勤予定", "お休み"]):
                    continue
                if "完売" in text:
                    total_staff += 1
                    continue
                match = re.search(r"(\d{1,2}):(\d{2})～(\d{1,2}):(\d{2})", text)
                if match:
                    start_h, start_m, end_h, end_m = map(int, match.groups())
                    current_time = datetime.now(jst)
                    parsed_start = datetime.strptime(f"{start_h}:{start_m}", "%H:%M").time()
                    parsed_end = datetime.strptime(f"{end_h}:{end_m}", "%H:%M").time()
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
                    start_time = jst.localize(start_time)
                    end_time = jst.localize(end_time)
                    total_staff += 1
                    if start_time <= current_time <= end_time:
                        working_staff += 1
                        status_container = wrapper.find("div", class_="sugunavi_spacer_1line")
                        if not status_container:
                            status_container = wrapper.find("div", class_="sugunavi_spacer_2line")
                        if status_container:
                            sugunavibox = status_container.find("div", class_="sugunavibox")
                            status_text = sugunavibox.get_text(strip=True) if sugunavibox else ""
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
    複数店舗のスクレイピングを、並列実行数制限付きで実行する関数
    バッチ間の待機時間は2秒に設定
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
    tasks = [scrape_store(browser, url, semaphore) for url in store_urls]
    results = []
    for i in range(0, len(tasks), MAX_CONCURRENT_TASKS):
        batch = tasks[i:i+MAX_CONCURRENT_TASKS]
        batch_results = await asyncio.gather(*batch, return_exceptions=True)
        results.extend(batch_results)
        await asyncio.sleep(2)
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
