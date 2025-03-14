import pytz
from datetime import datetime
from flask import request, abort
from math import ceil

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
    """
    # タイムスタンプにタイムゾーン情報を追加してJSTにする
    timestamp = item.timestamp
    if timestamp and timezone and timestamp.tzinfo is None:
        timestamp = timezone.localize(timestamp)

    formatted_timestamp = timestamp.isoformat() if timestamp else None

    # 稼働率の計算 - Noneチェックを強化
    rate = 0
    working_staff = item.working_staff if hasattr(item, 'working_staff') and item.working_staff is not None else 0
    active_staff = item.active_staff if hasattr(item, 'active_staff') and item.active_staff is not None else 0

    if working_staff > 0:
        # (勤務中 - 待機中) / 勤務中 × 100
        rate = ((working_staff - active_staff) / working_staff) * 100

    # 結果を辞書にまとめる
    return {
        'id': item.id,
        'timestamp': formatted_timestamp,
        'store_name': item.store_name,
        'biz_type': item.biz_type,
        'genre': item.genre,
        'area': item.area,
        'total_staff': item.total_staff,
        'working_staff': item.working_staff,
        'active_staff': item.active_staff,
        'rate': round(rate, 1),
        'url': item.url,
        'shift_time': item.shift_time
    }

def prepare_data_for_integrated_dashboard():
    """統合ダッシュボード用のデータを準備"""
    # 日本のタイムゾーンに設定
    now = datetime.now()
    jst_now = now.strftime('%Y年%m月%d日 %H:%M:%S')

    return {
        'title': '統合ダッシュボード',
        'current_time': jst_now
    }