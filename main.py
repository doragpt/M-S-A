
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn
import os
from starlette.responses import RedirectResponse

app = FastAPI()

# 静的ファイルを正しく提供するための設定
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    # インテグレーテッドダッシュボードを表示
    return templates.TemplateResponse("integrated_dashboard.html", {"request": request})

# APIルートをリダイレクト
@app.get("/api/{path:path}")
async def redirect_to_flask(path: str):
    return RedirectResponse(url=f"http://0.0.0.0:5000/api/{path}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
