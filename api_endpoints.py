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
                return f(*args, **kwargs)

            cache_key = key_prefix % request.path
            if request.query_string:
                cache_key = f"{cache_key}?{request.query_string.decode('utf-8')}"

            cached_response = cache.get(cache_key)
            if cached_response is not None:
                return cached_response

            response = f(*args, **kwargs)
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
            client_id = request.remote_addr if by_ip else 'global'

            if client_id not in rate_limit_cache:
                rate_limit_cache[client_id] = {
                    'count': 0,
                    'reset_at': now + timedelta(seconds=per)
                }

            if now >= rate_limit_cache[client_id]['reset_at']:
                rate_limit_cache[client_id]['count'] = 0
                rate_limit_cache[client_id]['reset_at'] = now + timedelta(seconds=per)

            rate_limit_cache[client_id]['count'] += 1
            if rate_limit_cache[client_id]['count'] > limit:
                return jsonify({'error': 'Rate limit exceeded'}), 429

            return f(*args, **kwargs)
        return decorated_function
    return decorator

# レスポンス用のヘルパー関数（修正版）
def api_response(data: Any, status: int = 200) -> Tuple[Dict, int]:
    """API応答を直接データで返す"""
    response = jsonify(data)
    response.headers['Content-Type'] = 'application/json'
    return response, status

def error_response(message: str, status: int = 400) -> Tuple[Dict, int]:
    """エラー応答の標準形式"""
    response = jsonify({'error': message})
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

# 最新の店舗データを取得するエンドポイント
def get_current_stores():
    """
    最新の店舗データを取得するエンドポイント
    """
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 0, type=int)
        biz_type = request.args.get('biz_type', '')
        genre = request.args.get('genre', '')
        area = request.args.get('area', '')
        sort = request.args.get('sort', 'rate')
        order = request.args.get('order', 'desc')
        search = request.args.get('search', '')
        favorites_str = request.args.get('favorites', '')
        favorites = [f.strip() for f in favorites_str.split(',') if f.strip()]

        query = """
        WITH latest_timestamps AS (
            SELECT store_name, MAX(timestamp) as latest_timestamp
            FROM store_status
            GROUP BY store_name
        )
        SELECT DISTINCT s.store_name,
               s.biz_type,
               s.genre,
               s.area,
               s.total_staff,
               s.working_staff,
               s.active_staff,
               CASE 
                   WHEN s.total_staff = 0 THEN 0
                   ELSE ROUND(CAST(s.working_staff AS FLOAT) / s.total_staff * 100, 2)
               END AS operation_rate
        FROM store_status s
        JOIN latest_timestamps lt
            ON s.store_name = lt.store_name 
            AND s.timestamp = lt.latest_timestamp
        WHERE 1=1
        """

        params = []
        if search:
            query += " AND (s.store_name LIKE ? OR s.biz_type LIKE ? OR s.genre LIKE ? OR s.area LIKE ?)"
            search_param = f"%{search}%"
            params.extend([search_param] * 4)
        if biz_type:
            query += " AND s.biz_type = ?"
            params.append(biz_type)
        if genre:
            query += " AND s.genre = ?"
            params.append(genre)
        if area:
            query += " AND s.area = ?"
            params.append(area)
        if favorites:
            placeholders = ','.join(['?' for _ in favorites])
            query += f" AND s.store_name IN ({placeholders})"
            params.extend(favorites)

        if sort == 'name':
            query += f" ORDER BY s.store_name {'ASC' if order == 'asc' else 'DESC'}"
        elif sort == 'rate':
            query += f" ORDER BY operation_rate {'ASC' if order == 'asc' else 'DESC'}, s.store_name ASC"

        if per_page > 0:
            query += " LIMIT ? OFFSET ?"
            params.extend([per_page, (page - 1) * per_page])

        conn = get_db_connection()
        results = conn.execute(query, params).fetchall()
        conn.close()

        stores = []
        for row in results:
            store_data = {
                'store_name': row['store_name'],
                'biz_type': row['biz_type'],
                'genre': row['genre'],
                'area': row['area'],
                'total_staff': row['total_staff'],
                'working_staff': row['working_staff'],
                'active_staff': row['active_staff'],
                'operation_rate': float(row['operation_rate'])
            }
            stores.append(store_data)

        return api_response(stores)  # 配列形式で直接返す

    except Exception as e:
        logger.error(f"店舗データ取得エラー: {str(e)}")
        return error_response(f"データ取得中にエラーが発生しました: {str(e)}", 500)

# 店舗履歴データを取得するエンドポイント（最適化版）
def get_store_history_optimized():
    """
    指定した店舗の履歴データを取得するエンドポイント（最適化版）
    """
    store_name = request.args.get('store_name', '')
    if not store_name:
        return error_response("店舗名が指定されていません", 400)

    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')
    interval = min(request.args.get('interval', 60, type=int), 1440)

    try:
        jst = pytz.timezone('Asia/Tokyo')
        now = datetime.now(jst)

        if not start_date_str:
            start_date = (now - timedelta(hours=24)).replace(tzinfo=None)
        else:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')

        if not end_date_str:
            end_date = now.replace(tzinfo=None)
        else:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)

        if (end_date - start_date).days > 30:
            interval = max(interval, 240)

        query = """
        SELECT 
            CAST(strftime('%s', timestamp) / (? * 60) * (? * 60) AS INTEGER) AS interval_timestamp,
            AVG(CASE WHEN total_staff = 0 THEN 0 ELSE CAST(working_staff AS FLOAT) / total_staff END) AS avg_operation_rate,
            MAX(CASE WHEN total_staff = 0 THEN 0 ELSE CAST(working_staff AS FLOAT) / total_staff END) AS max_operation_rate,
            MIN(CASE WHEN total_staff = 0 THEN 0 ELSE CAST(working_staff AS FLOAT) / total_staff END) AS min_operation_rate,
            AVG(working_staff) AS avg_working_staff,
            AVG(active_staff) AS avg_active_staff,
            COUNT(*) AS sample_count,
            MAX(timestamp) AS latest_timestamp
        FROM store_status
        WHERE store_name = ?
        AND timestamp BETWEEN ? AND ?
        GROUP BY interval_timestamp
        ORDER BY interval_timestamp
        """

        conn = get_db_connection()
        results = conn.execute(query, [interval, interval, store_name, start_date, end_date]).fetchall()
        conn.close()

        history_data = []
        for row in results:
            timestamp = datetime.fromtimestamp(row['interval_timestamp'])
            history_data.append({
                'timestamp': timestamp.isoformat(),
                'avg_operation_rate': row['avg_operation_rate'],
                'max_operation_rate': row['max_operation_rate'],
                'min_operation_rate': row['min_operation_rate'],
                'avg_working_staff': row['avg_working_staff'],
                'avg_active_staff': row['avg_active_staff'],
                'sample_count': row['sample_count']
            })

        return api_response(history_data)  # 配列形式で直接返す

    except Exception as e:
        logger.error(f"店舗履歴データ取得エラー: {e}")
        return error_response(f"履歴データ取得中にエラーが発生しました: {str(e)}", 500)

# 店舗名の一覧を取得するエンドポイント
def get_store_names():
    """
    店舗名の一覧を取得するエンドポイント
    """
    biz_type = request.args.get('biz_type', '')
    genre = request.args.get('genre', '')
    area = request.args.get('area', '')

    try:
        query = """
        WITH latest_timestamps AS (
            SELECT store_name, MAX(timestamp) as latest_timestamp
            FROM store_status
            GROUP BY store_name
        )
        SELECT DISTINCT s.store_name
        FROM store_status s
        JOIN latest_timestamps lt
            ON s.store_name = lt.store_name AND s.timestamp = lt.latest_timestamp
        WHERE 1=1
        """

        params = []
        if biz_type:
            query += " AND s.biz_type = ?"
            params.append(biz_type)
        if genre:
            query += " AND s.genre = ?"
            params.append(genre)
        if area:
            query += " AND s.area = ?"
            params.append(area)

        query += " ORDER BY s.store_name ASC"

        conn = get_db_connection()
        results = conn.execute(query, params).fetchall()
        conn.close()

        store_names = [row['store_name'] for row in results]
        return api_response(store_names)  # 配列形式で直接返す

    except Exception as e:
        logger.error(f"店舗名一覧取得エラー: {e}")
        return error_response(f"店舗名一覧取得中にエラーが発生しました: {str(e)}", 500)

# 時間帯別分析データを取得するエンドポイント
def get_hourly_analysis():
    """
    時間帯別の平均稼働率データを取得するエンドポイント
    """
    store = request.args.get('store', '')
    biz_type = request.args.get('biz_type', '')
    genre = request.args.get('genre', '')
    area = request.args.get('area', '')
    days = min(request.args.get('days', 7, type=int), 30)

    try:
        jst = pytz.timezone('Asia/Tokyo')
        end_date = datetime.now(jst).replace(tzinfo=None)
        start_date = end_date - timedelta(days=days)

        query = """
        SELECT 
            CAST(strftime('%H', timestamp) AS INTEGER) AS hour,
            AVG(CASE WHEN total_staff = 0 THEN 0 ELSE CAST(working_staff AS FLOAT) / total_staff END) AS avg_operation_rate,
            AVG(working_staff) AS avg_working_staff,
            AVG(active_staff) AS avg_active_staff,
            COUNT(DISTINCT date(timestamp)) AS day_count,
            COUNT(*) AS data_count
        FROM store_status
        WHERE timestamp BETWEEN ? AND ?
        """

        params = [start_date, end_date]
        if store:
            query += " AND store_name = ?"
            params.append(store)
        if biz_type:
            query += " AND biz_type = ?"
            params.append(biz_type)
        if genre:
            query += " AND genre = ?"
            params.append(genre)
        if area:
            query += " AND area = ?"
            params.append(area)

        query += " GROUP BY hour ORDER BY hour"

        conn = get_db_connection()
        results = conn.execute(query, params).fetchall()
        conn.close()

        hourly_data = []
        for row in results:
            hourly_data.append({
                'hour': row['hour'],
                'avg_operation_rate': row['avg_operation_rate'],
                'avg_working_staff': row['avg_working_staff'],
                'avg_active_staff': row['avg_active_staff'],
                'day_count': row['day_count'],
                'data_count': row['data_count']
            })

        return api_response(hourly_data)  # 配列形式で直接返す

    except Exception as e:
        logger.error(f"時間帯別分析データ取得エラー: {e}")
        return error_response(f"時間帯別分析データ取得中にエラーが発生しました: {str(e)}", 500)

# エリア別の統計情報を取得するエンドポイント
def get_area_stats():
    """
    エリア別の統計情報を取得するエンドポイント
    """
    biz_type = request.args.get('biz_type', '')
    genre = request.args.get('genre', '')

    try:
        query = """
        WITH latest_timestamps AS (
            SELECT store_name, MAX(timestamp) as latest_timestamp
            FROM store_status
            GROUP BY store_name
        )
        SELECT 
            s.area,
            COUNT(*) AS store_count,
            AVG(CASE WHEN s.total_staff = 0 THEN 0 ELSE CAST(s.working_staff AS FLOAT) / s.total_staff END) AS avg_operation_rate
        FROM store_status s
        JOIN latest_timestamps lt
            ON s.store_name = lt.store_name AND s.timestamp = lt.latest_timestamp
        WHERE 1=1
        """

        params = []
        if biz_type:
            query += " AND s.biz_type = ?"
            params.append(biz_type)
        if genre:
            query += " AND s.genre = ?"
            params.append(genre)

        query += " GROUP BY s.area ORDER BY store_count DESC, avg_operation_rate DESC"

        conn = get_db_connection()
        results = conn.execute(query, params).fetchall()
        conn.close()

        area_data = []
        for row in results:
            area_data.append({
                'area': row['area'],
                'store_count': row['store_count'],
                'avg_operation_rate': row['avg_operation_rate']
            })

        return api_response(area_data)  # 配列形式で直接返す

    except Exception as e:
        logger.error(f"エリア別統計データ取得エラー: {e}")
        return error_response(f"エリア別統計データ取得中にエラーが発生しました: {str(e)}", 500)

# 業種内ジャンル別の平均稼働率ランキングを取得するエンドポイント
def get_genre_ranking():
    """
    業種内のジャンル別平均稼働率ランキングを取得するエンドポイント
    """
    biz_type = request.args.get('biz_type', '')
    if not biz_type:
        return error_response("業種が指定されていません", 400)

    limit = min(request.args.get('limit', 10, type=int), 50)

    try:
        query = """
        WITH latest_timestamps AS (
            SELECT store_name, MAX(timestamp) as latest_timestamp
            FROM store_status
            GROUP BY store_name
        )
        SELECT 
            s.genre,
            COUNT(*) AS store_count,
            AVG(CASE WHEN s.total_staff = 0 THEN 0 ELSE CAST(s.working_staff AS FLOAT) / s.total_staff END) AS avg_operation_rate
        FROM store_status s
        JOIN latest_timestamps lt
            ON s.store_name = lt.store_name AND s.timestamp = lt.latest_timestamp
        WHERE s.biz_type = ?
        GROUP BY s.genre
        ORDER BY avg_operation_rate DESC
        LIMIT ?
        """

        conn = get_db_connection()
        results = conn.execute(query, [biz_type, limit]).fetchall()
        conn.close()

        genre_data = []
        for row in results:
            genre_data.append({
                'genre': row['genre'],
                'store_count': row['store_count'],
                'avg_operation_rate': row['avg_operation_rate']
            })

        return api_response(genre_data)  # 配列形式で直接返す

    except Exception as e:
        logger.error(f"ジャンル別ランキングデータ取得エラー: {e}")
        return error_response(f"ジャンル別ランキングデータ取得中にエラーが発生しました: {str(e)}", 500)

# 店舗の平均稼働率ランキングを取得するエンドポイント
def get_average_ranking():
    """
    店舗の平均稼働率ランキングを取得するエンドポイント
    """
    biz_type = request.args.get('biz_type', '')
    genre = request.args.get('genre', '')
    area = request.args.get('area', '')
    limit = min(request.args.get('limit', 10, type=int), 50)

    try:
        query = """
        WITH latest_timestamps AS (
            SELECT store_name, MAX(timestamp) as latest_timestamp
            FROM store_status
            GROUP BY store_name
        )
        SELECT 
            s.store_name,
            s.biz_type,
            s.genre,
            s.area,
            CASE WHEN s.total_staff = 0 THEN 0 ELSE CAST(s.working_staff AS FLOAT) / s.total_staff END AS operation_rate
        FROM store_status s
        JOIN latest_timestamps lt
            ON s.store_name = lt.store_name AND s.timestamp = lt.latest_timestamp
        WHERE 1=1
        """

        params = []
        if biz_type:
            query += " AND s.biz_type = ?"
            params.append(biz_type)
        if genre:
            query += " AND s.genre = ?"
            params.append(genre)
        if area:
            query += " AND s.area = ?"
            params.append(area)

        query += " ORDER BY operation_rate DESC LIMIT ?"
        params.append(limit)

        conn = get_db_connection()
        results = conn.execute(query, params).fetchall()
        conn.close()

        store_data = []
        for row in results:
            store_data.append({
                'store_name': row['store_name'],
                'biz_type': row['biz_type'],
                'genre': row['genre'],
                'area': row['area'],
                'operation_rate': row['operation_rate']
            })

        return api_response(store_data)  # 配列形式で直接返す

    except Exception as e:
        logger.error(f"稼働率ランキングデータ取得エラー: {e}")
        return error_response(f"稼働率ランキングデータ取得中にエラーが発生しました: {str(e)}", 500)

# 期間別の人気店舗ランキングを取得するエンドポイント
def get_popular_ranking():
    """
    期間別の人気店舗ランキングを取得するエンドポイント
    """
    period = request.args.get('period', 'daily')
    if period not in ['daily', 'weekly', 'monthly']:
        return error_response("無効な期間が指定されています（有効値: daily, weekly, monthly）", 400)

    biz_type = request.args.get('biz_type', '')
    genre = request.args.get('genre', '')
    area = request.args.get('area', '')
    limit = min(request.args.get('limit', 10, type=int), 50)

    try:
        jst = pytz.timezone('Asia/Tokyo')
        now = datetime.now(jst).replace(tzinfo=None)

        if period == 'daily':
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == 'weekly':
            start_date = now - timedelta(days=7)
        else:  # monthly
            start_date = now - timedelta(days=30)

        query = """
        SELECT 
            store_name,
            biz_type,
            genre,
            area,
            AVG(CASE WHEN total_staff = 0 THEN 0 ELSE CAST(working_staff AS FLOAT) / total_staff END) AS avg_operation_rate,
            COUNT(*) AS data_count
        FROM store_status
        WHERE timestamp BETWEEN ? AND ?
        """

        params = [start_date, now]
        if biz_type:
            query += " AND biz_type = ?"
            params.append(biz_type)
        if genre:
            query += " AND genre = ?"
            params.append(genre)
        if area:
            query += " AND area = ?"
            params.append(area)

        query += " GROUP BY store_name, biz_type, genre, area"
        query += " HAVING data_count >= 3"
        query += " ORDER BY avg_operation_rate DESC LIMIT ?"
        params.append(limit)

        conn = get_db_connection()
        results = conn.execute(query, params).fetchall()
        conn.close()

        store_data = []
        for row in results:
            store_data.append({
                'store_name': row['store_name'],
                'biz_type': row['biz_type'],
                'genre': row['genre'],
                'area': row['area'],
                'avg_operation_rate': row['avg_operation_rate'],
                'data_count': row['data_count']
            })

        return api_response(store_data)  # 配列形式で直接返す

    except Exception as e:
        logger.error(f"人気店舗ランキングデータ取得エラー: {e}")
        return error_response(f"人気店舗ランキングデータ取得中にエラーが発生しました: {str(e)}", 500)

# 期間別（日次/週次/月次）の平均稼働率データを取得するエンドポイント
def get_period_averages(period):
    """
    期間別の平均稼働率データを取得するエンドポイント
    """
    if period not in ['daily', 'weekly', 'monthly']:
        return error_response("無効な期間が指定されています（有効値: daily, weekly, monthly）", 400)

    biz_type = request.args.get('biz_type', '')
    genre = request.args.get('genre', '')
    area = request.args.get('area', '')
    limit = min(request.args.get('limit', 30, type=int), 90)

    try:
        if period == 'daily':
            date_format = '%Y-%m-%d'
            group_by = "strftime('%Y-%m-%d', timestamp)"
        elif period == 'weekly':
            date_format = '%Y-%W'
            group_by = "strftime('%Y-%W', timestamp)"
        else:  # monthly
            date_format = '%Y-%m'
            group_by = "strftime('%Y-%m', timestamp)"

        query = f"""
        SELECT 
            {group_by} AS period_label,
            AVG(CASE WHEN total_staff = 0 THEN 0 ELSE CAST(working_staff AS FLOAT) / total_staff END) AS avg_operation_rate,
            COUNT(DISTINCT store_name) AS store_count,
            COUNT(*) AS data_count
        FROM store_status
        WHERE 1=1
        """

        params = []
        if biz_type:
            query += " AND biz_type = ?"
            params.append(biz_type)
        if genre:
            query += " AND genre = ?"
            params.append(genre)
        if area:
            query += " AND area = ?"
            params.append(area)

        query += f" GROUP BY {group_by}"
        query += " ORDER BY period_label DESC LIMIT ?"
        params.append(limit)

        conn = get_db_connection()
        results = conn.execute(query, params).fetchall()
        conn.close()

        data = []
        for row in results:
            period_label = row['period_label']
            if period == 'weekly':
                year, week = period_label.split('-')
                try:
                    first_day = datetime.strptime(f'{year}-01-01', '%Y-%m-%d')
                    weekday = first_day.weekday()
                    first_monday = first_day - timedelta(days=weekday)
                    week_monday = first_monday + timedelta(weeks=int(week))
                    week_sunday = week_monday + timedelta(days=6)
                    formatted_date = f"{week_monday.strftime('%m/%d')}～{week_sunday.strftime('%m/%d')}"
                except Exception:
                    formatted_date = f"{year}年第{week}週"
            elif period == 'monthly':
                year, month = period_label.split('-')
                formatted_date = f"{year}年{month}月"
            else:
                formatted_date = period_label.replace('-', '/')

            data.append({
                'period': period_label,
                'formatted_date': formatted_date,
                'avg_operation_rate': row['avg_operation_rate'],
                'store_count': row['store_count'],
                'data_count': row['data_count']
            })

        return api_response(data)  # 配列形式で直接返す

    except Exception as e:
        logger.error(f"{period}平均データ取得エラー: {e}")
        return error_response(f"{period}平均データ取得中にエラーが発生しました: {str(e)}", 500)

# 店舗別の平均稼働率の時系列データを取得するエンドポイント
def get_store_averages():
    """
    店舗別の平均稼働率の時系列データを取得するエンドポイント
    """
    store_names_str = request.args.get('store_names', '')
    if not store_names_str:
        return error_response("店舗名が指定されていません", 400)

    store_names = [name.strip() for name in store_names_str.split(',')]
    if len(store_names) > 5:
        return error_response("一度に取得できる店舗数は最大5店舗です", 400)

    days = min(request.args.get('days', 7, type=int), 30)

    try:
        jst = pytz.timezone('Asia/Tokyo')
        end_date = datetime.now(jst).replace(tzinfo=None)
        start_date = end_date - timedelta(days=days)

        query = """
        SELECT 
            store_name,
            strftime('%Y-%m-%d', timestamp) AS date,
            AVG(CASE WHEN total_staff = 0 THEN 0 ELSE CAST(working_staff AS FLOAT) / total_staff END) AS avg_operation_rate,
            COUNT(*) AS data_count
        FROM store_status
        WHERE timestamp BETWEEN ? AND ?
        AND store_name IN ({})
        GROUP BY store_name, date
        ORDER BY date, store_name
        """.format(','.join(['?'] * len(store_names)))

        params = [start_date, end_date] + store_names

        conn = get_db_connection()
        results = conn.execute(query, params).fetchall()
        conn.close()

        store_data = {}
        for row in results:
            store_name = row['store_name']
            if store_name not in store_data:
                store_data[store_name] = []
            store_data[store_name].append({
                'date': row['date'],
                'avg_operation_rate': row['avg_operation_rate'],
                'data_count': row['data_count']
            })

        return api_response(store_data)  # オブジェクト形式で返す（店舗名ごとの配列）

    except Exception as e:
        logger.error(f"店舗別平均データ取得エラー: {e}")
        return error_response(f"店舗別平均データ取得中にエラーが発生しました: {str(e)}", 500)

# フィルター用のオプション（業種、ジャンル、エリア）を取得するエンドポイント
def get_filter_options():
    """フィルター用のオプション（業種、ジャンル、エリア）を取得するエンドポイント"""
    try:
        conn = get_db_connection()

        biz_types = conn.execute(
            "SELECT DISTINCT biz_type FROM store_status WHERE biz_type IS NOT NULL ORDER BY biz_type"
        ).fetchall()

        genres = conn.execute(
            "SELECT DISTINCT genre FROM store_status WHERE genre IS NOT NULL ORDER BY genre"
        ).fetchall()

        areas = conn.execute(
            "SELECT DISTINCT area FROM store_status WHERE area IS NOT NULL ORDER BY area"
        ).fetchall()

        conn.close()

        return api_response({
            'biz_types': [row['biz_type'] for row in biz_types],
            'genres': [row['genre'] for row in genres],
            'areas': [row['area'] for row in areas]
        })  # オブジェクト形式で返す

    except Exception as e:
        logger.error(f"フィルターオプション取得エラー: {e}")
        return error_response(f"フィルターオプション取得中にエラーが発生しました: {str(e)}", 500)

# APIエンドポイント関数をルート定義と関連付ける関数
def register_api_routes(api_bp):
    """APIエンドポイント関数をBlueprintに登録する"""
    api_bp.route('/stores/current', endpoint='stores_current')(cached(300)(rate_limit(limit=30)(get_current_stores)))
    api_bp.route('/stores/history/optimized', endpoint='stores_history_optimized')(cached(300)(rate_limit(limit=20)(get_store_history_optimized)))
    api_bp.route('/stores/names', endpoint='stores_names')(cached(600)(get_store_names))
    api_bp.route('/analysis/hourly', endpoint='analysis_hourly')(cached(1800)(get_hourly_analysis))
    api_bp.route('/stats/area', endpoint='stats_area')(cached(1800)(get_area_stats))
    api_bp.route('/ranking/genre', endpoint='ranking_genre')(cached(1800)(get_genre_ranking))
    api_bp.route('/ranking/average', endpoint='ranking_average')(cached(1800)(get_average_ranking))
    api_bp.route('/ranking/popular', endpoint='ranking_popular')(cached(1800)(get_popular_ranking))
    api_bp.route('/averages/daily', endpoint='averages_daily')(cached(3600)(lambda: get_period_averages('daily')))
    api_bp.route('/averages/weekly', endpoint='averages_weekly')(cached(3600)(lambda: get_period_averages('weekly')))
    api_bp.route('/averages/monthly', endpoint='averages_monthly')(cached(3600)(lambda: get_period_averages('monthly')))
    api_bp.route('/averages/stores', endpoint='averages_stores')(cached(1800)(get_store_averages))
    api_bp.route('/filter-options', endpoint='filter_options')(cached(3600)(get_filter_options))

# 既存のAPIエンドポイントと互換性を保つための関数を登録するためのヘルパー関数
def register_legacy_routes(app):
    """既存のAPIエンドポイントと互換性を保つための関数を登録する"""
    app.route('/api/data', endpoint='legacy_data')(cached(300)(rate_limit(limit=30)(get_current_stores)))
    app.route('/api/history/optimized', endpoint='legacy_history_optimized')(cached(300)(rate_limit(limit=20)(get_store_history_optimized)))
    app.route('/api/store-names', endpoint='legacy_store_names')(cached(600)(get_store_names))
    app.route('/api/hourly-analysis', endpoint='legacy_hourly_analysis')(cached(1800)(get_hourly_analysis))
    app.route('/api/area-stats', endpoint='legacy_area_stats')(cached(1800)(get_area_stats))
    app.route('/api/ranking/genre', endpoint='legacy_ranking_genre')(cached(1800)(get_genre_ranking))
    app.route('/api/ranking/average', endpoint='legacy_ranking_average')(cached(1800)(get_average_ranking))
    app.route('/api/ranking/popular', endpoint='legacy_ranking_popular')(cached(1800)(get_popular_ranking))
    app.route('/api/averages/daily', endpoint='legacy_averages_daily')(cached(3600)(lambda: get_period_averages('daily')))
    app.route('/api/averages/weekly', endpoint='legacy_averages_weekly')(cached(3600)(lambda: get_period_averages('weekly')))
    app.route('/api/averages/monthly', endpoint='legacy_averages_monthly')(cached(3600)(lambda: get_period_averages('monthly')))
    app.route('/api/averages/stores', endpoint='legacy_averages_stores')(cached(1800)(get_store_averages))
    app.route('/api/filter-options', endpoint='legacy_filter_options')(cached(3600)(get_filter_options))
