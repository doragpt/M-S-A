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

    # 結果と情報を辞書にまとめる
    return {
        "items": items,
        "meta": {
            "page": page,
            "per_page": per_page,
            "total_count": total_count,
            "total_pages": total_pages,
            "has_next": has_next,
            "has_prev": has_prev
        }
    }

def format_store_status(item, timezone=None):
    """
    StoreStatus モデルオブジェクトを JSON 形式に変換する

    引数:
        item: StoreStatus オブジェクト
        timezone: タイムゾーン（pytz タイムゾーンオブジェクト）

    戻り値:
        JSON シリアライズ可能な辞書
    """
    # JSTタイムゾーンがない場合はデフォルトで設定
    if timezone is None:
        timezone = pytz.timezone('Asia/Tokyo')

    # タイムスタンプが aware でない場合は JST として扱い、指定されたタイムゾーンに変換
    timestamp = item.timestamp
    if timestamp.tzinfo is None:
        # SQLiteではタイムゾーン情報がないので、JST(+9)として解釈
        jst = pytz.timezone('Asia/Tokyo')
        timestamp = jst.localize(timestamp)
    else:
        # すでに aware な datetime の場合は単に指定されたタイムゾーンに変換
        timestamp = timestamp.astimezone(timezone)

    # 稼働率の計算 (稼働中スタッフがいる場合のみ計算)
    rate = 0
    if item.working_staff > 0:
        # 稼働率 = (勤務中 - 待機中) / 勤務中 × 100
        rate = ((item.working_staff - item.active_staff) / item.working_staff) * 100

    # 結果を辞書にまとめる
    return {
        "id": item.id,
        "timestamp": timestamp.strftime('%Y-%m-%d %H:%M:%S %Z%z'),  # タイムゾーン情報を含めた形式
        "store_name": item.store_name,
        "biz_type": item.biz_type,
        "genre": item.genre,
        "area": item.area,
        "total_staff": item.total_staff,
        "working_staff": item.working_staff,
        "active_staff": item.active_staff,
        "rate": round(rate, 1),  # 小数点以下1桁に丸める
        "url": item.url,
        "shift_time": item.shift_time
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