from flask import Blueprint
from api_endpoints import register_api_routes

# API Blueprint
api_bp = Blueprint('api', __name__)

# エンドポイントをBlueprintに登録
register_api_routes(api_bp)
