"""
集計データを管理するモジュール
スクレイピングデータの集計を行い、高速なデータ参照を可能にします
"""

from sqlalchemy import func, and_, distinct
from datetime import datetime, timedelta
from app import db, StoreStatus, cache
import pytz

class AggregatedData:
    """
    スクレイピングデータの集計を管理するクラス
    日次・週次・月次の平均稼働率などを事前計算して保存します
    """

    @staticmethod
    def calculate_and_save_aggregated_data():
        """
        集計データの計算と保存を行う関数
        scheduled_scrape 関数から呼び出す
        """
        from app import DailyAverage, WeeklyAverage, StoreAverage

        # 現在の日付を取得
        today = datetime.now().date()
        one_day_ago = today - timedelta(days=1)
        one_week_ago = today - timedelta(weeks=1)

        # 日次平均の計算（最新24時間）- JSTの現在日付を基準に
        jst = pytz.timezone('Asia/Tokyo')
        today_jst = datetime.now(jst).date()
        one_day_ago_jst = today_jst - timedelta(days=1)

        # SQLiteの場合はタイムゾーン変換が必要
        # 日次平均の計算（最新24時間）
        daily_query = db.session.query(
            StoreStatus.store_name,
            func.avg(
                (StoreStatus.working_staff - StoreStatus.active_staff) * 100.0 / 
                func.nullif(StoreStatus.working_staff, 0)
            ).label('avg_rate'),
            func.count().label('sample_count'),
            func.min(StoreStatus.timestamp).label('start_date'),
            func.max(StoreStatus.timestamp).label('end_date'),
            func.max(StoreStatus.biz_type).label('biz_type'),
            func.max(StoreStatus.genre).label('genre'),
            func.max(StoreStatus.area).label('area')
        ).filter(
            StoreStatus.timestamp >= one_day_ago,
            StoreStatus.working_staff > 0
        ).group_by(
            StoreStatus.store_name
        ).all()

        # 既存の日次平均を削除
        db.session.query(DailyAverage).delete()

        # 新しい日次平均を保存 - JSTで現在時刻を取得
        now_jst = datetime.now(jst)
        for record in daily_query:
            # start_dateとend_dateのタイムゾーン情報を追加
            start_date = record.start_date
            if start_date.tzinfo is None:
                # SQLiteではタイムゾーン情報がないので、JST(+9)として解釈
                start_date = jst.localize(start_date)

            end_date = record.end_date
            if end_date.tzinfo is None:
                # SQLiteではタイムゾーン情報がないので、JST(+9)として解釈
                end_date = jst.localize(end_date)

            store_avg = DailyAverage(
                store_name=record.store_name,
                avg_rate=float(record.avg_rate),
                sample_count=record.sample_count,
                start_date=start_date,
                end_date=end_date,
                biz_type=record.biz_type,
                genre=record.genre,
                area=record.area,
                updated_at=now_jst
            )
            db.session.add(store_avg)

        # 週次平均の計算 - JSTタイムゾーンを考慮
        one_week_ago_jst = today_jst - timedelta(weeks=1)
        weekly_query = db.session.query(
            StoreStatus.store_name,
            func.avg(
                (StoreStatus.working_staff - StoreStatus.active_staff) * 100.0 / 
                func.nullif(StoreStatus.working_staff, 0)
            ).label('avg_rate'),
            func.count().label('sample_count'),
            func.min(StoreStatus.timestamp).label('start_date'),
            func.max(StoreStatus.timestamp).label('end_date'),
            func.max(StoreStatus.biz_type).label('biz_type'),
            func.max(StoreStatus.genre).label('genre'),
            func.max(StoreStatus.area).label('area')
        ).filter(
            StoreStatus.timestamp >= one_week_ago,
            StoreStatus.working_staff > 0
        ).group_by(
            StoreStatus.store_name
        ).all()

        # 既存の週次平均を削除
        db.session.query(WeeklyAverage).delete()

        # 新しい週次平均を保存 - JSTタイムゾーン対応
        for record in weekly_query:
            # start_dateとend_dateのタイムゾーン情報を追加
            start_date = record.start_date
            if start_date.tzinfo is None:
                # SQLiteではタイムゾーン情報がないので、JST(+9)として解釈
                start_date = jst.localize(start_date)

            end_date = record.end_date
            if end_date.tzinfo is None:
                # SQLiteではタイムゾーン情報がないので、JST(+9)として解釈
                end_date = jst.localize(end_date)

            store_avg = WeeklyAverage(
                store_name=record.store_name,
                avg_rate=float(record.avg_rate),
                sample_count=record.sample_count,
                start_date=start_date,
                end_date=end_date,
                biz_type=record.biz_type,
                genre=record.genre,
                area=record.area,
                updated_at=now_jst
            )
            db.session.add(store_avg)

        # 店舗別の全期間平均の計算（2年以内）
        two_years_ago = today - timedelta(days=730)
        store_query = db.session.query(
            StoreStatus.store_name,
            func.avg(
                (StoreStatus.working_staff - StoreStatus.active_staff) * 100.0 / 
                func.nullif(StoreStatus.working_staff, 0)
            ).label('avg_rate'),
            func.count().label('sample_count'),
            func.min(StoreStatus.timestamp).label('start_date'),
            func.max(StoreStatus.timestamp).label('end_date'),
            func.max(StoreStatus.biz_type).label('biz_type'),
            func.max(StoreStatus.genre).label('genre'),
            func.max(StoreStatus.area).label('area')
        ).filter(
            StoreStatus.timestamp >= two_years_ago,
            StoreStatus.working_staff > 0
        ).group_by(
            StoreStatus.store_name
        ).having(
            func.count() >= 10  # 最低10サンプル以上
        ).all()

        # 既存の店舗平均を削除
        db.session.query(StoreAverage).delete()

        # 新しい店舗平均を保存 - JSTタイムゾーン対応
        for record in store_query:
            # start_dateとend_dateのタイムゾーン情報を追加
            start_date = record.start_date
            if start_date.tzinfo is None:
                # SQLiteではタイムゾーン情報がないので、JST(+9)として解釈
                start_date = jst.localize(start_date)

            end_date = record.end_date
            if end_date.tzinfo is None:
                # SQLiteではタイムゾーン情報がないので、JST(+9)として解釈
                end_date = jst.localize(end_date)

            store_avg = StoreAverage(
                store_name=record.store_name,
                avg_rate=float(record.avg_rate),
                sample_count=record.sample_count,
                start_date=start_date,
                end_date=end_date,
                biz_type=record.biz_type,
                genre=record.genre,
                area=record.area,
                updated_at=now_jst
            )
            db.session.add(store_avg)

        # 変更をコミット
        db.session.commit()

        # キャッシュを更新
        cache.delete_memoized(get_daily_averages)
        cache.delete_memoized(get_weekly_averages)
        cache.delete_memoized(get_store_averages)

        return {
            "daily_count": len(daily_query),
            "weekly_count": len(weekly_query),
            "store_count": len(store_query)
        }

    @staticmethod
    @cache.memoize(timeout=3600)  # 1時間キャッシュ
    def get_daily_averages():
        """日次平均データを取得"""
        from app import DailyAverage
        return DailyAverage.query.all()

    @staticmethod
    @cache.memoize(timeout=3600)  # 1時間キャッシュ
    def get_weekly_averages():
        """週次平均データを取得"""
        from app import WeeklyAverage
        return WeeklyAverage.query.all()

    @staticmethod
    @cache.memoize(timeout=7200)  # 2時間キャッシュ
    def get_store_averages():
        """店舗別平均データを取得"""
        from app import StoreAverage
        return StoreAverage.query.all()

    @staticmethod
    def calculate_hourly_average(conn, store_name=None):
        """時間帯別の平均稼働率を計算"""
        query = 'SELECT * FROM history'
        params = []

        if store_name:
            query += ' WHERE store_name = ?'
            params.append(store_name)

        data = conn.execute(query, params).fetchall()
        hourly_data = {}

        for record in data:
            dt = datetime.strptime(record['timestamp'], '%Y-%m-%d %H:%M:%S')
            hour = dt.hour

            if record['working_staff'] > 0:
                rate = ((record['working_staff'] - record['active_staff']) / record['working_staff']) * 100
                if hour not in hourly_data:
                    hourly_data[hour] = {'sum': 0, 'count': 0}
                hourly_data[hour]['sum'] += rate
                hourly_data[hour]['count'] += 1

        # 平均を計算
        result = []
        for hour in range(24):
            if hour in hourly_data and hourly_data[hour]['count'] > 0:
                avg = hourly_data[hour]['sum'] / hourly_data[hour]['count']
                result.append({'hour': hour, 'rate': round(avg, 1)})
            else:
                result.append({'hour': hour, 'rate': 0})

        return result

    @staticmethod
    def calculate_area_statistics(conn):
        """エリア別の統計情報を計算"""
        query = """
        SELECT 
            area,
            COUNT(DISTINCT store_name) as store_count,
            AVG(CASE WHEN working_staff > 0 
                THEN ((working_staff - active_staff) * 100.0 / working_staff) 
                ELSE 0 END) as avg_rate
        FROM history
        GROUP BY area
        ORDER BY avg_rate DESC
        """

        data = conn.execute(query).fetchall()
        return data

    @staticmethod
    def calculate_genre_ranking(conn, biz_type=None):
        """業種内ジャンルの平均稼働率ランキングを計算"""
        query = """
        SELECT 
            genre,
            COUNT(DISTINCT store_name) as store_count,
            AVG(CASE WHEN working_staff > 0 
                THEN ((working_staff - active_staff) * 100.0 / working_staff) 
                ELSE 0 END) as avg_rate
        FROM history
        """

        params = []
        if biz_type:
            query += " WHERE biz_type = ? "
            params.append(biz_type)

        query += """
        GROUP BY genre
        HAVING store_count >= 2
        ORDER BY avg_rate DESC
        """

        data = conn.execute(query, params).fetchall()
        return data

    @staticmethod
    def calculate_store_ranking(conn, biz_type=None):
        """店舗の平均稼働率ランキングを計算"""
        query = """
        SELECT 
            store_name, 
            MAX(biz_type) as biz_type,
            MAX(genre) as genre,
            MAX(area) as area,
            AVG(CASE WHEN working_staff > 0 
                THEN ((working_staff - active_staff) * 100.0 / working_staff) 
                ELSE 0 END) as avg_rate,
            COUNT(*) as sample_count
        FROM history
        """

        params = []
        if biz_type:
            query += " WHERE biz_type = ? "
            params.append(biz_type)

        query += """
        GROUP BY store_name
        HAVING sample_count >= 5
        ORDER BY avg_rate DESC
        LIMIT 50
        """

        data = conn.execute(query, params).fetchall()
        return data