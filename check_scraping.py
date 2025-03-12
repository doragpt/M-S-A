
#!/usr/bin/env python3
"""
スクレイピング状態の確認とトラブルシューティングを行うスクリプト
"""
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
                time_since = datetime.now() - latest_scrape
                print(f"前回のスクレイピングから経過時間: {time_since}")
                print(f"24時間以内にスクレイピングされた店舗数: {recent_stores}/{total_urls}")
                
                if total_urls > 0:
                    coverage = (recent_stores / total_urls) * 100
                    print(f"カバレッジ率: {coverage:.1f}%")
                    
                    if coverage < 80:
                        print("\n⚠️ 警告: カバレッジが80%未満です")
                        
            print(f"エラーフラグがついている店舗数: {error_stores}")
            
            # エラーフラグがある店舗の詳細を表示（最大10件）
            if error_stores > 0:
                print("\nエラーのある店舗URL:")
                error_urls = db.session.query(StoreURL.id, StoreURL.store_url, StoreURL.error_flag)\
                    .filter(StoreURL.error_flag > 0)\
                    .order_by(StoreURL.error_flag.desc())\
                    .limit(10).all()
                    
                for id, url, error_count in error_urls:
                    print(f"  ID: {id}, エラー回数: {error_count}, URL: {url}")
                    
            return {
                "total_urls": total_urls,
                "latest_scrape": latest_scrape,
                "recent_stores": recent_stores,
                "error_stores": error_stores
            }
            
    except Exception as e:
        print(f"エラー: {e}")
        return None

def check_database_health():
    """データベース健全性チェック"""
    try:
        with app.app_context():
            # 総レコード数
            total_records = db.session.query(func.count(StoreStatus.id)).scalar()
            
            # 最古のレコード
            oldest_record = db.session.query(func.min(StoreStatus.timestamp)).scalar()
            
            # 最新のレコード
            newest_record = db.session.query(func.max(StoreStatus.timestamp)).scalar()
            
            # ユニーク店舗数
            unique_stores = db.session.query(func.count(func.distinct(StoreStatus.store_name))).scalar()
            
            # 結果出力
            print("\n===== データベース健全性レポート =====")
            print(f"総レコード数: {total_records}")
            print(f"最古のレコード: {oldest_record}")
            print(f"最新のレコード: {newest_record}")
            print(f"ユニーク店舗数: {unique_stores}")
            
            # 大きすぎる場合の警告
            if total_records > 1000000:  # 100万レコード以上
                print("\n⚠️ 警告: データベースサイズが非常に大きいです。古いデータのクリーンアップを検討してください。")
                
            return {
                "total_records": total_records,
                "oldest_record": oldest_record,
                "newest_record": newest_record,
                "unique_stores": unique_stores
            }
            
    except Exception as e:
        print(f"データベースチェックエラー: {e}")
        return None

def fix_error_flags():
    """エラーフラグをリセットする関数"""
    try:
        with app.app_context():
            count = db.session.query(StoreURL).filter(StoreURL.error_flag > 0).count()
            if count > 0:
                db.session.query(StoreURL).update({StoreURL.error_flag: 0})
                db.session.commit()
                print(f"{count}件のエラーフラグをリセットしました")
            else:
                print("リセット対象のエラーフラグはありません")
    except Exception as e:
        print(f"エラーフラグリセット失敗: {e}")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="スクレイピング状態確認ツール")
    parser.add_argument("--reset-errors", action="store_true", help="エラーフラグをリセット")
    parser.add_argument("--check-db", action="store_true", help="データベースの健全性をチェック")
    parser.add_argument("--all", action="store_true", help="すべてのチェックを実行")
    
    args = parser.parse_args()
    
    # 引数なしの場合は基本的なステータスチェックを実行
    if len(sys.argv) == 1:
        check_scraping_status()
    else:
        if args.reset_errors or args.all:
            fix_error_flags()
            
        if args.check_db or args.all:
            check_database_health()
            
        if not (args.reset_errors and not args.all):
            check_scraping_status()


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
