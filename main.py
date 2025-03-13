
# main.py - アプリケーション起動用エントリーポイント
from app import app, socketio
import pytz
from datetime import datetime
import sys
import os

if __name__ == '__main__':
    # タイムゾーン設定を確認
    jst = pytz.timezone('Asia/Tokyo')
    now_jst = datetime.now(jst)
    print(f"サーバー起動時刻（JST）: {now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
    
    # データベースインデックス作成（初回のみ）
    from create_indices import create_indices
    create_indices()
    
    # 環境変数のタイムゾーンを強制的に設定
    os.environ['TZ'] = 'Asia/Tokyo'
    
    try:
        # 起動
        print("アプリケーションを起動しています...")
        socketio.run(app, host="0.0.0.0", port=5000, debug=True, allow_unsafe_werkzeug=True)
    except Exception as e:
        print(f"アプリケーション起動中にエラーが発生しました: {e}")
        sys.exit(1)
