
#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sqlite3
from database import get_db_connection
import sys
import logging

"""
パフォーマンス改善のためのインデックス作成スクリプト
"""

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def create_indices():
    """データベースのパフォーマンス向上のためのインデックスを作成する"""
    try:
        conn = get_db_connection()
        logger.info("インデックス作成を開始します...")
        
        # 既存のインデックスを確認
        indices = conn.execute("SELECT name FROM sqlite_master WHERE type='index';").fetchall()
        existing_indices = [idx['name'] for idx in indices]
        logger.info(f"既存のインデックス: {existing_indices}")
        
        # 作成するインデックスのリスト
        indices_to_create = [
            ("CREATE INDEX IF NOT EXISTS idx_store_status_timestamp ON store_status(timestamp);", "timestamp"),
            ("CREATE INDEX IF NOT EXISTS idx_store_status_store_name ON store_status(store_name);", "store_name"),
            ("CREATE INDEX IF NOT EXISTS idx_store_status_area ON store_status(area);", "area"),
            ("CREATE INDEX IF NOT EXISTS idx_store_status_biz_type ON store_status(biz_type);", "biz_type"),
            ("CREATE INDEX IF NOT EXISTS idx_store_status_genre ON store_status(genre);", "genre"),
            ("CREATE INDEX IF NOT EXISTS idx_store_status_store_name_timestamp ON store_status(store_name, timestamp);", "store_name_timestamp"),
            ("CREATE INDEX IF NOT EXISTS idx_daily_stats_date ON daily_stats(date);", "daily_stats_date"),
        ]
        
        # インデックスを作成
        for sql, index_name in indices_to_create:
            try:
                logger.info(f"インデックス {index_name} を作成中...")
                conn.execute(sql)
                conn.commit()
                logger.info(f"インデックス {index_name} の作成に成功しました")
            except Exception as e:
                logger.error(f"インデックス {index_name} の作成中にエラーが発生しました: {e}")
                conn.rollback()
        
        # 最終確認
        indices_after = conn.execute("SELECT name FROM sqlite_master WHERE type='index';").fetchall()
        indices_after_list = [idx['name'] for idx in indices_after]
        logger.info(f"作成後のインデックス: {indices_after_list}")
        
        # ANALYZE実行（SQLiteのクエリプランナーの最適化）
        logger.info("ANALYZEを実行してクエリプランナーを最適化します...")
        conn.execute("ANALYZE;")
        conn.commit()
        
        logger.info("インデックス作成が完了しました")
        conn.close()
        return True
    except Exception as e:
        logger.error(f"インデックス作成中に予期しないエラーが発生しました: {e}")
        return False

if __name__ == "__main__":
    success = create_indices()
    sys.exit(0 if success else 1)
