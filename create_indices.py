
#!/usr/bin/env python3
"""
データベースのインデックスを最適化するスクリプト
大量の店舗データ（700店舗以上）を効率的に処理するために必要なインデックスを作成
"""
import sqlite3
import logging
import os

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def create_indices():
    """必要なインデックスを作成する"""
    db_path = 'store_data.db'
    if not os.path.exists(db_path):
        logger.error(f"データベースが見つかりません: {db_path}")
        return False
        
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 既存のインデックスを確認
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index';")
        existing_indices = [row[0] for row in cursor.fetchall()]
        logger.info(f"既存のインデックス: {existing_indices}")
        
        # 必要なインデックスの定義
        indices = [
            ("idx_store_status_timestamp", "CREATE INDEX IF NOT EXISTS idx_store_status_timestamp ON store_status(timestamp)"),
            ("idx_store_status_store_name", "CREATE INDEX IF NOT EXISTS idx_store_status_store_name ON store_status(store_name)"),
            ("idx_store_status_area", "CREATE INDEX IF NOT EXISTS idx_store_status_area ON store_status(area)"),
            ("idx_store_status_biz_type", "CREATE INDEX IF NOT EXISTS idx_store_status_biz_type ON store_status(biz_type)"),
            ("idx_store_status_genre", "CREATE INDEX IF NOT EXISTS idx_store_status_genre ON store_status(genre)"),
            ("idx_store_status_combined_1", "CREATE INDEX IF NOT EXISTS idx_store_status_combined_1 ON store_status(store_name, timestamp)"),
            ("idx_store_status_combined_2", "CREATE INDEX IF NOT EXISTS idx_store_status_combined_2 ON store_status(biz_type, genre)"),
            ("idx_store_url_store_url", "CREATE INDEX IF NOT EXISTS idx_store_url_store_url ON store_urls(store_url)")
        ]
        
        # インデックス作成
        for name, sql in indices:
            if name not in existing_indices:
                logger.info(f"インデックス作成: {name}")
                cursor.execute(sql)
            else:
                logger.info(f"インデックス {name} は既に存在します")
        
        conn.commit()
        
        # インデックスの確認
        cursor.execute("PRAGMA index_list(store_status);")
        logger.info("store_status テーブルのインデックス:")
        for row in cursor.fetchall():
            logger.info(f"  {row}")
            
        cursor.execute("PRAGMA index_list(store_urls);")
        logger.info("store_urls テーブルのインデックス:")
        for row in cursor.fetchall():
            logger.info(f"  {row}")
            
        conn.close()
        logger.info("インデックスの作成が完了しました")
        return True
        
    except Exception as e:
        logger.error(f"インデックス作成エラー: {e}")
        return False

if __name__ == "__main__":
    create_indices()


import os
from app import db, StoreStatus, StoreURL
from sqlalchemy import text

def create_indices():
    """
    パフォーマンス向上のための必要なインデックスを作成する
    """
    print("インデックス作成を開始します...")
    
    # SQLiteの場合はインデックス実装が異なる
    if 'sqlite' in db.engine.url.drivername:
        # SQLite用のインデックス作成
        with db.engine.connect() as conn:
            # 既存のインデックスを確認
            indexes = conn.execute(text("SELECT name FROM sqlite_master WHERE type='index';")).fetchall()
            index_names = [idx[0] for idx in indexes]
            
            # timestamp用インデックス
            if 'idx_store_status_timestamp' not in index_names:
                conn.execute(text("CREATE INDEX idx_store_status_timestamp ON store_status(timestamp);"))
            
            # store_name用インデックス  
            if 'idx_store_status_store_name' not in index_names:
                conn.execute(text("CREATE INDEX idx_store_status_store_name ON store_status(store_name);"))
            
            # biz_type用インデックス
            if 'idx_store_status_biz_type' not in index_names:
                conn.execute(text("CREATE INDEX idx_store_status_biz_type ON store_status(biz_type);"))
            
            # area用インデックス
            if 'idx_store_status_area' not in index_names:
                conn.execute(text("CREATE INDEX idx_store_status_area ON store_status(area);"))
            
            # 複合インデックス
            if 'idx_store_status_store_timestamp' not in index_names:
                conn.execute(text("CREATE INDEX idx_store_status_store_timestamp ON store_status(store_name, timestamp);"))
            
            conn.commit()
    else:
        # PostgreSQL用のインデックス作成
        with db.engine.connect() as conn:
            # 各種インデックス作成のSQLを実行
            conn.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_store_status_timestamp') THEN
                        CREATE INDEX idx_store_status_timestamp ON store_status(timestamp);
                    END IF;
                    
                    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_store_status_store_name') THEN
                        CREATE INDEX idx_store_status_store_name ON store_status(store_name);
                    END IF;
                    
                    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_store_status_biz_type') THEN
                        CREATE INDEX idx_store_status_biz_type ON store_status(biz_type);
                    END IF;
                    
                    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_store_status_area') THEN
                        CREATE INDEX idx_store_status_area ON store_status(area);
                    END IF;
                    
                    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_store_status_store_timestamp') THEN
                        CREATE INDEX idx_store_status_store_timestamp ON store_status(store_name, timestamp);
                    END IF;
                END $$;
            """))
            conn.commit()
    
    print("インデックス作成が完了しました！")

if __name__ == "__main__":
    with db.app.app_context():
        create_indices()
