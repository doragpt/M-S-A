from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI()

# CORS（クロスオリジン設定）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # すべてのオリジンを許可（必要に応じて制限）
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "FastAPI is running"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
# main.py - アプリケーション起動用エントリーポイント
from app import app, socketio
import pytz
from datetime import datetime

if __name__ == '__main__':
    # タイムゾーン設定を確認
    jst = pytz.timezone('Asia/Tokyo')
    now_jst = datetime.now(jst)
    print(f"サーバー起動時刻（JST）: {now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")
    
    # 起動
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, allow_unsafe_werkzeug=True)
