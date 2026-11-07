import os
import logging
import datetime
from datetime import date, timedelta
from typing import List, Optional, Tuple
import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)
from telegram.request import HTTPXRequest
from db import (
    init_db, get_user, ensure_user, set_sponsor_status,
    get_booking, set_booking, cancel_booking, get_user_id_by_username
)
from booking_service import (
    format_status_line,
    can_show_book_button,
    decide_booking_action,
    build_booking_result_message,
)
from dotenv import load_dotenv
from zoneinfo import ZoneInfo
from telegram.ext import JobQueue

load_dotenv()

def get_int_env(name: str, default: Optional[int] = None) -> int:
    value = os.getenv(name)
    if value is None:
        if default is not None:
            return default
        raise RuntimeError(f"Missing required environment variable: {name}")
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer") from exc


BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Missing required environment variable: BOT_TOKEN")

SUPER_ADMIN_ID = get_int_env("SUPER_ADMIN_ID")
ALLOWED_CHAT_ID = get_int_env("ALLOWED_CHAT_ID")
ALLOWED_THREAD_ID = get_int_env("ALLOWED_THREAD_ID")
DAY_DELTA = get_int_env("DAY_DELTA", default=14)
MEETING_THREAD_ID = get_int_env("MEETING_THREAD_ID")
MOSCOW_TZ = ZoneInfo("Europe/Moscow")
logging.basicConfig(level=logging.WARNING)

MAX_BOOKING_MESSAGE_LEN = 300
ASK_BOOKING_MESSAGE = 1

def in_allowed_topic(update: Update) -> bool:
    msg = update.effective_message
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


async def build_main_calendar_markup(user_id: int) -> InlineKeyboardMarkup:
    calendar_keyboard = await build_calendar_keyboard()
    keyboard = [row[:] for row in calendar_keyboard.inline_keyboard]
    keyboard.append([InlineKeyboardButton("🗑️ Закрыть", callback_data=f"close_{user_id}")])
    return InlineKeyboardMarkup(keyboard)


def parse_user_argument(args: List[str]) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    if not args:
        return None, None, "❌ Укажите @username или числовой ID."

    input_arg = args[0]
    if input_arg.startswith('@'):
        return None, input_arg[1:], None

    try:
        return int(input_arg), None, None
    except ValueError:
        return None, None, "❌ Укажите @username или числовой ID."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(update)
    if not in_allowed_topic(update):
        return
    user = update.effective_user
    await ensure_user(user.id, user.username or user.full_name)

    reply_markup = await build_main_calendar_markup(user.id)
    await update.message.reply_text("📅 Выберите дату:", reply_markup=reply_markup)

async def handle_date_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not in_allowed_topic(update):
        return

    user = query.from_user
    await ensure_user(user.id, user.username or f"user{user.id}")
    user_data = await get_user(user.id)
    is_sponsor = user_data["is_sponsor"]

    date_str = query.data[5:]  # "book_YYYY-MM-DD"
    target_date = date.fromisoformat(date_str)
    booking = await get_booking(date_str)

    status_line = format_status_line(booking)

    text = f"📅 <b>{target_date.strftime('%d.%m.%Y')}</b>\n\nСтатус: {status_line}"

    buttons = []

    # === Кнопка "Забронировать" ===
    show_book_button = can_show_book_button(booking, user.id, is_sponsor)
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
    reply_markup = await build_main_calendar_markup(user.id)
    await query.edit_message_text("📅 Выберите дату:", reply_markup=reply_markup)


async def sponsor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_topic(update):
        return
    if update.effective_user.id != SUPER_ADMIN_ID:
        await update.message.reply_text("❌ Только супер-админ может выдавать спонсорство.")
        return

    target_user_id, username, error = parse_user_argument(context.args)
    if error:
        await update.message.reply_text("Использование:\n/sponsor @username\nили\n/sponsor 123456789")
        return

    if username:
        target_user_id = await get_user_id_by_username(username)
        if target_user_id is None:
            await update.message.reply_text(
                f"❌ Пользователь @{username} не найден в базе.\n"
                "Он должен хотя бы раз воспользоваться ботом (нажать /start)."
            )
            return

    await set_sponsor_status(target_user_id, True)
    target_display = f"@{username}" if username else str(target_user_id)
    await update.message.reply_text(f"✅ Пользователь {target_display} теперь спонсор!")


# Аналогично для unsponsor
async def unsponsor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_topic(update):
        return
    if update.effective_user.id != SUPER_ADMIN_ID:
        await update.message.reply_text("❌ Только супер-админ может управлять спонсорством.")
        return

    target_user_id, username, error = parse_user_argument(context.args)
    if error:
        await update.message.reply_text("Использование:\n/unsponsor @username\nили\n/unsponsor 123456789")
        return

    if username:
        target_user_id = await get_user_id_by_username(username)
        if target_user_id is None:
            await update.message.reply_text(
                f"❌ Пользователь @{username} не найден в базе.\n"
                "Он должен хотя бы раз воспользоваться ботом (нажать /start)."
            )
            return

    await set_sponsor_status(target_user_id, False)
    target_display = f"@{username}" if username else str(target_user_id)
    await update.message.reply_text(f"❌ Спонсорство у пользователя {target_display} отозвано.")

async def post_init(application: Application):
    await init_db()
    logging.info("✅ Бот запущен с SQLite (v20+).")

def _back_to_calendar_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Назад к календарю", callback_data="back_calendar")
    ]])


async def confirm_booking_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Точка входа в диалог бронирования: проверяет, можно ли забронировать дату,
    и если да — просит пользователя ввести сообщение для брони."""
    query = update.callback_query
    await query.answer()

    if not in_allowed_topic(update):
        return ConversationHandler.END

    user = query.from_user
    await ensure_user(user.id, user.username or user.full_name)
    user_data = await get_user(user.id)
    is_sponsor = user_data["is_sponsor"]
    username = user.username or f"user{user.id}"

    date_str = query.data[8:]  # "confirm_YYYY-MM-DD"
    target_date = date.fromisoformat(date_str)
    target_date_label = target_date.strftime('%d.%m.%Y')

    # Получаем актуальную бронь (на случай, если за это время изменилась)
    booking = await get_booking(date_str)
    decision = decide_booking_action(booking, user.id, is_sponsor)

    if not decision.should_write:
        message = build_booking_result_message(decision, booking, target_date_label, False)
        await query.edit_message_text(
            f"📅 <b>{target_date_label}</b>\n\n{message}",
            reply_markup=_back_to_calendar_markup(),
            parse_mode="HTML"
        )
        return ConversationHandler.END

    context.user_data["pending_booking"] = {
        "date_str": date_str,
        "target_date_label": target_date_label,
        "user_id": user.id,
        "username": username,
        "write_as_sponsor": decision.write_as_sponsor,
        "decision": decision,
        "booking": booking,
    }

    await query.edit_message_text(
        f"📅 <b>{target_date_label}</b>\n\n"
        "✏️ Введите сообщение, которое увидят другие пользователи при просмотре этой даты "
        "(например, во сколько вас искать), или нажмите «Пропустить».",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⏭️ Пропустить", callback_data="skip_booking_message"),
            InlineKeyboardButton("❌ Отмена", callback_data="cancel_booking_message"),
        ]])
    )
    return ASK_BOOKING_MESSAGE


async def _finalize_booking(update: Update, context: ContextTypes.DEFAULT_TYPE, booking_message: Optional[str]):
    pending = context.user_data.pop("pending_booking", None)
    if not pending:
        return ConversationHandler.END

    if booking_message and len(booking_message) > MAX_BOOKING_MESSAGE_LEN:
        booking_message = booking_message[:MAX_BOOKING_MESSAGE_LEN]

    success = await set_booking(
        pending["date_str"],
        pending["user_id"],
        pending["username"],
        pending["write_as_sponsor"],
        booking_message,
    )

    result_text = build_booking_result_message(
        pending["decision"], pending["booking"], pending["target_date_label"], success
    )
    text = f"📅 <b>{pending['target_date_label']}</b>\n\n{result_text}"

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=_back_to_calendar_markup(), parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            text, reply_markup=_back_to_calendar_markup(), parse_mode="HTML"
        )
    return ConversationHandler.END


async def receive_booking_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_topic(update):
        return  # игнорируем сообщения из других топиков, ждём ответ в нужном
    return await _finalize_booking(update, context, update.message.text.strip())


async def skip_booking_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _finalize_booking(update, context, None)


async def cancel_booking_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop("pending_booking", None)
    reply_markup = await build_main_calendar_markup(query.from_user.id)
    await query.edit_message_text("📅 Выберите дату:", reply_markup=reply_markup)
    return ConversationHandler.END

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
    help_text += "• /book — календарь бронирования. Отображает статус бронирования, даёт возможность занять дату или отказаться от неё.\n"
    help_text += "• При бронировании можно оставить сообщение (например, во сколько вас искать) — его увидят другие пользователи при просмотре этой даты.\n\n"

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

POLL_WEEKDAYS = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
    "Подглядеть 👀"
]

POLL_OPTION_MAX_LEN = 100  # лимит Telegram для текста варианта ответа опроса


async def build_poll_options() -> List[str]:
    """Формирует варианты ответа опроса на СЛЕДУЮЩУЮ неделю (ПН-ВС)."""
    options = []
    today = datetime.datetime.now(MOSCOW_TZ).date()
    
    # Вычисляем, сколько дней осталось до ближайшего понедельника
    days_to_monday = (7 - today.weekday()) % 7
    if days_to_monday == 0:  # Если сегодня уже понедельник, берем следующий
        days_to_monday = 7
        
    next_monday = today + timedelta(days=days_to_monday)
    
    booked_info = {}  # weekday index -> описание брони

    for offset in range(7):
        d = next_monday + timedelta(days=offset)
        idx = d.weekday()  # 0 = понедельник ... 6 = воскресенье
        
        if idx in booked_info:
            continue  # уже нашли забронированный день с таким названием на этой неделе
            
        booking = await get_booking(d.isoformat())
        if not booking:
            continue

        username = str(booking.get("username") or "").lstrip("@")
        crown = "👑 " if booking.get("is_sponsor") else ""
        booking_message = (booking.get("message") or "").strip()

        if booking_message:
            desc = f"{crown}{username}: «{booking_message}»"
        elif username:
            desc = f"{crown}{username}"
        else:
            desc = "бронь"

        option = f"{POLL_WEEKDAYS[idx]} — занят: 🚫 {desc}"
        # обрезаем под лимит Telegram (100 символов на вариант ответа)
        if len(option) > POLL_OPTION_MAX_LEN:
            option = option[: POLL_OPTION_MAX_LEN - 1] + "…"
            
        booked_info[idx] = option

    # Собираем финальный список (0-6 дни недели + 7-й элемент "Подглядеть 👀")
    for idx, day_name in enumerate(POLL_WEEKDAYS):
        options.append(booked_info.get(idx, day_name))

    return options


async def send_weekly_poll(context: ContextTypes.DEFAULT_TYPE):
    question = "Когда вам удобно встретиться на этой неделе?"
    options = await build_poll_options()

    await context.bot.send_poll(
        chat_id=ALLOWED_CHAT_ID,
        message_thread_id=MEETING_THREAD_ID,
        question=question,
        options=options,
        is_anonymous=False,
        is_closed=False,
        allows_multiple_answers=True,
    )

async def test_weekly_meet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPER_ADMIN_ID:
       await update.message.reply_text("❌ Только супер-админ может запускать опрос для теста.") 
       return
    await send_weekly_poll(context)

def main():
    # Кастомный HTTP-транспорт: увеличенные таймауты помогают переживать
    # кратковременные сетевые сбои Telegram API (502 Bad Gateway и т.п.)
    request = HTTPXRequest(connect_timeout=20.0, read_timeout=30.0, media_write_timeout=60.0)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .request(request)
        .build()
    )

    job_queue = app.job_queue
    job_queue.run_daily(
        send_weekly_poll,
        time=datetime.time(hour=19, minute=0, tzinfo=MOSCOW_TZ),
        days=(6,),  # APScheduler (как в python-telegram-bot): 0=пн ... 6=вс — проверено, срабатывает в воскресенье
        name="weekly_poll",
    )

    booking_conversation = ConversationHandler(
        entry_points=[CallbackQueryHandler(confirm_booking_entry, pattern=r"^confirm_")],
        states={
            ASK_BOOKING_MESSAGE: [
                CallbackQueryHandler(skip_booking_message, pattern=r"^skip_booking_message$"),
                CallbackQueryHandler(cancel_booking_message, pattern=r"^cancel_booking_message$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_booking_message),
            ],
        },
        fallbacks=[CallbackQueryHandler(cancel_booking_message, pattern=r"^cancel_booking_message$")],
    )

    app.add_handler(CommandHandler("book", start))
    app.add_handler(CommandHandler("sponsor", sponsor_command))
    app.add_handler(CommandHandler("unsponsor", unsponsor_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("test_weekly_meet", test_weekly_meet_command))
    app.add_handler(CallbackQueryHandler(handle_date_callback, pattern=r"^book_"))
    app.add_handler(booking_conversation)
    app.add_handler(CallbackQueryHandler(cancel_booking_handler, pattern=r"^cancel_\d{4}-\d{2}-\d{2}$"))
    app.add_handler(CallbackQueryHandler(back_to_calendar, pattern=r"^back_calendar$"))
    app.add_handler(CallbackQueryHandler(close_message_handler, pattern=r"^close_\d+$"))

    # Обработчик ошибок: сетевые сбои Telegram (502 Bad Gateway и т.п.) — штатная ситуация,
    # PTB сам переподключается; пишем компактную строку вместо простыни трейсбеков в журнал.
    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
        exc = context.error
        if isinstance(exc, telegram.error.NetworkError):
            logging.getLogger("bot").warning(
                "Сетевая ошибка при работе с Telegram API (бот автоматически продолжит polling): %s",
                exc,
            )
            return  # не логируем полный traceback для временных сетевых сбоев
        logging.getLogger("bot").exception("Необработанное исключение в обработчике:", exc_info=exc)

    app.add_error_handler(on_error)

    app.run_polling()

if __name__ == "__main__":
    main()
