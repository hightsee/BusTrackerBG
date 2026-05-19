import sqlite3
from datetime import datetime
from typing import Optional, Dict, Any
from config import APP_DATA_DB


class AppDataManager:
    def __init__(self, db_path: str = APP_DATA_DB):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initializes the SQLite database with required web app tables."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Favorites table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                fav_name TEXT,
                station_uid TEXT,
                station_sid TEXT,
                line TEXT,
                UNIQUE(user_id, fav_name)
            )
        """)
        cursor.execute("PRAGMA table_info(favorites)")
        favorite_columns = {row[1] for row in cursor.fetchall()}
        if "line" not in favorite_columns:
            cursor.execute("ALTER TABLE favorites ADD COLUMN line TEXT")
        
        # API Users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password_hash TEXT,
                created_at TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT UNIQUE NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES api_users(id)
            )
        """)
        
        conn.commit()
        conn.close()

    def register_api_user(self, username: str, password_hash: str) -> bool:
        """Registers a new API user. Returns True on success, False if username exists."""
        current_time = datetime.now().isoformat()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        success = False
        try:
            cursor.execute("INSERT INTO api_users (username, password_hash, created_at) VALUES (?, ?, ?)",
                           (username, password_hash, current_time))
            conn.commit()
            success = True
        except sqlite3.IntegrityError:
            success = False
        finally:
            conn.close()
        return success

    def get_api_user(self, username: str) -> Optional[Dict[str, Any]]:
        """Retrieves an API user by username."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, password_hash FROM api_users WHERE username = ?", (username,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def update_api_user_password(self, user_id: int, password_hash: str) -> bool:
        """Updates a user's password hash."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE api_users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
        conn.commit()
        updated = cursor.rowcount > 0
        conn.close()
        return updated

    def create_password_reset_token(self, user_id: int, token_hash: str, expires_at: str) -> None:
        """Stores a hashed password reset token."""
        current_time = datetime.now().isoformat()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, token_hash, expires_at, current_time),
        )
        conn.commit()
        conn.close()

    def get_valid_password_reset_token(self, token_hash: str, now: str) -> Optional[Dict[str, Any]]:
        """Returns an unused, unexpired reset token record."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT password_reset_tokens.id, password_reset_tokens.user_id, api_users.username
            FROM password_reset_tokens
            JOIN api_users ON api_users.id = password_reset_tokens.user_id
            WHERE password_reset_tokens.token_hash = ?
              AND password_reset_tokens.used_at IS NULL
              AND password_reset_tokens.expires_at > ?
            """,
            (token_hash, now),
        )
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def mark_password_reset_token_used(self, token_id: int) -> None:
        """Marks a reset token as used."""
        current_time = datetime.now().isoformat()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE id = ?",
            (current_time, token_id),
        )
        conn.commit()
        conn.close()

    def save_favorite(self, user_id: str, fav_name: str, station_uid: str, station_sid: str, line: Optional[str] = None) -> str:
        """Saves a favorite station for a user and returns the stored favorite name."""
        user_id = str(user_id)
        base_name = str(fav_name).strip()
        stored_line = str(line).strip() if line else None
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            "SELECT station_uid, station_sid, line FROM favorites WHERE user_id = ? AND fav_name = ?",
            (user_id, base_name),
        )
        existing = cursor.fetchone()
        stored_name = base_name

        if existing:
            existing_line = existing["line"] or None
            same_preset = (
                str(existing["station_uid"]) == str(station_uid)
                and str(existing["station_sid"]) == str(station_sid)
                and existing_line == stored_line
            )

            if not same_preset:
                suffix = f" - {stored_line}" if stored_line else ""
                candidate = f"{base_name}{suffix}" if suffix else f"{base_name} (2)"
                counter = 2
                while True:
                    cursor.execute(
                        "SELECT 1 FROM favorites WHERE user_id = ? AND fav_name = ?",
                        (user_id, candidate),
                    )
                    if not cursor.fetchone():
                        stored_name = candidate
                        break
                    counter += 1
                    candidate = f"{base_name}{suffix} ({counter})" if suffix else f"{base_name} ({counter})"

        cursor.execute("""
            INSERT OR REPLACE INTO favorites (user_id, fav_name, station_uid, station_sid, line)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, stored_name, station_uid, station_sid, stored_line))
        conn.commit()
        conn.close()
        return stored_name

    def get_favorites(self, user_id: str) -> Dict[str, Dict[str, Any]]:
        """Returns all favorites for a user."""
        user_id = str(user_id)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT fav_name, station_uid, station_sid, line FROM favorites WHERE user_id = ?", (user_id,))
        rows = cursor.fetchall()
        conn.close()
        
        favs = {}
        for row in rows:
            favs[row['fav_name']] = {
                "uid": row['station_uid'],
                "sid": row['station_sid'],
                "line": row['line']
            }
        return favs

    def update_favorite(self, user_id: str, old_name: str, new_name: str, station_uid: str, station_sid: str, line: Optional[str] = None) -> Optional[str]:
        """Updates an existing favorite and returns the stored favorite name."""
        user_id = str(user_id)
        old_name = str(old_name).strip()
        base_name = str(new_name).strip()
        stored_line = str(line).strip() if line else None

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND fav_name = ?",
            (user_id, old_name),
        )
        if not cursor.fetchone():
            conn.close()
            return None

        stored_name = base_name
        counter = 2
        while True:
            cursor.execute(
                "SELECT 1 FROM favorites WHERE user_id = ? AND fav_name = ? AND fav_name != ?",
                (user_id, stored_name, old_name),
            )
            if not cursor.fetchone():
                break
            suffix = f" - {stored_line}" if stored_line else ""
            stored_name = f"{base_name}{suffix} ({counter})" if suffix else f"{base_name} ({counter})"
            counter += 1

        cursor.execute("""
            UPDATE favorites
            SET fav_name = ?, station_uid = ?, station_sid = ?, line = ?
            WHERE user_id = ? AND fav_name = ?
        """, (stored_name, station_uid, station_sid, stored_line, user_id, old_name))
        conn.commit()
        conn.close()
        return stored_name

    def delete_favorite(self, user_id: str, fav_name: str) -> bool:
        """Deletes a favorite station for a user."""
        user_id = str(user_id)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM favorites WHERE user_id = ? AND fav_name = ?", (user_id, fav_name))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success


app_data_manager = AppDataManager()
