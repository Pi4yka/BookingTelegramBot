# db.py
import sqlite3
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()
DB_PATH = "reservations.db"
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID"))

def _init_db_sync():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            is_sponsor BOOLEAN DEFAULT 0,
            is_super_admin BOOLEAN DEFAULT 0
        )
    ''')
    c.execute('''
        INSERT OR IGNORE INTO users (user_id, is_super_admin)
        VALUES (?, 1)
    ''', (SUPER_ADMIN_ID,))
    c.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            date TEXT PRIMARY KEY,
            user_id INTEGER,
            username TEXT,
            is_sponsor BOOLEAN
        )
    ''')
    conn.commit()
    conn.close()

def _get_user_sync(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, username, is_sponsor, is_super_admin FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "user_id": row[0],
            "username": row[1],
            "is_sponsor": bool(row[2]),
            "is_super_admin": bool(row[3])
        }
    return None

def _ensure_user_sync(user_id: int, username: str = None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username or "unknown"))
    if username:
        c.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
    conn.commit()
    conn.close()

def _set_sponsor_status_sync(target_user_id: int, is_sponsor: bool):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET is_sponsor = ? WHERE user_id = ?", (int(is_sponsor), target_user_id))
    conn.commit()
    conn.close()

def _get_booking_sync(date_str: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, username, is_sponsor FROM bookings WHERE date = ?", (date_str,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "user_id": row[0],
            "username": row[1],
            "is_sponsor": bool(row[2])
        }
    return None

def _set_booking_sync(date_str: str, user_id: int, username: str, is_sponsor: bool):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    current = _get_booking_sync(date_str)
    if current and current["is_sponsor"] and not is_sponsor:
        conn.close()
        return False
    c.execute('''
        INSERT OR REPLACE INTO bookings (date, user_id, username, is_sponsor)
        VALUES (?, ?, ?, ?)
    ''', (date_str, user_id, username, int(is_sponsor)))
    conn.commit()
    conn.close()
    return True

# Асинхронные обёртки
async def init_db():
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _init_db_sync)

async def get_user(user_id: int):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _get_user_sync, user_id)

async def ensure_user(user_id: int, username: str = None):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _ensure_user_sync, user_id, username)

async def set_sponsor_status(target_user_id: int, is_sponsor: bool):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _set_sponsor_status_sync, target_user_id, is_sponsor)

async def get_booking(date_str: str):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _get_booking_sync, date_str)

async def set_booking(date_str: str, user_id: int, username: str, is_sponsor: bool):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _set_booking_sync, date_str, user_id, username, is_sponsor)

def _cancel_booking_sync(date_str: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM bookings WHERE date = ?", (date_str,))
    conn.commit()
    conn.close()

async def cancel_booking(date_str: str):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _cancel_booking_sync, date_str)

def _get_user_id_by_username_sync(username: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE username = ? COLLATE NOCASE", (username,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

async def get_user_id_by_username(username: str):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _get_user_id_by_username_sync, username)