from flask import Blueprint
from api_endpoints import (
    register_api_routes,
    register_health_check
)

# API Blueprint
api_bp = Blueprint('api_v1', __name__)

# エンドポイントをBlueprintに登録
register_api_routes(api_bp)

# 従来のルートを登録する関数
def register_legacy_routes(app):
    """従来のAPIエンドポイントを登録する"""
    from api_endpoints import register_legacy_routes as register_legacy_endpoints
    register_health_check(app)
    register_legacy_endpoints(app)
