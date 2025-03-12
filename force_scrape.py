
import sys
import time
from datetime import datetime

# app.pyからの直接インポート
try:
    sys.path.append('.')
    from app import app, scheduled_scrape
    from check_scraping import check_scraping_status
except ImportError:
    print("app.pyからのインポートに失敗しました。同じディレクトリに配置されていることを確認してください。")
    sys.exit(1)

def force_scrape():
    """
    app.pyのscheduled_scrape関数を直接呼び出して強制的にスクレイピングを実行
    """
    start_time = time.time()
    print(f"強制スクレイピングを開始します: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        # Flaskアプリケーションコンテキストを作成
        with app.app_context():
            # scheduled_scrape関数を実行
            scheduled_scrape()
        
        elapsed_time = time.time() - start_time
        print(f"スクレイピングが完了しました: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"処理時間: {elapsed_time:.2f}秒")
        
        # 結果を確認
        check_scraping_status()
        
    except Exception as e:
        print(f"スクレイピング実行中にエラーが発生しました: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    force_scrape()
