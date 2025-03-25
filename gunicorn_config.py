
import os
import multiprocessing

# サーバーソケット
bind = "0.0.0.0:5000"

# ワーカープロセス数
workers = 1  # SocketIOを使用するため、workerは1に設定
worker_class = "eventlet"  # EventletベースのワーカーでWebSocketをサポート

# タイムアウト設定
timeout = 120
keepalive = 5

# ログ設定
accesslog = "gunicorn_access.log"
errorlog = "gunicorn_error.log"
loglevel = "info"

# プロセス名
proc_name = "msa_app"

# Flaskアプリケーションの環境変数
raw_env = [
    "FLASK_APP=main:app",
    "FLASK_DEBUG=False"
]
