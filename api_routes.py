
from flask import Blueprint
from api_endpoints import (
    get_current_stores,
    get_store_history,
    get_store_names,
    get_hourly_analysis,
    get_area_stats,
    get_genre_ranking,
    get_popular_ranking,
    get_aggregated_stats
)

api_bp = Blueprint('api', __name__)

# 基本データエンドポイント
api_bp.route('/stores/current')(get_current_stores)
api_bp.route('/stores/history')(get_store_history)
api_bp.route('/store-names')(get_store_names)

# 分析・ランキングエンドポイント
api_bp.route('/analysis/hourly')(get_hourly_analysis)
api_bp.route('/analysis/area')(get_area_stats)
api_bp.route('/ranking/genre')(get_genre_ranking)
api_bp.route('/ranking/popular')(get_popular_ranking)
api_bp.route('/stats/aggregated')(get_aggregated_stats)

# 既存APIとの互換性のための移行用ルート（仮）
def register_legacy_routes(app):
    """既存のAPIエンドポイントとの互換性のためのルートを登録"""
    # 1. 基本データ関連
    app.route('/api/data')(get_current_stores)
    app.route('/api/history')(get_store_history)
    app.route('/api/history/optimized')(get_store_history)
    app.route('/api/store-names')(get_store_names)
    
    # 2. 分析関連
    app.route('/api/hourly-analysis')(get_hourly_analysis)
    app.route('/api/area-stats')(get_area_stats)
    app.route('/api/ranking/genre')(get_genre_ranking)
    app.route('/api/ranking/popular')(get_popular_ranking)
    app.route('/api/aggregated')(get_aggregated_stats)
    
    # 3. 平均データ関連（既存エンドポイントは新しいAPIにリダイレクト）
    app.route('/api/averages/daily')(get_popular_ranking)  # daily期間指定で新APIにリダイレクト
    app.route('/api/averages/weekly')(get_popular_ranking)  # weekly期間指定で新APIにリダイレクト
    app.route('/api/averages/monthly')(get_popular_ranking)  # monthly期間指定で新APIにリダイレクト
    app.route('/api/averages/stores')(get_popular_ranking)  # all期間指定で新APIにリダイレクト
    
    # 4. ランキング関連
    app.route('/api/ranking/average')(get_popular_ranking)
    app.route('/api/ranking/top')(get_popular_ranking)  # 業種トップランキングは人気ランキングで代用
