
import sqlite3
import datetime
import pytz

def check_database_status():
    """データベース内のスクレイピング状況を確認する関数"""
    conn = sqlite3.connect('store_data.db')
    cursor = conn.cursor()
    
    # 登録されている店舗URL数を取得
    cursor.execute("SELECT COUNT(*) FROM store_urls")
    url_count = cursor.fetchone()[0]
    print(f"登録されている店舗URL数: {url_count}")
    
    # 最新のスクレイピング結果数を取得
    cursor.execute("""
        SELECT COUNT(DISTINCT store_name) 
        FROM store_status 
        WHERE timestamp >= datetime('now', '-24 hours')
    """)
    recent_stores = cursor.fetchone()[0]
    print(f"過去24時間以内にスクレイピングされた店舗数: {recent_stores}")
    
    # 最新のスクレイピング時刻を取得
    cursor.execute("SELECT MAX(timestamp) FROM store_status")
    last_scrape = cursor.fetchone()[0]
    
    if last_scrape:
        # タイムゾーン変換
        jst = pytz.timezone('Asia/Tokyo')
        utc_time = datetime.datetime.strptime(last_scrape, '%Y-%m-%d %H:%M:%S')
        utc_time = utc_time.replace(tzinfo=pytz.utc)
        jst_time = utc_time.astimezone(jst)
        
        print(f"最終スクレイピング時刻: {jst_time.strftime('%Y-%m-%d %H:%M:%S')} (JST)")
    else:
        print("スクレイピング結果が見つかりません")
    
    # レコード総数を確認
    cursor.execute("SELECT COUNT(*) FROM store_status")
    total_records = cursor.fetchone()[0]
    print(f"総レコード数: {total_records}")
    
    conn.close()

if __name__ == "__main__":
    check_database_status()
