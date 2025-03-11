
#!/usr/bin/env python3
"""
データベースメンテナンス用スクリプト
- 古いデータの削除
- インデックスの最適化
- テーブルのバキューム
- キャッシュクリア
"""
import os
import sys
import logging
from datetime import datetime, timedelta
import sqlite3
from flask import Flask
from flask_caching import Cache

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Flask アプリケーションの初期化（キャッシュクリア用）
app = Flask(__name__)
app.config['CACHE_TYPE'] = 'SimpleCache'
cache = Cache(app)

def clean_old_data(days=365):
    """指定日数よりも古いデータを削除する"""
    try:
        db_path = 'store_data.db'
        if not os.path.exists(db_path):
            logger.error(f"データベースが見つかりません: {db_path}")
            return False

        # 古いデータの基準日を計算
        retention_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 削除前のレコード数を取得
        cursor.execute("SELECT COUNT(*) FROM store_status")
        before_count = cursor.fetchone()[0]
        
        # 古いデータを削除
        cursor.execute(
            "DELETE FROM store_status WHERE date(timestamp) < ?", 
            (retention_date,)
        )
        conn.commit()
        
        # 削除後のレコード数を取得
        cursor.execute("SELECT COUNT(*) FROM store_status")
        after_count = cursor.fetchone()[0]
        
        deleted_count = before_count - after_count
        logger.info(f"{days}日より古いデータを削除しました: {deleted_count}件 ({before_count} → {after_count})")
        
        # テーブルの最適化
        cursor.execute("VACUUM")
        conn.commit()
        logger.info("データベースを最適化しました (VACUUM)")
        
        conn.close()
        return True
    except Exception as e:
        logger.error(f"データクリーンアップエラー: {e}")
        return False

def clear_app_cache():
    """アプリケーションキャッシュをクリアする"""
    try:
        with app.app_context():
            cache.clear()
        logger.info("アプリケーションキャッシュをクリアしました")
        return True
    except Exception as e:
        logger.error(f"キャッシュクリアエラー: {e}")
        return False

def main():
    """メイン処理"""
    args = sys.argv[1:]
    retention_days = 730  # デフォルト: 2年(730日)に設定
    
    if args and args[0].isdigit():
        retention_days = int(args[0])
    
    logger.info(f"メンテナンスを開始します（保持期間: {retention_days}日）")
    
    db_result = clean_old_data(retention_days)
    cache_result = clear_app_cache()
    
    if db_result and cache_result:
        logger.info("メンテナンスが正常に完了しました")
        return 0
    else:
        logger.error("メンテナンスの一部が失敗しました")
        return 1

if __name__ == "__main__":
    sys.exit(main())
