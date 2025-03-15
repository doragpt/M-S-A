"""
集計データを管理するモジュール
スクレイピングデータの集計を行い、高速なデータ参照を可能にします
"""

from datetime import datetime, timedelta
import pytz
from sqlalchemy import func, and_
from flask import current_app as app

from models import db, StoreStatus, DailyAverage, WeeklyAverage, MonthlyAverage, StoreAverage
from database import get_db_connection

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
        # 日本時間でタイムスタンプを扱う
        jst = pytz.timezone('Asia/Tokyo')
        now_jst = datetime.now(jst)

        # 各期間の日付範囲を計算
        daily_start = now_jst - timedelta(days=1)  # 直近24時間
        weekly_start = now_jst - timedelta(days=7)  # 直近7日間
        monthly_start = now_jst - timedelta(days=30)  # 直近30日間
        all_time_start = now_jst - timedelta(days=730)  # 直近2年間

        # SQLiteを直接使用して集計（SQLAlchemyのORM機能を避ける）
        conn = get_db_connection()
        if not conn:
            app.logger.error("集計データ計算中にデータベース接続エラーが発生しました")
            return False

        try:
            # 各集計テーブルをクリア
            conn.execute("DELETE FROM daily_averages")
            conn.execute("DELETE FROM weekly_averages")
            conn.execute("DELETE FROM monthly_averages")
            conn.execute("DELETE FROM store_averages")

            # 各平均値を計算して挿入

            # 1. 日次平均（直近24時間）
            daily_avg_query = """
            INSERT INTO daily_averages (
                store_name, avg_rate, sample_count, start_date, end_date, 
                biz_type, genre, area, updated_at
            )
            SELECT 
                store_name,
                AVG((working_staff - active_staff) * 100.0 / working_staff) as avg_rate,
                COUNT(*) as sample_count,
                MIN(timestamp) as start_date,
                MAX(timestamp) as end_date,
                MAX(biz_type) as biz_type,
                MAX(genre) as genre,
                MAX(area) as area,
                datetime('now') as updated_at
            FROM store_status
            WHERE working_staff > 0
            AND timestamp >= datetime(?, 'unixepoch')
            GROUP BY store_name
            """

            # 2. 週次平均（直近7日間）
            weekly_avg_query = """
            INSERT INTO weekly_averages (
                store_name, avg_rate, sample_count, start_date, end_date, 
                biz_type, genre, area, updated_at
            )
            SELECT 
                store_name,
                AVG((working_staff - active_staff) * 100.0 / working_staff) as avg_rate,
                COUNT(*) as sample_count,
                MIN(timestamp) as start_date,
                MAX(timestamp) as end_date,
                MAX(biz_type) as biz_type,
                MAX(genre) as genre,
                MAX(area) as area,
                datetime('now') as updated_at
            FROM store_status
            WHERE working_staff > 0
            AND timestamp >= datetime(?, 'unixepoch')
            GROUP BY store_name
            """

            # 3. 月次平均（直近30日間）
            monthly_avg_query = """
            INSERT INTO monthly_averages (
                store_name, avg_rate, sample_count, start_date, end_date, 
                biz_type, genre, area, updated_at
            )
            SELECT 
                store_name,
                AVG((working_staff - active_staff) * 100.0 / working_staff) as avg_rate,
                COUNT(*) as sample_count,
                MIN(timestamp) as start_date,
                MAX(timestamp) as end_date,
                MAX(biz_type) as biz_type,
                MAX(genre) as genre,
                MAX(area) as area,
                datetime('now') as updated_at
            FROM store_status
            WHERE working_staff > 0
            AND timestamp >= datetime(?, 'unixepoch')
            GROUP BY store_name
            """

            # 4. 全期間平均（直近2年間）
            store_avg_query = """
            INSERT INTO store_averages (
                store_name, avg_rate, sample_count, start_date, end_date, 
                biz_type, genre, area, updated_at
            )
            SELECT 
                store_name,
                AVG((working_staff - active_staff) * 100.0 / working_staff) as avg_rate,
                COUNT(*) as sample_count,
                MIN(timestamp) as start_date,
                MAX(timestamp) as end_date,
                MAX(biz_type) as biz_type,
                MAX(genre) as genre,
                MAX(area) as area,
                datetime('now') as updated_at
            FROM store_status
            WHERE working_staff > 0
            AND timestamp >= datetime(?, 'unixepoch')
            GROUP BY store_name
            """

            # クエリ実行
            daily_timestamp = int(daily_start.timestamp())
            weekly_timestamp = int(weekly_start.timestamp())
            monthly_timestamp = int(monthly_start.timestamp())
            all_time_timestamp = int(all_time_start.timestamp())

            conn.execute(daily_avg_query, [daily_timestamp])
            conn.execute(weekly_avg_query, [weekly_timestamp])
            conn.execute(monthly_avg_query, [monthly_timestamp])
            conn.execute(store_avg_query, [all_time_timestamp])

            # コミット
            conn.commit()
            app.logger.info("集計データの計算と保存が完了しました")
            return True

        except Exception as e:
            app.logger.error(f"集計データ計算中にエラーが発生しました: {e}")
            conn.rollback()
            return False

        finally:
            conn.close()

    @staticmethod
    def get_daily_averages():
        """日次平均（直近24時間）データの取得"""
        return DailyAverage.query.order_by(DailyAverage.avg_rate.desc()).all()

    @staticmethod
    def get_weekly_averages():
        """週次平均（直近7日間）データの取得"""
        return WeeklyAverage.query.order_by(WeeklyAverage.avg_rate.desc()).all()

    @staticmethod
    def get_monthly_averages():
        """月次平均（直近30日間）データの取得"""
        return MonthlyAverage.query.order_by(MonthlyAverage.avg_rate.desc()).all()

    @staticmethod
    def get_store_averages():
        """店舗全期間平均データの取得"""
        return StoreAverage.query.order_by(StoreAverage.avg_rate.desc()).all()

# Create a placeholder for the missing AggregatedStat if needed
class AggregatedStat:
    """Placeholder class for compatibility"""
    pass