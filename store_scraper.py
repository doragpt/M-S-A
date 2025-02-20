"""
store_scraper.py

このモジュールは、店舗情報をウェブサイトからスクレイピングする機能を提供します。
主な処理は以下のとおりです:
  - pyppeteerとpyppeteer_stealthを用いてヘッドレスブラウザを操作し、店舗の出勤情報ページを読み込む。
  - BeautifulSoupを利用してHTMLをパースし、店舗名、業種、ジャンル、エリア、シフト情報などを抽出する。
  - 店舗情報が「不明」の場合、最大 MAX_RETRIES_FOR_INFO 回まで再取得を試みる。
  - 複数店舗のスクレイピングを同時に（ただし並列数制限付きで）実行する。

関数:
  - fetch_page(page, url, retries=3): 指定URLのページ読み込みにリトライを含む。
  - scrape_store(browser, url, semaphore): 単一店舗のスクレイピング処理。再試行も含む。
  - _scrape_all(store_urls): 全店舗のスクレイピングを並列実行する。
  - scrape_store_data(store_urls): 非同期処理を同期的に実行するためのラッパー関数。
"""

import asyncio
from pyppeteer import launch
from pyppeteer_stealth import stealth
from bs4 import BeautifulSoup
import re
from datetime import datetime, timedelta
import pytz
import gc

# 一度に処理する店舗数（並列数の上限）
MAX_CONCURRENT_TASKS = 7

# 店舗情報が「不明」になった場合の再試行回数
MAX_RETRIES_FOR_INFO = 3

async def fetch_page(page, url, retries=3):
    """
    指定したページオブジェクトで、URLのページを読み込む（リトライ付き）。
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
    単一店舗の「本日出勤」情報をスクレイピングします。

    店舗情報（店舗名、業種、ジャンル、エリア）を取得し、もし「不明」となった場合は
    MAX_RETRIES_FOR_INFO 回まで再取得を試みます。また、シフト情報から総出勤数、勤務中人数、
    即ヒメ（待機中）人数をカウントします。
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

        # 初期値として店舗情報を「不明」と設定
        store_name, biz_type, genre, area = "不明", "不明", "不明", "不明"

        # 【変更部分】まず、エリア情報は <li class="area_menu_item current"> から取得する
        current_area_elem = soup.find("li", class_="area_menu_item current")
        if current_area_elem:
            a_elem = current_area_elem.find("a")
            if a_elem:
                area = a_elem.get_text(strip=True)

        # 以下、店舗情報（店舗名、業種、ジャンル）の取得（エリアは上書きしない）
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
            # 再取得時も、エリア情報は <li class="area_menu_item current"> から取得する
            current_area_elem = soup.find("li", class_="area_menu_item current")
            if current_area_elem:
                a_elem = current_area_elem.find("a")
                if a_elem:
                    area = a_elem.get_text(strip=True)

        if store_name == "不明":
            print("再取得に失敗: ", url)
            return {}

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
        today_tab = container.find("div", class_="item-0")
        wrappers = today_tab.find_all("div", class_="sugunavi_wrapper") if today_tab else []
        print("シフト件数:", len(wrappers))

        total_staff = 0
        working_staff = 0
        active_staff = 0

        jst = pytz.timezone('Asia/Tokyo')
        current_time = datetime.now(jst)

        for wrapper in wrappers:
            title_elem = wrapper.find("div", class_="title")
            title_text = title_elem.get_text(strip=True) if title_elem else ""
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
                    start_time = datetime.combine(current_time.date(), datetime.strptime(f"{start_h}:{start_m}", "%H:%M").time())
                    start_time = jst.localize(start_time)
                    end_time = datetime.combine(current_time.date(), datetime.strptime(f"{end_h}:{end_m}", "%H:%M").time())
                    end_time = jst.localize(end_time)
                    if end_time < start_time:
                        end_time += timedelta(days=1)
                    total_staff += 1
                    if start_time <= current_time <= end_time:
                        working_staff += 1
                        if "待機中" in text or "待機中" in title_text:
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
    複数店舗のスクレイピングを、並列実行数制限付きで実行します。

    Args:
        store_urls (list): スクレイピング対象の店舗URLのリスト。

    Returns:
        list: 各店舗のスクレイピング結果をまとめたリスト（各要素は辞書）。
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
    非同期スクレイピング処理の同期ラッパー関数。

    Args:
        store_urls (list): スクレイピング対象店舗のURLリスト。

    Returns:
        list: 各店舗のスクレイピング結果を含むリスト。
    """
    return asyncio.run(_scrape_all(store_urls))
