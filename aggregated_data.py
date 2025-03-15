"""
集計データを管理するモジュール
スクレイピングデータの集計を行い、高速なデータ参照を可能にします
"""

import logging
from datetime import datetime, timedelta
import pytz
from sqlalchemy import func, and_
from flask import current_app as app

from models import db, StoreStatus, DailyStats

logger = logging.getLogger(__name__)

class AggregatedData:
    """集計データを管理するクラス"""

    @staticmethod
    def calculate_and_save_aggregated_data():
        """
        スクレイピングデータから集計データを計算して保存する
        """
        try:
            logger.info("集計データの計算を開始します")

            # 最新の集計時刻を取得（JSTタイムゾーン）
            jst = pytz.timezone('Asia/Tokyo')
            current_time = datetime.now(jst)

            # 今日の日付（00:00:00）を取得
            today = current_time.replace(hour=0, minute=0, second=0, microsecond=0)

            # 今日分の集計データがあるか確認
            existing_daily = DailyStats.query.filter(
                func.date(DailyStats.date) == func.date(today)
            ).first()

            if existing_daily:
                logger.info(f"本日 {today.strftime('%Y-%m-%d')} の集計データは既に存在します。更新を行います。")
                daily = existing_daily
            else:
                logger.info(f"本日 {today.strftime('%Y-%m-%d')} の集計データを新規作成します。")
                daily = DailyStats(date=today)

            # 今日のデータを集計
            result = db.session.query(
                func.count(StoreStatus.id).label('record_count'),
                func.count(func.distinct(StoreStatus.store_name)).label('store_count'),
                func.avg(StoreStatus.working_staff).label('avg_working_staff'),
                func.avg(StoreStatus.total_staff).label('avg_total_staff'),
                func.avg(
                    func.case(
                        [(StoreStatus.total_staff > 0, 
                          StoreStatus.working_staff * 100.0 / StoreStatus.total_staff)],
                        else_=0
                    )
                ).label('avg_operation_rate')
            ).filter(
                func.date(StoreStatus.timestamp) == func.date(today)
            ).first()

            if result:
                daily.record_count = result.record_count or 0
                daily.store_count = result.store_count or 0
                daily.avg_working_staff = float(result.avg_working_staff or 0)
                daily.avg_total_staff = float(result.avg_total_staff or 0)
                daily.avg_operation_rate = float(result.avg_operation_rate or 0)
                daily.last_updated = current_time

                db.session.add(daily)
                db.session.commit()

                logger.info(f"集計データを保存しました: 対象店舗数={daily.store_count}, 平均稼働率={daily.avg_operation_rate:.2f}%")
            else:
                logger.warning("今日のデータが見つかりませんでした。集計をスキップします。")

        except Exception as e:
            logger.error(f"集計データの計算中にエラーが発生しました: {e}")
            db.session.rollback()

    @staticmethod
    def get_daily_averages():
        """日次平均データの取得"""
        return DailyStats.query.order_by(DailyStats.date.desc()).all()

    @staticmethod
    def get_weekly_averages():
        """週次平均データの取得 (実装が必要)"""
        return [] # Placeholder - needs implementation

    @staticmethod
    def get_monthly_averages():
        """月次平均データの取得 (実装が必要)"""
        return [] # Placeholder - needs implementation

    @staticmethod
    def get_store_averages():
        """店舗全期間平均データの取得 (実装が必要)"""
        return [] # Placeholder - needs implementation

# Remove the placeholder class
#class AggregatedStat:
#    """Placeholder class for compatibility"""
#    pass