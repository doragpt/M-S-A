
import pandas as pd
from datetime import datetime
import pytz
import os
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side, Color
from openpyxl.chart import BarChart, Reference, PieChart
from openpyxl.chart.label import DataLabelList
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.utils import units
from openpyxl.worksheet.dimensions import ColumnDimension, DimensionHolder
from openpyxl.formatting.rule import ColorScaleRule

class ReportGenerator:
    def __init__(self):
        # モダンなカラーパレット
        self.header_fill = PatternFill(start_color="1A73E8", end_color="1A73E8", fill_type="solid")
        self.header_font = Font(color="FFFFFF", bold=True, size=11, name='Yu Gothic')
        self.title_font = Font(color="202124", bold=True, size=14, name='Yu Gothic')
        self.border = Border(
            left=Side(style='thin', color="E8EAED"),
            right=Side(style='thin', color="E8EAED"),
            top=Side(style='thin', color="E8EAED"),
            bottom=Side(style='thin', color="E8EAED")
        )

    def generate_all_stores_report(self, stores_data, output_path):
        """全店舗の詳細Excelレポートを生成"""
        if not stores_data:
            raise ValueError("店舗データが空です")

        output_path = output_path.replace('.pdf', '.xlsx')
        
        try:
            with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
                # サマリーシート
                total_stores = len(stores_data)
                total_working = sum(store.get('working_staff', 0) for store in stores_data)
                total_active = sum(store.get('active_staff', 0) for store in stores_data)
                avg_rate = sum(store.get('rate', 0) for store in stores_data) / total_stores if total_stores > 0 else 0

                summary_data = {
                    '項目': ['総店舗数', '総勤務人数', '総即ヒメ数', '平均稼働率'],
                    '値': [
                        total_stores,
                        total_working,
                        total_active,
                        f"{avg_rate:.1f}%"
                    ]
                }
                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name='サマリー', index=False)
                
                # サマリーシートのスタイル設定
                ws = writer.sheets['サマリー']
                self._apply_sheet_styling(ws, summary_df)
                
                # 店舗詳細シート
                store_details = []
                for store in stores_data:
                    store_details.append({
                        '店舗名': store.get('store_name', ''),
                        '業種': store.get('biz_type', ''),
                        'ジャンル': store.get('genre', ''),
                        'エリア': store.get('area', ''),
                        '稼働率': store.get('rate', 0),
                        '勤務人数': store.get('working_staff', 0),
                        '即ヒメ数': store.get('active_staff', 0)
                    })
                
                details_df = pd.DataFrame(store_details)
                details_df.to_excel(writer, sheet_name='店舗詳細', index=False)
                
                # 店舗詳細シートのスタイル設定
                ws = writer.sheets['店舗詳細']
                self._apply_sheet_styling(ws, details_df)
                
                # 稼働率の条件付き書式
                self._apply_conditional_formatting(ws, details_df)
                
                # エリア分析シートの追加
                self._create_area_analysis_sheet(writer, store_details)
                
                # 全シートの幅調整
                for sheet_name in writer.sheets:
                    ws = writer.sheets[sheet_name]
                    self._adjust_column_widths(ws)

            return output_path
            
        except Exception as e:
            raise Exception(f"レポート生成中にエラーが発生しました: {str(e)}")

    def _apply_sheet_styling(self, ws, df):
        """シートの基本スタイル適用"""
        for col in range(1, len(df.columns) + 1):
            cell = ws.cell(row=1, column=col)
            cell.fill = self.header_fill
            cell.font = self.header_font
            cell.alignment = Alignment(horizontal='center', vertical='center')
            
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.border = self.border
                cell.alignment = Alignment(horizontal='center', vertical='center')
                cell.font = Font(name='Yu Gothic')

    def _apply_conditional_formatting(self, ws, df):
        """稼働率に応じた条件付き書式を適用"""
        rate_col = [i for i, col in enumerate(df.columns) if col == '稼働率'][0] + 1
        color_scale = ColorScaleRule(
            start_type='num', start_value=0, start_color='FF0000',
            mid_type='num', mid_value=50, mid_color='FFFF00',
            end_type='num', end_value=100, end_color='00FF00'
        )
        ws.conditional_formatting.add(f"{chr(64+rate_col)}2:{chr(64+rate_col)}{len(df)+1}", color_scale)

    def _create_area_analysis_sheet(self, writer, store_details):
        """エリア分析シートを作成"""
        df = pd.DataFrame(store_details)
        area_stats = df.groupby('エリア').agg({
            '店舗名': 'count',
            '稼働率': 'mean',
            '勤務人数': 'sum'
        }).reset_index()
        
        # 列名を変更
        area_stats.columns = ['エリア', '店舗数', '平均稼働率', '総勤務人数']
        
        area_stats.to_excel(writer, sheet_name='エリア分析', index=False)
        ws = writer.sheets['エリア分析']
        
        # グラフの追加
        chart = BarChart()
        chart.title = "エリア別平均稼働率"
        chart.y_axis.title = '稼働率 (%)'
        chart.x_axis.title = 'エリア'
        
        data = Reference(ws, min_col=3, min_row=1, max_row=len(area_stats)+1)
        cats = Reference(ws, min_col=1, min_row=2, max_row=len(area_stats)+1)
        
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        chart.style = 2
        
        ws.add_chart(chart, "E2")

    def _adjust_column_widths(self, ws):
        """列幅の自動調整"""
        for column in ws.columns:
            max_length = 0
            column = [cell for cell in column]
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = (max_length + 2) * 1.2
            ws.column_dimensions[column[0].column_letter].width = adjusted_width

    def generate_store_report(self, store_data, output_path):
        pass  # 未実装の機能
