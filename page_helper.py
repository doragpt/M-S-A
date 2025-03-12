from flask import request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
import pytz

def paginate_query_results(query, page, per_page):
    """
    SQLAlchemy クエリに対してページネーションを適用し、
    結果とメタデータを辞書形式で返す関数。
    パフォーマンス最適化版
    """
    # 1ページあたりの項目数を制限（最大100000 - 制限を大幅に緩和）
    per_page = min(per_page, 100000)

    # ページ番号の妥当性検証
    if page < 1:
        page = 1

    # オフセットとリミットを計算
    offset = (page - 1) * per_page

    # 総数取得前にクエリをクローン
    count_query = query.with_entities(func.count())

    # アイテム件数を別クエリで取得（効率化のため）
    try:
        # SQLite専用の最適化（可能な場合のみ）
        total_items = count_query.scalar() or 0
    except:
        # 一般的なカウント方法
        total_items = count_query.count()

    # 結果を取得
    items = query.offset(offset).limit(per_page).all()

    # 総ページ数を計算
    total_pages = max(1, (total_items + per_page - 1) // per_page)

    # 前後のページがあるかどうかを判定
    has_prev = page > 1
    has_next = page < total_pages

    # 結果とメタデータを辞書にまとめて返す
    return {
        "items": items,
        "meta": {
            "page": page,
            "per_page": per_page,
            "total_items": total_items,
            "total_pages": total_pages,
            "has_prev": has_prev,
            "has_next": has_next
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