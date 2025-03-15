
from flask import request, jsonify, current_app as app
from sqlalchemy import func, desc, and_
import pytz
from datetime import datetime, timedelta
from functools import wraps
from flask_caching import Cache

from database import get_db_connection
from models import StoreStatus, DailyAverage, WeeklyAverage, MonthlyAverage, StoreAverage

# キャッシュインスタンスを初期化（app.pyから渡される）
cache = None

def init_cache(cache_instance):
    """キャッシュインスタンスをセット"""
    global cache
    cache = cache_instance

# ----------------------- ヘルパー関数 -----------------------

def format_store_status(item, jst):
    """店舗データをフォーマットする関数"""
    try:
        # タイムスタンプの処理
        timestamp = None
        if hasattr(item, 'timestamp'):
            timestamp = item.timestamp
        elif isinstance(item, dict) and 'timestamp' in item:
            timestamp = item['timestamp']
            
        # ISO形式に変換
        if timestamp:
            if isinstance(timestamp, str):
                try:
                    timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                except ValueError:
                    try:
                        timestamp = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        timestamp = datetime.now(jst)
            
            # タイムゾーン変換
            if timestamp.tzinfo is None:
                timestamp = jst.localize(timestamp)
            else:
                timestamp = timestamp.astimezone(jst)
                
            formatted_timestamp = timestamp.isoformat()
        else:
            formatted_timestamp = datetime.now(jst).isoformat()

        # 店舗データの抽出
        if hasattr(item, 'store_name'):
            # SQLAlchemyモデルの場合
            store_name = item.store_name
            biz_type = item.biz_type
            genre = item.genre
            area = item.area
            total_staff = item.total_staff or 0
            working_staff = item.working_staff or 0
            active_staff = item.active_staff or 0
            url = item.url
            shift_time = item.shift_time
        else:
            # 辞書の場合
            store_name = item.get('store_name', '')
            biz_type = item.get('biz_type', '')
            genre = item.get('genre', '')
            area = item.get('area', '')
            total_staff = item.get('total_staff', 0) or 0
            working_staff = item.get('working_staff', 0) or 0
            active_staff = item.get('active_staff', 0) or 0
            url = item.get('url', '')
            shift_time = item.get('shift_time', '')

        # 稼働率の計算
        rate = 0
        if working_staff > 0:
            rate = ((working_staff - active_staff) / working_staff) * 100
            rate = round(rate, 1)  # 小数点第1位まで

        return {
            'timestamp': formatted_timestamp,
            'store_name': store_name,
            'biz_type': biz_type,
            'genre': genre,
            'area': area,
            'total_staff': total_staff,
            'working_staff': working_staff,
            'active_staff': active_staff,
            'url': url,
            'shift_time': shift_time,
            'rate': rate
        }
    except Exception as e:
        app.logger.error(f"データフォーマットエラー: {e}")
        # エラー時にも最低限のデータを返す
        return {
            'timestamp': datetime.now(jst).isoformat(),
            'store_name': getattr(item, 'store_name', '') if hasattr(item, 'store_name') else item.get('store_name', '不明'),
            'error': str(e)
        }

def error_response(message, status_code=200):
    """エラーレスポンスを統一形式で返す"""
    jst = pytz.timezone('Asia/Tokyo')
    now_jst = datetime.now(jst)
    return jsonify({
        "data": [],
        "meta": {
            "error": message,
            "message": message,
            "current_time": now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
        }
    }), status_code

def success_response(data, meta=None):
    """成功レスポンスを統一形式で返す"""
    jst = pytz.timezone('Asia/Tokyo')
    now_jst = datetime.now(jst)
    
    if meta is None:
        meta = {}
    
    meta["current_time"] = now_jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')
    
    return jsonify({
        "data": data,
        "meta": meta
    })

# APIエンドポイントの実装
# ----------------------- 1. 基本データエンドポイント -----------------------

@app.route('/api/v1/stores/current')
@cache.memoize(timeout=60)  # 1分間キャッシュ
def get_current_stores():
    """
    現在の全店舗データを取得するエンドポイント
    
    クエリパラメータ:
        page: ページ番号（1から開始）
        per_page: 1ページあたりの件数
        biz_type: 業種フィルター
        genre: ジャンルフィルター
        area: エリアフィルター
        search: 店舗名検索
    """
    jst = pytz.timezone('Asia/Tokyo')
    try:
        # キャッシュクリアパラメータ
        if request.args.get('refresh', '0') == '1':
            cache.delete_memoized(get_current_stores)
            
        # クエリパラメータ取得
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)  # 最大100件
        biz_type = request.args.get('biz_type')
        genre = request.args.get('genre')
        area = request.args.get('area')
        search = request.args.get('search')
        
        # SQLiteデータベースに直接接続
        conn = get_db_connection()
        if not conn:
            return error_response("データベース接続エラー")
        
        # 最新レコードを取得するサブクエリ
        query = """
        WITH LatestTimestamps AS (
            SELECT store_name, MAX(timestamp) as max_timestamp
            FROM store_status
            GROUP BY store_name
        )
        SELECT s.*
        FROM store_status s
        JOIN LatestTimestamps lt ON s.store_name = lt.store_name AND s.timestamp = lt.max_timestamp
        WHERE 1=1
        """
        
        params = []
        
        # フィルター条件を追加
        if biz_type and biz_type != 'all':
            query += " AND s.biz_type = ?"
            params.append(biz_type)
            
        if genre and genre != 'all':
            query += " AND s.genre = ?"
            params.append(genre)
            
        if area and area != 'all':
            query += " AND s.area = ?"
            params.append(area)
            
        if search:
            query += " AND s.store_name LIKE ?"
            params.append(f"%{search}%")
        
        # カウントクエリ実行（総件数取得）
        count_query = f"""
        SELECT COUNT(*) FROM ({query}) as count_table
        """
        
        try:
            total_count = conn.execute(count_query, params).fetchone()[0]
        except Exception as e:
            app.logger.error(f"カウントクエリエラー: {e}")
            total_count = 0
        
        # ページネーション
        if page > 0:
            query += f" LIMIT {per_page} OFFSET {(page - 1) * per_page}"
            
        # メインクエリ実行
        try:
            results = list(conn.execute(query, params).fetchall())
        except Exception as e:
            app.logger.error(f"メインクエリエラー: {e}")
            conn.close()
            return error_response(f"データ取得エラー: {e}")
            
        conn.close()
        
        # 結果が0件の場合
        if len(results) == 0:
            return success_response([], {
                "total_count": 0,
                "message": "データが見つかりません"
            })
            
        # 集計値の計算
        total_store_count = len(results)
        total_working_staff = 0
        total_active_staff = 0
        valid_stores = 0
        max_rate = 0
        
        for item in results:
            try:
                working_staff = item['working_staff'] if item['working_staff'] is not None else 0
                active_staff = item['active_staff'] if item['active_staff'] is not None else 0
                
                if working_staff > 0:
                    valid_stores += 1
                    total_working_staff += working_staff
                    total_active_staff += active_staff
                    
                    rate = ((working_staff - active_staff) / working_staff) * 100
                    max_rate = max(max_rate, rate)
            except (ValueError, TypeError, ZeroDivisionError, KeyError) as e:
                app.logger.debug(f"集計スキップ: {e}")
                continue
                
        # 全体平均稼働率の計算
        avg_rate = 0
        if valid_stores > 0 and total_working_staff > 0:
            avg_rate = ((total_working_staff - total_active_staff) / total_working_staff) * 100
            
        # データをフォーマット
        formatted_data = [format_store_status(item, jst) for item in results]
        
        # ページネーション情報
        meta = {
            "total_count": total_count,
            "valid_stores": valid_stores,
            "avg_rate": round(avg_rate, 1),
            "max_rate": round(max_rate, 1),
            "total_working_staff": total_working_staff,
            "total_active_staff": total_active_staff
        }
        
        if page > 0:
            total_pages = (total_count + per_page - 1) // per_page if per_page > 0 else 1
            meta.update({
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1
            })
            
        return success_response(formatted_data, meta)
        
    except Exception as e:
        app.logger.error(f"店舗データ取得エラー: {e}")
        return error_response(f"データ取得中にエラーが発生しました: {e}")

@app.route('/api/v1/stores/history')
@cache.memoize(timeout=300)  # 5分間キャッシュ
def get_store_history():
    """
    店舗の履歴データを取得するエンドポイント
    
    クエリパラメータ:
        store: 店舗名（必須）
        start_date: 開始日（YYYY-MM-DD形式）
        end_date: 終了日（YYYY-MM-DD形式）
        optimize: 大量データの場合に間引くかどうか（1=有効, 0=無効）
    """
    jst = pytz.timezone('Asia/Tokyo')
    try:
        # パラメータ取得
        store = request.args.get('store')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        optimize = request.args.get('optimize', '1') == '1'  # デフォルトで最適化ON
        
        # キャッシュクリアパラメータ
        if request.args.get('refresh', '0') == '1':
            cache.delete_memoized(get_store_history)
            
        # 店舗名は必須
        if not store:
            return error_response("店舗名が指定されていません")
            
        # SQLiteに直接接続
        conn = get_db_connection()
        if not conn:
            return error_response("データベース接続エラー")
            
        # クエリを構築
        query = "SELECT * FROM store_status WHERE store_name = ?"
        params = [store]
        
        # 日付フィルター
        if start_date:
            query += " AND date(timestamp) >= date(?)"
            params.append(start_date)
            
        if end_date:
            query += " AND date(timestamp) <= date(?)"
            params.append(end_date)
            
        # 時間順にソート
        query += " ORDER BY timestamp ASC"
        
        # クエリ実行
        try:
            results = list(conn.execute(query, params).fetchall())
        except Exception as e:
            app.logger.error(f"履歴クエリエラー: {e}")
            conn.close()
            return error_response(f"履歴データ取得エラー: {e}")
            
        conn.close()
        
        if len(results) == 0:
            return success_response([], {
                "message": f"店舗 '{store}' の履歴データが見つかりませんでした"
            })
            
        # データの間引き処理（最適化）
        if optimize and len(results) > 100:
            # データが大量の場合は間引き
            sample_rate = max(1, len(results) // 100)
            results = results[::sample_rate]
            
        # データをフォーマット
        formatted_data = [format_store_status(item, jst) for item in results]
        
        # メタデータ
        meta = {
            "store": store,
            "total_count": len(formatted_data),
            "start_date": start_date if start_date else formatted_data[0]['timestamp'].split('T')[0],
            "end_date": end_date if end_date else formatted_data[-1]['timestamp'].split('T')[0],
            "is_sampled": optimize and len(results) > 100
        }
        
        return success_response(formatted_data, meta)
        
    except Exception as e:
        app.logger.error(f"履歴データ取得エラー: {e}")
        return error_response(f"履歴データ取得中にエラーが発生しました: {e}")

@app.route('/api/v1/store-names')
@cache.memoize(timeout=600)  # 10分間キャッシュ
def get_store_names():
    """すべての店舗名リストを取得するエンドポイント"""
    try:
        conn = get_db_connection()
        if not conn:
            return error_response("データベース接続エラー")
            
        query = "SELECT DISTINCT store_name FROM store_status ORDER BY store_name ASC"
        
        try:
            results = conn.execute(query).fetchall()
            store_names = [row[0] for row in results if row[0]]
        except Exception as e:
            app.logger.error(f"店舗名取得クエリエラー: {e}")
            conn.close()
            return error_response(f"店舗名取得エラー: {e}")
            
        conn.close()
        
        return success_response(store_names, {
            "count": len(store_names)
        })
        
    except Exception as e:
        app.logger.error(f"店舗名取得エラー: {e}")
        return error_response(f"店舗名取得中にエラーが発生しました: {e}")

# ----------------------- 2. 分析・ランキングエンドポイント -----------------------

@app.route('/api/v1/analysis/hourly')
@cache.memoize(timeout=600)  # 10分間キャッシュ
def get_hourly_analysis():
    """
    時間帯別分析データを取得するエンドポイント
    
    クエリパラメータ:
        store: 特定店舗名（省略時は全店舗の平均）
        biz_type: 業種フィルター
    """
    try:
        # パラメータ取得
        store = request.args.get('store')
        biz_type = request.args.get('biz_type')
        
        # キャッシュクリアパラメータ
        if request.args.get('refresh', '0') == '1':
            cache.delete_memoized(get_hourly_analysis)
            
        conn = get_db_connection()
        if not conn:
            return error_response("データベース接続エラー")
            
        # クエリを構築
        query = """
        SELECT 
            strftime('%H', timestamp) AS hour,
            AVG((working_staff - active_staff) * 100.0 / working_staff) AS avg_rate,
            COUNT(*) AS sample_count
        FROM store_status
        WHERE working_staff > 0
        """
        
        params = []
        
        if store:
            query += " AND store_name = ?"
            params.append(store)
            
        if biz_type:
            query += " AND biz_type = ?"
            params.append(biz_type)
            
        query += " GROUP BY strftime('%H', timestamp) ORDER BY hour"
        
        # クエリ実行
        try:
            results = list(conn.execute(query, params).fetchall())
        except Exception as e:
            app.logger.error(f"時間帯分析クエリエラー: {e}")
            conn.close()
            return error_response(f"時間帯分析データ取得エラー: {e}")
            
        conn.close()
        
        # 0-23時の全時間帯データを生成
        hourly_data = []
        for hour in range(24):
            hour_str = f"{hour:02d}"
            
            # その時間帯のデータを探す
            found = False
            for row in results:
                if row['hour'] == hour_str:
                    try:
                        avg_rate = float(row['avg_rate']) if row['avg_rate'] is not None else 0
                    except (ValueError, TypeError):
                        avg_rate = 0
                        
                    hourly_data.append({
                        'hour': hour,
                        'hour_str': f"{hour}:00",
                        'avg_rate': round(avg_rate, 1),
                        'sample_count': row['sample_count']
                    })
                    found = True
                    break
                    
            # データがない時間帯は0で埋める
            if not found:
                hourly_data.append({
                    'hour': hour,
                    'hour_str': f"{hour}:00",
                    'avg_rate': 0,
                    'sample_count': 0
                })
                
        # 分析結果の生成
        analysis = {}
        
        # ピーク時間と空き時間
        sorted_by_rate = sorted(hourly_data, key=lambda x: x['avg_rate'], reverse=True)
        analysis['peak_hours'] = sorted_by_rate[:3]  # 上位3つ
        analysis['quiet_hours'] = sorted_by_rate[-3:]  # 下位3つ
        
        # 時間帯グループの平均
        time_groups = {
            'morning': {'hours': [6, 7, 8, 9], 'avg': 0},
            'noon': {'hours': [10, 11, 12, 13], 'avg': 0},
            'afternoon': {'hours': [14, 15, 16, 17], 'avg': 0},
            'evening': {'hours': [18, 19, 20, 21], 'avg': 0},
            'night': {'hours': [22, 23, 0, 1, 2, 3, 4, 5], 'avg': 0}
        }
        
        for group_name, group_data in time_groups.items():
            hours = group_data['hours']
            group_values = [item['avg_rate'] for item in hourly_data if item['hour'] in hours]
            if group_values:
                time_groups[group_name]['avg'] = sum(group_values) / len(group_values)
                
        analysis['time_groups'] = time_groups
        
        # 全体平均
        all_rates = [item['avg_rate'] for item in hourly_data]
        analysis['overall_avg'] = sum(all_rates) / len(all_rates) if all_rates else 0
        
        return success_response(hourly_data, {
            "store": store if store else "全店舗",
            "biz_type": biz_type if biz_type else "全業種",
            "analysis": analysis
        })
        
    except Exception as e:
        app.logger.error(f"時間帯分析エラー: {e}")
        return error_response(f"時間帯分析中にエラーが発生しました: {e}")

@app.route('/api/v1/analysis/area')
@cache.memoize(timeout=600)  # 10分間キャッシュ
def get_area_stats():
    """エリア別統計データを取得するエンドポイント"""
    try:
        # キャッシュクリアパラメータ
        if request.args.get('refresh', '0') == '1':
            cache.delete_memoized(get_area_stats)
            
        conn = get_db_connection()
        if not conn:
            return error_response("データベース接続エラー")
            
        # 最新のデータを使ったエリア統計
        query = """
        WITH LatestTimestamps AS (
            SELECT store_name, MAX(timestamp) as max_timestamp
            FROM store_status
            GROUP BY store_name
        )
        SELECT 
            s.area,
            COUNT(DISTINCT s.store_name) as store_count,
            AVG((s.working_staff - s.active_staff) * 100.0 / s.working_staff) as avg_rate
        FROM store_status s
        JOIN LatestTimestamps lt ON s.store_name = lt.store_name AND s.timestamp = lt.max_timestamp
        WHERE s.working_staff > 0 AND s.area IS NOT NULL AND s.area != ''
        GROUP BY s.area
        ORDER BY avg_rate DESC
        """
        
        try:
            results = list(conn.execute(query).fetchall())
        except Exception as e:
            app.logger.error(f"エリア統計クエリエラー: {e}")
            conn.close()
            return error_response(f"エリア統計データ取得エラー: {e}")
            
        conn.close()
        
        # 結果をフォーマット
        area_data = []
        for row in results:
            try:
                avg_rate = float(row['avg_rate']) if row['avg_rate'] is not None else 0
            except (ValueError, TypeError):
                avg_rate = 0
                
            area_data.append({
                'area': row['area'] if row['area'] else '不明',
                'store_count': row['store_count'],
                'avg_rate': round(avg_rate, 1)
            })
            
        return success_response(area_data, {
            "count": len(area_data)
        })
        
    except Exception as e:
        app.logger.error(f"エリア統計エラー: {e}")
        return error_response(f"エリア統計中にエラーが発生しました: {e}")

@app.route('/api/v1/ranking/genre')
@cache.memoize(timeout=600)  # 10分間キャッシュ
def get_genre_ranking():
    """
    ジャンル別ランキングを取得するエンドポイント
    
    クエリパラメータ:
        biz_type: 業種でフィルタリング
    """
    try:
        # パラメータ取得
        biz_type = request.args.get('biz_type')
        
        # キャッシュクリアパラメータ
        if request.args.get('refresh', '0') == '1':
            cache.delete_memoized(get_genre_ranking)
            
        conn = get_db_connection()
        if not conn:
            return error_response("データベース接続エラー")
            
        # クエリを構築
        query = """
        SELECT 
            genre,
            COUNT(DISTINCT store_name) as store_count,
            AVG((working_staff - active_staff) * 100.0 / working_staff) as avg_rate
        FROM store_status
        WHERE working_staff > 0 AND genre IS NOT NULL AND genre != ''
        """
        
        params = []
        
        if biz_type:
            query += " AND biz_type = ?"
            params.append(biz_type)
            
        query += " GROUP BY genre ORDER BY avg_rate DESC"
        
        # クエリ実行
        try:
            results = list(conn.execute(query, params).fetchall())
        except Exception as e:
            app.logger.error(f"ジャンルランキングクエリエラー: {e}")
            conn.close()
            return error_response(f"ジャンルランキング取得エラー: {e}")
            
        conn.close()
        
        if len(results) == 0:
            return success_response([{
                "genre": "該当データなし",
                "store_count": 0,
                "avg_rate": 0.0
            }], {
                "biz_type": biz_type if biz_type else "全業種",
                "message": "該当するジャンルデータがありません"
            })
            
        # 結果をフォーマット
        genre_data = []
        for row in results:
            try:
                avg_rate = float(row['avg_rate']) if row['avg_rate'] is not None else 0
            except (ValueError, TypeError):
                avg_rate = 0
                
            genre_data.append({
                'genre': row['genre'] if row['genre'] else '不明',
                'store_count': row['store_count'],
                'avg_rate': round(avg_rate, 1)
            })
            
        return success_response(genre_data, {
            "biz_type": biz_type if biz_type else "全業種",
            "count": len(genre_data)
        })
        
    except Exception as e:
        app.logger.error(f"ジャンルランキングエラー: {e}")
        return error_response(f"ジャンルランキング中にエラーが発生しました: {e}")

@app.route('/api/v1/ranking/popular')
@cache.memoize(timeout=600)  # 10分間キャッシュ
def get_popular_ranking():
    """
    人気店舗ランキングを取得するエンドポイント
    
    クエリパラメータ:
        period: 'daily', 'weekly', 'monthly', 'all'（デフォルトは'weekly'）
        biz_type: 業種フィルター
        limit: 上位何件を表示するか（デフォルト20件）
    """
    try:
        # パラメータ取得
        period = request.args.get('period', 'weekly')
        biz_type = request.args.get('biz_type')
        limit = request.args.get('limit', 20, type=int)
        
        # キャッシュクリアパラメータ
        if request.args.get('refresh', '0') == '1':
            cache.delete_memoized(get_popular_ranking)
            
        # 期間に基づいてテーブル選択
        table_name = {
            'daily': 'daily_averages',
            'weekly': 'weekly_averages',
            'monthly': 'monthly_averages',
            'all': 'store_averages'
        }.get(period, 'weekly_averages')
        
        conn = get_db_connection()
        if not conn:
            return error_response("データベース接続エラー")
            
        # クエリを構築
        query = f"""
        SELECT 
            store_name,
            avg_rate,
            sample_count,
            biz_type,
            genre,
            area
        FROM {table_name}
        WHERE sample_count > 0
        """
        
        params = []
        
        if biz_type:
            query += " AND biz_type = ?"
            params.append(biz_type)
            
        query += " ORDER BY avg_rate DESC"
        
        if limit > 0:
            query += f" LIMIT {limit}"
            
        # クエリ実行
        try:
            results = list(conn.execute(query, params).fetchall())
        except Exception as e:
            app.logger.error(f"人気店舗ランキングクエリエラー: {e}")
            conn.close()
            return error_response(f"人気店舗ランキング取得エラー: {e}")
            
        conn.close()
        
        # 結果をフォーマット
        ranking_data = []
        for idx, row in enumerate(results, 1):
            try:
                avg_rate = float(row['avg_rate']) if row['avg_rate'] is not None else 0
            except (ValueError, TypeError):
                avg_rate = 0
                
            ranking_data.append({
                'rank': idx,
                'store_name': row['store_name'],
                'avg_rate': round(avg_rate, 1),
                'sample_count': row['sample_count'],
                'biz_type': row['biz_type'] if row['biz_type'] else '不明',
                'genre': row['genre'] if row['genre'] else '不明',
                'area': row['area'] if row['area'] else '不明'
            })
            
        # 期間表示名
        period_names = {
            'daily': '直近24時間',
            'weekly': '直近1週間',
            'monthly': '直近1ヶ月',
            'all': '全期間'
        }
            
        return success_response(ranking_data, {
            "period": period,
            "period_name": period_names.get(period, '不明'),
            "biz_type": biz_type if biz_type else "全業種",
            "count": len(ranking_data)
        })
        
    except Exception as e:
        app.logger.error(f"人気店舗ランキングエラー: {e}")
        return error_response(f"人気店舗ランキング中にエラーが発生しました: {e}")

@app.route('/api/v1/stats/aggregated')
@cache.memoize(timeout=3600)  # 1時間キャッシュ
def get_aggregated_stats():
    """日付ごとに集計された平均稼働率データを取得するエンドポイント"""
    try:
        # キャッシュクリアパラメータ
        if request.args.get('refresh', '0') == '1':
            cache.delete_memoized(get_aggregated_stats)
            
        conn = get_db_connection()
        if not conn:
            return error_response("データベース接続エラー")
            
        # 日付ごとの平均稼働率を計算するクエリ
        query = """
        SELECT 
            date(timestamp) as date,
            AVG((working_staff - active_staff) * 100.0 / working_staff) as avg_rate,
            COUNT(*) as sample_count
        FROM store_status
        WHERE working_staff > 0
        GROUP BY date(timestamp)
        ORDER BY date(timestamp)
        """
        
        # クエリ実行
        try:
            results = list(conn.execute(query).fetchall())
        except Exception as e:
            app.logger.error(f"集計データクエリエラー: {e}")
            conn.close()
            return error_response(f"集計データ取得エラー: {e}")
            
        conn.close()
        
        # 結果をフォーマット
        aggregated_data = []
        for row in results:
            if not row['date']:
                continue
                
            try:
                avg_rate = 0.0
                if row['avg_rate'] is not None:
                    avg_rate = float(row['avg_rate'])
                    
                sample_count = row['sample_count'] or 0
                
                aggregated_data.append({
                    'date': row['date'],
                    'avg_rate': round(avg_rate, 1),
                    'sample_count': sample_count
                })
            except (ValueError, TypeError) as e:
                app.logger.warning(f"データ変換エラー: {e}, レコード: {row}")
                continue
                
        return success_response(aggregated_data, {
            "count": len(aggregated_data)
        })
        
    except Exception as e:
        app.logger.error(f"集計データエラー: {e}")
        return error_response(f"集計データ取得中にエラーが発生しました: {e}")
