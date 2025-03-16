
from flask import Blueprint
from api_endpoints import register_api_routes

api_bp = Blueprint('api', __name__)

# シンプル化したAPIエンドポイントを登録
register_api_routes(api_bp)
