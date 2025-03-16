import os
import logging
import json
from datetime import datetime, timedelta
from functools import wraps
from typing import Dict, List, Union, Optional, Any, Tuple
import pytz

from flask import Blueprint, request, jsonify, current_app
from flask_caching import Cache

from models import db, StoreStatus
from database import get_db_connection

# キャッシュ設定
cache = None

def init_cache(cache_instance):
    """キャッシュインスタンスを初期化"""
    global cache
    cache = cache_instance

# APIロガーの設定
logger = logging.getLogger('api')

# レートリミット用の簡易キャッシュ（本番環境ではRedisなどに置き換え）
rate_limit_cache = {}

# キャッシュデコレーター関数
def cached(timeout=300, key_prefix='view/%s'):
    """キャッシュするためのデコレーター"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if cache is None:
                # キャッシュが設定されていない場合は通常通り実行
                return f(*args, **kwargs)

            cache_key = key_prefix % request.path

            # クエリパラメータがある場合はキャッシュキーに追加
            if request.query_string:
                cache_key = f"{cache_key}?{request.query_string.decode('utf-8')}"

            # キャッシュから取得
            cached_response = cache.get(cache_key)
            if cached_response is not None:
                return cached_response

            # 関数を実行してレスポンスを生成
            response = f(*args, **kwargs)

            # レスポンスをキャッシュに保存
            cache.set(cache_key, response, timeout=timeout)

            return response
        return decorated_function
    return decorator

# レートリミットデコレーター
def rate_limit(limit=60, per=60, by_ip=True):
    """簡易的なレートリミット"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            now = datetime.now()

            # クライアントの識別子
            client_id = request.remote_addr if by_ip else 'global'

            # レートリミット情報の取得または初期化
            if client_id not in rate_limit_cache:
                rate_limit_cache[client_id] = {
                    'count': 0,
                    'reset_at': now + timedelta(seconds=per)
                }

            # 制限時間が経過していれば、カウンタをリセット
            if now >= rate_limit_cache[client_id]['reset_at']:
                rate_limit_cache[client_id]['count'] = 0
                rate_limit_cache[client_id]['reset_at'] = now + timedelta(seconds=per)

            # リクエスト数のカウントアップ
            rate_limit_cache[client_id]['count'] += 1

            # 制限を超えたかチェック
            if rate_limit_cache[client_id]['count'] > limit:
                return jsonify({'error': 'Rate limit exceeded'}), 429

            return f(*args, **kwargs)
        return decorated_function
    return decorator

# レスポンス用のヘルパー関数
def api_response(data: Any, status: int = 200, message: str = 'success') -> Tuple[Dict, int]:
    """API応答の標準形式"""
    response_data = {
        'status': message,
        'data': data
    }

    # デバッグログ - レスポンスの構造を出力
    if logger.isEnabledFor(logging.DEBUG):
        import json
        logger.debug(f"API応答: {json.dumps(response_data, ensure_ascii=False, default=str)[:200]}...")

    response = jsonify(response_data)
    response.headers['Content-Type'] = 'application/json'
    return response, status

def error_response(message: str, status: int = 400) -> Tuple[Dict, int]:
    """エラー応答の標準形式"""
    response = jsonify({
        'status': 'error',
        'message': message
    })
    response.headers['Content-Type'] = 'application/json'
    return response, status

# グローバルヘルスチェックエンドポイント（Blueprintの外側）
def register_health_check(app):
    @app.route('/api/v1/health')
    @cached(timeout=10)
    def health_check():
        """APIの健康状態を確認するエンドポイント"""
        return api_response({
            'status': 'ok',
            'timestamp': datetime.now().isoformat(),
            'version': '1.0'
        })


from flask import request, jsonify
from datetime import datetime, timedelta
import pytz
from models import db, StoreStatus
from database import get_db_connection

def register_api_routes(bp):
    """シンプル化したAPIエンドポイント"""

    @bp.route('/data')
    def get_current_data():
        """現在の店舗データを取得"""
        try:
            conn = get_db_connection()
            query = """
            WITH latest_data AS (
                SELECT store_name, MAX(timestamp) as max_time
                FROM store_status
                GROUP BY store_name
            )
            SELECT s.*
            FROM store_status s
            JOIN latest_data l 
                ON s.store_name = l.store_name 
                AND s.timestamp = l.max_time
            """
            results = conn.execute(query).fetchall()
            stores = [{
                'store_name': r['store_name'],
                'biz_type': r['biz_type'],
                'genre': r['genre'],
                'area': r['area'],
                'total_staff': r['total_staff'],
                'working_staff': r['working_staff'],
                'active_staff': r['active_staff']
            } for r in results]

            return jsonify({'status': 'success', 'data': stores})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @bp.route('/history/optimized')
    def get_store_history():
        """店舗の履歴データを取得"""
        store = request.args.get('store')
        if not store:
            return jsonify({'status': 'error', 'message': '店舗名が必要です'}), 400

        try:
            days = 7  # デフォルト7日間
            jst = pytz.timezone('Asia/Tokyo')
            end_date = datetime.now(jst)
            start_date = end_date - timedelta(days=days)

            conn = get_db_connection()
            query = """
            SELECT *
            FROM store_status
            WHERE store_name = ?
            AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp
            """
            results = conn.execute(query, [store, start_date, end_date]).fetchall()

            history = [{
                'timestamp': r['timestamp'].isoformat(),
                'working_staff': r['working_staff'],
                'active_staff': r['active_staff'],
                'total_staff': r['total_staff']
            } for r in results]

            return jsonify({'status': 'success', 'data': history})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @bp.route('/store-names')
    def get_store_names():
        """店舗名の一覧を取得"""
        try:
            conn = get_db_connection()
            results = conn.execute(
                "SELECT DISTINCT store_name FROM store_status ORDER BY store_name"
            ).fetchall()
            names = [r['store_name'] for r in results]
            return jsonify({'status': 'success', 'data': names})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @bp.route('/area-stats')
    def get_area_stats():
        """エリア別の統計を取得"""
        try:
            conn = get_db_connection()
            query = """
            WITH latest_data AS (
                SELECT store_name, MAX(timestamp) as max_time
                FROM store_status
                GROUP BY store_name
            )
            SELECT 
                area,
                COUNT(DISTINCT s.store_name) as store_count,
                AVG(CASE WHEN s.working_staff > 0 
                    THEN CAST((s.working_staff - s.active_staff) AS FLOAT) / s.working_staff * 100 
                    ELSE 0 END) as avg_rate
            FROM store_status s
            JOIN latest_data l 
                ON s.store_name = l.store_name 
                AND s.timestamp = l.max_time
            GROUP BY area
            ORDER BY store_count DESC
            """
            results = conn.execute(query).fetchall()
            stats = [{
                'area': r['area'],
                'store_count': r['store_count'],
                'avg_rate': round(r['avg_rate'], 1)
            } for r in results]

            return jsonify({'status': 'success', 'data': stats})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @bp.route('/ranking/genre')
    def get_genre_ranking():
        """ジャンル別ランキングを取得"""
        try:
            biz_type = request.args.get('biz_type')
            conn = get_db_connection()
            
            query = """
            WITH latest_data AS (
                SELECT store_name, MAX(timestamp) as max_time
                FROM store_status
                WHERE biz_type = ?
                GROUP BY store_name
            )
            SELECT 
                genre,
                COUNT(DISTINCT s.store_name) as store_count,
                AVG(CASE WHEN s.working_staff > 0 
                    THEN CAST((s.working_staff - s.active_staff) AS FLOAT) / s.working_staff * 100 
                    ELSE 0 END) as avg_rate
            FROM store_status s
            JOIN latest_data l 
                ON s.store_name = l.store_name 
                AND s.timestamp = l.max_time
            GROUP BY genre
            HAVING genre IS NOT NULL
            ORDER BY avg_rate DESC
            """
            results = conn.execute(query, [biz_type]).fetchall()
            
            data = [{
                'genre': r['genre'],
                'store_count': r['store_count'],
                'avg_rate': round(r['avg_rate'], 1)
            } for r in results]

            return jsonify({'status': 'success', 'data': data})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @bp.route('/ranking/average')
    def get_store_ranking():
        """店舗別平均稼働率ランキングを取得"""
        try:
            biz_type = request.args.get('biz_type')
            limit = request.args.get('limit', default=15, type=int)
            
            conn = get_db_connection()
            
            query = """
            WITH store_rates AS (
                SELECT 
                    store_name,
                    biz_type,
                    genre,
                    area,
                    AVG(CASE WHEN working_staff > 0 
                        THEN CAST((working_staff - active_staff) AS FLOAT) / working_staff * 100 
                        ELSE 0 END) as avg_rate,
                    COUNT(*) as sample_count
                FROM store_status
                WHERE biz_type = ?
                GROUP BY store_name, biz_type, genre, area
                HAVING sample_count >= 5
            )
            SELECT *
            FROM store_rates
            ORDER BY avg_rate DESC
            LIMIT ?
            """
            
            results = conn.execute(query, [biz_type, limit]).fetchall()
            
            data = [{
                'store_name': r['store_name'],
                'biz_type': r['biz_type'],
                'genre': r['genre'],
                'area': r['area'],
                'avg_rate': round(r['avg_rate'], 1),
                'sample_count': r['sample_count']
            } for r in results]

            return jsonify({'status': 'success', 'data': data})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500