"""Authenticated exports of the report currently selected by the user."""
import io
from html import escape
from fastapi import Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

class ReportExport(BaseModel):
    title: str = Field(max_length=160)
    period: str = Field(max_length=100)
    headers: list[str] = Field(max_length=15)
    rows: list[list[str | int | float | None]] = Field(max_length=20000)

def install(app, auth):
    @app.post('/api/sales-reports/export/{extension}')
    def export(extension: str, report: ReportExport, authorization: str | None = Header(default=None)):
        auth(authorization)
        if extension not in ('pdf', 'xlsx'):
            raise HTTPException(400, 'Formato inválido')
        if not report.headers or any(len(r) != len(report.headers) for r in report.rows):
            raise HTTPException(400, 'Colunas inválidas')
        if any(len(str(v)) > 4000 for row in [report.headers, *report.rows] for v in row):
            raise HTTPException(400, 'Conteúdo muito extenso')
        out = io.BytesIO()
        if extension == 'xlsx':
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
            from openpyxl.utils import get_column_letter
            book = Workbook(); sheet = book.active; sheet.title = 'Relatório'
            for row in [[report.title], [report.period], report.headers, *report.rows]:
                sheet.append(row)
                for cell in sheet[sheet.max_row]:
                    if isinstance(cell.value, str): cell.data_type = 's'
            sheet.freeze_panes = 'A4'; sheet.auto_filter.ref = f'A3:{get_column_letter(len(report.headers))}{sheet.max_row}'
            for cell in sheet[3]:
                cell.font = Font(color='FFFFFF', bold=True); cell.fill = PatternFill('solid', fgColor='163F48')
            for i in range(1, len(report.headers)+1): sheet.column_dimensions[get_column_letter(i)].width = 26
            book.save(out); mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        else:
            from reportlab.platypus import SimpleDocTemplate, Paragraph, LongTable, TableStyle, Spacer
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.lib.pagesizes import A4, landscape
            from reportlab.lib import colors
            styles = getSampleStyleSheet(); width = landscape(A4)[0]-56
            def para(v): return Paragraph(escape(str(v if v is not None else '—')), styles['BodyText'])
            data = [[para(v) for v in row] for row in [report.headers, *report.rows]]
            table = LongTable(data, colWidths=[width/len(report.headers)]*len(report.headers), repeatRows=1)
            table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#dce8ea')),('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.3,colors.lightgrey),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
            SimpleDocTemplate(out, pagesize=landscape(A4), leftMargin=28,rightMargin=28,topMargin=28,bottomMargin=28).build([Paragraph(escape(report.title),styles['Title']),para(report.period),Spacer(1,12),table])
            mime = 'application/pdf'
        return Response(out.getvalue(), media_type=mime, headers={'Content-Disposition': f'attachment; filename="L2_Relatorio.{extension}"','Cache-Control':'no-store'})
