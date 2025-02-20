import asyncio
from pyppeteer import launch
from pyppeteer_stealth import stealth
from bs4 import BeautifulSoup
import re
from datetime import datetime, timedelta
import pytz
import gc

MAX_CONCURRENT_TASKS = 10
MAX_RETRIES_FOR_INFO = 3

async def fetch_page(page, url, retries=3):
    for attempt in range(retries):
        try:
            await page.goto(url, waitUntil='networkidle0', timeout=30000)
            return True
        except Exception as e:
            print(f"ページロード失敗（リトライ {attempt+1}/{retries}）: {url} - {e}")
            await asyncio.sleep(5)
    return False

async def scrape_store(browser, url: str, semaphore) -> dict:
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
            # タイトル要素からテキストを取得
            title_elem = wrapper.find("div", class_="title")
            title_text = title_elem.get_text(strip=True) if title_elem else ""
            # wrapper全体のテキストを取得（即ヒメ判定に利用）
            wrapper_text = wrapper.get_text(strip=True)
            p_elems = wrapper.find_all("p", class_="time_font_size shadow shukkin_detail_time")
            for p_elem in p_elems:
                text = p_elem.get_text(strip=True)
                if not text:
                    continue
                # 「明日」「次回」「出勤予定」「お休み」が含まれる場合は対象外
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
                        # 【修正箇所】
                        # wrapper全体のテキストに「待機中」が含まれているか、または全体テキストが空の場合は即ヒメと判断
                        if ("待機中" in wrapper_text) or (wrapper_text == ""):
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
    return asyncio.run(_scrape_all(store_urls))
