import pandas as pd
from datetime import datetime
import pytz
import os

class ReportGenerator:
    def __init__(self):
        self.styles = getSampleStyleSheet()

    def generate_all_stores_report(self, stores_data, output_path):
        """全店舗の詳細Excelレポートを生成"""
        # ファイル名の拡張子を.xlsxに変更
        output_path = output_path.replace('.pdf', '.xlsx')
        
        # データフレームの作成
        df = pd.DataFrame(stores_data)
        
        # Excelファイルの作成
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            # サマリーシート
            summary_data = {
                '項目': ['総店舗数', '総勤務人数', '総即ヒメ数', '平均稼働率'],
                '値': [
                    len(stores_data),
                    sum(store.get('working_staff', 0) for store in stores_data),
                    sum(store.get('active_staff', 0) for store in stores_data),
                    f"{sum(store.get('rate', 0) for store in stores_data) / len(stores_data):.1f}%"
                ]
            }
            pd.DataFrame(summary_data).to_excel(writer, sheet_name='サマリー', index=False)
            
            # 店舗詳細シート
            store_details = []
            for store in stores_data:
                store_details.append({
                    '店舗名': store.get('store_name', ''),
                    '業種': store.get('biz_type', ''),
                    'ジャンル': store.get('genre', ''),
                    'エリア': store.get('area', ''),
                    '稼働率': f"{store.get('rate', 0)}%",
                    '勤務人数': store.get('working_staff', 0),
                    '即ヒメ数': store.get('active_staff', 0)
                })
            
            pd.DataFrame(store_details).to_excel(writer, sheet_name='店舗詳細', index=False)
            
            # シートの幅を自動調整
            for sheet_name in writer.sheets:
                worksheet = writer.sheets[sheet_name]
                for column in worksheet.columns:
                    max_length = 0
                    column = [cell for cell in column]
                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except:
                            pass
                    adjusted_width = (max_length + 2)
                    worksheet.column_dimensions[column[0].column_letter].width = adjusted_width
        
        return output_path

    def generate_store_report(self, store_data, output_path):
        pass # Placeholder -  This function is not used in the provided changes.