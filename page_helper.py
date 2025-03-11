
from flask import request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
import pytz

def paginate_query_results(query, page=1, per_page=20):
    """
    クエリ結果をページネーションする
    
    Args:
        query: SQLAlchemyクエリオブジェクト
        page: 現在のページ番号（1から始まる）
        per_page: 1ページあたりの項目数
        
    Returns:
        ページネーションされたアイテムと、メタデータを含む辞書
    """
    from sqlalchemy import func
    
    # ページネーションパラメータ
    page = max(1, page)  # ページは最低1
    per_page = min(100, max(1, per_page))  # 1〜100の範囲に制限
    
    # 総アイテム数を取得（パフォーマンス最適化: サブクエリカウント）
    total_count = query.with_entities(func.count().label('count')).scalar()
    
    # ページの総数を計算
    total_pages = (total_count + per_page - 1) // per_page
    
    # クエリの実行とスライシング（パフォーマンス最適化: LIMIT/OFFSETを明示的に適用）
    offset = (page - 1) * per_page
    items = query.limit(per_page).offset(offset).all()
    
    # 前後のページがあるかどうか
    has_prev = page > 1
    has_next = page < total_pages
    
    # 結果を返す
    return {
        'items': items,
        'meta': {
            'page': page,
            'per_page': per_page,
            'total_pages': total_pages, 
            'total_count': total_count,
            'has_prev': has_prev,
            'has_next': has_next
        }
    }

def format_store_status(item, timezone=None):
    """
    StoreStatusオブジェクトをJSONシリアライズ可能な辞書に変換する
    
    Args:
        item: StoreStatusオブジェクト
        timezone: 変換に使用するタイムゾーン（オプション）
        
    Returns:
        辞書形式のデータ
    """
    result = {
        'id': item.id,
        'store_name': item.store_name,
        'biz_type': item.biz_type,
        'genre': item.genre,
        'area': item.area,
        'total_staff': item.total_staff,
        'working_staff': item.working_staff,
        'active_staff': item.active_staff,
        'url': item.url,
        'shift_time': item.shift_time
    }
    
    # タイムスタンプをタイムゾーン指定でフォーマット
    if timezone and item.timestamp:
        localized_time = item.timestamp.replace(tzinfo=pytz.utc).astimezone(timezone)
        result['timestamp'] = localized_time.strftime('%Y-%m-%d %H:%M:%S')
    else:
        result['timestamp'] = item.timestamp.strftime('%Y-%m-%d %H:%M:%S') if item.timestamp else None
    
    return result
