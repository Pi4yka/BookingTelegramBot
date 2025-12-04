# bot.py
import os
import logging
from datetime import date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from db import (
    init_db, get_user, ensure_user, set_sponsor_status,
    get_booking, set_booking, cancel_booking  # ← ДОБАВЬ cancel_booking сюда
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID"))
ALLOWED_CHAT_ID = int(os.getenv("ALLOWED_CHAT_ID"))

logging.basicConfig(level=logging.INFO)

def in_allowed_topic(update: Update) -> bool:
    msg = update.effective_message
    print(msg)
    return bool(msg and msg.chat_id == ALLOWED_CHAT_ID)
    # return bool(msg and msg.chat_id == ALLOWED_CHAT_ID)

def get_dates_in_month():
    today = date.today()
    next_month = (today.replace(day=28) + timedelta(days=4))
    last_day = next_month - timedelta(days=next_month.day)
    return [today + timedelta(days=i) for i in range((last_day - today).days + 1)]

# --- Генерация клавиатуры календаря (выносим в отдельную функцию) ---
async def build_calendar_keyboard():
    keyboard = []
    row = []
    for d in get_dates_in_month():
        d_str = d.isoformat()
        booking = await get_booking(d_str)
        is_booked = booking is not None
        is_sponsor_booking = booking["is_sponsor"] if booking else False
        emoji = "👑" if is_sponsor_booking else "❌" if is_booked else "📅"
        row.append(InlineKeyboardButton(f"{emoji} {d.strftime('%d.%m')}", callback_data=f"book_{d_str}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

# --- /start — показывает календарь ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_topic(update):
        return
    user = update.effective_user
    await ensure_user(user.id, user.username or user.full_name)
    reply_markup = await build_calendar_keyboard()
    await update.message.reply_text("📅 Выберите дату:", reply_markup=reply_markup)

async def handle_date_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not in_allowed_topic(update):
        await query.message.reply_text("❌ Бот доступен только в определённом топике.")
        return

    user = query.from_user
    await ensure_user(user.id, user.username or f"user{user.id}")
    user_data = await get_user(user.id)
    is_sponsor = user_data["is_sponsor"]

    date_str = query.data[5:]  # "book_YYYY-MM-DD"
    target_date = date.fromisoformat(date_str)
    booking = await get_booking(date_str)

    # Формируем текст
    if booking:
        mark = "👑" if booking["is_sponsor"] else "❌"
        username_display = booking["username"] or f"user{booking['user_id']}"
        status_line = f"{mark} Забронировано @{username_display}"
    else:
        status_line = "Свободно"

    text = f"📅 <b>{target_date.strftime('%d.%m.%Y')}</b>\n\nСтатус: {status_line}"

    buttons = []

    # === Кнопка "Забронировать" ===
    show_book_button = True
    if booking:
        if booking["user_id"] == user.id:
            show_book_button = False  # уже забронировано тобой
        elif booking["is_sponsor"] and not is_sponsor:
            show_book_button = False  # обычный не может брать у спонсора

    if show_book_button:
        buttons.append(InlineKeyboardButton("✅ Забронировать", callback_data=f"confirm_{date_str}"))

    # === Кнопка "Отказаться от брони" ===
    if booking and booking["user_id"] == user.id:
        buttons.append(InlineKeyboardButton("🗑️ Отказаться от брони", callback_data=f"cancel_{date_str}"))

    buttons.append(InlineKeyboardButton("⬅️ Назад к календарю", callback_data="back_calendar"))

    # Разбиваем кнопки по строкам (макс 2 в строке для читаемости)
    keyboard = []
    for i in range(0, len(buttons), 2):
        keyboard.append(buttons[i:i+2])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

# --- Обработка кнопки "Назад к календарю" ---
async def back_to_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🔒 Проверка топика (для безопасности)
    if not in_allowed_topic(update):
        await query.message.reply_text("❌ Бот доступен только в определённом топике.")
        return

    reply_markup = await build_calendar_keyboard()
    await query.edit_message_text("📅 Выберите дату:", reply_markup=reply_markup)

async def sponsor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_topic(update):
        return
    if update.effective_user.id != SUPER_ADMIN_ID:
        await update.message.reply_text("❌ Только супер-админ может выдавать спонсорство.")
        return
    if not context.args:
        await update.message.reply_text("Использование: /sponsor 123456789")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Укажите ID (число).")
        return
    await set_sponsor_status(target_id, True)
    await update.message.reply_text(f"✅ Пользователь {target_id} — теперь спонсор!")

async def unsponsor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPER_ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /unsponsor 123456789")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Укажите ID (число).")
        return
    await set_sponsor_status(target_id, False)
    await update.message.reply_text(f"❌ Спонсорство у {target_id} отозвано.")

async def post_init(application: Application):
    await init_db()
    logging.info("✅ Бот запущен с SQLite (v20+).")

async def confirm_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🔒 Проверка топика
    if not in_allowed_topic(update):
        await query.message.reply_text("❌ Бот доступен только в определённом топике.")
        return

    user = query.from_user
    await ensure_user(user.id, user.username or user.full_name)
    user_data = await get_user(user.id)
    is_sponsor = user_data["is_sponsor"]
    username = user.username or f"user{user.id}"

    date_str = query.data[8:]  # "confirm_YYYY-MM-DD"
    target_date = date.fromisoformat(date_str)

    # Получаем актуальную бронь (на случай, если за это время изменилась)
    booking = await get_booking(date_str)

    message = ""
    success = False

    if booking:
        if booking["user_id"] == user.id:
            message = "❌ Вы уже забронировали этот день."
        elif booking["is_sponsor"] and not is_sponsor:
            message = f"❌ Дата занята спонсором @{booking['username']}. Обычные пользователи не могут её забронировать."
        elif not booking["is_sponsor"] and is_sponsor:
            # Спонсор перебронирует обычного
            success = await set_booking(date_str, user.id, username, True)
            if success:
                message = f"👑 Спонсор! Бронь на {target_date.strftime('%d.%m.%Y')} передана вам."
            else:
                message = "⚠️ Ошибка при перебронировании."
        else:
            # Например: обычный нажал на дату, занятую другим обычным (но это не должно происходить — кнопка не отображается)
            message = "❌ Дата уже занята другим пользователем."
    else:
        # Дата свободна
        success = await set_booking(date_str, user.id, username, is_sponsor)
        if success:
            mark = "👑" if is_sponsor else "❌"
            message = f"{mark} Дата {target_date.strftime('%d.%m.%Y')} успешно забронирована!"
        else:
            message = "⚠️ Не удалось выполнить бронирование."

    # Отправляем результат
    await query.edit_message_text(
        f"📅 <b>{target_date.strftime('%d.%m.%Y')}</b>\n\n{message}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Назад к календарю", callback_data="back_calendar")
        ]]),
        parse_mode="HTML"
    )

async def cancel_booking_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not in_allowed_topic(update):
        await query.message.reply_text("❌ Бот доступен только в определённом топике.")
        return

    user = query.from_user
    date_str = query.data[7:]  # "cancel_YYYY-MM-DD"
    target_date = date.fromisoformat(date_str)

    # Проверим, что бронь действительно принадлежит пользователю
    booking = await get_booking(date_str)
    if not booking or booking["user_id"] != user.id:
        await query.edit_message_text(
            f"📅 <b>{target_date.strftime('%d.%m.%Y')}</b>\n\n❌ Вы не можете отменить чужую бронь.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Назад к календарю", callback_data="back_calendar")
            ]])
        )
        return

    # Удаляем бронь
    await cancel_booking(date_str)

    await query.edit_message_text(
        f"📅 <b>{target_date.strftime('%d.%m.%Y')}</b>\n\n✅ Ваша бронь отменена. Дата теперь свободна.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Назад к календарю", callback_data="back_calendar")
        ]])
    )

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("sponsor", sponsor_command))
    app.add_handler(CommandHandler("unsponsor", unsponsor_command))
    app.add_handler(CallbackQueryHandler(handle_date_callback, pattern=r"^book_"))
    app.add_handler(CallbackQueryHandler(confirm_booking, pattern=r"^confirm_"))
    app.add_handler(CallbackQueryHandler(cancel_booking_handler, pattern=r"^cancel_"))  # ← НОВОЕ
    app.add_handler(CallbackQueryHandler(back_to_calendar, pattern=r"^back_calendar$"))
    app.run_polling()

if __name__ == "__main__":
    main()