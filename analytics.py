
import os
import logging
from datetime import datetime, timedelta
import pytz
from sqlalchemy import func
from app import db, StoreStatus, cache

# ロギング設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Analytics:
    """
    データ分析・集計を行うクラス
    事前計算したデータをキャッシュに保存してパフォーマンスを向上させる
    """
    
    @staticmethod
    def calculate_store_averages(days=30):
        """
        店舗ごとの平均稼働率を計算し、結果をキャッシュする
        
        Args:
            days: 集計対象の日数（デフォルト30日）
        
        Returns:
            店舗ごとの平均稼働率を含む辞書
        """
        try:
            # キャッシュから取得を試みる
            cache_key = f"store_averages_{days}"
            cached_data = cache.get(cache_key)
            if cached_data:
                logger.info(f"店舗平均稼働率をキャッシュから取得（{days}日間）")
                return cached_data
            
            logger.info(f"店舗平均稼働率を再計算（{days}日間）")
            cutoff_date = datetime.now() - timedelta(days=days)
            
            # 期間内の各店舗の平均稼働率を計算するクエリ
            query = db.session.query(
                StoreStatus.store_name,
                func.avg(
                    (StoreStatus.working_staff - StoreStatus.active_staff) * 100.0 / 
                    func.nullif(StoreStatus.working_staff, 0)
                ).label('avg_rate'),
                func.count().label('sample_count'),
                func.max(StoreStatus.area).label('area'),
                func.max(StoreStatus.biz_type).label('biz_type'),
                func.max(StoreStatus.genre).label('genre')
            ).filter(
                StoreStatus.timestamp >= cutoff_date,
                StoreStatus.working_staff > 0
            ).group_by(
                StoreStatus.store_name
            ).having(
                func.count() >= 3  # 最低3サンプル以上
            )
            
            results = query.all()
            
            # 結果を整形
            store_averages = {
                r.store_name: {
                    'avg_rate': float(r.avg_rate) if r.avg_rate is not None else 0.0,
                    'sample_count': r.sample_count,
                    'area': r.area,
                    'biz_type': r.biz_type,
                    'genre': r.genre
                }
                for r in results
            }
            
            # 全体平均も計算
            all_rates = [data['avg_rate'] for data in store_averages.values() if data['avg_rate'] > 0]
            overall_avg = sum(all_rates) / len(all_rates) if all_rates else 0
            
            result = {
                'store_averages': store_averages,
                'overall_avg': overall_avg,
                'sample_count': len(store_averages),
                'calculation_time': datetime.now().isoformat(),
                'period_days': days
            }
            
            # キャッシュに保存（1時間有効）
            cache.set(cache_key, result, timeout=3600)
            return result
            
        except Exception as e:
            logger.error(f"平均稼働率計算エラー: {str(e)}")
            return {
                'store_averages': {},
                'overall_avg': 0,
                'sample_count': 0,
                'error': str(e)
            }
    
    @staticmethod
    def get_time_period_averages():
        """
        異なる時間期間（7日、30日、90日）での平均稼働率をまとめて取得
        
        Returns:
            各期間の平均稼働率データ
        """
        result = {
            'week': Analytics.calculate_store_averages(7),
            'month': Analytics.calculate_store_averages(30),
            'quarter': Analytics.calculate_store_averages(90)
        }
        return result
