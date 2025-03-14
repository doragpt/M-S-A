
# main.py - アプリケーション起動用エントリーポイント
from app import app, socketio
import pytz
from datetime import datetime
import sys
import os
import logging

# ロガー設定
logger = logging.getLogger('app')

if __name__ == '__main__':
    # タイムゾーン設定を確認
    jst = pytz.timezone('Asia/Tokyo')
    now_jst = datetime.now(jst)
    print(f"サーバー起動時刻（JST）: {now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
    
    # データベースインデックス作成（初回のみ）
    try:
        from create_indices import create_indices
        create_indices()
    except Exception as e:
        logger.warning(f"インデックス作成中にエラーが発生しました（無視して続行）: {e}")
    
    # 環境変数のタイムゾーンを強制的に設定
    os.environ['TZ'] = 'Asia/Tokyo'
    
    try:
        # Flask-SocketIOを使用してアプリケーションを起動
        port = int(os.environ.get("PORT", 5000))
        print(f"アプリケーションを起動しています: http://0.0.0.0:{port}")
        socketio.run(app, host="0.0.0.0", port=port, debug=True, allow_unsafe_werkzeug=True)
    except Exception as e:
        logger.error(f"アプリケーション起動中にエラーが発生しました: {e}")
        sys.exit(1)
