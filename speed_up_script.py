
"""
スクレイピング高速実行スクリプト
- 通常のアプリケーションとは別に、スクレイピングのみを高速実行するためのスクリプト
- データベースへの保存を効率化
"""
import asyncio
import logging
import time
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import text
import gc
import os
import sys

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
    """データベースから全店舗URLを取得"""
    session = Session()
    try:
        results = session.execute(text("SELECT store_url FROM store_urls")).fetchall()
        return [r[0] for r in results]
    finally:
        session.close()

def bulk_insert_store_statuses(results):
    """スクレイピング結果を一括でデータベースに挿入"""
    if not results:
        logger.warning("挿入するデータがありません")
        return 0
    
    session = Session()
    try:
        # 現在時刻（全レコード共通）
        now = datetime.now()
        
        # SQLite用のバルクインサート文を構築
        insert_values = []
        for r in results:
            if not r:  # 空のレコードはスキップ
                continue
                
            store_name = r.get('store_name', '')
            if not store_name or store_name == '不明':
                continue
                
            # 値の挿入
            insert_values.append({
                'timestamp': now,
                'store_name': store_name,
                'biz_type': r.get('biz_type', ''),
                'genre': r.get('genre', ''),
                'area': r.get('area', ''),
                'total_staff': r.get('total_staff', 0),
                'working_staff': r.get('working_staff', 0),
                'active_staff': r.get('active_staff', 0),
                'url': r.get('url', ''),
                'shift_time': r.get('shift_time', '')
            })
        
        if not insert_values:
            return 0
            
        # バルクインサートの実行
        session.execute(
            text("""
            INSERT INTO store_status (
                timestamp, store_name, biz_type, genre, area, 
                total_staff, working_staff, active_staff, url, shift_time
            ) VALUES (
                :timestamp, :store_name, :biz_type, :genre, :area,
                :total_staff, :working_staff, :active_staff, :url, :shift_time
            )
            """),
            insert_values
        )
        session.commit()
        return len(insert_values)
    except Exception as e:
        session.rollback()
        logger.error(f"バルクインサートエラー: {e}")
        return 0
    finally:
        session.close()

def main():
    """メイン処理"""
    start_time = time.time()
    logger.info("高速スクレイピング処理を開始します")
    
    # 店舗URL取得
    store_urls = get_all_store_urls()
    if not store_urls:
        logger.error("店舗URLが見つかりません")
        return
    
    logger.info(f"スクレイピング対象: {len(store_urls)}店舗")
    
    # スクレイピング実行
    results = scrape_store_data(store_urls)
    logger.info(f"スクレイピング完了: {len(results)}件の結果を取得")
    
    # 結果をデータベースに保存
    inserted = bulk_insert_store_statuses(results)
    logger.info(f"データベース保存完了: {inserted}件のレコードを挿入")
    
    # メモリ解放
    results = None
    gc.collect()
    
    # 処理時間計測
    elapsed_time = time.time() - start_time
    minutes = int(elapsed_time // 60)
    seconds = int(elapsed_time % 60)
    logger.info(f"処理完了時間: {minutes}分{seconds}秒")

if __name__ == "__main__":
    main()
