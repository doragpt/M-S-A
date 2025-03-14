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

def format_store_status(status, timezone=None):
    """
    StoreStatus オブジェクトまたは辞書を整形して返す関数
    -status: StoreStatus オブジェクトまたは辞書
    -timezone: タイムゾーン（オプション）
    """
    # 時間情報のローカライズ
    if timezone is None:
        timezone = pytz.timezone('Asia/Tokyo')

    try:
        # StoreStatus オブジェクトをディクショナリに変換
        if hasattr(status, '__dict__'):
            status_dict = status.__dict__.copy()
            if '_sa_instance_state' in status_dict:
                del status_dict['_sa_instance_state']
        else:
            # SQLiteの Row オブジェクトの場合（辞書っぽいオブジェクト）
            if hasattr(status, 'keys'):
                status_dict = {key: status[key] for key in status.keys()}
            else:
                status_dict = dict(status)

        # タイムスタンプをローカライズ
        if 'timestamp' in status_dict and status_dict['timestamp']:
            timestamp = status_dict['timestamp']
            if isinstance(timestamp, str):
                # 文字列の場合はパース
                try:
                    timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                except ValueError:
                    try:
                        timestamp = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        # その他の形式も試す
                        formats = ['%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f']
                        for fmt in formats:
                            try:
                                timestamp = datetime.strptime(timestamp, fmt)
                                break
                            except ValueError:
                                continue

            # タイムゾーン情報がない場合は追加
            if isinstance(timestamp, datetime):
                if timestamp.tzinfo is None:
                    timestamp = timezone.localize(timestamp)
                # 指定されたタイムゾーンに変換
                else:
                    timestamp = timestamp.astimezone(timezone)
                status_dict['timestamp'] = timestamp

        # 整形されたディクショナリを返す
        formatted = {
            'id': status_dict.get('id'),
            'timestamp': status_dict.get('timestamp').isoformat() if isinstance(status_dict.get('timestamp'), datetime) else None,
            'store_name': status_dict.get('store_name', '不明') or '不明',
            'biz_type': status_dict.get('biz_type', '不明') or '不明',
            'genre': status_dict.get('genre', '不明') or '不明',
            'area': status_dict.get('area', '不明') or '不明',
            'total_staff': int(status_dict.get('total_staff', 0) or 0),
            'working_staff': int(status_dict.get('working_staff', 0) or 0),
            'active_staff': int(status_dict.get('active_staff', 0) or 0),
            'url': status_dict.get('url', '') or '',
            'shift_time': status_dict.get('shift_time', '') or ''
        }

        # 稼働率を計算
        if formatted['working_staff'] > 0:
            formatted['rate'] = round((formatted['working_staff'] - formatted['active_staff']) / formatted['working_staff'] * 100, 1)
        else:
            formatted['rate'] = 0

        # total_staffが設定されていない場合はworking_staffとactive_staffから計算
        if formatted['total_staff'] == 0:
            formatted['total_staff'] = formatted['working_staff'] + formatted['active_staff']

        return formatted
    except Exception as e:
        from app import app
        app.logger.error(f"データフォーマットエラー: {e}")
        return None

def prepare_data_for_integrated_dashboard():
    """統合ダッシュボード用のデータを準備"""
    # 日本のタイムゾーンに設定
    now = datetime.now()
    jst_now = now.strftime('%Y年%m月%d日 %H:%M:%S')

    return {
        'title': '統合ダッシュボード',
        'current_time': jst_now
    }