
import os
import asyncio
import logging
import time
from datetime import datetime
import re
import random
from concurrent.futures import ThreadPoolExecutor
import pytz
from bs4 import BeautifulSoup
import traceback

from pyppeteer import launch
from pyppeteer.errors import TimeoutError, PageError

# スピードアップスクリプトからの最適化関数のインポート
from speed_up_script import (
    calculate_optimal_batch_size,
    log_performance_metrics,
    adaptive_sleep
)

# ロギング設定
logger = logging.getLogger('app')

# セッション共有のグローバル変数
global_browser = None
browser_creation_time = None
MAX_BROWSER_LIFETIME = 30 * 60  # ブラウザの最大稼働時間（秒）

async def init_browser():
    """新しいブラウザインスタンスを初期化"""
    global global_browser, browser_creation_time
    
    # すでにブラウザが存在する場合は閉じる
    if global_browser:
        try:
            await global_browser.close()
        except Exception:
            pass
    
    # 新しいブラウザを起動（メモリ最適化設定）
    global_browser = await launch({
        'headless': True,
        'args': [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-accelerated-2d-canvas',
            '--disable-gpu',
            '--disable-extensions',
            '--disable-background-networking',
            '--disable-default-apps',
            '--disable-sync',
            '--disable-translate',
            '--hide-scrollbars',
            '--metrics-recording-only',
            '--mute-audio',
            '--no-first-run',
            '--safebrowsing-disable-auto-update',
            '--js-flags=--max-old-space-size=1024',  # V8メモリ制限
        ],
        'ignoreHTTPSErrors': True,
        'defaultViewport': {'width': 1280, 'height': 800},
    })
    browser_creation_time = time.time()
    return global_browser

async def get_page(browser):
    """新しいページを取得し、ステルスモードを設定"""
    page = await browser.newPage()
    
    # ブラウザフィンガープリントの偽装（検出対策）
    await page.evaluateOnNewDocument("""
    () => {
        Object.defineProperty(navigator, 'webdriver', {
            get: () => false,
        });
    }
    """)
    
    # タイムアウト設定（12GB/6コア環境に最適化）
    await page.setDefaultNavigationTimeout(15000)
    await page.setRequestInterception(True)
    
    # リソース節約のため不要なリクエストをブロック
    page.on('request', lambda req: asyncio.ensure_future(
        req.continue_() if req.resourceType in ['document', 'xhr', 'fetch'] 
        else req.abort()
    ))
    
    return page

async def scrape_single_store(url, retry_count=0, max_retries=2):
    """単一の店舗ページをスクレイピングする関数"""
    global global_browser, browser_creation_time
    
    # ブラウザインスタンスのライフタイムチェック
    if not global_browser or time.time() - browser_creation_time > MAX_BROWSER_LIFETIME:
        try:
            await init_browser()
        except Exception as e:
            logger.error(f"ブラウザの初期化に失敗: {e}")
            return None
    
    try:
        # 店舗URLのログ出力
        logger.info(f"スクレイピング開始: {url}")
        
        page = await get_page(global_browser)
        
        # ページ読み込み
        await page.goto(url, {'waitUntil': 'domcontentloaded'})
        
        # ページが完全に読み込まれるのを待機
        await asyncio.sleep(1)
        
        # HTMLコンテンツの取得
        content = await page.content()
        
        # ページを閉じる
        await page.close()
        
        # BeautifulSoupでHTMLを解析
        soup = BeautifulSoup(content, 'html.parser')
        
        # 店舗名の取得
        store_name = None
        menushop_div = soup.find("div", class_="menushopname none")
        if menushop_div:
            h1_elem = menushop_div.find("h1")
            if h1_elem:
                store_name = h1_elem.text.strip()
        
        if not store_name:
            shop_name_elem = soup.find("p", class_="shopname")
            if shop_name_elem:
                store_name = shop_name_elem.text.strip()
        
        # エリア情報の取得
        area = ""
        area_elem = soup.find("span", id="area_name")
        if area_elem:
            area = area_elem.text.strip()
        
        # 業種の取得
        biz_type = ""
        biz_type_elem = soup.find("p", class_="type")
        if biz_type_elem:
            biz_type = biz_type_elem.text.strip()
        
        # ジャンルの取得
        genre = ""
        genre_elem = soup.find("p", class_="genre")
        if genre_elem:
            genre = genre_elem.text.strip()
        
        # 在籍スタッフ数・待機スタッフ数の取得
        total_staff = 0
        active_staff = 0
        working_staff = 0
        
        # 在籍スタッフ数
        staff_counts = soup.find_all("p", class_="inPosition")
        if staff_counts:
            for p in staff_counts:
                if "在籍" in p.text:
                    match = re.search(r'(\d+)', p.text)
                    if match:
                        total_staff = int(match.group(1))
        
        # 待機スタッフ数
        stand_by_section = soup.find("section", class_="standby")
        if stand_by_section:
            # スタンバイリストの取得
            standby_lists = stand_by_section.find_all("ul", class_="girlslist")
            active_staff_count = 0
            
            for standby_list in standby_lists:
                girls = standby_list.find_all("li")
                active_staff_count += len(girls)
            
            active_staff = active_staff_count
        
        # 出勤スタッフ（シフト）
        working_section = soup.find("div", class_="shiftbox")
        if working_section:
            working_lists = working_section.find_all("ul", class_="girlslist")
            working_staff_count = 0
            
            for working_list in working_lists:
                girls = working_list.find_all("li")
                working_staff_count += len(girls)
            
            working_staff = working_staff_count
        
        # シフト時間の取得
        shift_time = ""
        shift_elem = soup.find("p", class_="shoptime")
        if shift_elem:
            shift_time = shift_elem.text.strip()
        
        # データの整形
        result = {
            "store_name": store_name,
            "area": area,
            "biz_type": biz_type,
            "genre": genre,
            "total_staff": total_staff,
            "active_staff": active_staff,
            "working_staff": working_staff,
            "url": url,
            "shift_time": shift_time
        }
        
        return result
        
    except TimeoutError:
        logger.warning(f"ページロード失敗（リトライ {retry_count+1}/{max_retries}）: {url} - Navigation Timeout Exceeded: 15000 ms exceeded.")
    except PageError as e:
        logger.warning(f"ページロード失敗（リトライ {retry_count+1}/{max_retries}）: {url} - {e}")
    except Exception as e:
        logger.warning(f"ページロード失敗（リトライ {retry_count+1}/{max_retries}）: {url} - {e}")
    
    # エラー時のリトライ処理
    if retry_count < max_retries:
        # ランダムな待機時間を追加（0.5〜2秒）
        await asyncio.sleep(0.5 + random.random() * 1.5)
        return await scrape_single_store(url, retry_count + 1, max_retries)
    
    return None

async def scrape_multiple_stores(urls, max_workers=None):
    """
    複数の店舗URLを並行してスクレイピングする関数
    
    Args:
        urls: スクレイピングするURLのリスト
        max_workers: 最大並行処理数（Noneの場合は自動設定）
    
    Returns:
        結果リスト
    """
    total_urls = len(urls)
    logger.info(f"スクレイピングを開始します（店舗数: {total_urls}、並列実行数: {max_workers if max_workers else '自動'}）")
    
    # 環境に合わせた最適なバッチサイズとワーカー数を計算
    batch_size, num_workers = calculate_optimal_batch_size(total_urls, max_workers)
    
    results = []
    processed_count = 0
    
    # ブラウザインスタンスの初期化
    try:
        await init_browser()
    except Exception as e:
        logger.error(f"ブラウザ初期化エラー: {e}")
        return []
    
    # バッチ処理
    for i in range(0, total_urls, batch_size):
        batch_start = time.time()
        
        # 現在のバッチのURLを取得
        batch_urls = urls[i:i+batch_size]
        batch_size_actual = len(batch_urls)
        
        logger.info(f"バッチ処理中: {i+1}〜{i+batch_size_actual}店舗 / 合計{total_urls}店舗")
        
        # リソース使用状況のログ記録
        log_performance_metrics()
        
        # 現在のバッチを非同期に処理
        tasks = [scrape_single_store(url) for url in batch_urls]
        batch_results = await asyncio.gather(*tasks)
        
        # 有効な結果のみを追加
        valid_results = [r for r in batch_results if r]
        results.extend(valid_results)
        
        # 進捗更新
        processed_count += batch_size_actual
        success_rate = len(valid_results) / batch_size_actual * 100 if batch_size_actual > 0 else 0
        
        # バッチ処理時間
        batch_duration = time.time() - batch_start
        logger.info(f"バッチ完了: {len(valid_results)}/{batch_size_actual}件成功 ({success_rate:.1f}%), "
                   f"処理時間: {batch_duration:.1f}秒, 総進捗: {processed_count}/{total_urls}件")
        
        # バッチ処理間の適応的スリープ（サーバー負荷軽減とブロック回避）
        if i + batch_size < total_urls:
            adaptive_sleep(batch_duration)
    
    # ブラウザを閉じる
    try:
        await global_browser.close()
        global_browser = None
    except Exception:
        pass
    
    return results

def scrape_store_data(store_urls, max_workers=None):
    """
    店舗データをスクレイピングするためのメイン関数
    
    Args:
        store_urls: スクレイピングするURLのリスト
        max_workers: 最大並行処理数（Noneの場合は自動設定）
    
    Returns:
        スクレイピング結果のリスト
    """
    # 高CPU/メモリ環境向けの最適化設定
    if max_workers is None:
        # 12GB/6コア環境向けデフォルト値
        max_workers = 3
    
    # 非同期処理の実行
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        results = loop.run_until_complete(scrape_multiple_stores(store_urls, max_workers))
    except Exception as e:
        logger.error(f"スクレイピング中にエラーが発生しました: {e}")
        logger.error(traceback.format_exc())
        results = []
    finally:
        loop.close()
    
    return results
