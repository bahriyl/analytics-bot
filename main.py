import logging
import yaml
from datetime import datetime, timedelta
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


# Завантаження конфігурації
def load_config():
    with open("config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


config = load_config()
TOKEN = config["TELEGRAM_TOKEN"]
WHITELIST = config["WHITELIST_IDS"]
SHEET_ID = config["GOOGLE_SHEET_ID"]
CREDENTIALS_FILE = config["GOOGLE_CREDENTIALS_FILE"]
RANGE_NAME = config.get("RANGE_NAME", "Sheet1!A:F")

# Налаштування логування
logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)

# Ініціалізація бота
bot = telebot.TeleBot(TOKEN)


# Підключення до Google Sheets API
def init_sheets():
    creds = Credentials.from_service_account_file(
        CREDENTIALS_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    service = build('sheets', 'v4', credentials=creds)
    return service.spreadsheets()


sheet = init_sheets()


# Парсинг чисел та відсотків
def parse_number(v) -> float:
    """
    Приймає int, float або рядок, замінює кому на крапку, прибирає пробіли.
    Якщо є знак '%', видаляє його і повертає значення у відсоткових пунктах (10% -> 10.0).
    Неконвертовані значення повертають 0.0
    """
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.replace('\u00A0', '').strip()
        # якщо відсоток, забираємо символ '%'
        is_percent = s.endswith('%')
        if is_percent:
            s = s.rstrip('%').strip()
        s = s.replace(',', '.')
        try:
            num = float(s)
        except ValueError:
            return 0.0
        return num
    return 0.0


# Отримання даних
def fetch_data() -> list[dict]:
    try:
        result = sheet.values().get(
            spreadsheetId=SHEET_ID,
            range=RANGE_NAME,
            valueRenderOption='FORMATTED_VALUE',
            dateTimeRenderOption='FORMATTED_STRING'
        ).execute()
        values = result.get('values', [])
        data = []
        for row in values[1:]:
            if len(row) < 6:
                continue
            raw_date = row[0]
            try:
                date_obj = datetime.strptime(raw_date, "%d.%m.%Y").date()
            except Exception:
                continue
            data.append({
                'date': date_obj,
                'batons': parse_number(row[1]),
                'sales': parse_number(row[2]),
                'type': row[3],
                'expense': parse_number(row[4]),
                'margin': parse_number(row[5])  # процентні або числові значення
            })
        return data
    except Exception:
        logging.exception("Помилка при зчитуванні даних з Google Sheets")
        return []


# Фільтрація за датою
def filter_data(data, start_date=None, end_date=None) -> list[dict]:
    return [item for item in data
            if (start_date is None or item['date'] >= start_date)
            and (end_date is None or item['date'] <= end_date)]


# Обчислення метрик звіту
def compute_report(rows: list[dict]) -> dict:
    total_batons = sum(item['batons'] for item in rows)
    total_sales = sum(item['sales'] for item in rows)
    total_online = sum(item['sales'] for item in rows if item['type'] == 'Онлайн')
    total_fop = sum(item['sales'] for item in rows if item['type'] == 'ФОП')
    total_expense = sum(item['expense'] for item in rows)
    margins = [item['margin'] for item in rows]
    margins_online = [item['margin'] for item in rows if item['type'] == 'Онлайн']
    margins_fop = [item['margin'] for item in rows if item['type'] == 'ФОП']

    avg_margin = sum(margins) / len(margins) if margins else 0.0
    avg_online = sum(margins_online) / len(margins_online) if margins_online else 0.0
    avg_fop = sum(margins_fop) / len(margins_fop) if margins_fop else 0.0

    return {
        'total_batons': total_batons,
        'total_sales': total_sales,
        'total_online': total_online,
        'total_fop': total_fop,
        'expense': total_expense,
        'avg_margin': avg_margin,
        'avg_online': avg_online,
        'avg_fop': avg_fop
    }


# Форматування звіту
def format_report(start_date: datetime.date, end_date: datetime.date, stats: dict) -> str:
    header = f"📊 Звіт Чіназес за {start_date.strftime('%d.%m.%Y')}–{end_date.strftime('%d.%m.%Y')}\n\n"
    body = (
        f"🔹 Батонів продано: {stats['total_batons']}\n"
        f"🔹 Сума продажів: {stats['total_sales']}\n\n"
        f"💳 ФОП-онлайн: {stats['total_online']}\n"
        f"🤝 ФОП-ФОП: {stats['total_fop']}\n\n"
        f"💸 Витрати: {stats['expense']}\n\n"
        f"📈 Середня маржа: {stats['avg_margin']:.2f}%\n"
        f"- Онлайн: {stats['avg_online']:.2f}%\n"
        f"- ФОП: {stats['avg_fop']:.2f}%\n"
    )
    return header + body


# Декоратор доступу
def restricted(func):
    def wrapper(message):
        if message.from_user.id not in WHITELIST:
            bot.send_message(message.chat.id, "⛔️ Доступ заборонено.")
            return
        return func(message)

    return wrapper


@bot.message_handler(commands=['start'])
@restricted
def start_command(message):
    kb = InlineKeyboardMarkup(row_width=1)
    for label, cb in [
        ("Чіназес", "chinazes_")
    ]:
        kb.add(InlineKeyboardButton(label, callback_data=cb))
    bot.send_message(message.chat.id, "Оберіть тип звіту:", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith('chinazes'))
@restricted
def period_command(call):
    kb = InlineKeyboardMarkup(row_width=1)
    for label, cb in [
        ("🗓 Звіт за тиждень", "report_week"),
        ("🗓 Звіт за місяць", "report_month"),
        ("📆 Звіт з початку продажів", "report_all"),
    ]:
        kb.add(InlineKeyboardButton(label, callback_data=cb))
    bot.send_message(call.message.chat.id, "Оберіть період звіту:", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith('report_'))
@restricted
def callback_report(call):
    now = datetime.now().date()
    if call.data == 'report_week':
        start = now - timedelta(days=7)
    elif call.data == 'report_month':
        start = now - timedelta(days=30)
    else:
        all_data = fetch_data()
        start = min((d['date'] for d in all_data), default=now)
    end = now

    data = fetch_data()
    filtered = filter_data(data, start, end)
    stats = compute_report(filtered)
    report_text = format_report(start, end, stats)
    bot.send_message(call.message.chat.id, report_text)


@bot.message_handler(commands=['myid'])
def myid_command(message):
    bot.send_message(message.chat.id, f"🆔 Ваш Telegram ID: `{message.from_user.id}`", parse_mode="Markdown")


if __name__ == '__main__':
    bot.infinity_polling()
