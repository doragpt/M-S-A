"""
store_scraper.py

このモジュールは、店舗の「本日出勤」情報をウェブサイトからスクレイピングするためのものです。
主な機能：
  - pyppeteer と pyppeteer_stealth を用いてヘッドレスブラウザで対象ページにアクセス
  - BeautifulSoup によりHTMLをパースし、店舗名、業種、ジャンル、エリア情報を取得
  - 「本日出勤」の情報（item-0）からシフト情報を取得し、総出勤数、勤務中人数、即ヒメ（待機中）人数をカウント
  - 明日の出勤情報（item-1 以降）は無視（対象外）とする

※ 特に、即ヒメのカウントについては、各シフト（sugunavi_wrapper）の全体テキストを参照し、
    「～待機中」という文字列が含まれる、またはステータス情報が全く記載されていない場合は即ヒメとしてカウントします。
"""

import asyncio
from pyppeteer import launch
from pyppeteer_stealth import stealth
from bs4 import BeautifulSoup
import re
from datetime import datetime, timedelta
import pytz
import gc

# 並列に処理する店舗数の上限
MAX_CONCURRENT_TASKS = 7
# 店舗情報が「不明」となった場合に再取得を試みる最大回数
MAX_RETRIES_FOR_INFO = 3

async def fetch_page(page, url, retries=3):
    """
    指定したページオブジェクトを用いて、与えられたURLのページを読み込みます（リトライ付き）。

    Args:
        page: pyppeteer のページオブジェクト
        url (str): 読み込むURL
        retries (int): リトライ回数（デフォルトは3回）

    Returns:
        True: ページ読み込みに成功した場合
        False: 全リトライが失敗した場合
    """
    for attempt in range(retries):
        try:
            # networkidle0：全てのネットワーク接続がアイドル状態になるまで待つ
            await page.goto(url, waitUntil='networkidle0', timeout=30000)
            return True
        except Exception as e:
            print(f"ページロード失敗（リトライ {attempt+1}/{retries}）: {url} - {e}")
            await asyncio.sleep(5)
    return False

async def scrape_store(browser, url: str, semaphore) -> dict:
    """
    単一店舗の「本日出勤」情報をスクレイピングします。
    
    ・店舗情報（店舗名、業種、ジャンル、エリア）は、対象ページ内の特定のHTML要素から取得します。
    ・出勤情報は、シフト情報が含まれる「shukkin-list-container」内のitem-0（本日出勤）から取得し、
      明日の出勤情報（item-1 以降）は対象外とします。
    ・各シフトごとに総出勤数、勤務中（実際に働いている）人数、および
      「～待機中」またはステータス情報がない場合に即ヒメとしてカウントします。

    Args:
        browser: pyppeteer のブラウザオブジェクト
        url (str): 店舗の基本URL（末尾に "/" がない場合は追加される）
        semaphore: 並列処理制御用のセマフォ

    Returns:
        dict: 取得した店舗情報および出勤情報
    """
    async with semaphore:
        # URLの末尾にスラッシュがなければ追加
        if not url.endswith("/"):
            url += "/"
        # 出勤情報ページ（attend/soon/）のURLを作成
        attend_url = url + "attend/soon/"

        page = await browser.newPage()
        try:
            await stealth(page)  # ブラウザの検出回避のための処理
        except Exception as e:
            print("stealth 適用エラー:", e)

        # ユーザーエージェントの設定
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

        # 初期値として店舗情報は「不明」としておく
        store_name, biz_type, genre, area = "不明", "不明", "不明", "不明"

        # エリア情報は、<li class="area_menu_item current"> 内のリンクから取得
        current_area_elem = soup.find("li", class_="area_menu_item current")
        if current_area_elem:
            a_elem = current_area_elem.find("a")
            if a_elem:
                area = a_elem.get_text(strip=True)

        # 店舗情報の取得（店舗名、業種、ジャンル）は、<div class="menushopname none"> 内の h1 要素から取得
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
                        # もしエリア情報が未取得の場合は、抽出した値を使用
                        if area == "不明":
                            area = extracted_area
            if store_name != "不明":
                break  # 情報が取得できたらループを抜ける
            attempt += 1
            print(f"店舗情報再取得試行 {attempt} 回目: {url}")
            # 再取得用の処理（新たにページを開いて再度情報取得）
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
            # 再取得時もエリア情報を更新
            current_area_elem = soup.find("li", class_="area_menu_item current")
            if current_area_elem:
                a_elem = current_area_elem.find("a")
                if a_elem:
                    area = a_elem.get_text(strip=True)

        if store_name == "不明":
            print("再取得に失敗: ", url)
            return {}

        # 出勤情報のコンテナ部分を取得
        container = soup.find("div", class_="shukkin-list-container")
        if not container:
            # 出勤情報がなければ空の結果を返す
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
        # 本日の出勤情報は「item-0」の部分に含まれる（明日の出勤情報は item-1 以降）
        today_tab = container.find("div", class_="item-0")
        # item-0 内の各シフト情報は、sugunavi_wrapper クラスで囲まれている
        wrappers = today_tab.find_all("div", class_="sugunavi_wrapper") if today_tab else []
        print("シフト件数:", len(wrappers))

        total_staff = 0      # 総出勤数
        working_staff = 0    # 勤務中の人数
        active_staff = 0     # 即ヒメ（待機中）人数

        # 現在時刻（日本時間）を取得
        jst = pytz.timezone('Asia/Tokyo')
        current_time = datetime.now(jst)

        # 各シフト情報(wrapper)に対して処理を行う
        for wrapper in wrappers:
            # wrapper 内の title 要素からテキストを取得（例："14:50 ～待機中" など）
            title_elem = wrapper.find("div", class_="title")
            title_text = title_elem.get_text(strip=True) if title_elem else ""
            # ここで wrapper 内の全体テキストを取得し、即ヒメ判定に利用する
            wrapper_text = wrapper.get_text(strip=True)
            # シフト時間が記載されている p 要素群を取得
            p_elems = wrapper.find_all("p", class_="time_font_size shadow shukkin_detail_time")
            for p_elem in p_elems:
                text = p_elem.get_text(strip=True)
                if not text:
                    continue
                # 「明日」「次回」「出勤予定」「お休み」が含まれる場合はスキップ（対象外）
                if any(kw in text for kw in ["明日", "次回", "出勤予定", "お休み"]):
                    continue
                # 「完売」の場合は出勤数のみカウント
                if "完売" in text:
                    total_staff += 1
                    continue
                # シフトの開始・終了時間を正規表現で抽出（例：14:00～18:00）
                match = re.search(r"(\d{1,2}):(\d{2})～(\d{1,2}):(\d{2})", text)
                if match:
                    start_h, start_m, end_h, end_m = map(int, match.groups())
                    # 当日の日付と組み合わせ、開始時刻・終了時刻を作成
                    start_time = datetime.combine(current_time.date(), datetime.strptime(f"{start_h}:{start_m}", "%H:%M").time())
                    start_time = jst.localize(start_time)
                    end_time = datetime.combine(current_time.date(), datetime.strptime(f"{end_h}:{end_m}", "%H:%M").time())
                    end_time = jst.localize(end_time)
                    # 終了時刻が開始時刻より早い場合、翌日扱いにする
                    if end_time < start_time:
                        end_time += timedelta(days=1)
                    total_staff += 1
                    # 現在時刻がシフト時間内なら勤務中とカウント
                    if start_time <= current_time <= end_time:
                        working_staff += 1
                        # 【即ヒメの判定処理】
                        # wrapper 内の全体テキストに「待機中」が含まれる場合、
                        # またはwrapper 内のテキストが空の場合（ステータス情報なし＝①のケース）に即ヒメとしてカウント
                        if ("待機中" in wrapper_text) or (wrapper_text == ""):
                            active_staff += 1

        # 取得した各情報を辞書で返す
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
    複数店舗のスクレイピングを、指定した並列数制限付きで実行します。

    Args:
        store_urls (list): スクレイピング対象の店舗URLリスト

    Returns:
        list: 各店舗のスクレイピング結果をまとめたリスト（各要素は辞書）
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
    # 使用するブラウザの実行ファイルパス（環境に合わせて変更してください）
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
    # 各店舗URLに対してスクレイピングタスクを作成
    tasks = [scrape_store(browser, url, semaphore) for url in store_urls]
    results = []
    # 並列制限に合わせてバッチ処理を実施
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
    非同期スクレイピング処理を同期的に実行するためのラッパー関数。

    Args:
        store_urls (list): スクレイピング対象の店舗URLリスト

    Returns:
        list: 各店舗のスクレイピング結果を含むリスト
    """
    return asyncio.run(_scrape_all(store_urls))
