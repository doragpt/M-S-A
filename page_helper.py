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
    
    from app import app
    logger = app.logger

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
            timestamp_str = None
            
            # デバッグログ
            if isinstance(timestamp, str):
                logger.debug(f"文字列タイムスタンプ処理: {timestamp}")
            elif hasattr(timestamp, 'isoformat'):
                logger.debug(f"datetime タイムスタンプ処理: {timestamp.isoformat() if hasattr(timestamp, 'isoformat') else 'フォーマット不可'}")
            
            if isinstance(timestamp, str):
                # 文字列の場合はパース
                timestamp_str = timestamp  # 元の文字列を保存
                try:
                    if 'Z' in timestamp:
                        timestamp = timestamp.replace('Z', '+00:00')
                    if timestamp.endswith('+00:00') or '+' in timestamp:
                        timestamp = datetime.fromisoformat(timestamp)
                    elif 'T' in timestamp:
                        # マイクロ秒の処理
                        if '.' in timestamp:
                            parts = timestamp.split('.')
                            base_time = parts[0]
                            timestamp = datetime.fromisoformat(base_time)
                        else:
                            timestamp = datetime.fromisoformat(timestamp)
                    else:
                        timestamp = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
                except ValueError as e:
                    logger.warning(f"一次的な日付変換エラー: {e}, 入力値: {timestamp}")
                    try:
                        # その他の形式も試す
                        formats = [
                            '%Y-%m-%d %H:%M:%S',
                            '%Y-%m-%d %H:%M:%S.%f',
                            '%Y-%m-%dT%H:%M:%S',
                            '%Y-%m-%dT%H:%M:%S.%f'
                        ]
                        for fmt in formats:
                            try:
                                # マイクロ秒を含む形式の場合は切り捨て処理
                                if '.%f' in fmt and '.' in timestamp:
                                    main_part, _ = timestamp.split('.', 1)
                                    if 'T' in fmt:
                                        main_part += '.000000'  # マイクロ秒を追加
                                    else:
                                        main_part += '.000000'  # マイクロ秒を追加
                                    timestamp = datetime.strptime(main_part, fmt)
                                else:
                                    timestamp = datetime.strptime(timestamp, fmt)
                                break
                            except ValueError:
                                continue
                    except Exception as parse_error:
                        logger.error(f"複数形式での日付変換も失敗: {parse_error}, 入力値: {timestamp}")
                        # エラー回避のため現在時刻を使用
                        timestamp = datetime.now()

            # タイムゾーン情報がない場合は追加
            if isinstance(timestamp, datetime):
                if timestamp.tzinfo is None:
                    timestamp = timezone.localize(timestamp)
                # 指定されたタイムゾーンに変換
                else:
                    timestamp = timestamp.astimezone(timezone)
                status_dict['timestamp'] = timestamp
            else:
                # datetimeに変換できない場合は元の値を使用
                logger.warning(f"タイムスタンプの変換失敗: {timestamp_str if timestamp_str else timestamp}, 型: {type(timestamp)}")
                # 現在時刻を代用
                status_dict['timestamp'] = timezone.localize(datetime.now())

        # 整形されたディクショナリを返す
        try:
            formatted = {
                'id': status_dict.get('id'),
                'timestamp': status_dict.get('timestamp').isoformat() if hasattr(status_dict.get('timestamp'), 'isoformat') else None,
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

            # timestamp が None の場合は現在時刻を設定
            if formatted['timestamp'] is None:
                formatted['timestamp'] = timezone.localize(datetime.now()).isoformat()

            # 稼働率を計算
            if formatted['working_staff'] > 0:
                formatted['rate'] = round((formatted['working_staff'] - formatted['active_staff']) / formatted['working_staff'] * 100, 1)
            else:
                formatted['rate'] = 0

            # total_staffが設定されていない場合はworking_staffとactive_staffから計算
            if formatted['total_staff'] == 0:
                formatted['total_staff'] = formatted['working_staff'] + formatted['active_staff']

            return formatted
        except Exception as fmt_error:
            logger.error(f"データフォーマット処理エラー: {fmt_error}")
            # 最低限必要なフィールドだけのフォールバックレスポンス
            return {
                'id': status_dict.get('id') if 'id' in status_dict else 0,
                'timestamp': timezone.localize(datetime.now()).isoformat(),
                'store_name': status_dict.get('store_name', '不明') or '不明',
                'biz_type': '不明',
                'genre': '不明',
                'area': '不明',
                'total_staff': 0,
                'working_staff': 0,
                'active_staff': 0,
                'url': '',
                'shift_time': '',
                'rate': 0
            }
    except Exception as e:
        from app import app
        app.logger.error(f"データフォーマットエラー: {e}")
        # エラーでもNoneを返さず、最低限の情報を含む空のレコードを返す
        return {
            'id': 0,
            'timestamp': timezone.localize(datetime.now()).isoformat(),
            'store_name': '不明',
            'biz_type': '不明',
            'genre': '不明',
            'area': '不明',
            'total_staff': 0,
            'working_staff': 0,
            'active_staff': 0,
            'url': '',
            'shift_time': '',
            'rate': 0
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