
"""
データベースパフォーマンス向上のためのインデックスを作成するスクリプト
"""

from app import app, db
from sqlalchemy import text

def create_indices():
    """
    StoreStatus テーブルとその他テーブルにインデックスを作成
    """
    with app.app_context():
        print("データベースインデックスの作成を開始します...")
        
        # store_status テーブルのインデックス
        # timestamp 列に単独インデックス
        db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_timestamp ON store_status (timestamp)"))
        
        # store_name 列に単独インデックス
        db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_store_name ON store_status (store_name)"))
        
        # 複合インデックス: store_name + timestamp
        db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_store_name_timestamp ON store_status (store_name, timestamp)"))
        
        # 複合インデックス: biz_type + timestamp
        db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_biz_type_timestamp ON store_status (biz_type, timestamp)"))
        
        # 複合インデックス: area + timestamp
        db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_area_timestamp ON store_status (area, timestamp)"))
        
        # 集計テーブルのインデックス
        # daily_averages テーブル
        db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_avg_store_name ON daily_averages (store_name)"))
        db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_avg_rate ON daily_averages (avg_rate)"))
        
        # weekly_averages テーブル
        db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_weekly_avg_store_name ON weekly_averages (store_name)"))
        db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_weekly_avg_rate ON weekly_averages (avg_rate)"))
        
        # store_averages テーブル
        db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_store_avg_store_name ON store_averages (store_name)"))
        db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_store_avg_rate ON store_averages (avg_rate)"))
        
        db.session.commit()
        print("データベースインデックスの作成が完了しました。")
        
        # データベース統計情報を更新（SQLiteでは機能しない場合がある）
        try:
            db.session.execute(text("ANALYZE"))
            db.session.commit()
            print("データベース統計情報を更新しました。")
        except:
            print("データベース統計情報の更新はスキップされました（SQLite では不要です）")

if __name__ == "__main__":
    create_indices()
