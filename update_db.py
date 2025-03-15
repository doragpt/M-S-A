
import sqlite3
from main import app
from models import StoreURL

# アプリケーションコンテキスト内で実行
with app.app_context():
    # 既存のテーブルに error_flag カラムを追加
    try:
        conn = sqlite3.connect('store_data.db')
        cursor = conn.cursor()
        
        # error_flag カラムが存在するかチェック
        cursor.execute("PRAGMA table_info(store_urls)")
        columns = cursor.fetchall()
        column_names = [column[1] for column in columns]
        
        if 'error_flag' not in column_names:
            print("store_urls テーブルに error_flag カラムを追加しています...")
            cursor.execute("ALTER TABLE store_urls ADD COLUMN error_flag INTEGER DEFAULT 0")
            conn.commit()
            print("カラムが正常に追加されました")
        else:
            print("error_flag カラムは既に存在します")
            
        conn.close()
    except Exception as e:
        print(f"エラーが発生しました: {e}")
