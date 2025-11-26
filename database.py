import sqlite3
from datetime import date

DB_NAME = "reservations.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Таблица бронирований: добавляем username (TEXT, может быть NULL)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            date TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            username TEXT,
            is_sponsor BOOLEAN NOT NULL
        )
    ''')

    # Таблица спонсоров
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sponsors (
            user_id INTEGER PRIMARY KEY
        )
    ''')

    # Попытка добавить столбец username, если таблица уже существует (миграция)
    try:
        cursor.execute('ALTER TABLE bookings ADD COLUMN username TEXT')
    except sqlite3.OperationalError:
        # Столбец уже существует
        pass

    conn.commit()
    conn.close()

def add_booking(booking_date: date, user_id: int, username: str | None, is_sponsor: bool):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO bookings (date, user_id, username, is_sponsor)
        VALUES (?, ?, ?, ?)
    ''', (booking_date.isoformat(), user_id, username, is_sponsor))
    conn.commit()
    conn.close()

def get_booking(booking_date: date):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, username, is_sponsor 
        FROM bookings 
        WHERE date = ?
    ''', (booking_date.isoformat(),))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            'user_id': row[0],
            'username': row[1],      # может быть None
            'is_sponsor': bool(row[2])
        }
    return None

def get_all_bookings_in_date_range(dates: list[date]):
    if not dates:
        return {}
    date_strs = [d.isoformat() for d in dates]
    placeholders = ','.join('?' * len(date_strs))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT date, user_id, username, is_sponsor 
        FROM bookings 
        WHERE date IN ({placeholders})
    ''', date_strs)
    rows = cursor.fetchall()
    conn.close()
    return {
        row[0]: {
            'user_id': row[1],
            'username': row[2],
            'is_sponsor': bool(row[3])
        }
        for row in rows
    }

# --- Спонсоры (без изменений) ---
def add_sponsor(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO sponsors (user_id) VALUES (?)', (user_id,))
    conn.commit()
    conn.close()

def is_sponsor(user_id: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM sponsors WHERE user_id = ?', (user_id,))
    result = cursor.fetchone() is not None
    conn.close()
    return result

def get_all_sponsors() -> set:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM sponsors')
    rows = cursor.fetchall()
    conn.close()
    return {row[0] for row in rows}