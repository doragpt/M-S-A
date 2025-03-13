"""
データベースにインデックスを作成してパフォーマンスを向上させるスクリプト
"""
from app import app, db
from sqlalchemy import text

def create_indices():
    with app.app_context():
        try:
            print("データベースインデックスを作成しています...")

            # StoreStatus テーブルにインデックスを作成
            # タイムスタンプ検索用インデックス（時系列データ検索の高速化）
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_store_status_timestamp ON store_status (timestamp)"))

            # 店舗名検索用インデックス（特定店舗のデータ絞り込み高速化）
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_store_status_store_name ON store_status (store_name)"))

            # 業種・ジャンル・エリア検索用インデックス（統計分析の高速化）
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_store_status_biz_type ON store_status (biz_type)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_store_status_genre ON store_status (genre)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_store_status_area ON store_status (area)"))

            # 複合インデックス（店舗名+タイムスタンプ）
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_store_timestamp ON store_status (store_name, timestamp)"))

            # 勤務中スタッフカウント用インデックス（稼働率計算の高速化）
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_store_status_working_staff ON store_status (working_staff)"))

            db.session.commit()
            print("インデックス作成が完了しました")

        except Exception as e:
            db.session.rollback()
            print(f"インデックス作成中にエラーが発生しました: {e}")

if __name__ == "__main__":
    create_indices()