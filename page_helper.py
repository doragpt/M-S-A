
import pytz
from datetime import datetime

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
    total_pages = (total_count + per_page - 1) // per_page
    
    # 次のページと前のページがあるかどうか
    has_next = page < total_pages
    has_prev = page > 1
    
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
    StoreStatus オブジェクトを JSON 化可能な辞書に変換する
    
    引数:
        item: StoreStatus モデルのオブジェクト
        timezone: タイムゾーンオブジェクト（デフォルトはUTC）
    
    戻り値:
        JSON 化可能な辞書
    """
    timestamp = item.timestamp
    
    # タイムゾーン変換（指定がある場合）
    if timezone:
        if timestamp.tzinfo is None:
            # タイムゾーン情報がない場合はUTCとして扱う
            timestamp = pytz.utc.localize(timestamp)
        timestamp = timestamp.astimezone(timezone)
    
    # フォーマット済みの辞書を返す
    return {
        'id': item.id,
        'timestamp': timestamp.isoformat(),
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
