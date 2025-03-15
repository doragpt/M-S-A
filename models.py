
"""
データベースモデルの定義
"""

from sqlalchemy import Column, Integer, Float, Text, DateTime, func
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class StoreStatus(db.Model):
    """
    店舗ごとのスクレイピング結果を保存するテーブル。
    各レコードは、ある時点での店舗の稼働情報を表します。
    """
    __tablename__ = 'store_status'
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, server_default=func.now(), index=True)
    store_name = Column(Text)
    biz_type = Column(Text)
    genre = Column(Text)
    area = Column(Text)
    total_staff = Column(Integer)
    working_staff = Column(Integer)
    active_staff = Column(Integer)
    url = Column(Text)
    shift_time = Column(Text)


class StoreURL(db.Model):
    """
    スクレイピング対象の店舗URLを保存するテーブル。
    """
    __tablename__ = 'store_urls'
    id = Column(Integer, primary_key=True)
    store_url = Column(Text, unique=True, nullable=False)
    error_flag = Column(Integer, default=0)


# 集計データテーブル
class DailyAverage(db.Model):
    """日次の平均稼働率（直近24時間）"""
    __tablename__ = 'daily_averages'
    id = Column(Integer, primary_key=True)
    store_name = Column(Text, index=True)
    avg_rate = Column(Float)
    sample_count = Column(Integer)
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    biz_type = Column(Text)
    genre = Column(Text)
    area = Column(Text)
    updated_at = Column(DateTime, default=func.now())


class WeeklyAverage(db.Model):
    """週次の平均稼働率（直近7日間）"""
    __tablename__ = 'weekly_averages'
    id = Column(Integer, primary_key=True)
    store_name = Column(Text, index=True)
    avg_rate = Column(Float)
    sample_count = Column(Integer)
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    biz_type = Column(Text)
    genre = Column(Text)
    area = Column(Text)
    updated_at = Column(DateTime, default=func.now())


class MonthlyAverage(db.Model):
    """月次の平均稼働率（直近30日間）"""
    __tablename__ = 'monthly_averages'
    id = Column(Integer, primary_key=True)
    store_name = Column(Text, index=True)
    avg_rate = Column(Float)
    sample_count = Column(Integer)
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    biz_type = Column(Text)
    genre = Column(Text)
    area = Column(Text)
    updated_at = Column(DateTime, default=func.now())


class DailyStats(db.Model):
    """日次の統計データ"""
    __tablename__ = 'daily_stats'
    id = Column(Integer, primary_key=True)
    date = Column(DateTime, index=True)
    record_count = Column(Integer, default=0)
    store_count = Column(Integer, default=0)
    avg_working_staff = Column(Float, default=0.0)
    avg_total_staff = Column(Float, default=0.0)
    avg_operation_rate = Column(Float, default=0.0)
    last_updated = Column(DateTime, default=func.now())


class StoreAverage(db.Model):
    """店舗ごとの全期間平均稼働率（2年以内）"""
    __tablename__ = 'store_averages'
    id = Column(Integer, primary_key=True)
    store_name = Column(Text, index=True)
    avg_rate = Column(Float)
    sample_count = Column(Integer)
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    biz_type = Column(Text)
    genre = Column(Text)
    area = Column(Text)
    updated_at = Column(DateTime, default=func.now())
