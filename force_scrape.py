
import sys
from app import scheduled_scrape, cache

def force_scrape_and_clear_cache():
    print("スクレイピングを手動で実行します...")
    scheduled_scrape()
    print("スクレイピング完了！")
    
    print("アプリケーションキャッシュをクリアします...")
    try:
        cache.clear()
        print("キャッシュクリア完了！")
    except Exception as e:
        print(f"キャッシュクリアでエラーが発生しました: {e}")
    
    print("スクレイピング結果の反映確認:")
    # 簡易的な確認として最新レコードのタイムスタンプを表示
    import sqlite3
    from datetime import datetime
    import pytz
    
    conn = sqlite3.connect('store_data.db')
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(timestamp) FROM store_status")
    last_scrape = cursor.fetchone()[0]
    
    if last_scrape:
        jst = pytz.timezone('Asia/Tokyo')
        utc_time = datetime.strptime(last_scrape, '%Y-%m-%d %H:%M:%S')
        utc_time = utc_time.replace(tzinfo=pytz.utc)
        jst_time = utc_time.astimezone(jst)
        
        print(f"最新データのタイムスタンプ: {jst_time.strftime('%Y-%m-%d %H:%M:%S')} (JST)")
    else:
        print("スクレイピング結果が見つかりません")
    
    conn.close()

if __name__ == "__main__":
    force_scrape_and_clear_cache()
