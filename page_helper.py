import pytz
from datetime import datetime
from flask import request, abort
from math import ceil
import logging

def paginate_query_results(query, page, per_page, max_per_page=100):
    """
    SQLAlchemy クエリオブジェクトに対してページネーションを適用する

    引数:
        query: SQLAlchemy クエリオブジェクト
        page: ページ番号（1から始まる）
        per_page: 1ページあたりのアイテム数
        max_per_page: 1ページあたりの最大アイテム数

    戻り値:
        ページネーション済みの結果と、ページネーション情報を含む辞書
    """
    # 範囲チェック
    if page < 1:
        page = 1

    # 最大アイテム数を制限
    if per_page > max_per_page:
        per_page = max_per_page

    # 結果を取得
    items = query.limit(per_page).offset((page - 1) * per_page).all()

    # 総アイテム数とページ数を計算
    total_count = query.count()
    total_pages = ceil(total_count / per_page) if per_page > 0 else 0

    # 次のページと前のページがあるかどうか
    has_next = page < total_pages
    has_prev = page > 1

    # レスポンス生成
    return {
        'items': items,
        'meta': {
            'page': page,
            'per_page': per_page,
            'total_count': total_count,
            'total_pages': total_pages,
            'has_next': has_next,
            'has_prev': has_prev
        }
    }

def format_store_status(item, timezone=None):
    """
    StoreStatusモデルのレコードをAPIレスポンス用に整形する関数
    timezonがNoneの場合はタイムゾーンを考慮しない
    
    エラーが発生した場合も有効なデータを返す（例外を発生させない）
    """
    # デフォルト値を用意
    default_response = {
        'id': None,
        'timestamp': None,
        'store_name': '不明',
        'biz_type': '不明',
        'genre': '不明',
        'area': '不明',
        'total_staff': 0,
        'working_staff': 0,
        'active_staff': 0,
        'rate': 0,
        'url': '',
        'shift_time': ''
    }
    
    if not item:
        logging.warning("format_store_status: 入力アイテムがNoneです")
        return default_response
        
    try:
        # 変数をデフォルト値で初期化
        item_id = None
        formatted_timestamp = None
        store_name = '不明'
        biz_type = '不明'
        genre = '不明'
        area = '不明'
        working_staff = 0
        active_staff = 0
        url = ''
        shift_time = ''
        
        # 各フィールドを個別に安全に取得
        try:
            item_id = getattr(item, 'id', None)
        except Exception:
            pass
            
        # タイムスタンプ処理
        try:
            timestamp = getattr(item, 'timestamp', None)
            if timestamp:
                if timezone and timestamp.tzinfo is None:
                    try:
                        timestamp = timezone.localize(timestamp)
                    except Exception:
                        pass
                        
                try:
                    formatted_timestamp = timestamp.isoformat()
                except Exception:
                    formatted_timestamp = str(timestamp)
        except Exception:
            pass
            
        # 文字列フィールド
        try:
            store_name = getattr(item, 'store_name', '不明') or '不明'
        except Exception:
            pass
            
        try:
            biz_type = getattr(item, 'biz_type', '不明') or '不明'
        except Exception:
            pass
            
        try:
            genre = getattr(item, 'genre', '不明') or '不明'
        except Exception:
            pass
            
        try:
            area = getattr(item, 'area', '不明') or '不明'
        except Exception:
            pass
            
        try:
            url = getattr(item, 'url', '') or ''
        except Exception:
            pass
            
        try:
            shift_time = getattr(item, 'shift_time', '') or ''
        except Exception:
            pass
            
        # 数値フィールド
        try:
            working_staff = getattr(item, 'working_staff', 0)
            working_staff = 0 if working_staff is None else int(working_staff)
        except Exception:
            working_staff = 0
            
        try:
            active_staff = getattr(item, 'active_staff', 0)
            active_staff = 0 if active_staff is None else int(active_staff)
        except Exception:
            active_staff = 0
            
        # 稼働率計算
        rate = 0
        if working_staff > 0:
            try:
                rate = ((working_staff - active_staff) / working_staff) * 100
                rate = round(rate, 1)
            except Exception:
                rate = 0
        
        # 結果を辞書にまとめる
        return {
            'id': item_id,
            'timestamp': formatted_timestamp,
            'store_name': store_name,
            'biz_type': biz_type,
            'genre': genre,
            'area': area,
            'total_staff': working_staff + active_staff,
            'working_staff': working_staff,
            'active_staff': active_staff,
            'rate': rate,
            'url': url,
            'shift_time': shift_time
        }
    except Exception as e:
        logging.error(f"形式化エラー: {e}")
        return default_response

def prepare_data_for_integrated_dashboard():
    """統合ダッシュボード用のデータを準備"""
    # 日本のタイムゾーンに設定
    now = datetime.now()
    jst_now = now.strftime('%Y年%m月%d日 %H:%M:%S')

    return {
        'title': '統合ダッシュボード',
        'current_time': jst_now
    }