import sqlite3
import json
import os
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any
from config import BOT_DATA_DB
from api_client import fetch_stations_list, find_station_id_by_uid


class BotDataManager:
    def __init__(self, db_path: str = BOT_DATA_DB):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initializes the SQLite database with required tables."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT,
                first_started TEXT,
                last_used TEXT
            )
        """)
        
        # Favorites table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                fav_name TEXT,
                station_uid TEXT,
                station_sid TEXT,
                UNIQUE(user_id, fav_name)
            )
        """)
        
        # API Users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password_hash TEXT,
                created_at TEXT
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

    def update_user(self, user_id: str, username: str):
        """Adds a new user or updates an existing user's last_used timestamp."""
        user_id = str(user_id)
        current_time = datetime.now().isoformat()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT first_started FROM users WHERE user_id = ?", (user_id,))
        res = cursor.fetchone()
        
        if res:
            cursor.execute("UPDATE users SET username = ?, last_used = ? WHERE user_id = ?", 
                           (username, current_time, user_id))
        else:
            cursor.execute("INSERT INTO users (user_id, username, first_started, last_used) VALUES (?, ?, ?, ?)",
                           (user_id, username, current_time, current_time))
            logging.info(f"New user registered: {user_id} ({username})")
            
        conn.commit()
        conn.close()

    def get_users(self) -> List[Dict[str, Any]]:
        """Returns a list of all registered users."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users ORDER BY first_started DESC")
        users = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return users

    def save_favorite(self, user_id: str, fav_name: str, station_uid: str, station_sid: str):
        """Saves a favorite station for a user."""
        user_id = str(user_id)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO favorites (user_id, fav_name, station_uid, station_sid)
            VALUES (?, ?, ?, ?)
        """, (user_id, fav_name, station_uid, station_sid))
        conn.commit()
        conn.close()

    def get_favorites(self, user_id: str) -> Dict[str, Dict[str, Any]]:
        """Returns all favorites for a user in the same format as the old JSON structure."""
        user_id = str(user_id)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT fav_name, station_uid, station_sid FROM favorites WHERE user_id = ?", (user_id,))
        rows = cursor.fetchall()
        conn.close()
        
        favs = {}
        for row in rows:
            favs[row['fav_name']] = {
                "uid": row['station_uid'],
                "sid": row['station_sid']
            }
        return favs

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

    def fix_missing_station_ids(self):
        """Fixes existing favorites that have 'N/A' as their station_sid."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, station_uid FROM favorites WHERE station_sid = 'N/A' OR station_sid IS NULL")
        rows = cursor.fetchall()
        
        if not rows:
            conn.close()
            return
            
        logging.info(f"Found {len(rows)} favorites with missing station IDs. Resolving...")
        all_stations = fetch_stations_list()
        
        for row_id, s_uid in rows:
            s_sid = find_station_id_by_uid(s_uid, all_stations)
            if s_sid and s_sid != "N/A":
                cursor.execute("UPDATE favorites SET station_sid = ? WHERE id = ?", (s_sid, row_id))
        
        conn.commit()
        conn.close()
        logging.info("Station ID fix completed.")

    def migrate_from_json(self, users_file: str = "users.json", favorites_file: str = "favorites.json"):
        """Migrates data from legacy JSON files to SQLite."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if users already exist
        cursor.execute("SELECT COUNT(*) FROM users")
        if cursor.fetchone()[0] == 0 and os.path.exists(users_file):
            logging.info(f"Migrating users from {users_file}...")
            try:
                with open(users_file, 'r', encoding='utf-8') as f:
                    users_data = json.load(f)
                    for uid, info in users_data.items():
                        cursor.execute("""
                            INSERT INTO users (user_id, username, first_started, last_used)
                            VALUES (?, ?, ?, ?)
                        """, (uid, info.get("username", "N/A"), info.get("first_started", "Nepoznato"), info.get("first_started", "Nepoznato")))
                conn.commit()
                logging.info("Users migration completed.")
            except Exception as e:
                logging.error(f"Error migrating users: {e}")

        # Check if favorites already exist
        cursor.execute("SELECT COUNT(*) FROM favorites")
        if cursor.fetchone()[0] == 0 and os.path.exists(favorites_file):
            logging.info(f"Migrating favorites from {favorites_file}...")
            try:
                with open(favorites_file, 'r', encoding='utf-8') as f:
                    favs_data = json.load(f)
                    for uid, user_favs in favs_data.items():
                        for fav_name, fav_info in user_favs.items():
                            if isinstance(fav_info, dict):
                                s_uid = fav_info.get("uid")
                                s_sid = fav_info.get("sid", "N/A")
                            else:
                                s_uid = fav_info
                                s_sid = "N/A" # Will be updated if possible or kept as N/A
                            
                            cursor.execute("""
                                INSERT OR REPLACE INTO favorites (user_id, fav_name, station_uid, station_sid)
                                VALUES (?, ?, ?, ?)
                            """, (uid, fav_name, s_uid, s_sid))
                conn.commit()
                logging.info("Favorites migration completed.")
            except Exception as e:
                logging.error(f"Error migrating favorites: {e}")
        
        conn.close()




bot_data_manager = BotDataManager()
