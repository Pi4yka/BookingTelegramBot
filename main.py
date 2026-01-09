# bot.py
import os
import logging
from datetime import date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from db import (
    init_db, get_user, ensure_user, set_sponsor_status,
    get_booking, set_booking, cancel_booking, get_user_id_by_username
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID"))
ALLOWED_CHAT_ID = int(os.getenv("ALLOWED_CHAT_ID"))
ALLOWED_THREAD_ID = int(os.getenv("ALLOWED_THREAD_ID"))
day_delta_str = os.getenv("DAY_DELTA")
DAY_DELTA = int(day_delta_str) if day_delta_str is not None else 14
logging.basicConfig(level=logging.WARNING)

def in_allowed_topic(update: Update) -> bool:
    msg = update.effective_message
    print(msg)
    return bool(msg and msg.chat_id == ALLOWED_CHAT_ID and msg.message_thread_id == ALLOWED_THREAD_ID)

def get_dates_in_month():
    today = date.today()

    return [today + timedelta(days=i) for i in range(DAY_DELTA)]

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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_topic(update):
        return
    user = update.effective_user
    await ensure_user(user.id, user.username or user.full_name)

    # Получаем клавиатуру как список списков
    calendar_keyboard = await build_calendar_keyboard()
    # keyboard — это список списков кнопок
    keyboard = [row[:] for row in calendar_keyboard.inline_keyboard]  # делаем копию как список

    # Добавляем кнопку "Закрыть" для автора
    close_button = InlineKeyboardButton("🗑️ Закрыть", callback_data=f"close_{user.id}")
    keyboard.append([close_button])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📅 Выберите дату:", reply_markup=reply_markup)

async def handle_date_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not in_allowed_topic(update):
        # await query.message.reply_text("❌ Бот доступен только в определённом топике.")
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
            show_book_button = False  # уже твоя — не показываем
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

    if not in_allowed_topic(update):
        return

    user = query.from_user

    # Получаем клавиатуру календаря
    calendar_markup = await build_calendar_keyboard()
    keyboard = [row[:] for row in calendar_markup.inline_keyboard]  # делаем копию

    # Добавляем кнопку "Закрыть" для автора
    close_button = InlineKeyboardButton("🗑️ Закрыть", callback_data=f"close_{user.id}")
    keyboard.append([close_button])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("📅 Выберите дату:", reply_markup=reply_markup)


async def sponsor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    input_arg = context.args[0]

    if input_arg.startswith('@'):
        username = input_arg[1:]

    if not in_allowed_topic(update):
        return
    if update.effective_user.id != SUPER_ADMIN_ID:
        await update.message.reply_text("❌ Только супер-админ может выдавать спонсорство.")
        return

    if not context.args:
        await update.message.reply_text(
            "Использование:\n"
            "/sponsor @username\n"
            "или\n"
            "/sponsor 123456789"
        )
        return

    input_arg = context.args[0]

    # Определяем тип: username или ID
    if input_arg.startswith('@'):
        username = input_arg[1:]
        target_user_id = await get_user_id_by_username(username)
        if target_user_id is None:
            await update.message.reply_text(
                f"❌ Пользователь @{username} не найден в базе.\n"
                "Он должен хотя бы раз воспользоваться ботом (нажать /start)."
            )
            return
    else:
        try:
            target_user_id = int(input_arg)
        except ValueError:
            await update.message.reply_text("❌ Укажите @username.")
            return

    await set_sponsor_status(target_user_id, True)
    await update.message.reply_text(f"✅ Пользователь {username} теперь спонсор!")


# Аналогично для unsponsor
async def unsponsor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    input_arg = context.args[0]

    if input_arg.startswith('@'):
        username = input_arg[1:]

    if not in_allowed_topic(update):
        return
    if update.effective_user.id != SUPER_ADMIN_ID:
        await update.message.reply_text("❌ Только супер-админ может управлять спонсорством.")
        return

    if not context.args:
        await update.message.reply_text(
            "Использование:\n"
            "/unsponsor @username\n"
            "или\n"
            "/unsponsor 123456789"
        )
        return

    input_arg = context.args[0]

    if input_arg.startswith('@'):
        username = input_arg[1:]
        target_user_id = await get_user_id_by_username(username)
        if target_user_id is None:
            await update.message.reply_text(
                f"❌ Пользователь @{username} не найден в базе.\n"
                "Он должен хотя бы раз воспользоваться ботом (нажать /start)."
            )
            return
    else:
        try:
            target_user_id = int(input_arg)
        except ValueError:
            await update.message.reply_text("❌ Укажите @username или числовой ID.")
            return

    await set_sponsor_status(target_user_id, False)
    await update.message.reply_text(f"❌ Спонсорство у пользователя {username} отозвано.")

async def post_init(application: Application):
    await init_db()
    logging.info("✅ Бот запущен с SQLite (v20+).")

async def confirm_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🔒 Проверка топика
    if not in_allowed_topic(update):
        # await query.message.reply_text("❌ Бот доступен только в определённом топике.")
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
        elif booking["is_sponsor"] and is_sponsor:
            # Спонсор перебронирует спонсора
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
        # await query.message.reply_text("❌ Бот доступен только в определённом топике.")
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

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_topic(update):
        # await update.message.reply_text("❌ Бот доступен только в определённом топике.")
        return

    user = update.effective_user
    user_data = await get_user(user.id)
    is_super_admin = user_data["is_super_admin"]
    is_sponsor = user_data["is_sponsor"]

    help_text = "ℹ️ <b>Booking Bot — Система бронирования дат</b>\n\n"

    # Общие команды
    help_text += "<b>Доступно всем:</b>\n"
    help_text += "• /book — календарь бронирования. Отображает статус бронирования, даёт возможность занять дату или отказаться от неё.\n\n"

    # Права
    if is_sponsor:
        help_text += "<b>Ваши права:</b> 👑 Спонсор\n"
        help_text += "• Можете бронировать любые даты (включая занятые обычными и спонсорами)\n\n"
    else:
        help_text += "<b>Ваши права:</b> ❌ Обычный пользователь\n"
        help_text += "• Можете бронировать только свободные даты или занятые другими обычными пользователями\n\n"

    # Команды для супер-админа
    if is_super_admin:
        help_text += "<b>Команды супер-админа:</b>\n"
        help_text += "• /sponsor @username — назначить спонсора\n"
        help_text += "• /unsponsor @username — отозвать спонсорство\n"

    help_text += "<i>💡 Чтобы попасть в базу — пользователь должен хотя бы раз написать /book в этом топике.</i>\n"

    # help_text += "<i>💎 Developer: https://github.com/Pi4yka </i>"
    await update.message.reply_text(help_text, parse_mode="HTML")


async def close_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        expected_user_id = int(query.data.split("_")[1])
    except (IndexError, ValueError):
        await query.message.reply_text("❌ Неверный формат данных.")
        return

    if query.from_user.id != expected_user_id:
        await query.answer("🔒 Только автор может закрыть это сообщение.", show_alert=True)
        return

    try:
        await query.message.delete()
    except Exception:
        pass  # Игнорируем ошибки (уже удалено, нет прав и т.д.)


def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("book", start))
    app.add_handler(CommandHandler("sponsor", sponsor_command))
    app.add_handler(CommandHandler("unsponsor", unsponsor_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(handle_date_callback, pattern=r"^book_"))
    app.add_handler(CallbackQueryHandler(confirm_booking, pattern=r"^confirm_"))
    app.add_handler(CallbackQueryHandler(cancel_booking_handler, pattern=r"^cancel_"))
    app.add_handler(CallbackQueryHandler(back_to_calendar, pattern=r"^back_calendar$"))
    app.add_handler(CallbackQueryHandler(close_message_handler, pattern=r"^close_\d+$"))
    app.run_polling()

if __name__ == "__main__":
    main()