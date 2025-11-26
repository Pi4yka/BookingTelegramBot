import logging
import os
from datetime import datetime, date, timedelta
from calendar import monthrange
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv
from telegram.ext import MessageHandler, filters
from database import (
    init_db, add_booking, get_booking, get_all_bookings_in_date_range,
    add_sponsor as db_add_sponsor, is_sponsor, get_all_sponsors
)

# Инициализация БД при старте
init_db()

# Загрузим спонсоров в память (опционально, но ускорит проверки)
sponsors_cache = get_all_sponsors()

load_dotenv()
# === Настройка логирования ===
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Отслеживаем username → обновляем кэш спонсоров при необходимости (не обязательно, но полезно для отладки)
usernames = {}

def get_next_30_days():
    today = date.today()
    return [today + timedelta(days=i) for i in range(30)]


def date_to_str(d: date) -> str:
    return d.isoformat()


def is_valid_date_in_range(d_str: str) -> bool:
    """Проверяет, что дата в формате YYYY-MM-DD и попадает в диапазон [сегодня, сегодня+29]."""
    try:
        d = datetime.fromisoformat(d_str).date()
    except ValueError:
        return False
    today = date.today()
    last_day = today + timedelta(days=29)
    return today <= d <= last_day


# === Команды бота ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "Добро пожаловать!\n"
        "Команды:\n"
        "/book YYYY-MM-DD — забронировать стол\n"
        "/list — посмотреть занятые и свободные дни\n"
        "(Админ) /add_sponsor <user_id> — добавить спонсора"
    )
    await update.message.reply_text(msg)

async def book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username  # может быть None
    user_is_sponsor = is_sponsor(user_id)

    if not context.args:
        await update.message.reply_text("Укажите дату: /book YYYY-MM-DD")
        return

    d_str = context.args[0].strip()
    if not is_valid_date_in_range(d_str):
        await update.message.reply_text("Некорректная дата/Дата уже прошла. Используйте YYYY-MM-DD в текущем месяце.")
        return

    d = datetime.fromisoformat(d_str).date()
    current = get_booking(d)

    if current is None:
        add_booking(d, user_id, username, user_is_sponsor)
        await update.message.reply_text(f"✅ Забронировано на {d_str}!")
    else:
        if user_is_sponsor:
            if not current['is_sponsor']:
                add_booking(d, user_id, username, True)
                await update.message.reply_text(f"👑 Спонсор! Бронь на {d_str} передана вам.")
            else:
                await update.message.reply_text(f"❌ Уже занято другим спонсором.")
        else:
            if current['is_sponsor']:
                await update.message.reply_text(f"❌ Занято спонсором.")
            else:
                await update.message.reply_text(f"❌ Уже занято.")


async def list_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dates = get_next_30_days()
    bookings = get_all_bookings_in_date_range(dates)

    lines = []
    for d in dates:
        d_str = d.isoformat()
        booking = bookings.get(d_str)
        if booking:
            status = "👑" if booking['is_sponsor'] else "👤"
            username = booking['username']
            if username:
                user_display = f"@{username}"
            else:
                user_display = f"user{booking['user_id']}"
            lines.append(f"{d_str}: {status} занято → {user_display}")
        else:
            lines.append(f"{d_str}: ✅ свободно")

    message = "📅 Бронирования на ближайшие 30 дней:\n\n" + "\n".join(lines)
    await update.message.reply_text(message)

# Админ-SUPER-команда: добавить спонсора
ADMIN_SUPER_USER_ID = os.getenv("ADMIN_SUPER_USER_ID")  # ⚠️ Замените на ваш user_id

async def add_sponsor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(update.effective_user.id)
    print(ADMIN_SUPER_USER_ID)
    if update.effective_user.id != ADMIN_SUPER_USER_ID:
        await update.message.reply_text("❌ Доступ запрещён.")
        return

    if not context.args:
        await update.message.reply_text("Укажите username: /add_sponsor @username")
        return

    username = context.args[0].lstrip('@').lower()
    if username not in usernames:
        await update.message.reply_text(
            f"❌ @{username} не найден. Пусть пользователь напишет боту хотя бы раз."
        )
        return

    target_id = usernames[username]
    db_add_sponsor(target_id)
    # Обновим кэш (опционально)
    sponsors_cache.add(target_id)
    await update.message.reply_text(f"✅ @{username} теперь спонсор!")

async def track_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.username:
        usernames[user.username.lower()] = user.id  # сохраняем в нижнем регистре для надёжности

# === Запуск бота ===
def main():
    TOKEN = os.getenv("TELEGRAM_TOKEN")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("book", book))
    app.add_handler(CommandHandler("list", list_days))
    app.add_handler(CommandHandler("add_sponsor", add_sponsor))
    app.add_handler(MessageHandler(filters.ALL, track_user))

    print("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()