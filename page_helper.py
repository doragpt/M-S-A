
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
    
    # メタデータ
    meta = {
        'page': page,
        'per_page': per_page,
        'total_count': total_count,
        'total_pages': total_pages,
        'has_prev': has_prev,
        'has_next': has_next
    }
    
    return {
        'items': items,
        'meta': meta
    }

def format_store_status(store_status, jst=pytz.timezone('Asia/Tokyo')):
    """
    StoreStatusモデルを辞書形式にフォーマット
    
    Args:
        store_status: StoreStatusモデルのインスタンス
        jst: 日本標準時のタイムゾーンオブジェクト
        
    Returns:
        フォーマットされた辞書
    """
    return {
        "id": store_status.id,
        "timestamp": store_status.timestamp.astimezone(jst).isoformat(),
        "store_name": store_status.store_name,
        "biz_type": store_status.biz_type,
        "genre": store_status.genre,
        "area": store_status.area,
        "total_staff": store_status.total_staff,
        "working_staff": store_status.working_staff,
        "active_staff": store_status.active_staff,
        "url": store_status.url,
        "shift_time": store_status.shift_time
    }
