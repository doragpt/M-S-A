
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

# スクレイパーのインポート
from store_scraper import scrape_store_data

# データベース設定（app.pyと同じ設定を使用）
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///store_data.db')
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

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
    """スクレイピング結果を一括でデータベースに挿入（最適化版）"""
    session = Session()
    try:
        # バルクインサート用のデータを準備（分割処理で最適化）
        BATCH_SIZE = 100  # 一回のコミットあたりの最大レコード数
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
            
            # バッチサイズに達したらコミット
            if len(insert_values) >= BATCH_SIZE:
                # バルクインサートの実行
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
                # メモリを解放するため配列をクリア
                insert_values = []
                # 定期的にガベージコレクション実行
                gc.collect()
        
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

def main():
    """メイン処理（メモリ管理を最適化）"""
    start_time = time.time()
    logger.info("高速スクレイピング処理を開始します")
    
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
        
        # スクレイピング実行
        results = scrape_store_data(store_urls)
        
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
