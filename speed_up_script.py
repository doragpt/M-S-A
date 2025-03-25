
"""
スクレイピング高速実行スクリプト
- 通常のアプリケーションとは別に、スクレイピングのみを高速実行するためのスクリプト
- データベースへの保存を効率化
"""
import asyncio
import logging
import time
from datetime import datetime
import pytz
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import gc  # ガベージコレクションモジュール
import os
import sys
import psutil  # メモリ使用状況監視用にpsutilを追加

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 以前のインポートを削除
# from store_scraper import scrape_store_data

# データベース設定（app.pyと同じ設定を使用）
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///store_data.db')
engine = create_engine(DATABASE_URL, pool_size=20, max_overflow=40)
Session = sessionmaker(bind=engine)

# 12GB/6コア環境向けリソース最適化設定
MEMORY_LIMIT_GB = 12
CPU_CORES = 6

def get_all_store_urls():
    """データベースから全ての店舗URLを取得"""
    session = Session()
    try:
        # StoreURLテーブルから全URLを取得
        result = session.execute(text("SELECT store_url FROM store_urls"))
        urls = [row[0] for row in result]
        logger.info(f"{len(urls)}件の店舗URLを取得しました")
        return urls
    except Exception as e:
        logger.error(f"店舗URL取得エラー: {e}")
        return []
    finally:
        session.close()

def bulk_insert_results(results, timestamp):
    """スクレイピング結果を一括でデータベースに挿入（12GB/6コア向け最適化版）"""
    session = Session()
    try:
        # 12GB/6コア向けにバッチサイズを最適化
        #BATCH_SIZE = 250  # 12GB RAMに最適化したバッチサイズ, now determined dynamically
        insert_values = []
        total_inserted = 0
        
        for record in results:
            if not record or 'store_name' not in record:
                continue

            # NULLチェック
            store_name = record.get('store_name', '不明')
            if not store_name or store_name == '':
                continue

            # レコードをバルクインサート用の形式に変換
            insert_values.append({
                'timestamp': timestamp,
                'store_name': store_name,
                'biz_type': record.get('biz_type', '不明'),
                'genre': record.get('genre', '不明'),
                'area': record.get('area', '不明'),
                'total_staff': record.get('total_staff', 0) or 0,
                'working_staff': record.get('working_staff', 0) or 0,
                'active_staff': record.get('active_staff', 0) or 0,
                'url': record.get('url', ''),
                'shift_time': record.get('shift_time', '')
            })
            
            # バッチサイズに達したらコミット (handled dynamically now)

        # 残りのレコードを処理
        if insert_values:
            session.execute(text("""
                INSERT INTO store_status 
                (timestamp, store_name, biz_type, genre, area, 
                 total_staff, working_staff, active_staff, url, shift_time)
                VALUES (
                    :timestamp, :store_name, :biz_type, :genre, :area,
                    :total_staff, :working_staff, :active_staff, :url, :shift_time
                )
                """),
                insert_values
            )
            session.commit()
            total_inserted += len(insert_values)
        
        return total_inserted
    except Exception as e:
        session.rollback()
        logger.error(f"バルクインサートエラー: {e}")
        return 0
    finally:
        session.close()

import os
import psutil
import logging
import time
from datetime import datetime

logger = logging.getLogger('app')

def get_system_resources():
    """システムリソースの使用状況を取得"""
    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    memory_percent = memory.percent
    available_memory_mb = memory.available / (1024 * 1024)

    return {
        'cpu_percent': cpu_percent,
        'memory_percent': memory_percent,
        'available_memory_mb': available_memory_mb
    }

def calculate_optimal_batch_size(total_urls, max_workers=None):
    """
    システムリソースに基づいて最適なバッチサイズとワーカー数を計算

    Args:
        total_urls: 処理する総URL数
        max_workers: 最大ワーカー数（指定がない場合は自動計算）

    Returns:
        (batch_size, num_workers): バッチサイズとワーカー数のタプル
    """
    # システムリソース情報の取得
    resources = get_system_resources()
    cpu_count = psutil.cpu_count(logical=True)

    # 環境情報のログ出力
    logger.info(f"システム情報: CPU={cpu_count}コア, メモリ使用率={resources['memory_percent']}%, "
                f"利用可能メモリ={resources['available_memory_mb']:.1f}MB")

    # 最大ワーカー数の決定（指定がない場合）
    if max_workers is None:
        # 12GB/6コアの環境向けの最適化：CPUコア数の半分を使用
        # ブラウザインスタンスはメモリを大量に消費するため、同時実行数を制限
        max_workers = min(cpu_count // 2 + 1, 3)  # 少なくとも1、最大でも3

    # メモリ使用率に基づいてワーカー数を調整
    if resources['memory_percent'] > 80:
        # メモリ使用率が高い場合は並列数を減らす
        num_workers = max(1, max_workers - 1)
    elif resources['available_memory_mb'] < 1000:  # 1GB未満の場合
        num_workers = 1  # 極端にメモリが少ない場合は1にする
    else:
        num_workers = max_workers

    # バッチサイズの決定
    # 各ワーカーあたり最大20URLを処理（メモリ消費を抑えるため）
    worker_batch_size = min(20, max(5, int(1000 / num_workers)))
    batch_size = worker_batch_size * num_workers

    # 最小バッチサイズは1、最大はURLの総数
    batch_size = min(max(1, batch_size), total_urls)

    logger.info(f"最適化設定: ワーカー数={num_workers}, バッチサイズ={batch_size}, "
                f"ワーカーあたりのURL数={worker_batch_size}")

    return batch_size, num_workers

def log_performance_metrics():
    """パフォーマンス測定の記録を出力"""
    resources = get_system_resources()
    logger.debug(f"現在のリソース使用状況: CPU={resources['cpu_percent']}%, "
                f"メモリ={resources['memory_percent']}%, "
                f"利用可能メモリ={resources['available_memory_mb']:.1f}MB")

def adaptive_sleep(last_batch_time, target_time=5.0):
    """
    バッチ処理間で適応的なスリープを実行

    Args:
        last_batch_time: 前回のバッチ処理にかかった時間（秒）
        target_time: 目標とするバッチ処理+スリープの合計時間（秒）
    """
    if last_batch_time < target_time:
        sleep_time = target_time - last_batch_time
        logger.debug(f"適応スリープ: {sleep_time:.2f}秒")
        time.sleep(sleep_time)

def main():
    """メイン処理（メモリ管理を最適化）"""
    start_time = time.time()
    logger.info("高速スクレイピング処理を開始します")
    
    # store_scraper をここでインポートして循環参照を防ぐ
    from store_scraper import scrape_store_data
    
    # 初期メモリ使用量をログ出力
    process = psutil.Process(os.getpid())
    initial_memory = process.memory_info().rss / 1024 / 1024
    logger.info(f"初期メモリ使用量: {initial_memory:.1f}MB")
    
    # 店舗URL取得
    store_urls = get_all_store_urls()
    if not store_urls:
        logger.error("店舗URLが見つかりません")
        return
    
    total_stores = len(store_urls)
    logger.info(f"スクレイピング対象: {total_stores}店舗")
    
    # スクレイピング実行
    try:
        # 処理時間計測スタート
        scrape_start = time.time()
        
        #最適なバッチサイズとワーカー数を計算
        batch_size, num_workers = calculate_optimal_batch_size(total_stores)

        # スクレイピング実行 (assuming scrape_store_data handles batching internally or can be modified to do so)
        results = scrape_store_data(store_urls, batch_size=batch_size, num_workers=num_workers)
        
        # URLリストは不要になったのでメモリ解放
        store_urls = None
        gc.collect()
        
        scrape_end = time.time()
        memory_after_scrape = process.memory_info().rss / 1024 / 1024
        logger.info(f"スクレイピング完了: {len(results)}件 ({scrape_end - scrape_start:.2f}秒)")
        logger.info(f"スクレイピング後メモリ使用量: {memory_after_scrape:.1f}MB")
        
        # データベースに保存
        timestamp = datetime.now(pytz.timezone('Asia/Tokyo'))
        inserted = bulk_insert_results(results, timestamp)
        
        # 結果データは不要になったのでメモリ解放
        results = None
        gc.collect()
        
        store_end = time.time()
        memory_after_store = process.memory_info().rss / 1024 / 1024
        logger.info(f"データベース保存完了: {inserted}件 ({store_end - scrape_end:.2f}秒)")
        logger.info(f"保存後メモリ使用量: {memory_after_store:.1f}MB")
        
        # 合計処理時間
        total_time = time.time() - start_time
        logger.info(f"全処理完了: 合計{total_time:.2f}秒")
        logger.info(f"メモリ削減量: {memory_after_scrape - memory_after_store:.1f}MB")
        
        # メモリ解放の強化
        gc.collect()
        gc.collect()  # 二回実行して確実にメモリを解放
        
    except Exception as e:
        logger.error(f"処理中にエラーが発生しました: {e}")
        import traceback
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    main()
