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
            SELECT s.*
            FROM store_status s
            INNER JOIN (
                SELECT store_name, MAX(timestamp) as max_time
                FROM store_status
                GROUP BY store_name
            ) latest ON s.store_name = latest.store_name 
                AND s.timestamp = latest.max_time
            SELECT s.*
            FROM store_status s
            JOIN latest_data l 
                ON s.store_name = l.store_name 
                AND s.timestamp = l.max_time
            """
            results = conn.execute(query).fetchall()
            
            if not results:
                return jsonify({
                    'status': 'success',
                    'data': [],
                    'message': 'データが見つかりません'
                })

            stores = [{
                'store_name': r['store_name'],
                'biz_type': r['biz_type'] or '',
                'genre': r['genre'] or '',
                'area': r['area'] or '',
                'total_staff': int(r['total_staff'] or 0),
                'working_staff': int(r['working_staff'] or 0),
                'active_staff': int(r['active_staff'] or 0),
                'timestamp': r['timestamp'].isoformat() if r['timestamp'] else None
            } for r in results]

            response_data = {
                'status': 'success',
                'data': stores
            }
            return jsonify(response_data)
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e),
                'data': []
            }), 500

    @bp.route('/history/optimized')
    def get_store_history():
        """店舗の履歴データを取得"""
        try:
            store = request.args.get('store', '')
            start_date = request.args.get('start_date', '')
            end_date = request.args.get('end_date', '')

            if not all([store, start_date, end_date]):
                return jsonify({
                    'status': 'error',
                    'message': 'パラメータが不足しています。store, start_date, end_dateが必要です。',
                    'data': None
                }), 400

            try:
                start = datetime.strptime(start_date, '%Y-%m-%d')
                end = datetime.strptime(end_date, '%Y-%m-%d')
                end = end.replace(hour=23, minute=59, second=59)
            except ValueError:
                return jsonify({
                    'status': 'error',
                    'message': '日付形式が無効です。YYYY-MM-DD形式で指定してください。',
                    'data': None
                }), 400

            # 必須パラメータの検証
            if not all([store, start_date, end_date]):
                return jsonify({
                    'status': 'error',
                    'message': '店舗名、開始日、終了日が必要です',
                    'data': None
                }), 400

            # 日付形式の検証
            try:
                start = datetime.strptime(start_date, '%Y-%m-%d')
                end = datetime.strptime(end_date, '%Y-%m-%d')
                end = end.replace(hour=23, minute=59, second=59)
            except ValueError:
                return jsonify({
                    'status': 'error',
                    'message': '無効な日付形式です',
                    'data': None
                }), 400

            conn = get_db_connection()
            query = """
            SELECT * FROM store_status 
            WHERE store_name = ? 
            AND timestamp BETWEEN ? AND ?
            ORDER BY timestamp
            """
            results = conn.execute(query, [store, start, end]).fetchall()

            history = [{
                'store_name': store,
                'timestamp': r['timestamp'].isoformat() if r['timestamp'] else None,
                'working_staff': int(r['working_staff'] or 0),
                'active_staff': int(r['active_staff'] or 0)
            } for r in results]

            return jsonify({
                'status': 'success',
                'data': history
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500

        try:
            jst = pytz.timezone('Asia/Tokyo')
            if not start_date or not end_date:
                # デフォルト7日間
                end_date = datetime.now(jst)
                start_date = end_date - timedelta(days=7)
            else:
                start_date = datetime.strptime(start_date, '%Y-%m-%d')
                end_date = datetime.strptime(end_date, '%Y-%m-%d')
                end_date = end_date.replace(hour=23, minute=59, second=59)

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
                'store_name': store,
                'timestamp': r['timestamp'].isoformat() if r['timestamp'] else None,
                'working_staff': int(r['working_staff'] or 0),
                'active_staff': int(r['active_staff'] or 0),
                'total_staff': int(r['total_staff'] or 0),
                'biz_type': r['biz_type'] or '',
                'genre': r['genre'] or '',
                'area': r['area'] or '',
                'rate': round(((int(r['working_staff'] or 0) - int(r['active_staff'] or 0)) / int(r['working_staff'] or 1)) * 100, 1) if int(r['working_staff'] or 0) > 0 else 0
            } for r in results]

            response_data = {
                'status': 'success',
                'data': {
                    'store': store,
                    'records': history,
                    'period': {
                        'start': start_date.isoformat(),
                        'end': end_date.isoformat()
                    }
                }
            }

            if not history:
                response_data = {
                    'status': 'error',
                    'message': 'データが見つかりませんでした',
                    'data': {
                        'store': store,
                        'records': [],
                        'period': {
                            'start': start_date.isoformat(),
                            'end': end_date.isoformat()
                        }
                    }
                }
                return jsonify(response_data), 404

            return jsonify(response_data)
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

    @bp.route('/averages/weekly')
    def get_weekly_averages():
        """週間の平均データを取得"""
        try:
            conn = get_db_connection()
            query = """
            WITH weekly_data AS (
                SELECT 
                    store_name,
                    strftime('%Y-%W', timestamp) as week,
                    AVG(CASE WHEN working_staff > 0 
                        THEN CAST((working_staff - active_staff) AS FLOAT) / working_staff * 100 
                        ELSE 0 END) as weekly_rate
                FROM store_status
                GROUP BY store_name, week
                HAVING week IS NOT NULL
            )
            SELECT 
                store_name,
                AVG(weekly_rate) as avg_rate,
                COUNT(DISTINCT week) as weeks_count
            FROM weekly_data
            GROUP BY store_name
            HAVING weeks_count >= 1
            ORDER BY avg_rate DESC
            LIMIT 20
            """
            
            results = conn.execute(query).fetchall()
            data = [{
                'store_name': r['store_name'],
                'avg_rate': round(r['avg_rate'], 1),
                'weeks_count': r['weeks_count']
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