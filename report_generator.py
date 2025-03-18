
import pandas as pd
from datetime import datetime
import pytz
import os
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.chart import BarChart, Reference
from openpyxl.drawing.text import RichText, Paragraph, ParagraphProperties, CharacterProperties

class ReportGenerator:
    def __init__(self):
        self.header_fill = PatternFill(start_color="1A73E8", end_color="1A73E8", fill_type="solid")
        self.header_font = Font(color="FFFFFF", bold=True)
        self.border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
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
                
                # グラフの追加
                self._add_charts(ws, details_df)
                
                # ヘッダー/フッターの追加
                self._add_header_footer(ws)
                
                # 列幅の自動調整
                self._adjust_column_widths(ws)

            return output_path
            
        except Exception as e:
            raise Exception(f"レポート生成中にエラーが発生しました: {str(e)}")

    def _apply_sheet_styling(self, ws, df):
        """シートの基本スタイルを適用"""
        for col in range(1, len(df.columns) + 1):
            cell = ws.cell(row=1, column=col)
            cell.fill = self.header_fill
            cell.font = self.header_font
            cell.alignment = Alignment(horizontal='center')
            
        for row in ws.iter_rows():
            for cell in row:
                cell.border = self.border
                cell.alignment = Alignment(horizontal='center')

    def _apply_conditional_formatting(self, ws, df):
        """稼働率に応じた条件付き書式を適用"""
        rate_col = df.columns.get_loc('稼働率') + 1
        for row in range(2, len(df) + 2):
            cell = ws.cell(row=row, column=rate_col)
            rate = float(str(cell.value).replace('%', ''))
            if rate >= 80:
                cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
            elif rate <= 30:
                cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")

    def _add_charts(self, ws, df):
        """グラフを追加"""
        chart = BarChart()
        chart.title = "エリア別平均稼働率"
        chart.y_axis.title = '稼働率 (%)'
        chart.x_axis.title = 'エリア'
        
        area_stats = df.groupby('エリア')['稼働率'].mean()
        data = Reference(ws, min_col=5, min_row=2, max_row=len(df) + 1)
        cats = Reference(ws, min_col=4, min_row=2, max_row=len(df) + 1)
        
        chart.add_data(data)
        chart.set_categories(cats)
        
        ws.add_chart(chart, "J2")

    def _add_header_footer(self, ws):
        """ヘッダーとフッターを追加"""
        jst = pytz.timezone('Asia/Tokyo')
        now = datetime.now(jst)
        ws.oddHeader.left.text = "店舗稼働状況レポート"
        ws.oddHeader.right.text = now.strftime("%Y年%m月%d日 %H:%M")
        ws.oddFooter.center.text = "powered by Store Analytics System"

    def _adjust_column_widths(self, ws):
        """列幅を自動調整"""
        for column in ws.columns:
            max_length = 0
            column = [cell for cell in column]
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = (max_length + 2)
            ws.column_dimensions[column[0].column_letter].width = adjusted_width

    def generate_store_report(self, store_data, output_path):
        pass  # 未実装の機能
