import logging
import json
import base64
import time
import os
import urllib.parse
from typing import List, Optional, Dict, Any, Set, cast
import requests
from dotenv import load_dotenv
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

# Load environment variables
load_dotenv()

# --- API and Encryption Logic (Reused from bus_test.py) ---

API_KEY = "1688dc355af72ef09287"
BASE_URL = "https://announcement-bgnaplata.ticketing.rs"
AES_KEY_B64 = "3+Lhz8XaOli6bHIoYPGuq9Y8SZxEjX6eN7AFPZuLCLs="
AES_IV_B64 = "IvUScqUudyxBTBU9ZCyjow=="
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
FAVORITES_FILE = "favorites.json"
USERS_FILE = "users.json"

def load_favorites() -> Dict[str, Dict[str, Any]]:
    """Loads favorites from the JSON file."""
    try:
        import os
        if not os.path.exists(FAVORITES_FILE):
            return {}
        with open(FAVORITES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error loading favorites: {e}")
        return {}

def save_favorites(favorites_data: Dict[str, Dict[str, Any]]):
    """Saves favorites to the JSON file."""
    try:
        with open(FAVORITES_FILE, 'w', encoding='utf-8') as f:
            json.dump(favorites_data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Error saving favorites: {e}")

def load_users() -> Dict[str, Dict[str, Any]]:
    """Loads users from the JSON file."""
    try:
        if not os.path.exists(USERS_FILE):
            return {}
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error loading users: {e}")
        return {}

def save_users(users_data: Dict[str, Dict[str, Any]]):
    """Saves users to the JSON file."""
    try:
        with open(USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(users_data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Error saving users: {e}")

def get_aes_cipher():
    key = base64.b64decode(AES_KEY_B64)
    iv = base64.b64decode(AES_IV_B64)
    return AES.new(key, AES.MODE_CBC, iv)

def encrypt_payload(data_str):
    cipher = get_aes_cipher()
    padded_data = pad(data_str.encode('utf-8'), AES.block_size)
    encrypted_bytes = cipher.encrypt(padded_data)
    encrypted_b64 = base64.b64encode(encrypted_bytes).decode('utf-8')
    return urllib.parse.quote(encrypted_b64)

def decrypt_response(encrypted_str):
    url_decoded = urllib.parse.unquote(encrypted_str)
    encrypted_bytes = base64.b64decode(url_decoded)
    cipher = get_aes_cipher()
    decrypted_padded = cipher.decrypt(encrypted_bytes)
    decrypted_bytes = unpad(decrypted_padded, AES.block_size)
    return decrypted_bytes.decode('utf-8')

def fetch_stations_list() -> List[Dict[str, Any]]:
    url = f"{BASE_URL}/publicapi/v1/networkextended.php?action=get_cities_extended"
    headers = {
        "X-Api-Authentication": API_KEY,
        "User-Agent": "okhttp/4.10.0"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        stations = data.get('stations', [])
        return stations
    except Exception as e:
        logging.error(f"Error in fetch_stations_list: {e}")
        return []

def find_station_uid(target_id: str, all_stations: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    if all_stations is None:
        all_stations = fetch_stations_list()
    
    for station in all_stations:
        stat_id = str(station.get("station_id", ""))
        internal_uid = str(station.get("id", ""))
        
        if stat_id == target_id or internal_uid == target_id:
            return str(station.get("id"))
    return None

def find_station_id_by_uid(target_uid: str, all_stations: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """Finds the public station ID for a given internal UID."""
    if all_stations is None:
        all_stations = fetch_stations_list()
    if not all_stations:
        return None
        
    for station in all_stations:
        if str(station.get("id")) == str(target_uid):
            return str(station.get("station_id", "N/A"))
    return None

async def resolve_station_identifier(user_id: str, identifier: str) -> Dict[str, Any]:
    """
    Resolves a station identifier (favorite name, station ID, or station name) to a station UID.
    Returns a dict with 'uid', 'name', 'id_display', and 'error' or 'matches' (for ambiguity).
    """
    user_id_str = str(user_id)
    favorites = load_favorites()
    user_favs = favorites.get(user_id_str, {})

    # 1. Check favorites
    for fav_name, fav_data in user_favs.items():
        if fav_name.lower() == identifier.lower():
            # Handle both old string format and new dict format
            if isinstance(fav_data, dict):
                return {"uid": fav_data["uid"], "name": fav_name, "id_display": fav_data.get("sid", "FAV")}
            else:
                return {"uid": fav_data, "name": fav_name, "id_display": "FAV"}

    # 2. Check if numeric (raw station ID)
    if identifier.isdigit():
        uid = find_station_uid(identifier)
        if uid:
            return {"uid": uid, "name": f"Station {identifier}", "id_display": identifier}

    # 3. Search by name
    all_stations = fetch_stations_list()
    if not all_stations:
        return {"error": "Failed to fetch station list for name resolution."}
        
    matches = search_stations(identifier, all_stations)
    
    if not matches:
        return {"error": f"No station or favorite found matching '{identifier}'."}
        
    if len(matches) == 1:
        s = matches[0]
        return {
            "uid": str(s.get("id")),
            "name": str(s.get("name", "Unknown")),
            "id_display": str(s.get("station_id", "N/A"))
        }
        
    # Multiple matches
    return {"matches": matches}

def normalize_text(text: str) -> str:
    """Replaces Serbian special characters with their basic equivalents."""
    replacements = {
        'č': 'c', 'ć': 'c',
        'š': 's',
        'ž': 'z',
        'đ': 'd',
        'Č': 'C', 'Ć': 'C',
        'Š': 'S',
        'Ž': 'Z',
        'Đ': 'D'
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    return text.lower()

def search_stations(query: str, all_stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Searches for stations where the normalized name contains the normalized search query."""
    normalized_query = normalize_text(query)
    matches: List[Dict[str, Any]] = []
    
    for station in all_stations:
        name = str(station.get("name", ""))
        normalized_name = normalize_text(name)
        if normalized_query in normalized_name:
            matches.append(station)
            
    return matches

def get_arrivals(station_uid: str, target_lines: Optional[List[str]] = None) -> str:
    url = f"{BASE_URL}/publicapi/v2/api.php"
    headers = {
        "X-Api-Authentication": API_KEY,
        "User-Agent": "okhttp/4.10.0",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    payload_dict = {
        "station_uid": str(station_uid),
        "session_id": f"A{int(time.time() * 1000)}"
    }
    
    payload_json = json.dumps(payload_dict)
    encrypted_base = encrypt_payload(payload_json)
    body = f"action=data_bulletin&base={encrypted_base}"
    
    try:
        response = requests.post(url, headers=headers, data=body, timeout=10)
        response.raise_for_status()
        decrypted_text = decrypt_response(response.text)
        data = json.loads(decrypted_text)
        
        if "data" in data and isinstance(data["data"], list) and len(data["data"]) > 0:
            if data["data"][0].get("success") is False:
                return f"Trenutno nema zakazanih autobusa ili dostupnog praćenja za stanicu {station_uid}."
            
            station_name = str(data["data"][0].get("station_name", f"Stanica {station_uid}"))
            
            # Use a list of dicts to store arrival info for sorting
            arrivals_data: List[Dict[str, Any]] = []
            found_lines: Set[str] = set()
            
            # Explicitly narrow target_lines to avoid linter confusion
            actual_targets: Set[str] = set()
            use_filter = False
            if target_lines is not None:
                actual_targets = set(target_lines)
                use_filter = True
            
            for item in data["data"]:
                line_no = str(item.get("line_number", "Unknown")).strip()
                eta_seconds = item.get("seconds_left")
                
                if use_filter and line_no not in actual_targets:
                    continue
                
                found_lines.add(line_no)
                if eta_seconds is not None:
                    arrivals_data.append({
                        "line": line_no,
                        "eta_mins": int(eta_seconds) // 60,
                        "eta_secs": int(eta_seconds)
                    })
            
            # Sort arrivals by seconds_left (ascending)
            arrivals_data.sort(key=lambda x: x["eta_secs"])
            
            lines_info: List[str] = []
            for arr in arrivals_data:
                lines_info.append(f"Linija {arr['line']:4} - Stiže za {arr['eta_mins']:2} min ({arr['eta_secs']} sek)")
            
            if use_filter:
                missing_lines = actual_targets.difference(found_lines)
                for line in sorted(list(missing_lines)):
                    lines_info.append(f"Linija {line:4} - još nije krenula.")
            
            if not lines_info:
                return f"Nema rezultata za tražene linije na stanici {station_name}."
                
            return f"<b>Dolasci za: {station_name}</b>\n\n" + "\n".join(lines_info)
        else:
            return "Trenutno nema dostupnih podataka o praćenju uživo."
    except requests.exceptions.RequestException as e:
        logging.error(f"Network error in get_arrivals: {e}")
        return "Mrežna greška. Molimo pokušajte ponovo kasnije."
    except json.JSONDecodeError as e:
        logging.error(f"JSON error in get_arrivals: {e}")
        return "Greška pri obradi odgovora servera."
    except Exception as e:
        logging.error(f"Unexpected error in get_arrivals: {e}")
        return f"Došlo je do neočekivane greške: {str(e)}"

# --- Telegram Bot Handlers ---

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.effective_user:
            user_id = str(update.effective_user.id)
            username = update.effective_user.username or "N/A"
            current_date = time.strftime("%Y-%m-%d %H:%M:%S")

            users = load_users()
            if user_id not in users:
                users[user_id] = {
                    "username": username,
                    "first_started": current_date
                }
                save_users(users)
                logging.info(f"New user registered: {user_id} ({username})")

        welcome_text = (
            "Dobrodošli u Bus Bot! 🚌\n\n"
            "Mogu vam pomoći da pratite dolaske autobusa uživo u Beogradu.\n\n"
            "Ukucajte /help da vidite sve dostupne komande i kako da ih koristite."
        )
        if update.message:
            await update.message.reply_text(welcome_text)
    except Exception as e:
        logging.error(f"Error in start handler: {e}")

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return

        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ Niste ovlašćeni za korišćenje ove komande.")
            return

        users = load_users()
        total_users = len(users)
        
        user_list = []
        for uid, info in users.items():
            username = info.get("username", "N/A")
            since = info.get("first_started", "Nepoznato")
            user_list.append(f"• ID: <code>{uid}</code> | @{username} | Prvi put pokrenut: {since}")

        report = (
            f"📊 <b>Ukupno korisnika: {total_users}</b>\n\n" +
            "\n".join(user_list)
        )
        
        # Split message if it's too long (Telegram limit is 4096)
        report_str: str = str(report)
        if len(report_str) > 4000:
            for i in range(0, len(report_str), 4000):
                end_idx = min(i + 4000, len(report_str))
                chunk = cast(Any, report_str)[i:end_idx]
                await update.message.reply_text(chunk, parse_mode='HTML')
        else:
            await update.message.reply_text(report_str, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Error in users_command handler: {e}")
        await update.message.reply_text("Došlo je do greške prilikom preuzimanja liste korisnika.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message:
            return
        help_text = (
            "<b>Dostupne komande:</b>\n\n"
            "<b>Informacije o stanicama</b>\n"
            "• /stations - Prikaži primer dostupnih stanica\n"
            "• /search [naziv] - Pretraži stanicu po nazivu\n"
            "• /check [id/naziv/favorit] [linije] - Proveri dolaske (npr. /check 182, /check kuca 16, /check 'Skola Josif Pancic')\n\n"
            "<b>Omiljene Stanice</b>\n"
            "• /save [naziv] [id/naziv_stanice] - Sačuvaj stanicu u Omiljene Stanice\n"
            "• /favorites - Izlistaj sve sačuvane Omiljene Stanice\n"
            "• /delete [naziv] - Obriši Omiljenu Stanicu\n\n"
            "<i>Ako vam je potrebna pomoć sa komandama, ukucajte /help u bilo kom trenutku.</i>"
        )
        await update.message.reply_text(help_text, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Error in help handler: {e}")

async def stations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message:
            return
        await update.message.reply_text("Preuzimanje liste stanica... ⏳")
        all_stations = fetch_stations_list()
        
        if not all_stations:
            await update.message.reply_text("Neuspešno preuzimanje stanica. Molimo pokušajte ponovo kasnije.")
            return

        # Filter/Sample some stations for display
        sample_size = 20
        stations_info: List[str] = []
        stations_to_sample = all_stations
        for i in range(min(len(stations_to_sample), sample_size)):
            s = stations_to_sample[i]
            name = str(s.get("name", "Nepoznato"))
            station_id = str(s.get("station_id", "N/A"))
            stations_info.append(f"• <b>{name}</b> (ID: {station_id})")
        
        stations_text = (
            "<b>Dostupne stanice (primer):</b>\n\n" +
            "\n".join(stations_info) +
            f"\n\nPrikazano {sample_size} od {len(all_stations)} stanica.\n"
            "Koristite /check [ID] [Linije] za dolaske.\n\n"
            "<i>Ukucajte /help za više informacija.</i>"
        )
        await update.message.reply_text(stations_text, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Error in stations handler: {e}")
        if update.message:
            await update.message.reply_text("Došlo je do greške prilikom preuzimanja stanica.")

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message:
            return
        if not context.args:
            await update.message.reply_text("Upotreba: /search [naziv stanice]\nPrimer: /search Skola Josif Pancic")
            return
        
        query = " ".join(context.args)
        await update.message.reply_text(f"Pretražujem '{query}'... ⏳")
        
        all_stations = fetch_stations_list()
        if not all_stations:
            await update.message.reply_text("Neuspešno preuzimanje stanica. Molimo pokušajte ponovo kasnije.")
            return
            
        matches = search_stations(query, all_stations)
        
        if not matches:
            await update.message.reply_text(f"Nisu pronađene stanice koje odgovaraju '{query}'.")
            return
            
        results_info: List[str] = []
        # Limit results to avoid long messages
        max_results = 30
        matches_to_show = matches
        for i in range(min(len(matches_to_show), max_results)):
            s = matches_to_show[i]
            name = str(s.get("name", "Nepoznato"))
            station_id = str(s.get("station_id", "N/A"))
            results_info.append(f"• <b>{name}</b> (ID: <code>{station_id}</code>)")
            
        results_text = (
            f"<b>Pronađeno {len(matches)} stanica:</b>\n\n" +
            "\n".join(results_info)
        )
        
        if len(matches) > max_results:
            results_text += f"\n\n...i još {len(matches) - max_results}. Pokušajte sa specifičnijim nazivom ako je potrebno."
            
        results_text += "\n\nKoristite /check [ID] za dolaske.\n\n"
        results_text += "<i>Ukucajte /help za više informacija.</i>"
        
        await update.message.reply_text(results_text, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Error in search handler: {e}")
        if update.message:
            await update.message.reply_text("Došlo je do greške prilikom pretrage.")

async def save_favorite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        if not context.args or len(context.args) < 2:
            await update.message.reply_text("Upotreba: /save [naziv_favorita] [id_ili_naziv_stanice]\nPrimer: /save kuca 465")
            return
        
        fav_name = context.args[0]
        identifier = " ".join(context.args[1:])
        user_id = str(update.effective_user.id)
        
        await update.message.reply_text(f"Pretražujem '{identifier}' za favorit '{fav_name}'... ⏳")
        
        res = await resolve_station_identifier(user_id, identifier)
        
        if "error" in res:
            await update.message.reply_text(res["error"])
            return
            
        if "matches" in res:
            matches = res["matches"]
            results_info: List[str] = []
            for i in range(min(len(matches), 10)):
                s = matches[i]
                results_info.append(f"• <b>{s.get('name')}</b> (ID: <code>{s.get('station_id')}</code>)")
            
            error_text = (
                f"Pronađeno je više stanica za '{identifier}'. Molimo koristite tačan ID za čuvanje:\n\n" +
                "\n".join(results_info)
            )
            await update.message.reply_text(error_text, parse_mode='HTML')
            return
            
        # Success: Save it
        favorites = load_favorites()
        if user_id not in favorites:
            favorites[user_id] = {}
        
        # Store both UID and Station ID (sid) for easier display
        favorites[user_id][fav_name] = {
            "uid": res["uid"],
            "sid": res["id_display"]
        }
        save_favorites(favorites)
        
        await update.message.reply_text(
            f"✅ Sačuvano: <b>{fav_name}</b> -> {res['name']} (ID: {res['id_display']})\n\n"
            "<i>Ukucajte /help za više informacija.</i>",
            parse_mode='HTML'
        )
    except Exception as e:
        logging.error(f"Error in save handler: {e}")
        if update.message:
            await update.message.reply_text("Došlo je do greške prilikom čuvanja favorita.")

async def list_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        
        user_id = str(update.effective_user.id)
        favorites = load_favorites()
        user_favs = favorites.get(user_id, {})
        
        if not user_favs:
            await update.message.reply_text("Još uvek niste sačuvali nijedan favorit. Koristite /save da ih dodate!")
            return
            
        favs_list: List[str] = []
        all_stations = None # Lazy fetch if needed
        for name, fav_data in user_favs.items():
            if isinstance(fav_data, dict):
                sid = fav_data.get("sid", "N/A")
            else:
                # Compatibility: Try to resolve the SID from the UID
                if all_stations is None:
                    all_stations = fetch_stations_list()
                sid = find_station_id_by_uid(fav_data, all_stations) or "N/A"
            favs_list.append(f"• <b>{name}</b> (ID: <code>{sid}</code>)")
            
        results_text = "<b>Vaše Omiljene Stanice:</b>\n\n" + "\n".join(favs_list)
        results_text += "\n\nKoristite /check [naziv_omiljene_stanice] da vidite dolaske.\n\n"
        results_text += "<i>Ukucajte /help za više informacija.</i>"
        await update.message.reply_text(results_text, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Error in favorites handler: {e}")
        if update.message:
            await update.message.reply_text("Došlo je do greške prilikom izlistavanja favorita.")

async def delete_favorite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        if not context.args:
            await update.message.reply_text("Upotreba: /delete [naziv_favorita]")
            return
            
        fav_name = context.args[0]
        user_id = str(update.effective_user.id)
        favorites = load_favorites()
        
        if user_id in favorites and fav_name in favorites[user_id]:
            favorites[user_id].pop(fav_name, None)
            save_favorites(favorites)
            await update.message.reply_text(
                f"✅ Obrisan favorit '{fav_name}'.\n\n"
                "<i>Ukucajte /help za više informacija.</i>",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(f"Favorit '{fav_name}' nije pronađen.")
    except Exception as e:
        logging.error(f"Error in delete handler: {e}")
        if update.message:
            await update.message.reply_text("Došlo je do greške prilikom brisanja favorita.")

async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message:
            return
            
        if not context.args:
            await update.message.reply_text("Upotreba: /check [id_ili_naziv_stanice] [opcione_linije]\nPrimer: /check 182 58 74 ili /check 'Zeleni venac'")
            return
        
        # New parsing logic: reading from the end backwards
        user_id = str(update.effective_user.id)
        args_list: List[str] = list(context.args or [])
        station_id_or_name = ""
        target_lines: Optional[List[str]] = None

        if len(args_list) > 1:
            # Check from the end for items that look like line numbers
            # (must contain at least one digit and we stop before the very first argument)
            split_idx = len(args_list)
            for i in range(len(args_list) - 1, 0, -1):
                arg = args_list[i]
                if any(c.isdigit() for c in arg):
                    split_idx = i
                else:
                    break
            
            station_id_or_name = " ".join(cast(Any, args_list)[0:split_idx])
            # If nothing is left for the name, treat everything as the name (e.g. /check 16 17)
            if not station_id_or_name:
                station_id_or_name = " ".join(args_list)
                target_lines = None
            else:
                target_lines = cast(Any, args_list)[split_idx:len(args_list)] if split_idx < len(args_list) else None
        else:
            station_id_or_name = args_list[0]
            target_lines = None

        if not station_id_or_name:
             await update.message.reply_text("Molimo navedite naziv ili ID stanice.")
             return

        res = await resolve_station_identifier(user_id, station_id_or_name)

        if "error" in res:
            await update.message.reply_text(res["error"])
            return
            
        if "matches" in res:
            matches = res["matches"]
            results_info: List[str] = []
            # List all matching stations with their IDs
            for s in matches:
                results_info.append(f"• <b>{s.get('name')}</b> (ID: <code>{s.get('station_id')}</code>)")
            
            error_text = (
                f"Više stanica odgovara nazivu '{station_id_or_name}'. "
                "Molimo koristite ID stanice direktno umesto naziva:\n\n" +
                "\n".join(results_info) +
                "\n\n<i>Ukucajte /help za više informacija.</i>"
            )
            
            # Ensure message doesn't exceed Telegram length limit
            if len(error_text) > 4000:
                truncated_text = cast(Any, error_text)[0:3900]
                error_text = truncated_text + "\n\n... (previše rezultata, pokušajte sa specifičnijim nazivom)"
                
            await update.message.reply_text(error_text, parse_mode='HTML')
            return

        station_uid = res["uid"]
        display_name = res["name"]
        sid_display = res["id_display"]

        status_msg = f"Preuzimam podatke uživo za <b>{display_name}</b>"
        if sid_display != "FAV":
            status_msg += f" (ID: <code>{sid_display}</code>)"
        status_msg += "... ⏳"
        
        await update.message.reply_text(status_msg, parse_mode='HTML')
        result = get_arrivals(station_uid, target_lines)
        
        # Append help hint to arrivals result if successful
        if "⚠️" not in result:
             result += "\n\n<i>Ukucajte /help za više informacija.</i>"
             
        await update.message.reply_text(result, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Error in check handler: {e}")
        if update.message:
            await update.message.reply_text("Došlo je do greške prilikom preuzimanja dolazaka.")

if __name__ == '__main__':
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    start_handler = CommandHandler('start', start)
    stations_handler = CommandHandler('stations', stations)
    search_handler = CommandHandler('search', search)
    check_handler = CommandHandler('check', check)
    save_handler = CommandHandler('save', save_favorite)
    favs_handler = CommandHandler('favorites', list_favorites)
    delete_handler = CommandHandler('delete', delete_favorite)
    help_handler = CommandHandler('help', help_command)
    users_handler = CommandHandler('users', users_command)
    
    application.add_handler(start_handler)
    application.add_handler(stations_handler)
    application.add_handler(search_handler)
    application.add_handler(check_handler)
    application.add_handler(save_handler)
    application.add_handler(favs_handler)
    application.add_handler(delete_handler)
    application.add_handler(help_handler)
    application.add_handler(users_handler)
    
    print("Bot is running...")
    application.run_polling()
