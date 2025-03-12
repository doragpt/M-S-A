
import os
import sqlite3
from datetime import datetime, timedelta

# データベースへの接続
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///store_data.db')
# SQLiteの場合はパスだけ取得
if DATABASE_URL.startswith('sqlite:///'):
    db_path = DATABASE_URL.replace('sqlite:///', '')
else:
    print("サポートされていないデータベースタイプです。SQLiteのみ対応しています。")
    exit(1)

def check_scraping_status():
    """スクレイピングの実行状況を確認する関数"""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 店舗URLの総数を取得
        cursor.execute("SELECT COUNT(*) FROM store_urls")
        total_urls = cursor.fetchone()[0]
        
        # 最新のスクレイピング時間を取得
        cursor.execute("SELECT MAX(timestamp) FROM store_status")
        latest_scrape = cursor.fetchone()[0]
        
        # 最新スクレイピングでのユニーク店舗数
        if latest_scrape:
            # 最新の日付から24時間以内のデータ
            cursor.execute("""
                SELECT COUNT(DISTINCT store_name) 
                FROM store_status 
                WHERE timestamp >= ?
            """, (datetime.strptime(latest_scrape, '%Y-%m-%d %H:%M:%S.%f') - timedelta(hours=24),))
            recent_stores = cursor.fetchone()[0]
        else:
            recent_stores = 0
        
        # エラーフラグがついている店舗数
        cursor.execute("SELECT COUNT(*) FROM store_urls WHERE error_flag > 0")
        error_stores = cursor.fetchone()[0]
        
        # 結果出力
        print("\n===== スクレイピング状況レポート =====")
        print(f"登録済み店舗URL総数: {total_urls}")
        print(f"最新スクレイピング時間: {latest_scrape or '未実行'}")
        
        if latest_scrape:
            latest_time = datetime.strptime(latest_scrape, '%Y-%m-%d %H:%M:%S.%f')
            time_diff = datetime.now() - latest_time
            hours_ago = time_diff.total_seconds() / 3600
            print(f"最終実行からの経過時間: {hours_ago:.1f}時間前")
        
        print(f"直近24時間内のスクレイプ店舗数: {recent_stores}")
        print(f"エラーフラグのある店舗数: {error_stores}")
        
        # カバレッジ計算
        if total_urls > 0:
            coverage = (recent_stores / total_urls) * 100
            print(f"スクレイピングカバレッジ: {coverage:.1f}%")
        
        # 最近追加された店舗（最新10件）
        print("\n----- 最近追加された店舗 -----")
        cursor.execute("SELECT id, store_url FROM store_urls ORDER BY id DESC LIMIT 10")
        recent_urls = cursor.fetchall()
        for id, url in recent_urls:
            print(f"ID: {id}, URL: {url}")
        
        # エラーのある店舗
        if error_stores > 0:
            print("\n----- エラーのある店舗 -----")
            cursor.execute("SELECT id, store_url FROM store_urls WHERE error_flag > 0 LIMIT 10")
            error_urls = cursor.fetchall()
            for id, url in error_urls:
                print(f"ID: {id}, URL: {url}")
        
        conn.close()
        return {
            "total_urls": total_urls,
            "latest_scrape": latest_scrape,
            "recent_stores": recent_stores,
            "error_stores": error_stores,
            "coverage": (recent_stores / total_urls * 100) if total_urls > 0 else 0
        }
    
    except Exception as e:
        print(f"エラーが発生しました: {e}")
        return None

if __name__ == "__main__":
    check_scraping_status()
