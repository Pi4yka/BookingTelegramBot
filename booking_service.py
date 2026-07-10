import html
from dataclasses import dataclass
from typing import Any, Mapping, Literal, Optional


BookingAction = Literal[
    "already_mine",
    "blocked_by_sponsor",
    "sponsor_override",
    "occupied_by_user",
    "book_free_date",
]


@dataclass(frozen=True)
class BookingDecision:
    action: BookingAction
    should_write: bool
    write_as_sponsor: bool


def format_status_line(booking: Optional[Mapping[str, Any]]) -> str:
    if not booking:
        return "Свободно"

    mark = "👑" if booking["is_sponsor"] else "❌"
    username_display = booking["username"] or f"user{booking['user_id']}"
    line = f"{mark} Забронировано @{username_display}"

    booking_message = booking.get("message") if hasattr(booking, "get") else None
    if booking_message:
        line += f"\n💬 {html.escape(booking_message)}"

    return line


def can_show_book_button(
    booking: Optional[Mapping[str, Any]],
    user_id: int,
    is_sponsor: bool,
) -> bool:
    if not booking:
        return True

    if booking["user_id"] == user_id:
        return False

    if booking["is_sponsor"] and not is_sponsor:
        return False

    return True


def decide_booking_action(
    booking: Optional[Mapping[str, Any]],
    user_id: int,
    is_sponsor: bool,
) -> BookingDecision:
    if not booking:
        return BookingDecision(
            action="book_free_date",
            should_write=True,
            write_as_sponsor=is_sponsor,
        )

    if booking["user_id"] == user_id:
        return BookingDecision(
            action="already_mine",
            should_write=False,
            write_as_sponsor=False,
        )

    if booking["is_sponsor"] and not is_sponsor:
        return BookingDecision(
            action="blocked_by_sponsor",
            should_write=False,
            write_as_sponsor=False,
        )

    if is_sponsor:
        return BookingDecision(
            action="sponsor_override",
            should_write=True,
            write_as_sponsor=True,
        )

    return BookingDecision(
        action="occupied_by_user",
        should_write=False,
        write_as_sponsor=False,
    )


def build_booking_result_message(
    decision: BookingDecision,
    booking: Optional[Mapping[str, Any]],
    target_date_label: str,
    write_success: bool,
) -> str:
    if decision.action == "already_mine":
        return "❌ Вы уже забронировали этот день."

    if decision.action == "blocked_by_sponsor":
        sponsor_name = booking["username"] if booking else "unknown"
        return f"❌ Дата занята спонсором @{sponsor_name}. Обычные пользователи не могут её забронировать."

    if decision.action == "sponsor_override":
        if write_success:
            return f"👑 Спонсор! Бронь на {target_date_label} передана вам."
        return "⚠️ Ошибка при перебронировании."

    if decision.action == "book_free_date":
        if write_success:
            mark = "👑" if decision.write_as_sponsor else "❌"
            return f"{mark} Дата {target_date_label} успешно забронирована!"
        return "⚠️ Не удалось выполнить бронирование."

    return "❌ Дата уже занята другим пользователем."
