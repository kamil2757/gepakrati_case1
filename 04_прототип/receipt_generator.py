# receipt_generator.py
import datetime
import io
import os
from fpdf import FPDF
import qrcode

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RECEIPTS_DIR = os.path.join(BASE_DIR, "receipts")
os.makedirs(RECEIPTS_DIR, exist_ok=True)


def get_font_paths():
    """Ищет шрифты с поддержкой кириллицы (Windows / Linux / Mac)."""
    candidates_reg = [
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    candidates_bold = [
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\calibrib.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    reg = next((p for p in candidates_reg if os.path.exists(p)), None)
    bold = next((p for p in candidates_bold if os.path.exists(p)), None)
    return reg, bold


def format_receipt_date(iso_str: str) -> str:
    """Преобразует дату в вид: 26.02.2025 в 21.39"""
    try:
        dt = datetime.datetime.fromisoformat(iso_str.replace("Z", "+03:00"))
    except Exception:
        dt = datetime.datetime.now()
    return dt.strftime("%d.%m.%Y в %H.%M")


def draw_dashed(pdf, y, x1=5, x2=75, step=2):
    """Рисует аккуратную пунктирную линию."""
    for x in range(int(x1), int(x2), int(step * 2)):
        pdf.line(x, y, x + step, y)


def generate_fiscal_receipt(payment: dict, auto_open: bool = True) -> str:
    """Генерирует векторный PDF-чек с выделяемым текстом по макету."""
    payment_id = payment.get("payment_id", "0000")
    receipt_no = abs(hash(payment_id)) % 10000

    date_str = format_receipt_date(payment.get("confirmed_at", ""))
    recipient_name = payment.get("recipient", "Клиника Мед-Сервис")
    purpose = payment.get("purpose", "Оплата медицинских услуг")
    if len(purpose) > 22:
        purpose = purpose[:20] + ".."
    amount = float(payment.get("amount", 0.0))

    # Стандартный кассовый формат ленты: ширина 80 мм, высота 165 мм
    pdf = FPDF(unit="mm", format=(80, 165))
    pdf.set_margins(5, 5, 5)
    pdf.set_auto_page_break(False)
    pdf.add_page()

    # Подключаем кириллицу
    font_reg, font_bold = get_font_paths()
    if font_reg:
        pdf.add_font("ReceiptFont", "", font_reg)
        pdf.add_font("ReceiptFont", "B", font_bold if font_bold else font_reg)
        font_name = "ReceiptFont"
    else:
        font_name = "helvetica"

    # Шапка
    pdf.set_font(font_name, "B", 12)
    pdf.cell(70, 5, f"КАССОВЫЙ ЧЕК № {receipt_no}", align="C")
    pdf.ln(5)

    pdf.set_font(font_name, "", 8)
    pdf.cell(70, 4, date_str, align="C")
    pdf.ln(4)

    pdf.set_font(font_name, "B", 9)
    pdf.cell(70, 4, recipient_name, align="C")
    pdf.ln(4)

    pdf.set_font(font_name, "", 8)
    pdf.cell(70, 4, "ИНН 5645434352", align="C")
    pdf.ln(4)
    pdf.cell(70, 4, "105077, г. Москва, ул. Эво Тора, 6", align="C")
    pdf.ln(5)

    # ПРИХОД
    pdf.set_font(font_name, "B", 10)
    pdf.cell(70, 5, "ПРИХОД", align="L")
    pdf.ln(6)

    draw_dashed(pdf, pdf.get_y())
    pdf.ln(2)

    # Заголовки таблицы
    col_w = [30, 14, 10, 16]
    pdf.set_font(font_name, "", 8)
    pdf.cell(col_w[0], 4, "Наименование", align="L")
    pdf.cell(col_w[1], 4, "Цена", align="R")
    pdf.cell(col_w[2], 4, "Кол-во", align="R")
    pdf.cell(col_w[3], 4, "Сумма", align="R")
    pdf.ln(5)

    draw_dashed(pdf, pdf.get_y())
    pdf.ln(2)

    # Строка товара
    pdf.set_font(font_name, "", 8)
    pdf.cell(col_w[0], 5, purpose, align="L")
    pdf.cell(col_w[1], 5, f"{amount:.2f}", align="R")
    pdf.cell(col_w[2], 5, "1", align="R")
    pdf.cell(col_w[3], 5, f"{amount:.2f}", align="R")
    pdf.ln(6)

    draw_dashed(pdf, pdf.get_y())
    pdf.ln(3)

    # ИТОГО
    pdf.set_font(font_name, "B", 10)
    pdf.cell(35, 6, "Итого", align="L")
    pdf.cell(35, 6, f"{amount:.2f}", align="R")
    pdf.ln(8)

    # Способ оплаты
    pdf.set_font(font_name, "", 9)
    pdf.cell(70, 5, "Оплата СБП Альфа-банк", align="C")
    pdf.ln(6)

    # Генерация и вставка QR-кода
    qr_payload = f"t={payment.get('confirmed_at')}&s={amount:.2f}&fn=99990789&i={receipt_no}&fp=12345678&n=1"
    qr_img = qrcode.make(qr_payload)
    buf = io.BytesIO()
    qr_img.save(buf, format="PNG")
    buf.seek(0)

    # Центрируем QR-код (размер 34x34 мм на ленте 80 мм)
    pdf.image(buf, x=23, y=pdf.get_y(), w=34, h=34)

    # Сохраняем PDF
    filename = f"receipt_{payment_id}.pdf"
    filepath = os.path.join(RECEIPTS_DIR, filename)
    pdf.output(filepath)

    # Открываем в стандартном просмотрщике PDF (браузер / Acrobat)
    if auto_open:
        try:
            os.startfile(os.path.abspath(filepath))
        except Exception:
            pass

    return os.path.join("receipts", filename)