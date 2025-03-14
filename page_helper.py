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
    店舗ステータスレコードを整形してフロントエンド用JSONに変換する関数

    Parameters:
    -----------
    item : dict or SQLAlchemy model
        変換する店舗ステータスレコード
    timezone : pytz timezone object, optional
        変換先のタイムゾーン（指定しない場合はUTC）

    Returns:
    --------
    dict
        整形されたJSONオブジェクト
    """
    import logging
    import datetime
    import pytz
    logger = logging.getLogger('app')

    # SQLAlchemy モデルオブジェクトの場合は辞書に変換
    if hasattr(item, '__dict__'):
        item_dict = {}
        for key in ['id', 'timestamp', 'store_name', 'biz_type', 'genre', 'area', 
                   'total_staff', 'working_staff', 'active_staff', 'url', 'shift_time']:
            if hasattr(item, key):
                item_dict[key] = getattr(item, key)
        item = item_dict

    # SQLite の Row オブジェクトの場合
    elif hasattr(item, 'keys'):
        try:
            item_dict = {}
            for key in item.keys():
                item_dict[key] = item[key]
            item = item_dict
        except Exception as e:
            logger.error(f"SQLite Row の変換エラー: {e}")
            # 続行するために空の辞書を作成
            item = {'error': f"データ変換エラー: {str(e)}"}

    # 辞書でない場合は処理中止
    if not isinstance(item, dict):
        logger.error(f"未対応の型: {type(item)}")
        return {
            'id': None,
            'timestamp': datetime.datetime.now().isoformat(),
            'store_name': '不明',
            'biz_type': '不明',
            'genre': '不明',
            'area': '不明',
            'total_staff': 0,
            'working_staff': 0,
            'active_staff': 0,
            'url': '',
            'shift_time': '',
            'rate': 0.0,
            'error': f"未対応の型: {type(item)}"
        }

    try:
        # タイムスタンプ処理 - データベースからのISO 8601形式に対応
        timestamp = item.get('timestamp')

        if timestamp is not None:
            if isinstance(timestamp, datetime.datetime):
                # すでにdatetime型の場合は何もしない
                pass
            elif isinstance(timestamp, str):
                try:
                    # ISO 8601形式のパース（マイクロ秒対応）
                    if 'T' in timestamp:
                        # Z形式をタイムゾーン付きに変換
                        iso_str = timestamp.replace('Z', '+00:00')
                        
                        # タイムゾーン情報がない場合
                        if '+' not in iso_str and '-' not in iso_str[10:]:
                            iso_str = iso_str + '+00:00'
                        
                        try:
                            # Python 3.7以降はfromisoformatを使用
                            if hasattr(datetime.datetime, 'fromisoformat'):
                                timestamp = datetime.datetime.fromisoformat(iso_str)
                            else:
                                # マイクロ秒ありのフォーマット対応
                                if '.' in iso_str:
                                    main_part = iso_str.split('+')[0]
                                    timestamp = datetime.datetime.strptime(main_part, '%Y-%m-%dT%H:%M:%S.%f')
                                else:
                                    # マイクロ秒なし
                                    main_part = iso_str.split('+')[0]
                                    timestamp = datetime.datetime.strptime(main_part, '%Y-%m-%dT%H:%M:%S')
                        except ValueError as e:
                            logger.warning(f"ISO形式のパースに失敗、フォールバック: {e}")
                            # マイクロ秒形式を直接試す（database.pyのエラーに対応）
                            try:
                                if '.' in timestamp:
                                    parts = timestamp.split('.')
                                    base = parts[0]
                                    # 最大6桁のマイクロ秒まで処理
                                    micro = parts[1][:6]
                                    if len(micro) < 6:
                                        micro = micro.ljust(6, '0')
                                    timestamp = datetime.datetime.strptime(f"{base}.{micro}", '%Y-%m-%dT%H:%M:%S.%f')
                                else:
                                    timestamp = datetime.datetime.strptime(timestamp, '%Y-%m-%dT%H:%M:%S')
                            except ValueError:
                                # 日付部分だけを使用
                                try:
                                    date_part = timestamp.split('T')[0]
                                    timestamp = datetime.datetime.strptime(date_part, '%Y-%m-%d')
                                except ValueError:
                                    logger.warning(f"日付変換に最終的に失敗: {timestamp}")
                                    timestamp = datetime.datetime.now()
                    else:
                        # 通常の日時形式を試す
                        try:
                            if '.' in timestamp:
                                timestamp = datetime.datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S.%f')
                            else:
                                timestamp = datetime.datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
                        except ValueError:
                            # 日付のみのフォーマット
                            try:
                                timestamp = datetime.datetime.strptime(timestamp, '%Y-%m-%d')
                            except ValueError:
                                logger.warning(f"日付変換に失敗、現在時刻を使用: {timestamp}")
                                timestamp = datetime.datetime.now()
                except Exception as dt_err:
                    logger.error(f"日付変換中の予期しないエラー: {dt_err}, 値: {timestamp}")
                    timestamp = datetime.datetime.now()
            else:
                # 他の型の場合は現在時刻を使用
                logger.warning(f"未対応のタイムスタンプ型: {type(timestamp)}")
                timestamp = datetime.datetime.now()
        else:
            # タイムスタンプがない場合は現在時刻を使用
            timestamp = datetime.datetime.now()

        # タイムゾーン変換
        if timezone and timestamp:
            try:
                # タイムゾーン情報がない場合はUTCと仮定して変換
                if not timestamp.tzinfo:
                    timestamp = pytz.utc.localize(timestamp)
                # 指定されたタイムゾーンに変換
                timestamp = timestamp.astimezone(timezone)
            except Exception as tz_err:
                logger.error(f"タイムゾーン変換エラー: {tz_err}")
                # エラー時は現在時刻を使用
                timestamp = datetime.datetime.now(timezone)

        # 文字列がない場合は"不明"、数値がない場合は0にする
        store_name = item.get('store_name', '不明')
        if store_name is None or store_name == '':
            store_name = '不明'

        biz_type = item.get('biz_type', '不明')
        if biz_type is None or biz_type == '':
            biz_type = '不明'

        genre = item.get('genre', '不明')
        if genre is None or genre == '':
            genre = '不明'

        area = item.get('area', '不明')
        if area is None or area == '':
            area = '不明'

        # 数値型データの処理 (None or '' -> 0)
        try:
            total_staff = int(item.get('total_staff', 0) or 0)
        except (ValueError, TypeError):
            total_staff = 0

        try:
            working_staff = int(item.get('working_staff', 0) or 0)
        except (ValueError, TypeError):
            working_staff = 0

        try:
            active_staff = int(item.get('active_staff', 0) or 0)
        except (ValueError, TypeError):
            active_staff = 0

        # URL がない場合は空文字列
        url = item.get('url', '')
        if url is None:
            url = ''

        # シフト時間がない場合は空文字列
        shift_time = item.get('shift_time', '')
        if shift_time is None:
            shift_time = ''

        # 稼働率計算
        rate = 0.0
        if working_staff > 0:
            rate = ((working_staff - active_staff) / working_staff) * 100
            rate = round(rate, 1)  # 小数点第1位で丸める

        # 整形済みデータ
        formatted = {
            'id': item.get('id'),
            'timestamp': timestamp.isoformat() if timestamp else datetime.datetime.now().isoformat(),
            'store_name': store_name,
            'biz_type': biz_type,
            'genre': genre,
            'area': area,
            'total_staff': total_staff,
            'working_staff': working_staff,
            'active_staff': active_staff,
            'url': url,
            'shift_time': shift_time,
            'rate': rate
        }

        return formatted

    except Exception as e:
        logger.error(f"データフォーマットエラー: {e}")
        logger.error(f"エラーの原因となったデータ: {item}")

        # 最低限のフォールバックデータを返す
        try:
            store_name = item.get('store_name', '不明')
            if store_name is None or store_name == '':
                store_name = '不明'

            return {
                'id': item.get('id'),
                'timestamp': datetime.datetime.now().isoformat(),
                'store_name': store_name,
                'biz_type': '不明',
                'genre': '不明',
                'area': '不明',
                'total_staff': 0,
                'working_staff': 0,
                'active_staff': 0,
                'url': '',
                'shift_time': '',
                'rate': 0.0,
                'error': str(e)
            }
        except Exception as fallback_err:
            logger.error(f"フォールバックデータ作成エラー: {fallback_err}")
            return {
                'id': None,
                'timestamp': datetime.datetime.now().isoformat(),
                'store_name': '不明',
                'biz_type': '不明',
                'genre': '不明',
                'area': '不明',
                'total_staff': 0,
                'working_staff': 0,
                'active_staff': 0,
                'url': '',
                'shift_time': '',
                'rate': 0.0,
                'error': '重大なフォーマットエラー'
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