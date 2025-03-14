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
    if not item:
        logging.warning("format_store_status: 入力アイテムがNoneです")
        return {
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
        
    try:
        # タイムスタンプにタイムゾーン情報を追加してJSTにする
        timestamp = getattr(item, 'timestamp', None)
        formatted_timestamp = None
        
        if timestamp:
            try:
                if timezone and timestamp.tzinfo is None:
                    timestamp = timezone.localize(timestamp)
                formatted_timestamp = timestamp.isoformat()
            except Exception as ts_err:
                logging.error(f"タイムスタンプ変換エラー: {ts_err}, 原値: {timestamp}")
                # フォールバック: 文字列化
                formatted_timestamp = str(timestamp)

        # 稼働率の計算 - ゼロ除算エラー対策を追加
        rate = 0
        working_staff = getattr(item, 'working_staff', 0)
        active_staff = getattr(item, 'active_staff', 0)
        
        # None値の処理
        working_staff = 0 if working_staff is None else working_staff
        active_staff = 0 if active_staff is None else active_staff

        if working_staff > 0:
            try:
                rate = ((working_staff - active_staff) / working_staff) * 100
            except (ZeroDivisionError, TypeError) as calc_err:
                logging.warning(f"稼働率計算エラー: {calc_err}, working_staff={working_staff}, active_staff={active_staff}")
                rate = 0

        # 安全に属性を取得
        store_name = getattr(item, 'store_name', '不明')
        biz_type = getattr(item, 'biz_type', '不明') or '不明'
        genre = getattr(item, 'genre', '不明') or '不明'
        area = getattr(item, 'area', '不明') or '不明'
        item_id = getattr(item, 'id', None)
        url = getattr(item, 'url', '') or ''
        shift_time = getattr(item, 'shift_time', '') or ''
        
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
            'rate': round(rate, 1),
            'url': url,
            'shift_time': shift_time
        }
    except Exception as e:
        logging.error(f"形式化エラー: {e}, item: {item}")
        # 最低限のデータを返す
        try:
            return {
                'id': getattr(item, 'id', None),
                'timestamp': str(getattr(item, 'timestamp', None)),
                'store_name': getattr(item, 'store_name', '不明'),
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
        except:
            # 完全にフォールバック
            return {
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

def prepare_data_for_integrated_dashboard():
    """統合ダッシュボード用のデータを準備"""
    # 日本のタイムゾーンに設定
    now = datetime.now()
    jst_now = now.strftime('%Y年%m月%d日 %H:%M:%S')

    return {
        'title': '統合ダッシュボード',
        'current_time': jst_now
    }