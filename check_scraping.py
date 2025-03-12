
import sqlite3
import datetime
import pytz
import sys

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
    
    # 店舗ごとのレコード件数を表示
    if len(sys.argv) > 1 and sys.argv[1] == "--detail":
        cursor.execute("""
            SELECT store_name, COUNT(*) as count
            FROM store_status
            GROUP BY store_name
            ORDER BY count DESC
            LIMIT 20
        """)
        print("\n店舗別レコード数（上位20件）:")
        for name, count in cursor.fetchall():
            print(f"  {name}: {count}件")
    
    # 日付別のレコード数を表示
    cursor.execute("""
        SELECT date(timestamp) as date, COUNT(*) as count
        FROM store_status
        GROUP BY date(timestamp)
        ORDER BY date DESC
        LIMIT 10
    """)
    print("\n日付別レコード数（最新10日）:")
    for date, count in cursor.fetchall():
        print(f"  {date}: {count}件")
    
    # 最新データのチェック - 古いデータが表示されていないか確認
    cursor.execute("""
        SELECT store_name, timestamp, working_staff, active_staff 
        FROM store_status 
        ORDER BY timestamp DESC 
        LIMIT 5
    """)
    print("\n最新のレコード（5件）:")
    for store, time, working, active in cursor.fetchall():
        print(f"  {store} - {time} - 稼働中:{working}人, 待機中:{active}人")
    
    conn.close()

if __name__ == "__main__":
    check_database_status()
