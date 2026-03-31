import logging
import threading
from typing import List, Optional, Dict, Any, cast
from telegram import Update
from telegram.ext import ContextTypes
from config import ADMIN_ID
from db_manager import bot_data_manager
from gtfs_manager import gtfs_manager
from api_client import fetch_stations_list, search_stations, get_arrivals, find_station_uid

FOOTER = "\n\n⚠️ /check komanda trenutno nije dostupna zbog promene API-ja. Koristite /nextat ili /predict.\n⚠️ /check command is currently unavailable due to API changes. Use /nextat or /predict instead."

async def reply_with_footer(message, text, **kwargs):
    if not message:
        return
    if not text.endswith(FOOTER):
        text += FOOTER
    await message.reply_text(text, **kwargs)

async def split_and_send_message(message, text, limit=3500):
    lines = text.split('\n')
    current_chunk = []
    current_len = 0
    for line in lines:
        if current_len + len(line) + 1 > limit:
            if current_chunk:
                await reply_with_footer(message, "\n".join(current_chunk), parse_mode='HTML')
            current_chunk = [line]
            current_len = len(line) + 1
        else:
            current_chunk.append(line)
            current_len += len(line) + 1
    if current_chunk:
        await reply_with_footer(message, "\n".join(current_chunk), parse_mode='HTML')

async def resolve_station_identifier(user_id: str, identifier: str) -> Dict[str, Any]:
    """
    Legacy resolver - used only by old components. New nextat avoids this.
    """
    user_id_str = str(user_id)
    user_favs = bot_data_manager.get_favorites(user_id_str)
    for fav_name, fav_data in user_favs.items():
        if fav_name.lower() == identifier.lower():
            if isinstance(fav_data, dict):
                return {"uid": fav_data["uid"], "name": fav_name, "id_display": fav_data.get("sid", "FAV")}
            else:
                return {"uid": fav_data, "name": fav_name, "id_display": "FAV"}
    if identifier.isdigit():
        uid = find_station_uid(identifier)
        if uid:
            return {"uid": uid, "name": f"Station {identifier}", "id_display": identifier}
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
    return {"matches": matches}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.effective_user:
            user_id = str(update.effective_user.id)
            username = update.effective_user.username or "N/A"
            bot_data_manager.update_user(user_id, username)

        welcome_text = (
            "Dobrodošli u Bus Bot! 🚌\n\n"
            "Mogu vam pomoći da pratite dolaske autobusa uživo u Beogradu.\n\n"
            "Ukucajte /help da vidite sve dostupne komande i kako da ih koristite."
        )
        if update.message:
            await reply_with_footer(update.message, welcome_text)
    except Exception as e:
        logging.error(f"Error in start handler: {e}")

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        if update.effective_user.id != ADMIN_ID:
            await reply_with_footer(update.message, "⛔ Niste ovlašćeni za korišćenje ove komande.")
            return

        users = bot_data_manager.get_users()
        total_users = len(users)
        
        user_list = []
        for info in users:
            uid = info.get("user_id", "N/A")
            username = info.get("username", "N/A")
            since = info.get("first_started", "Nepoznato")
            last = info.get("last_used", "Nepoznato")
            user_list.append(f"• ID: <code>{uid}</code> | @{username} | Registrovan: {since} | Poslednji put: {last}")

        report = f"📊 <b>Ukupno korisnika: {total_users}</b>\n\n" + "\n".join(user_list)
        report_str = str(report)
        
        await split_and_send_message(update.message, report_str)
    except Exception as e:
        logging.error(f"Error in users_command handler: {e}")
        await reply_with_footer(update.message, "Došlo je do greške prilikom preuzimanja liste korisnika.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message:
            return
        help_text = (
            "<b>Dostupne komande / Available commands:</b>\n\n"
            "<b>Informacije o stanicama i linijama / Station and Line Info</b>\n"
            "• /search [naziv] - Pretraži stanicu / Search station\n"
            "• /timetable [linija] - Planirani red vožnje / Scheduled timetable\n"
            "• /predict [linija] - Predviđene pozicije autobusa / Predicted bus positions\n"
            "• /nextat [stanica/favorit] [linije] - Sledeći polasci sa stanice / Next scheduled arrivals\n"
            "• /route [linija] - Sve stanice na liniji / Full route stops\n\n"
            "<b>Omiljene Stanice / Favorite Stops</b>\n"
            "• /save [naziv] [id_stanice] - Sačuvaj favorit (samo numerički) / Save favorite\n"
            "• /favorites - Vaši favoriti / Your favorites\n"
            "• /delete [naziv] - Obriši favorit / Delete favorite\n\n"
            "<i>Ukucajte /help za pomoć / Type /help for assistance.</i>"
        )
        await reply_with_footer(update.message, help_text, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Error in help handler: {e}")

async def stations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await reply_with_footer(update.message, "Ova komanda je trenutno onemogućena.\nThis command is currently disabled.")

async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await reply_with_footer(update.message, "Ova komanda je trenutno onemogućena zbog nedostupnog API-ja.\nThis command is currently disabled. Use /nextat or /predict instead.")

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message:
            return
        if not context.args:
            await reply_with_footer(update.message, "Upotreba: /search [naziv stanice]\nPrimer: /search Skola Josif Pancic")
            return
        
        query = " ".join(context.args)
        await reply_with_footer(update.message, f"Pretražujem '{query}'... ⏳")
        
        from api_client import normalize_text
        norm_query = normalize_text(query)
        
        import sqlite3
        from config import GTFS_DB
        conn = sqlite3.connect(GTFS_DB)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT stop_id, stop_name FROM stops")
        all_gtfs_stops = cursor.fetchall()
        
        matches = []
        for r in all_gtfs_stops:
            gtfs_name = str(r['stop_name'])
            if norm_query in normalize_text(gtfs_name):
                # Ensure it's a valid GTFS stop ID starting with 20000+
                if r['stop_id'].isdigit() and int(r['stop_id']) > 20000:
                    buslogic_id = int(r['stop_id']) - 20000
                    matches.append((gtfs_name, buslogic_id))
                
        conn.close()
        
        if not matches:
            await reply_with_footer(update.message, f"Nisu pronađene stanice koje odgovaraju '{query}'.")
            return
            
        # Deduplicate and limit to 10
        unique_matches = []
        seen = set()
        for name, bid in matches:
            if bid not in seen:
                seen.add(bid)
                unique_matches.append((name, bid))
        
        results_info = []
        for name, b_id in unique_matches[:10]:
            results_info.append(f"• {name} (ID: <code>{b_id}</code>)")
            
        results_text = "<b>Pronađeno stanica:</b>\n\n" + "\n".join(results_info) + "\n\nKoristite /nextat [ID] [linije] za dolaske."
        await reply_with_footer(update.message, results_text, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Error in search handler: {e}")
        if update.message:
            await reply_with_footer(update.message, "Došlo je do greške prilikom pretrage.")

async def save_favorite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        if not context.args or len(context.args) < 2:
            await reply_with_footer(update.message, "Upotreba: /save [naziv] [buslogic_station_id]\nPrimer: /save home 182")
            return
        
        fav_name = context.args[0]
        identifier = context.args[1]
        
        if not identifier.isdigit():
            await reply_with_footer(update.message, "ID stanice mora biti numerički! Primer: /save home 182\nStation ID must be numeric.")
            return
            
        user_id = str(update.effective_user.id)
        gtfs_id = str(int(identifier) + 20000)
        
        # Internally store GTFS stop_id inside `station_uid`, and BusLogic ID inside `station_sid`
        # Because later when we pull Favorites, we just pull the dictionary.
        bot_data_manager.save_favorite(user_id, fav_name, gtfs_id, identifier)
        
        await reply_with_footer(update.message, f"✅ Sačuvano: <b>{fav_name}</b> -> ID: {identifier}", parse_mode='HTML')
    except Exception as e:
        logging.error(f"Error in save handler: {e}")
        if update.message:
            await reply_with_footer(update.message, "Došlo je do greške prilikom čuvanja favorita.")

async def list_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        
        user_id = str(update.effective_user.id)
        user_favs = bot_data_manager.get_favorites(user_id)
        
        if not user_favs:
            await reply_with_footer(update.message, "Još uvek niste sačuvali nijedan favorit. Koristite /save da ih dodate!")
            return
            
        favs_list: List[str] = []
        for name, fav_data in user_favs.items():
            sid = fav_data.get("sid", "N/A")
            favs_list.append(f"• <b>{name}</b> (ID: <code>{sid}</code>)")
            
        results_text = "<b>Vaše Omiljene Stanice:</b>\n\n" + "\n".join(favs_list)
        results_text += "\n\nKoristite /nextat [naziv_omiljene_stanice ] da vidite dolaske.\n\n"
        results_text += "<i>Ukucajte /help za više informacija.</i>"
        await reply_with_footer(update.message, results_text, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Error in favorites handler: {e}")
        if update.message:
            await reply_with_footer(update.message, "Došlo je do greške prilikom izlistavanja favorita.")

async def delete_favorite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        if not context.args:
            await reply_with_footer(update.message, "Upotreba: /delete [naziv_favorita]")
            return
            
        fav_name = context.args[0]
        user_id = str(update.effective_user.id)
        
        if bot_data_manager.delete_favorite(user_id, fav_name):
            await reply_with_footer(update.message, 
                f"✅ Obrisan favorit '{fav_name}'.\n\n"
                "<i>Ukucajte /help za više informacija.</i>",
                parse_mode='HTML'
            )
        else:
            await reply_with_footer(update.message, f"Favorit '{fav_name}' nije pronađen.")
    except Exception as e:
        logging.error(f"Error in delete handler: {e}")
        if update.message:
            await reply_with_footer(update.message, "Došlo je do greške prilikom brisanja favorita.")

async def timetable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message:
            return
        if not context.args:
            await reply_with_footer(update.message, "Upotreba: /timetable [broj_linije]\nPrimer: /timetable 58")
            return
        
        line_no = context.args[0].replace(",", "")
        await reply_with_footer(update.message, f"Preuzimam red vožnje za liniju {line_no}... ⏳")
        
        if not gtfs_manager.get_last_update():
            await reply_with_footer(update.message, "Podaci o redu vožnje se još uvek preuzimaju, molimo pokušajte ponovo za nekoliko minuta.")
            return
            
        if gtfs_manager.is_data_outdated():
            await reply_with_footer(update.message, "⚠️ <b>UPOZORENJE:</b> Podaci o redu vožnje su možda zastareli.\n⚠️ <b>WARNING:</b> Timetable data might be outdated.", parse_mode='HTML')

        result = gtfs_manager.get_timetable(line_no)
        if len(result) > 4000:
            result = result[:3900] + "\n\n... ukucajte /timetable [linija] za određenu liniju"
        await reply_with_footer(update.message, result, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Error in timetable handler: {e}")
        if update.message:
            await reply_with_footer(update.message, "Došlo je do greške prilikom preuzimanja reda vožnje.")

async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not context.args:
            await reply_with_footer(update.message, "Upotreba: /predict [broj_linije]\nUsage: /predict [line_number]")
            return
            
        line_no = context.args[0].replace(",", "")
        await reply_with_footer(update.message, f"Računam predviđene pozicije za liniju {line_no}... ⏳")
        
        if gtfs_manager.is_data_outdated():
            await reply_with_footer(update.message, "⚠️ <b>UPOZORENJE:</b> Podaci su možda zastareli.\n⚠️ <b>WARNING:</b> Data might be outdated.", parse_mode='HTML')

        buses = gtfs_manager.predict_bus_position(line_no)
        if not buses:
            await reply_with_footer(update.message, f"Trenutno nema aktivnih polazaka za liniju {line_no} prema redu vožnje.\nNo active trips for line {line_no} right now.")
            return
            
        parts = [f"<b>🚌 Predviđene pozicije: Linija {line_no}</b>", f"<b>Predicted positions: Line {line_no}</b>\n"]
        for b in buses:
            if b['status'] == 'in_transit':
                parts.append(f"📍 {b['position']}")
                parts.append(f"➡️ Smer / Direction: {b['direction']}")
                parts.append(f"🏁 Sledeća stanica / Next stop: {b['next_stop']} (@ {b['arrival_time']})")
                parts.append(f"⏱️ Stiže za / Arriving in: {b['mins_until']} min\n")
            else:
                parts.append(f"🕒 Polazi uskoro / Departing soon: {b['direction']}")
                parts.append(f"🏁 Prva stanica / First stop: {b['next_stop']} (@ {b['arrival_time']})")
                parts.append(f"⏱️ Kreće za / Starts in: {b['mins_until']} min\n")
                
        text = "\n".join(parts)
        await split_and_send_message(update.message, text)
    except Exception as e:
        logging.error(f"Error in predict_command: {e}")
        if update.message:
            await reply_with_footer(update.message, "Greška pri računanju pozicija. / Error calculating positions.")

async def nextat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not context.args:
            await reply_with_footer(update.message, "Upotreba: /nextat [stanica/favorit] [opcione_linije]\nUsage: /nextat [station/favorite] [optional_lines]")
            return
            
        args = list(context.args)
        split_idx = len(args)
        for i in range(len(args) - 1, 0, -1):
            if any(c.isdigit() for c in args[i]):
                split_idx = i
            else:
                break
        
        slice_end = int(split_idx)
        stop_id_or_name = " ".join([str(args[i]) for i in range(slice_end)])
        target_lines = [str(args[i]) for i in range(slice_end, len(args))]
        if not target_lines:
            target_lines = None
            
        final_stop_id_or_name = stop_id_or_name
        
        user_id = str(update.effective_user.id) if update.effective_user else ""
        user_favorites = bot_data_manager.get_favorites(user_id)
        
        found_favorite = False
        lower_input = stop_id_or_name.lower().strip()
        for fav_name, fav_data in user_favorites.items():
            if fav_name.lower() == lower_input:
                gtfs_uid = fav_data.get("uid")
                if gtfs_uid and gtfs_uid != "N/A":
                    final_stop_id_or_name = gtfs_uid
                    found_favorite = True
                    break

        await reply_with_footer(update.message, f"Tražim sledeće polaske za: {stop_id_or_name}{' (favorit)' if found_favorite else ''}... ⏳")
        
        if gtfs_manager.is_data_outdated():
            await reply_with_footer(update.message, "⚠️ <b>UPOZORENJE:</b> Podaci su možda zastareli.\n⚠️ <b>WARNING:</b> Data might be outdated.", parse_mode='HTML')

        arrivals = gtfs_manager.predict_arrivals_at_stop(final_stop_id_or_name, target_lines)
        if not arrivals:
            await reply_with_footer(update.message, "Nema planiranih polazaka u skorije vreme.\nNo scheduled arrivals found soon.")
            return
            
        if 'error' in arrivals[0]:
            await reply_with_footer(update.message, arrivals[0]['error'])
            return
            
        if 'empty' in arrivals[0]:
            stop_name = arrivals[0]['stop_name']
            buslogic_name = arrivals[0]['buslogic_name']
            await reply_with_footer(update.message, f"Pronađene GTFS stanice za '{buslogic_name}': {stop_name}\n\nNema planiranih polazaka u skorije vreme.\nNo scheduled arrivals found soon.")
            return

        stop_name = arrivals[0]['stop_name']
        buslogic_name = arrivals[0]['buslogic_name']
        parts = [f"<b>🕒 Sledeći polasci: {buslogic_name}</b>\n<i>GTFS Stanica: {stop_name}</i>\n"]
        for a in arrivals:
            parts.append(f"<b>Linija {a['line']}</b> - {a['arrival_time']} ({a['mins_remaining']} min)")
            parts.append(f"➡️ Smer / Direction: {a['direction']}\n")
            
        text = "\n".join(parts)
        if len(text) > 4000:
            text = text[:3900] + "\n\n... ukucajte /nextat [stanica] [linija] za određenu liniju"
            
        await reply_with_footer(update.message, text, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Error in nextat_command: {e}")
        if update.message:
            await reply_with_footer(update.message, "Greška pri pretrazi polazaka. / Error fetching arrivals.")

async def route_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not context.args:
            await reply_with_footer(update.message, "Upotreba: /route [broj_linije]\nUsage: /route [line_number]")
            return
            
        line_no = context.args[0].replace(",", "")
        if gtfs_manager.is_data_outdated():
            await reply_with_footer(update.message, "⚠️ <b>UPOZORENJE:</b> Podaci su možda zastareli.\n⚠️ <b>WARNING:</b> Data might be outdated.", parse_mode='HTML')

        route_data = gtfs_manager.get_line_route(line_no)
        if not route_data:
            await reply_with_footer(update.message, f"Ruta za liniju {line_no} nije pronađena.\nRoute for line {line_no} not found.")
            return
            
        for direction in route_data:
            parts = [f"<b>🚩 Linija {line_no}: {direction['headsign']}</b>\n"]
            for s in direction['stops']:
                parts.append(f"• {s['stop_name']} (<code>{s['stop_id']}</code>)")
            
            text = "\n".join(parts)
            await split_and_send_message(update.message, text)
    except Exception as e:
        logging.error(f"Error in route_command: {e}")
        if update.message:
            await reply_with_footer(update.message, "Greška pri preuzimanju rute. / Error fetching route.")

async def refresh_timetable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        if update.effective_user.id != ADMIN_ID:
            await reply_with_footer(update.message, "⛔ Niste ovlašćeni za ovu komandu.")
            return

        await reply_with_footer(update.message, "Pokrećem osvežavanje GTFS baze podataka u pozadini... ⏳")
        threading.Thread(target=gtfs_manager.update_gtfs).start()
    except Exception as e:
        logging.error(f"Error in refresh_timetable: {e}")

async def timetable_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.effective_user:
            return
        if update.effective_user.id != ADMIN_ID:
            await reply_with_footer(update.message, "⛔ Niste ovlašćeni za ovu komandu.")
            return

        last_update = gtfs_manager.get_last_update()
        if last_update:
            msg = f"✅ GTFS baza je poslednji put ažurirana: <code>{last_update}</code>"
        else:
            msg = "❌ GTFS baza još uvek nije inicijalizovana."
        await reply_with_footer(update.message, msg, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Error in timetable_status: {e}")
