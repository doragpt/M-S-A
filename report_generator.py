import pandas as pd
from datetime import datetime
import pytz

# 日本語フォントの登録
from reportlab.pdfbase.ttfonts import TTFont
import os

FONT_PATH = "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf"
if os.path.exists(FONT_PATH):
    pdfmetrics.registerFont(TTFont('IPAGothic', FONT_PATH))
else:
    # フォールバック: 組み込みのHeiseiフォントを使用
    pdfmetrics.registerFont(UnicodeCIDFont('HeiseiKakuGo-W5'))

# スタイル設定の更新
default_font = 'IPAGothic' if os.path.exists(FONT_PATH) else 'HeiseiKakuGo-W5'
from datetime import datetime
import pytz

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
        doc = SimpleDocTemplate(
            output_path,
            pagesize=landscape(A4),
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36,
            encoding='utf-8'
        )

        elements = []

        # タイトル
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=self.styles['Heading1'],
            fontSize=24,
            spaceAfter=30,
            fontName=default_font,
            encoding='utf-8'
        )
        elements.append(Paragraph("全店舗稼働状況レポート", title_style))

        # 生成日時
        jst = pytz.timezone('Asia/Tokyo')
        now = datetime.now(jst)
        date_str = now.strftime("%Y年%m月%d日 %H:%M")
        elements.append(Paragraph(f"生成日時: {date_str}", self.styles["Normal"]))
        elements.append(Spacer(1, 20))

        # 全体サマリー
        total_stores = len(stores_data)
        total_working = sum(store.get('working_staff', 0) for store in stores_data)
        total_active = sum(store.get('active_staff', 0) for store in stores_data)
        avg_rate = sum(store.get('rate', 0) for store in stores_data) / total_stores if total_stores > 0 else 0

        summary_data = [
            ["総店舗数", "総勤務人数", "総即ヒメ数", "平均稼働率"],
            [
                str(total_stores),
                str(total_working),
                str(total_active),
                f"{round(avg_rate, 1)}%"
            ]
        ]

        summary_table = Table(summary_data)
        summary_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a73e8')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, -1), 'HeiseiKakuGo-W5'),
                ('FONTSIZE', (0, 0), (-1, 0), 14),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#e8f0fe')),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#202124')),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#dadce0')),
                ('FONTSIZE', (0, 1), (-1, -1), 12),
                ('TOPPADDING', (0, 0), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                ('LEFTPADDING', (0, 0), (-1, -1), 12),
                ('RIGHTPADDING', (0, 0), (-1, -1), 12),
                ('ROWHEIGHT', (0, 0), (-1, -1), 30),
            ]))

        elements.append(summary_table)
        elements.append(Spacer(1, 30))

        # 店舗別詳細（2カラムレイアウト）
        stores_data.sort(key=lambda x: x.get('rate', 0), reverse=True)
        
        store_tables = []
        for i in range(0, len(stores_data), 2):
            row = []
            for j in range(2):
                if i + j < len(stores_data):
                    store = stores_data[i + j]
                    store_detail = Table([
                        [Paragraph(f"店舗名: {store.get('store_name', '')}", self.styles['Heading3'])],
                        [Table([
                            ["業種", "ジャンル", "エリア", "稼働率"],
                            [
                                store.get('biz_type', ''),
                                store.get('genre', ''),
                                store.get('area', ''),
                                f"{store.get('rate', 0)}%"
                            ]
                        ], colWidths=[80, 80, 80, 60])]
                    ])
                    store_detail.setStyle(TableStyle([
                        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                        ('FONTNAME', (0, 0), (-1, -1), 'HeiseiKakuGo-W5'),
                        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#dadce0')),
                        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f8f9fa')),
                    ]))
                    row.append(store_detail)
                    row.append(Spacer(20, 20))
            if row:
                store_tables.append(row)
        
        for table_row in store_tables:
            elements.append(Table([table_row], colWidths=[280, 20, 280]))

        # レポートの生成
        doc.build(elements)
        return output_path

    def generate_store_report(self, store_data, output_path):
        pass # Placeholder -  This function is not used in the provided changes.