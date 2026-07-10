import unittest

from booking_service import (
    build_booking_result_message,
    can_show_book_button,
    decide_booking_action,
    format_status_line,
)


class BookingServiceTests(unittest.TestCase):
    def test_format_status_line_for_free_date(self):
        self.assertEqual(format_status_line(None), "Свободно")

    def test_format_status_line_for_booked_date(self):
        booking = {"user_id": 7, "username": "alice", "is_sponsor": True}
        self.assertEqual(format_status_line(booking), "👑 Забронировано @alice")

    def test_format_status_line_includes_booking_message(self):
        booking = {"user_id": 7, "username": "alice", "is_sponsor": False, "message": "буду после 18:00"}
        self.assertEqual(
            format_status_line(booking),
            "❌ Забронировано @alice\n💬 буду после 18:00",
        )

    def test_format_status_line_escapes_booking_message(self):
        booking = {"user_id": 7, "username": "alice", "is_sponsor": False, "message": "<script>&"}
        self.assertEqual(
            format_status_line(booking),
            "❌ Забронировано @alice\n💬 &lt;script&gt;&amp;",
        )

    def test_format_status_line_without_message_key(self):
        booking = {"user_id": 7, "username": "alice", "is_sponsor": False}
        self.assertEqual(format_status_line(booking), "❌ Забронировано @alice")

    def test_show_book_button_for_own_booking_is_hidden(self):
        booking = {"user_id": 7, "username": "alice", "is_sponsor": False}
        self.assertFalse(can_show_book_button(booking, user_id=7, is_sponsor=False))

    def test_show_book_button_for_sponsor_booking_is_hidden_for_regular_user(self):
        booking = {"user_id": 7, "username": "alice", "is_sponsor": True}
        self.assertFalse(can_show_book_button(booking, user_id=9, is_sponsor=False))

    def test_show_book_button_for_sponsor_on_taken_date_is_visible(self):
        booking = {"user_id": 7, "username": "alice", "is_sponsor": False}
        self.assertTrue(can_show_book_button(booking, user_id=9, is_sponsor=True))

    def test_decision_for_free_date(self):
        decision = decide_booking_action(None, user_id=9, is_sponsor=False)
        self.assertEqual(decision.action, "book_free_date")
        self.assertTrue(decision.should_write)
        self.assertFalse(decision.write_as_sponsor)

    def test_decision_blocked_by_sponsor(self):
        booking = {"user_id": 7, "username": "alice", "is_sponsor": True}
        decision = decide_booking_action(booking, user_id=9, is_sponsor=False)
        self.assertEqual(decision.action, "blocked_by_sponsor")
        self.assertFalse(decision.should_write)

    def test_decision_sponsor_override(self):
        booking = {"user_id": 7, "username": "alice", "is_sponsor": False}
        decision = decide_booking_action(booking, user_id=9, is_sponsor=True)
        self.assertEqual(decision.action, "sponsor_override")
        self.assertTrue(decision.should_write)
        self.assertTrue(decision.write_as_sponsor)

    def test_build_message_for_sponsor_override_success(self):
        booking = {"user_id": 7, "username": "alice", "is_sponsor": False}
        decision = decide_booking_action(booking, user_id=9, is_sponsor=True)
        message = build_booking_result_message(decision, booking, "15.04.2026", write_success=True)
        self.assertEqual(message, "👑 Спонсор! Бронь на 15.04.2026 передана вам.")

    def test_build_message_for_blocked_by_sponsor(self):
        booking = {"user_id": 7, "username": "alice", "is_sponsor": True}
        decision = decide_booking_action(booking, user_id=9, is_sponsor=False)
        message = build_booking_result_message(decision, booking, "15.04.2026", write_success=False)
        self.assertEqual(
            message,
            "❌ Дата занята спонсором @alice. Обычные пользователи не могут её забронировать.",
        )


if __name__ == "__main__":
    unittest.main()
