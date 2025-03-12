
import os
import sys
from datetime import datetime, timedelta
from flask import Flask
from sqlalchemy import func, create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

# app.pyからモデルをインポート
sys.path.append('.')
try:
    from app import db, StoreStatus, StoreURL, app
except ImportError:
    print("app.pyからのインポートに失敗しました。同じディレクトリに配置されていることを確認してください。")
    sys.exit(1)

def check_scraping_status():
    """スクレイピングの実行状況を確認する関数 (SQLAlchemyを使用)"""
    try:
        with app.app_context():
            # 店舗URLの総数を取得
            total_urls = db.session.query(func.count(StoreURL.id)).scalar()
            
            # 最新のスクレイピング時間を取得
            latest_scrape = db.session.query(func.max(StoreStatus.timestamp)).scalar()
            
            # 最新スクレイピングでのユニーク店舗数
            recent_stores = 0
            if latest_scrape:
                # 最新の日付から24時間以内のデータ
                day_ago = latest_scrape - timedelta(hours=24)
                recent_stores = db.session.query(func.count(func.distinct(StoreStatus.store_name)))\
                    .filter(StoreStatus.timestamp >= day_ago).scalar()
            
            # エラーフラグがついている店舗数
            error_stores = db.session.query(func.count(StoreURL.id))\
                .filter(StoreURL.error_flag > 0).scalar()
            
            # 結果出力
            print("\n===== スクレイピング状況レポート =====")
            print(f"登録済み店舗URL総数: {total_urls}")
            print(f"最新スクレイピング時間: {latest_scrape or '未実行'}")
            
            if latest_scrape:
                time_diff = datetime.now() - latest_scrape
                hours_ago = time_diff.total_seconds() / 3600
                print(f"最終実行からの経過時間: {hours_ago:.1f}時間前")
            
            print(f"直近24時間内のスクレイプ店舗数: {recent_stores}")
            print(f"エラーフラグのある店舗数: {error_stores}")
            
            # カバレッジ計算
            coverage = 0
            if total_urls > 0:
                coverage = (recent_stores / total_urls) * 100
                print(f"スクレイピングカバレッジ: {coverage:.1f}%")
            
            # 最近追加された店舗（最新10件）
            print("\n----- 最近追加された店舗 -----")
            recent_urls = db.session.query(StoreURL.id, StoreURL.store_url)\
                .order_by(StoreURL.id.desc()).limit(10).all()
            for id, url in recent_urls:
                print(f"ID: {id}, URL: {url}")
            
            # エラーのある店舗
            if error_stores > 0:
                print("\n----- エラーのある店舗 -----")
                error_urls = db.session.query(StoreURL.id, StoreURL.store_url)\
                    .filter(StoreURL.error_flag > 0).limit(10).all()
                for id, url in error_urls:
                    print(f"ID: {id}, URL: {url}")
            
            return {
                "total_urls": total_urls,
                "latest_scrape": latest_scrape,
                "recent_stores": recent_stores,
                "error_stores": error_stores,
                "coverage": coverage
            }
    
    except Exception as e:
        print(f"エラーが発生しました: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    check_scraping_status()
