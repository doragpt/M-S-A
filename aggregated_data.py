
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
        
        # 新しい日次平均を保存
        for record in daily_query:
            store_avg = DailyAverage(
                store_name=record.store_name,
                avg_rate=float(record.avg_rate),
                sample_count=record.sample_count,
                start_date=record.start_date,
                end_date=record.end_date,
                biz_type=record.biz_type,
                genre=record.genre,
                area=record.area,
                updated_at=datetime.now()
            )
            db.session.add(store_avg)
        
        # 週次平均の計算
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
        
        # 新しい週次平均を保存
        for record in weekly_query:
            store_avg = WeeklyAverage(
                store_name=record.store_name,
                avg_rate=float(record.avg_rate),
                sample_count=record.sample_count,
                start_date=record.start_date,
                end_date=record.end_date,
                biz_type=record.biz_type,
                genre=record.genre,
                area=record.area,
                updated_at=datetime.now()
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
        
        # 新しい店舗平均を保存
        for record in store_query:
            store_avg = StoreAverage(
                store_name=record.store_name,
                avg_rate=float(record.avg_rate),
                sample_count=record.sample_count,
                start_date=record.start_date,
                end_date=record.end_date,
                biz_type=record.biz_type,
                genre=record.genre,
                area=record.area,
                updated_at=datetime.now()
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
