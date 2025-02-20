"""
store_scraper.py

このモジュールは、店舗の「本日出勤」情報をウェブサイトからスクレイピングするためのものです。
主な機能：
  - pyppeteer と pyppeteer_stealth を用いてヘッドレスブラウザで対象ページにアクセス
  - BeautifulSoup によりHTMLをパースし、店舗名、業種、ジャンル、エリア情報を取得
  - 「本日出勤」の情報は、出勤情報ページ内の「item-0」部分から取得します（明日の出勤情報は対象外）
  - 各シフト情報(wrapper)から、総出勤数、勤務中人数、及び「即ヒメ（待機中）人数」をカウントします。
    ※ 即ヒメのカウントは、シフト内の「sugunavi_spacer_1line」または「sugunavi_spacer_2line」内に
       「sugunavibox」要素が存在するかどうかで判断します。存在しなければ、またはテキストに「待機中」
       が含まれていれば、即ヒメとしてカウントします。
"""

import asyncio
from pyppeteer import launch
from pyppeteer_stealth import stealth
from bs4 import BeautifulSoup
import re
from datetime import datetime, timedelta
import pytz
import gc

# 並列処理する店舗数の上限
MAX_CONCURRENT_TASKS = 8
# 店舗情報が「不明」となった場合の再試行回数
MAX_RETRIES_FOR_INFO = 3

async def fetch_page(page, url, retries=3):
    """
    指定ページで与えられたURLの読み込みを行い、ネットワークアイドル状態になるまで待機します。
    リトライも実施します。
    """
    for attempt in range(retries):
        try:
            await page.goto(url, waitUntil='networkidle0', timeout=30000)
            return True
        except Exception as e:
            print(f"ページロード失敗（リトライ {attempt+1}/{retries}）: {url} - {e}")
            await asyncio.sleep(5)
    return False

async def scrape_store(browser, url: str, semaphore) -> dict:
    """
    単一店舗の「本日出勤」情報をスクレイピングする関数。
    
    ・店舗情報（店舗名、業種、ジャンル、エリア）は、対象ページ内の特定要素から取得します。
    ・出勤情報は、shukkin-list-container 内の item-0 に含まれるシフト(wrapper)情報から取得し、
      各シフトごとに総出勤数、勤務中人数、および「即ヒメ」（待機中）人数をカウントします。
    ・即ヒメのカウントについては、各 wrapper 内の 
      "sugunavi_spacer_1line" または "sugunavi_spacer_2line" 内にある "sugunavibox" 要素を調査し、
      存在しなければ、または存在してそのテキストに「待機中」が含まれている場合に即ヒメとしてカウントします。
    
    Args:
        browser: pyppeteer のブラウザオブジェクト
        url (str): 店舗基本URL（必要に応じて末尾に "/" を追加）
        semaphore: 並列処理制御用のセマフォ
    
    Returns:
        dict: 取得した店舗情報および出勤シフトの集計結果
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
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        )

        print("初回アクセス中:", attend_url)
        success = await fetch_page(page, attend_url)
        if not success:
            await page.close()
            return {}
        await asyncio.sleep(1)
        content = await page.content()
        await page.close()
        soup = BeautifulSoup(content, "html.parser")

        # 初期値：店舗情報を「不明」として設定
        store_name, biz_type, genre, area = "不明", "不明", "不明", "不明"

        # エリア情報は、<li class="area_menu_item current"> 内のテキストから取得
        current_area_elem = soup.find("li", class_="area_menu_item current")
        if current_area_elem:
            a_elem = current_area_elem.find("a")
            if a_elem:
                area = a_elem.get_text(strip=True)

        # 店舗名、業種、ジャンルは <div class="menushopname none"> 内の h1 要素から取得
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
            page = await browser.newPage()
            try:
                await stealth(page)
            except Exception as e:
                print("stealth 再適用エラー:", e)
            await page.setUserAgent(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
            )
            success = await fetch_page(page, attend_url)
            if not success:
                await page.close()
                return {}
            await asyncio.sleep(1)
            content = await page.content()
            await page.close()
            soup = BeautifulSoup(content, "html.parser")
            current_area_elem = soup.find("li", class_="area_menu_item current")
            if current_area_elem:
                a_elem = current_area_elem.find("a")
                if a_elem:
                    area = a_elem.get_text(strip=True)

        if store_name == "不明":
            print("再取得に失敗: ", url)
            return {}

        # 出勤情報のコンテナ取得
        container = soup.find("div", class_="shukkin-list-container")
        if not container:
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
        # 本日の出勤情報は item-0 部分（明日の出勤情報は item-1 以降）
        today_tab = container.find("div", class_="item-0")
        wrappers = today_tab.find_all("div", class_="sugunavi_wrapper") if today_tab else []
        print("シフト件数:", len(wrappers))

        total_staff = 0     # 総出勤数
        working_staff = 0   # 勤務中の人数
        active_staff = 0    # 即ヒメ（待機中）人数

        # 各シフト(wrapper)ごとに処理する際、個別に現在時刻を取得して最新の値を使用する
        for wrapper in wrappers:
            p_elems = wrapper.find_all("p", class_="time_font_size shadow shukkin_detail_time")
            for p_elem in p_elems:
                text = p_elem.get_text(strip=True)
                if not text:
                    continue
                # 「明日」「次回」「出勤予定」「お休み」などは対象外とする
                if any(kw in text for kw in ["明日", "次回", "出勤予定", "お休み"]):
                    continue
                # 「完売」の場合は出勤数のみカウント
                if "完売" in text:
                    total_staff += 1
                    continue
                match = re.search(r"(\d{1,2}):(\d{2})～(\d{1,2}):(\d{2})", text)
                if match:
                    start_h, start_m, end_h, end_m = map(int, match.groups())
                    # 各シフトごとに最新の現在時刻を取得
                    current_time = datetime.now(pytz.timezone('Asia/Tokyo'))
                    start_time = datetime.combine(current_time.date(), datetime.strptime(f"{start_h}:{start_m}", "%H:%M").time())
                    start_time = pytz.timezone('Asia/Tokyo').localize(start_time)
                    end_time = datetime.combine(current_time.date(), datetime.strptime(f"{end_h}:{end_m}", "%H:%M").time())
                    end_time = pytz.timezone('Asia/Tokyo').localize(end_time)
                    if end_time < start_time:
                        end_time += timedelta(days=1)
                    total_staff += 1
                    if start_time <= current_time <= end_time:
                        working_staff += 1
                        # 【新しい即ヒメ判定処理】
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

async def _scrape_all(store_urls: list) -> list:
    """
    複数店舗のスクレイピングを、並列実行数制限付きで実行する関数。
    
    Args:
        store_urls (list): 対象店舗のURLリスト
    
    Returns:
        list: 各店舗のスクレイピング結果（辞書のリスト）
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
        await asyncio.sleep(3)
    gc.collect()
    await browser.close()
    return results

def scrape_store_data(store_urls: list) -> list:
    """
    非同期スクレイピング処理を同期的に実行するラッパー関数。
    
    Args:
        store_urls (list): 対象店舗のURLリスト
    
    Returns:
        list: 各店舗のスクレイピング結果（辞書のリスト）
    """
    return asyncio.run(_scrape_all(store_urls))
