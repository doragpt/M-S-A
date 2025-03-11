
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
