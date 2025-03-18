import os
import logging
import json
from datetime import datetime, timedelta
from functools import wraps
from typing import Dict, List, Union, Optional, Any, Tuple
import pytz

from flask import Blueprint, request, jsonify, current_app, send_file
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
            WITH latest_timestamps AS (
                SELECT store_name, MAX(timestamp) as max_timestamp
                FROM store_status
                GROUP BY store_name
            )
            SELECT 
                s.store_name,
                s.biz_type,
                s.genre,
                s.area,
                s.total_staff,
                s.working_staff,
                s.active_staff,
                s.timestamp,
                s.url
            FROM store_status s
            JOIN latest_timestamps lt 
                ON s.store_name = lt.store_name 
                AND s.timestamp = lt.max_timestamp
            WHERE s.store_name IS NOT NULL
            AND s.area IS NOT NULL
            """
            results = conn.execute(query).fetchall()

            if not results:
                return jsonify({
                    'status': 'error',
                    'message': 'データが見つかりません',
                    'error_code': 'NO_DATA',
                    'data': []
                }), 404

            stores = []
            for r in results:
                r_dict = dict(r)
                if not r_dict.get('store_name') or not r_dict.get('area'):
                    continue

                working_staff = int(r_dict.get('working_staff', 0))
                active_staff = int(r_dict.get('active_staff', 0))
                total_staff = int(r_dict.get('total_staff', 0))

                rate = 0
                if working_staff > 0:
                    rate = round(((working_staff - active_staff) / working_staff) * 100, 1)

                store = {
                    'store_name': r_dict['store_name'],
                    'biz_type': r_dict.get('biz_type', ''),
                    'genre': r_dict.get('genre', ''),
                    'area': r_dict['area'],
                    'total_staff': total_staff,
                    'working_staff': working_staff,
                    'active_staff': active_staff,
                    'timestamp': r_dict['timestamp'].isoformat() if r_dict.get('timestamp') else None,
                    'rate': rate
                }
                stores.append(store)

            return jsonify({
                'status': 'success',
                'data': {
                    'meta': {
                        'last_updated': datetime.now(pytz.UTC).isoformat(),
                        'total_count': len(stores)
                    },
                    'stores': stores
                }
            })
        except Exception as e:
            logger.error(f"APIエラー: {str(e)}")
            return jsonify({
                'status': 'error',
                'message': 'データの取得に失敗しました',
                'data': None
            }), 500

    @bp.route('/history')
    def get_store_history():
        """店舗の履歴データを取得"""
        try:
            store = request.args.get('store')
            conn = get_db_connection()

            if store:
                query = """
                SELECT * FROM store_status 
                WHERE store_name = ?
                ORDER BY timestamp DESC
                LIMIT 500
                """
                results = conn.execute(query, [store]).fetchall()
            else:
                query = """
                SELECT * FROM store_status 
                ORDER BY timestamp DESC
                LIMIT 500
                """
                results = conn.execute(query).fetchall()

            history = [{
                'store_name': r['store_name'],
                'timestamp': r['timestamp'],
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


    @bp.route('/history/optimized')
    def get_store_history_optimized():
        """店舗の履歴データを取得"""
        try:
            start_date = request.args.get('start_date')
            end_date = request.args.get('end_date')
            store = request.args.get('store', '')

            if not all([start_date, end_date]):
                return jsonify({
                    'status': 'error',
                    'message': '開始日と終了日を指定してください',
                    'data': []
                }), 400

            try:
                # 現在時刻を取得してデフォルトの検索期間を設定
                now = datetime.now(pytz.UTC)

                # 指定された日付があればそれを使用
                if start_date and end_date:
                    # naiveなdatetimeを作成してUTCに変換
                    start = datetime.strptime(f"{start_date} 00:00:00", '%Y-%m-%d %H:%M:%S')
                    end = datetime.strptime(f"{end_date} 23:59:59", '%Y-%m-%d %H:%M:%S')
                    start = start.replace(tzinfo=pytz.UTC)
                    end = end.replace(tzinfo=pytz.UTC)

                    # 2025年のデータなので未来の日付チェックは不要
                else:
                    # デフォルトは現在時刻から過去7日間
                    end = now
                    start = now - timedelta(days=7)

                # デバッグログ
                logger.debug(f"検索期間: {start} - {end}")
                logger.debug(f"現在時刻(UTC): {now}")

            except ValueError as e:
                logger.error(f"日付変換エラー: {e}")
                return jsonify({
                    'status': 'error',
                    'message': '日付形式が無効です（YYYY-MM-DD）',
                    'data': []
                }), 400

            conn = get_db_connection()
            query = """
            SELECT 
                store_name,
                timestamp,
                working_staff,
                active_staff,
                total_staff,
                biz_type,
                genre,
                area
            FROM store_status 
            WHERE timestamp BETWEEN ? AND ?
            """
            params = [start, end]

            if store:
                query += " AND store_name = ?"
                params.append(store)

            query += " ORDER BY timestamp"
            results = conn.execute(query, params).fetchall()

            history = [{
                'store_name': r['store_name'],
                'timestamp': r['timestamp'].isoformat() if r['timestamp'] else None,
                'working_staff': int(r['working_staff'] or 0),
                'active_staff': int(r['active_staff'] or 0),
                'total_staff': int(r['total_staff'] or 0)
            } for r in results]

            return jsonify({
                'status': 'success',
                'data': history
            })

        except Exception as e:
            logger.error(f"履歴データ取得エラー: {str(e)}")
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 500

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
            limit = request.args.get('limit', default=20, type=int)
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
            """

            results = conn.execute(query).fetchall()
            data = [{
                'store_name': r['store_name'],
                'avg_rate': round(r['avg_rate'], 1),
                'weeks_count': r['weeks_count']
            } for r in results[:limit]]
            data = [{
                'store_name': r['store_name'],
                'avg_rate': round(r['avg_rate'], 1),
                'weeks_count': r['weeks_count']
            } for r in results]

            return jsonify({'status': 'success', 'data': data})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @bp.route('/averages/daily')
    def get_daily_averages():
        """日次平均データを取得"""
        try:
            conn = get_db_connection()
            query = """
            SELECT date(timestamp) as date,
                   AVG(CASE WHEN working_staff > 0 
                       THEN CAST((working_staff - active_staff) AS FLOAT) / working_staff * 100 
                       ELSE 0 END) as avg_rate,
                   COUNT(DISTINCT store_name) as store_count
            FROM store_status
            GROUP BY date(timestamp)
            ORDER BY date DESC
            LIMIT 30
            """
            results = conn.execute(query).fetchall()
            data = [{
                'date': r['date'],
                'avg_rate': round(r['avg_rate'], 1),
                'store_count': r['store_count']
            } for r in results]
            return jsonify({'status': 'success', 'data': data})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @bp.route('/averages/monthly')
    def get_monthly_averages():
        """月次平均データを取得"""
        try:
            conn = get_db_connection()
            query = """
            SELECT strftime('%Y-%m', timestamp) as month,
                   AVG(CASE WHEN working_staff > 0 
                       THEN CAST((working_staff - active_staff) AS FLOAT) / working_staff * 100 
                       ELSE 0 END) as avg_rate,
                   COUNT(DISTINCT store_name) as store_count
            FROM store_status
            GROUP BY strftime('%Y-%m', timestamp)
            ORDER BY month DESC
            LIMIT 12
            """
            results = conn.execute(query).fetchall()
            data = [{
                'month': r['month'],
                'avg_rate': round(r['avg_rate'], 1),
                'store_count': r['store_count']
            } for r in results]
            return jsonify({'status': 'success', 'data': data})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @bp.route('/averages/stores')
    def get_store_averages():
        """店舗ごとの平均データを取得"""
        try:
            conn = get_db_connection()
            query = """
            SELECT store_name,
                   AVG(CASE WHEN working_staff > 0 
                       THEN CAST((working_staff - active_staff) AS FLOAT) / working_staff * 100 
                       ELSE 0 END) as avg_rate,
                   COUNT(*) as sample_count
            FROM store_status
            GROUP BY store_name
            HAVING sample_count >= 5
            ORDER BY avg_rate DESC
            LIMIT 50
            """
            results = conn.execute(query).fetchall()
            data = [{
                'store_name': r['store_name'],
                'avg_rate': round(r['avg_rate'], 1),
                'sample_count': r['sample_count']
            } for r in results]
            return jsonify({'status': 'success', 'data': data})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @bp.route('/report/all-stores/excel', methods=['GET'])
    def generate_all_stores_excel_report():
        """全店舗のExcelレポートを生成して返す"""
        try:
            # 全店舗データの取得
            conn = get_db_connection()
            stores_data = conn.execute("""
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
                ORDER BY s.store_name
            """).fetchall()

            if not stores_data:
                return jsonify({'status': 'error', 'message': '店舗データが見つかりません'}), 404

            # データの整形
            stores_list = []
            for store in stores_data:
                store_dict = dict(store)
                working_staff = int(store_dict.get('working_staff', 0))
                active_staff = int(store_dict.get('active_staff', 0))
                rate = 0
                if working_staff > 0:
                    rate = round(((working_staff - active_staff) / working_staff) * 100, 1)
                store_dict['rate'] = rate
                stores_list.append(store_dict)

            try:
                # レポート生成
                from report_generator import ReportGenerator
                generator = ReportGenerator()
                report_path = "/tmp/all_stores_report.xlsx"
                
                # レポートディレクトリの存在確認
                os.makedirs(os.path.dirname(report_path), exist_ok=True)
                
                generated_path = generator.generate_all_stores_report(stores_list, report_path)

                if not os.path.exists(generated_path):
                    raise FileNotFoundError("Excelファイルの生成に失敗しました")

                # レポートの送信
                return send_file(
                    generated_path,
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    as_attachment=True,
                    download_name=f"all_stores_report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
                )
            except Exception as e:
                logger.error(f"PDFレポート生成エラー: {str(e)}")
                return jsonify({
                    'status': 'error',
                    'message': f'PDFレポートの生成に失敗しました: {str(e)}'
                }), 500

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