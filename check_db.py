
#!/usr/bin/env python
import os
import sys
import time
from sqlalchemy import func
from datetime import datetime

# アプリケーションのパスを追加
sys.path.append('.')

# 必要なモジュールをインポート
try:
    from app import app, db, StoreURL, StoreStatus
except ImportError:
    print("エラー: app.pyからのインポートに失敗しました。")
    sys.exit(1)

def check_database():
    """データベース接続とテーブル内容の確認"""
    print("=== データベース接続テスト ===")
    
    with app.app_context():
        try:
            # 店舗URL数の確認
            store_url_count = db.session.query(func.count(StoreURL.id)).scalar()
            print(f"StoreURL テーブルの登録件数: {store_url_count}")
            
            # スクレイピング結果数の確認
            store_status_count = db.session.query(func.count(StoreStatus.id)).scalar()
            print(f"StoreStatus テーブルの登録件数: {store_status_count}")
            
            # 最新のスクレイピング時刻
            latest_time = db.session.query(func.max(StoreStatus.timestamp)).scalar()
            if latest_time:
                print(f"最新のスクレイピング時刻: {latest_time}")
                time_diff = datetime.now() - latest_time
                print(f"最終スクレイピングからの経過時間: {time_diff}")
            else:
                print("スクレイピングデータがありません")
            
            # エラーフラグのある店舗数
            error_count = db.session.query(func.count(StoreURL.id)).filter(StoreURL.error_flag > 0).scalar()
            print(f"エラーフラグがついている店舗数: {error_count}")
            
            if error_count > 0:
                # エラーフラグのある店舗を表示
                error_stores = db.session.query(StoreURL).filter(StoreURL.error_flag > 0).limit(5).all()
                print("\nエラーフラグのある店舗の例:")
                for store in error_stores:
                    print(f"  - ID: {store.id}, URL: {store.store_url}")
            
            # 作業中のスタッフがいない店舗の確認
            zero_working = db.session.query(func.count(StoreStatus.id)).filter(
                StoreStatus.working_staff == 0
            ).scalar()
            print(f"作業中スタッフが0の店舗数: {zero_working}")
            
            print("\nデータベース接続テスト完了")
        except Exception as e:
            print(f"データベース接続中にエラーが発生しました: {str(e)}")
            import traceback
            print(traceback.format_exc())

if __name__ == "__main__":
    check_database()
    print("\nこのスクリプトをローカル環境で実行し、データベース接続とデータの状態を確認してください。")
    print("404エラーの場合は、store_urlsテーブルの店舗URLが有効かどうか確認してください。")
    print("500エラーの場合は、アプリケーションのログを確認してより詳細な情報を得てください。")
