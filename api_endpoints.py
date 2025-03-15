
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
    return jsonify({
        'status': message,
        'data': data
    }), status

def error_response(message: str, status: int = 400) -> Tuple[Dict, int]:
    """エラー応答の標準形式"""
    return jsonify({
        'status': 'error',
        'message': message
    }), status

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
    
    クエリパラメータ:
    - page: ページ番号（デフォルト: 1）
    - per_page: 1ページあたりの表示件数（デフォルト: 50）
    - biz_type: 業種でフィルタリング
    - genre: ジャンルでフィルタリング
    - area: エリアでフィルタリング
    - sort: ソート順（稼働率 - rate, 名前 - name）
    - order: 昇順/降順（asc/desc）
    """
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 100)
    biz_type = request.args.get('biz_type', '')
    genre = request.args.get('genre', '')
    area = request.args.get('area', '')
    sort = request.args.get('sort', 'rate')
    order = request.args.get('order', 'desc')
    
    try:
        # SQLクエリ構築
        query = """
        WITH latest_timestamps AS (
            SELECT store_name, MAX(timestamp) as latest_timestamp
            FROM store_status
            GROUP BY store_name
        )
        SELECT s.*,
               CASE 
                   WHEN s.total_staff = 0 THEN 0
                   ELSE CAST(s.working_staff AS FLOAT) / s.total_staff
               END AS operation_rate
        FROM store_status s
        JOIN latest_timestamps lt
            ON s.store_name = lt.store_name AND s.timestamp = lt.latest_timestamp
        WHERE 1=1
        """
        
        params = []
        
        # フィルター条件の追加
        if biz_type:
            query += " AND s.biz_type = ?"
            params.append(biz_type)
        if genre:
            query += " AND s.genre = ?"
            params.append(genre)
        if area:
            query += " AND s.area = ?"
            params.append(area)
        
        # ソート順の設定
        if sort == 'name':
            query += f" ORDER BY s.store_name {'ASC' if order == 'asc' else 'DESC'}"
        else:
            query += f" ORDER BY operation_rate {'ASC' if order == 'asc' else 'DESC'}, s.store_name ASC"
        
        # ページネーション
        query += " LIMIT ? OFFSET ?"
        params.extend([per_page, (page - 1) * per_page])
        
        # クエリ実行
        conn = get_db_connection()
        results = conn.execute(query, params).fetchall()
        
        # 総件数の取得（別クエリ）
        count_query = """
        WITH latest_timestamps AS (
            SELECT store_name, MAX(timestamp) as latest_timestamp
            FROM store_status
            GROUP BY store_name
        )
        SELECT COUNT(*) as total
        FROM store_status s
        JOIN latest_timestamps lt
            ON s.store_name = lt.store_name AND s.timestamp = lt.latest_timestamp
        WHERE 1=1
        """
        
        count_params = []
        if biz_type:
            count_query += " AND s.biz_type = ?"
            count_params.append(biz_type)
        if genre:
            count_query += " AND s.genre = ?"
            count_params.append(genre)
        if area:
            count_query += " AND s.area = ?"
            count_params.append(area)
        
        total = conn.execute(count_query, count_params).fetchone()['total']
        conn.close()
        
        # 結果の整形
        stores = []
        for row in results:
            store = {
                'id': row['id'],
                'store_name': row['store_name'],
                'biz_type': row['biz_type'],
                'genre': row['genre'],
                'area': row['area'],
                'total_staff': row['total_staff'],
                'working_staff': row['working_staff'],
                'active_staff': row['active_staff'],
                'operation_rate': row['operation_rate'],
                'timestamp': row['timestamp'],
                'url': row['url'],
                'shift_time': row['shift_time']
            }
            stores.append(store)
        
        # メタデータを含むレスポンス
        result = {
            'stores': stores,
            'meta': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': (total + per_page - 1) // per_page
            }
        }
        
        return api_response(result)
        
    except Exception as e:
        logger.error(f"店舗データ取得エラー: {e}")
        return error_response(f"データ取得中にエラーが発生しました: {str(e)}")

# 店舗履歴データを取得するエンドポイント（最適化版）
def get_store_history_optimized():
    """
    指定した店舗の履歴データを取得するエンドポイント（最適化版）
    
    クエリパラメータ:
    - store_name: 店舗名（必須）
    - start_date: 開始日（YYYY-MM-DD）
    - end_date: 終了日（YYYY-MM-DD）
    - interval: データ間隔（分、デフォルト: 60）
    """
    store_name = request.args.get('store_name', '')
    if not store_name:
        return error_response("店舗名が指定されていません")
    
    # 日付範囲のパース
    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')
    interval = min(request.args.get('interval', 60, type=int), 1440)  # 最大値は1日（1440分）
    
    try:
        jst = pytz.timezone('Asia/Tokyo')
        now = datetime.now(jst)
        
        # デフォルト: 過去24時間
        if not start_date_str:
            start_date = (now - timedelta(hours=24)).replace(tzinfo=None)
        else:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        
        if not end_date_str:
            end_date = now.replace(tzinfo=None)
        else:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
            # 終了日は23:59:59に設定
            end_date = end_date.replace(hour=23, minute=59, second=59)
        
        # 期間が30日以上の場合はintervalを大きく
        if (end_date - start_date).days > 30:
            interval = max(interval, 240)  # 4時間以上
        
        # SQLクエリ構築（時間でグループ化）
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
        
        # 結果の整形
        history_data = []
        for row in results:
            # Unixタイムスタンプを日時に変換
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
        
        return api_response({
            'store_name': store_name,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'interval_minutes': interval,
            'data_points': len(history_data),
            'history': history_data
        })
        
    except Exception as e:
        logger.error(f"店舗履歴データ取得エラー: {e}")
        return error_response(f"履歴データ取得中にエラーが発生しました: {str(e)}")

# 店舗名の一覧を取得するエンドポイント
def get_store_names():
    """
    店舗名の一覧を取得するエンドポイント
    
    クエリパラメータ:
    - biz_type: 業種でフィルタリング
    - genre: ジャンルでフィルタリング
    - area: エリアでフィルタリング
    """
    biz_type = request.args.get('biz_type', '')
    genre = request.args.get('genre', '')
    area = request.args.get('area', '')
    
    try:
        # 最新の店舗状態に基づいて店舗名を取得
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
        
        # フィルター条件の追加
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
        
        # 結果をリスト化
        store_names = [row['store_name'] for row in results]
        
        return api_response({
            'count': len(store_names),
            'store_names': store_names
        })
        
    except Exception as e:
        logger.error(f"店舗名一覧取得エラー: {e}")
        return error_response(f"店舗名一覧取得中にエラーが発生しました: {str(e)}")

# 時間帯別分析データを取得するエンドポイント
def get_hourly_analysis():
    """
    時間帯別の平均稼働率データを取得するエンドポイント
    
    クエリパラメータ:
    - store: 特定の店舗名（指定がなければ全店舗の平均）
    - biz_type: 業種でフィルタリング
    - genre: ジャンルでフィルタリング
    - area: エリアでフィルタリング
    - days: 過去何日分のデータを使用するか（デフォルト: 7）
    """
    store = request.args.get('store', '')
    biz_type = request.args.get('biz_type', '')
    genre = request.args.get('genre', '')
    area = request.args.get('area', '')
    days = min(request.args.get('days', 7, type=int), 30)  # 最大30日まで
    
    try:
        # 過去X日間のデータに限定
        jst = pytz.timezone('Asia/Tokyo')
        end_date = datetime.now(jst).replace(tzinfo=None)
        start_date = end_date - timedelta(days=days)
        
        # SQLクエリ構築（時間帯別にグループ化）
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
        
        # フィルター条件の追加
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
        
        # 結果の整形
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
        
        # ピーク時間の計算
        if hourly_data:
            peak_hour = max(hourly_data, key=lambda x: x['avg_operation_rate'])
            min_hour = min(hourly_data, key=lambda x: x['avg_operation_rate'])
        else:
            peak_hour = None
            min_hour = None
        
        return api_response({
            'store': store or '全店舗',
            'days_analyzed': days,
            'hourly_data': hourly_data,
            'peak_hour': peak_hour,
            'min_hour': min_hour
        })
        
    except Exception as e:
        logger.error(f"時間帯別分析データ取得エラー: {e}")
        return error_response(f"時間帯別分析データ取得中にエラーが発生しました: {str(e)}")

# エリア別の統計情報を取得するエンドポイント
def get_area_stats():
    """
    エリア別の統計情報を取得するエンドポイント
    
    クエリパラメータ:
    - biz_type: 業種でフィルタリング
    - genre: ジャンルでフィルタリング
    """
    biz_type = request.args.get('biz_type', '')
    genre = request.args.get('genre', '')
    
    try:
        # SQLクエリ構築（エリア別に集計）
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
        
        # フィルター条件の追加
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
        
        # 結果の整形
        area_data = []
        for row in results:
            area_data.append({
                'area': row['area'],
                'store_count': row['store_count'],
                'avg_operation_rate': row['avg_operation_rate']
            })
        
        return api_response({
            'area_count': len(area_data),
            'areas': area_data
        })
        
    except Exception as e:
        logger.error(f"エリア別統計データ取得エラー: {e}")
        return error_response(f"エリア別統計データ取得中にエラーが発生しました: {str(e)}")

# 業種内ジャンル別の平均稼働率ランキングを取得するエンドポイント
def get_genre_ranking():
    """
    業種内のジャンル別平均稼働率ランキングを取得するエンドポイント
    
    クエリパラメータ:
    - biz_type: 業種（必須）
    - limit: 上位何件を取得するか（デフォルト: 10）
    """
    biz_type = request.args.get('biz_type', '')
    if not biz_type:
        return error_response("業種が指定されていません")
    
    limit = min(request.args.get('limit', 10, type=int), 50)  # 最大50件まで
    
    try:
        # SQLクエリ構築（ジャンル別に集計）
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
        
        # 結果の整形
        genre_data = []
        for row in results:
            genre_data.append({
                'genre': row['genre'],
                'store_count': row['store_count'],
                'avg_operation_rate': row['avg_operation_rate']
            })
        
        return api_response({
            'biz_type': biz_type,
            'genre_count': len(genre_data),
            'genres': genre_data
        })
        
    except Exception as e:
        logger.error(f"ジャンル別ランキングデータ取得エラー: {e}")
        return error_response(f"ジャンル別ランキングデータ取得中にエラーが発生しました: {str(e)}")

# 店舗の平均稼働率ランキングを取得するエンドポイント
def get_average_ranking():
    """
    店舗の平均稼働率ランキングを取得するエンドポイント
    
    クエリパラメータ:
    - biz_type: 業種でフィルタリング
    - genre: ジャンルでフィルタリング
    - area: エリアでフィルタリング
    - limit: 上位何件を取得するか（デフォルト: 10）
    """
    biz_type = request.args.get('biz_type', '')
    genre = request.args.get('genre', '')
    area = request.args.get('area', '')
    limit = min(request.args.get('limit', 10, type=int), 50)  # 最大50件まで
    
    try:
        # SQLクエリ構築（店舗別に集計）
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
        
        # フィルター条件の追加
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
        
        # 結果の整形
        store_data = []
        for row in results:
            store_data.append({
                'store_name': row['store_name'],
                'biz_type': row['biz_type'],
                'genre': row['genre'],
                'area': row['area'],
                'operation_rate': row['operation_rate']
            })
        
        return api_response({
            'limit': limit,
            'store_count': len(store_data),
            'stores': store_data
        })
        
    except Exception as e:
        logger.error(f"稼働率ランキングデータ取得エラー: {e}")
        return error_response(f"稼働率ランキングデータ取得中にエラーが発生しました: {str(e)}")

# 期間別の人気店舗ランキングを取得するエンドポイント
def get_popular_ranking():
    """
    期間別の人気店舗ランキングを取得するエンドポイント
    
    クエリパラメータ:
    - period: 期間（daily, weekly, monthly）
    - biz_type: 業種でフィルタリング
    - genre: ジャンルでフィルタリング
    - area: エリアでフィルタリング
    - limit: 上位何件を取得するか（デフォルト: 10）
    """
    period = request.args.get('period', 'daily')
    if period not in ['daily', 'weekly', 'monthly']:
        return error_response("無効な期間が指定されています（有効値: daily, weekly, monthly）")
    
    biz_type = request.args.get('biz_type', '')
    genre = request.args.get('genre', '')
    area = request.args.get('area', '')
    limit = min(request.args.get('limit', 10, type=int), 50)  # 最大50件まで
    
    try:
        # 期間に応じたデータ範囲の設定
        jst = pytz.timezone('Asia/Tokyo')
        now = datetime.now(jst).replace(tzinfo=None)
        
        if period == 'daily':
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == 'weekly':
            start_date = now - timedelta(days=7)
        else:  # monthly
            start_date = now - timedelta(days=30)
        
        # SQLクエリ構築（期間内の平均稼働率）
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
        
        # フィルター条件の追加
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
        query += " HAVING data_count >= 3"  # 少なくとも3件以上のデータがある店舗のみ
        query += " ORDER BY avg_operation_rate DESC LIMIT ?"
        params.append(limit)
        
        conn = get_db_connection()
        results = conn.execute(query, params).fetchall()
        conn.close()
        
        # 結果の整形
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
        
        return api_response({
            'period': period,
            'start_date': start_date.isoformat(),
            'end_date': now.isoformat(),
            'limit': limit,
            'store_count': len(store_data),
            'stores': store_data
        })
        
    except Exception as e:
        logger.error(f"人気店舗ランキングデータ取得エラー: {e}")
        return error_response(f"人気店舗ランキングデータ取得中にエラーが発生しました: {str(e)}")

# 期間別（日次/週次/月次）の平均稼働率データを取得するエンドポイント
def get_period_averages(period):
    """
    期間別の平均稼働率データを取得するエンドポイント
    
    クエリパラメータ:
    - biz_type: 業種でフィルタリング
    - genre: ジャンルでフィルタリング
    - area: エリアでフィルタリング
    - limit: データ件数の上限（デフォルト: 30）
    """
    if period not in ['daily', 'weekly', 'monthly']:
        return error_response("無効な期間が指定されています（有効値: daily, weekly, monthly）")
    
    biz_type = request.args.get('biz_type', '')
    genre = request.args.get('genre', '')
    area = request.args.get('area', '')
    limit = min(request.args.get('limit', 30, type=int), 90)  # 最大90件まで
    
    try:
        # 日付グループ化の設定
        if period == 'daily':
            date_format = '%Y-%m-%d'
            group_by = "strftime('%Y-%m-%d', timestamp)"
        elif period == 'weekly':
            date_format = '%Y-%W'  # 年と週番号
            group_by = "strftime('%Y-%W', timestamp)"
        else:  # monthly
            date_format = '%Y-%m'
            group_by = "strftime('%Y-%m', timestamp)"
        
        # SQLクエリ構築
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
        
        # フィルター条件の追加
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
        
        # 結果の整形と日付のフォーマット
        data = []
        for row in results:
            # 日付表示の整形
            period_label = row['period_label']
            if period == 'weekly':
                # 週番号から日付範囲への変換
                year, week = period_label.split('-')
                try:
                    import datetime
                    # 指定した年の第1週の月曜日を取得
                    first_day = datetime.datetime.strptime(f'{year}-01-01', '%Y-%m-%d')
                    # 週の始まりを月曜にする
                    weekday = first_day.weekday()
                    # 第1週の月曜日を計算（前年の最終週になることもある）
                    first_monday = first_day - datetime.timedelta(days=weekday)
                    # 指定した週の月曜日を計算
                    week_monday = first_monday + datetime.timedelta(weeks=int(week))
                    # 週の終わり（日曜日）
                    week_sunday = week_monday + datetime.timedelta(days=6)
                    # 表示用にフォーマット
                    formatted_date = f"{week_monday.strftime('%m/%d')}～{week_sunday.strftime('%m/%d')}"
                except Exception:
                    formatted_date = f"{year}年第{week}週"
            elif period == 'monthly':
                # YYYY-MM to YYYY年MM月
                year, month = period_label.split('-')
                formatted_date = f"{year}年{month}月"
            else:
                # YYYY-MM-DD to YYYY/MM/DD
                formatted_date = period_label.replace('-', '/')
            
            data.append({
                'period': period_label,
                'formatted_date': formatted_date,
                'avg_operation_rate': row['avg_operation_rate'],
                'store_count': row['store_count'],
                'data_count': row['data_count']
            })
        
        return api_response({
            'period_type': period,
            'data_count': len(data),
            'averages': data
        })
        
    except Exception as e:
        logger.error(f"{period}平均データ取得エラー: {e}")
        return error_response(f"{period}平均データ取得中にエラーが発生しました: {str(e)}")

# 店舗別の平均稼働率の時系列データを取得するエンドポイント
def get_store_averages():
    """
    店舗別の平均稼働率の時系列データを取得するエンドポイント
    
    クエリパラメータ:
    - store_names: カンマ区切りの店舗名リスト（必須）
    - days: 過去何日分のデータを取得するか（デフォルト: 7）
    """
    store_names_str = request.args.get('store_names', '')
    if not store_names_str:
        return error_response("店舗名が指定されていません")
    
    store_names = [name.strip() for name in store_names_str.split(',')]
    if len(store_names) > 5:
        return error_response("一度に取得できる店舗数は最大5店舗です")
    
    days = min(request.args.get('days', 7, type=int), 30)  # 最大30日まで
    
    try:
        # 過去X日間のデータに限定
        jst = pytz.timezone('Asia/Tokyo')
        end_date = datetime.now(jst).replace(tzinfo=None)
        start_date = end_date - timedelta(days=days)
        
        # SQLクエリ構築（日付と店舗でグループ化）
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
        
        # 店舗ごとのデータを整理
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
        
        return api_response({
            'stores': store_names,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'days': days,
            'data': store_data
        })
        
    except Exception as e:
        logger.error(f"店舗別平均データ取得エラー: {e}")
        return error_response(f"店舗別平均データ取得中にエラーが発生しました: {str(e)}")

# フィルター用のオプション（業種、ジャンル、エリア）を取得するエンドポイント
def get_filter_options():
    """フィルター用のオプション（業種、ジャンル、エリア）を取得するエンドポイント"""
    try:
        conn = get_db_connection()
        
        # 業種の取得
        biz_types = conn.execute(
            "SELECT DISTINCT biz_type FROM store_status WHERE biz_type IS NOT NULL ORDER BY biz_type"
        ).fetchall()
        
        # ジャンルの取得
        genres = conn.execute(
            "SELECT DISTINCT genre FROM store_status WHERE genre IS NOT NULL ORDER BY genre"
        ).fetchall()
        
        # エリアの取得
        areas = conn.execute(
            "SELECT DISTINCT area FROM store_status WHERE area IS NOT NULL ORDER BY area"
        ).fetchall()
        
        conn.close()
        
        return api_response({
            'biz_types': [row['biz_type'] for row in biz_types],
            'genres': [row['genre'] for row in genres],
            'areas': [row['area'] for row in areas]
        })
        
    except Exception as e:
        logger.error(f"フィルターオプション取得エラー: {e}")
        return error_response(f"フィルターオプション取得中にエラーが発生しました: {str(e)}")

# APIエンドポイント関数をルート定義と関連付ける関数
def register_api_routes(api_bp):
    """APIエンドポイント関数をBlueprintに登録する"""
    # 最新店舗データ
    api_bp.route('/stores/current')(cached(300)(rate_limit(limit=30)(get_current_stores)))
    
    # 店舗履歴データ
    api_bp.route('/stores/history/optimized')(cached(300)(rate_limit(limit=20)(get_store_history_optimized)))
    
    # 店舗名リスト
    api_bp.route('/stores/names')(cached(600)(get_store_names))
    
    # 時間帯別分析
    api_bp.route('/analysis/hourly')(cached(1800)(get_hourly_analysis))
    
    # エリア統計
    api_bp.route('/stats/area')(cached(1800)(get_area_stats))
    
    # ランキング
    api_bp.route('/ranking/genre')(cached(1800)(get_genre_ranking))
    api_bp.route('/ranking/average')(cached(1800)(get_average_ranking))
    api_bp.route('/ranking/popular')(cached(1800)(get_popular_ranking))
    
    # 期間別平均
    api_bp.route('/averages/daily')(cached(3600)(lambda: get_period_averages('daily')))
    api_bp.route('/averages/weekly')(cached(3600)(lambda: get_period_averages('weekly')))
    api_bp.route('/averages/monthly')(cached(3600)(lambda: get_period_averages('monthly')))
    
    # 店舗別平均
    api_bp.route('/averages/stores')(cached(1800)(get_store_averages))
    
    # フィルターオプション
    api_bp.route('/filter-options')(cached(3600)(get_filter_options))

# 既存のAPIエンドポイントと互換性を保つための関数を登録するためのヘルパー関数
def register_legacy_routes(app):
    """既存のAPIエンドポイントと互換性を保つための関数を登録する"""
    # 互換性のためのルート
    app.route('/api/data')(cached(300)(rate_limit(limit=30)(get_current_stores)))
    app.route('/api/history/optimized')(cached(300)(rate_limit(limit=20)(get_store_history_optimized)))
    app.route('/api/store-names')(cached(600)(get_store_names))
    app.route('/api/hourly-analysis')(cached(1800)(get_hourly_analysis))
    app.route('/api/area-stats')(cached(1800)(get_area_stats))
    app.route('/api/ranking/genre')(cached(1800)(get_genre_ranking))
    app.route('/api/ranking/average')(cached(1800)(get_average_ranking))
    app.route('/api/ranking/popular')(cached(1800)(get_popular_ranking))
    app.route('/api/averages/daily')(cached(3600)(lambda: get_period_averages('daily')))
    app.route('/api/averages/weekly')(cached(3600)(lambda: get_period_averages('weekly')))
    app.route('/api/averages/monthly')(cached(3600)(lambda: get_period_averages('monthly')))
    app.route('/api/averages/stores')(cached(1800)(get_store_averages))
    app.route('/api/filter-options')(cached(3600)(get_filter_options))
