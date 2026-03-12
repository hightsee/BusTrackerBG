import logging
import json
import base64
import time
import os
import urllib.parse
from typing import List, Optional, Dict, Any, Set
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
FAVORITES_FILE = "favorites.json"

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
                return f"No buses are currently scheduled or tracking available for station {station_uid}."
            
            station_name = str(data["data"][0].get("station_name", f"Station {station_uid}"))
            
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
                lines_info.append(f"Line {arr['line']:4} - Arriving in {arr['eta_mins']:2} min ({arr['eta_secs']} sec)")
            
            if use_filter:
                missing_lines = actual_targets.difference(found_lines)
                for line in sorted(list(missing_lines)):
                    lines_info.append(f"Line {line:4} - didn't start it's journey yet.")
            
            if not lines_info:
                return f"No matching lines found at {station_name}."
                
            return f"<b>Arrivals for: {station_name}</b>\n\n" + "\n".join(lines_info)
        else:
            return "No live tracking data available right now."
    except requests.exceptions.RequestException as e:
        logging.error(f"Network error in get_arrivals: {e}")
        return "Network error. Please try again later."
    except json.JSONDecodeError as e:
        logging.error(f"JSON error in get_arrivals: {e}")
        return "Error parsing server response."
    except Exception as e:
        logging.error(f"Unexpected error in get_arrivals: {e}")
        return f"An unexpected error occurred: {str(e)}"

# --- Telegram Bot Handlers ---

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        welcome_text = (
            "Welcome to the Bus Bot! 🚌\n\n"
            "I can help you track live bus arrivals in Belgrade.\n\n"
            "Type /help to see all available commands and how to use them."
        )
        if update.message:
            await update.message.reply_text(welcome_text)
    except Exception as e:
        logging.error(f"Error in start handler: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message:
            return
        help_text = (
            "<b>Available Commands:</b>\n\n"
            "<b>Station Information</b>\n"
            "• /stations - List sample available stations\n"
            "• /search [name] - Search for a station by its name\n"
            "• /check [id/name/fav] [lines] - Get live arrivals (e.g. /check 182, /check home 16, /check 'Skola Josif Pancic')\n\n"
            "<b>Favorites</b>\n"
            "• /save [name] [id/station_name] - Save a station to your favorites\n"
            "• /favorites - List all your saved favorites\n"
            "• /delete [name] - Remove a favorite\n\n"
            "<i>If you need help with commands, type /help at any time.</i>"
        )
        await update.message.reply_text(help_text, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Error in help handler: {e}")

async def stations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message:
            return
        await update.message.reply_text("Fetching station list... ⏳")
        all_stations = fetch_stations_list()
        
        if not all_stations:
            await update.message.reply_text("Failed to fetch stations. Please try again later.")
            return

        # Filter/Sample some stations for display
        sample_size = 20
        stations_info: List[str] = []
        stations_to_sample = all_stations
        for i in range(min(len(stations_to_sample), sample_size)):
            s = stations_to_sample[i]
            name = str(s.get("name", "Unknown"))
            station_id = str(s.get("station_id", "N/A"))
            stations_info.append(f"• <b>{name}</b> (ID: {station_id})")
        
        stations_text = (
            "<b>Available Stations (Sample):</b>\n\n" +
            "\n".join(stations_info) +
            f"\n\nListing {sample_size} out of {len(all_stations)} stations.\n"
            "Use /check [ID] [Lines] to get arrivals.\n\n"
            "<i>Type /help for more info.</i>"
        )
        await update.message.reply_text(stations_text, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Error in stations handler: {e}")
        if update.message:
            await update.message.reply_text("An error occurred while fetching stations.")

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message:
            return
        if not context.args:
            await update.message.reply_text("Usage: /search [station name]\nExample: /search Skola Josif Pancic")
            return
        
        query = " ".join(context.args)
        await update.message.reply_text(f"Searching for '{query}'... ⏳")
        
        all_stations = fetch_stations_list()
        if not all_stations:
            await update.message.reply_text("Failed to fetch stations. Please try again later.")
            return
            
        matches = search_stations(query, all_stations)
        
        if not matches:
            await update.message.reply_text(f"No stations found matching '{query}'.")
            return
            
        results_info: List[str] = []
        # Limit results to avoid long messages
        max_results = 30
        matches_to_show = matches
        for i in range(min(len(matches_to_show), max_results)):
            s = matches_to_show[i]
            name = str(s.get("name", "Unknown"))
            station_id = str(s.get("station_id", "N/A"))
            results_info.append(f"• <b>{name}</b> (ID: <code>{station_id}</code>)")
            
        results_text = (
            f"<b>Found {len(matches)} matching stations:</b>\n\n" +
            "\n".join(results_info)
        )
        
        if len(matches) > max_results:
            results_text += f"\n\n...and {len(matches) - max_results} more. Try a more specific search if needed."
            
        results_text += "\n\nUse /check [ID] to get arrivals.\n\n"
        results_text += "<i>Type /help for more info.</i>"
        
        await update.message.reply_text(results_text, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Error in search handler: {e}")
        if update.message:
            await update.message.reply_text("An error occurred during search.")

async def save_favorite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        if not context.args or len(context.args) < 2:
            await update.message.reply_text("Usage: /save [favorite_name] [station_id_or_name]\nExample: /save home 465")
            return
        
        fav_name = context.args[0]
        identifier = " ".join(context.args[1:])
        user_id = str(update.effective_user.id)
        
        await update.message.reply_text(f"Resolving '{identifier}' for favorite '{fav_name}'... ⏳")
        
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
                f"Multiple stations found for '{identifier}'. Please use the specific ID to save:\n\n" +
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
            f"✅ Saved <b>{fav_name}</b> -> {res['name']} (ID: {res['id_display']})\n\n"
            "<i>Type /help for more info.</i>",
            parse_mode='HTML'
        )
    except Exception as e:
        logging.error(f"Error in save handler: {e}")
        if update.message:
            await update.message.reply_text("An error occurred while saving favorite.")

async def list_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        
        user_id = str(update.effective_user.id)
        favorites = load_favorites()
        user_favs = favorites.get(user_id, {})
        
        if not user_favs:
            await update.message.reply_text("You haven't saved any favorites yet. Use /save to add some!")
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
            
        results_text = "<b>Your Favorites:</b>\n\n" + "\n".join(favs_list)
        results_text += "\n\nUse /check [favorite_name] to see arrivals.\n\n"
        results_text += "<i>Type /help for more info.</i>"
        await update.message.reply_text(results_text, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Error in favorites handler: {e}")
        if update.message:
            await update.message.reply_text("An error occurred while listing favorites.")

async def delete_favorite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        if not context.args:
            await update.message.reply_text("Usage: /delete [favorite_name]")
            return
            
        fav_name = context.args[0]
        user_id = str(update.effective_user.id)
        favorites = load_favorites()
        
        if user_id in favorites and fav_name in favorites[user_id]:
            favorites[user_id].pop(fav_name, None)
            save_favorites(favorites)
            await update.message.reply_text(
                f"✅ Deleted favorite '{fav_name}'.\n\n"
                "<i>Type /help for more info.</i>",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(f"Could not find favorite '{fav_name}'.")
    except Exception as e:
        logging.error(f"Error in delete handler: {e}")
        if update.message:
            await update.message.reply_text("An error occurred while deleting favorite.")

async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message:
            return
            
        if not context.args:
            await update.message.reply_text("Usage: /check [station_id_or_name] [optional_lines]\nExample: /check 182 58 74 or /check 'Zeleni venac'")
            return
        
        # Smarter parsing: try to distinguish between station name/id and lines
        # Rule of thumb: lines are usually numeric and come at the end
        user_id = str(update.effective_user.id)
        args_list: List[str] = list(context.args or [])
        station_id_or_name = " ".join(args_list) # Default to everything
        target_lines = None

        # Try to see if the full string works
        res = await resolve_station_identifier(user_id, station_id_or_name)
        
        # If no match or error, try checking if the last args are actually lines
        if ("error" in res or "matches" in res) and len(args_list) > 1:
            for i in range(1, len(args_list) + 1):
                # We try from 1 to all args as being potentially lines
                # If we have something like /check Skola Josif Pancic 23
                potential_name = " ".join([args_list[j] for j in range(len(args_list) - i)])
                potential_lines = [args_list[j] for j in range(len(args_list) - i, len(args_list))]
                
                if not potential_name:
                    break # Ran out of words for name

                # Check if potential_lines actually look like lines (alphanumeric containing digits)
                if all(any(c.isdigit() for c in l) for l in potential_lines):
                    temp_res = await resolve_station_identifier(user_id, potential_name)
                    if "uid" in temp_res:
                        res = temp_res
                        target_lines = potential_lines
                        station_id_or_name = potential_name
                        break

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
                f"Multiple stations found for '{station_id_or_name}'. Please use the specific ID:\n\n" +
                "\n".join(results_info) +
                "\n\n<i>Type /help for more info.</i>"
            )
            await update.message.reply_text(error_text, parse_mode='HTML')
            return

        station_uid = res["uid"]
        display_name = res["name"]
        sid_display = res["id_display"]

        status_msg = f"Fetching live data for <b>{display_name}</b>"
        if sid_display != "FAV":
            status_msg += f" (ID: <code>{sid_display}</code>)"
        status_msg += "... ⏳"
        
        await update.message.reply_text(status_msg, parse_mode='HTML')
        result = get_arrivals(station_uid, target_lines)
        
        # Append help hint to arrivals result if successful
        if "⚠️" not in result:
             result += "\n\n<i>Type /help for more info.</i>"
             
        await update.message.reply_text(result, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Error in check handler: {e}")
        if update.message:
            await update.message.reply_text("An error occurred while fetching arrivals.")

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
    
    application.add_handler(start_handler)
    application.add_handler(stations_handler)
    application.add_handler(search_handler)
    application.add_handler(check_handler)
    application.add_handler(save_handler)
    application.add_handler(favs_handler)
    application.add_handler(delete_handler)
    application.add_handler(help_handler)
    
    print("Bot is running...")
    application.run_polling()
