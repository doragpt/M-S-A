import sqlite3

def clear_db():
    conn = sqlite3.connect("store_data.db")
    cur = conn.cursor()
    cur.execute("DELETE FROM store_status;")
    conn.commit()
    conn.close()
    print("store_status テーブルのデータを削除しました。")

if __name__ == "__main__":
    clear_db()
