from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from datetime import datetime
import pytz

class ReportGenerator:
    def __init__(self):
        self.styles = getSampleStyleSheet()

    def generate_all_stores_report(self, stores_data, output_path):
        """全店舗の詳細PDFレポートを生成"""
        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=72
        )

        elements = []

        # タイトル
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=self.styles['Heading1'],
            fontSize=24,
            spaceAfter=30
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
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
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

        # 店舗別詳細
        stores_data.sort(key=lambda x: x.get('rate', 0), reverse=True)

        for store in stores_data:
            # 店舗名
            elements.append(Paragraph(f"店舗名: {store.get('store_name', '')}", self.styles['Heading2']))

            # 店舗詳細テーブル
            store_detail = [
                ["業種", "ジャンル", "エリア", "稼働率"],
                [
                    store.get('biz_type', ''),
                    store.get('genre', ''),
                    store.get('area', ''),
                    f"{store.get('rate', 0)}%"
                ]
            ]

            detail_table = Table(store_detail)
            detail_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))

            elements.append(detail_table)
            elements.append(Spacer(1, 20))

        # レポートの生成
        doc.build(elements)
        return output_path

    def generate_store_report(self, store_data, output_path):
        pass # Placeholder -  This function is not used in the provided changes.