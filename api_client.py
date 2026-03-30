import logging
import json
import time
import requests
from typing import List, Optional, Dict, Any, Set
from config import BASE_URL, API_KEY
from crypto import encrypt_payload, decrypt_response

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
            # Even if success is False, we might have station metadata
            first_item = data["data"][0]
            success = first_item.get("success") is not False
            station_name = str(first_item.get("station_name", f"Stanica {station_uid}"))
            
            arrivals_by_line: Dict[str, List[Dict[str, Any]]] = {}
            found_lines: Set[str] = set()
            
            actual_targets: Set[str] = set()
            use_filter = False
            if target_lines is not None:
                actual_targets = {str(line).strip() for line in target_lines}
                use_filter = True
            
            if success:
                for item in data["data"]:
                    line_no = str(item.get("line_number", "Unknown")).strip()
                    eta_seconds = item.get("seconds_left")
                    
                    if use_filter and line_no not in actual_targets:
                        continue
                        
                    if eta_seconds is not None:
                        found_lines.add(line_no)
                        if line_no not in arrivals_by_line:
                            arrivals_by_line[line_no] = []
                        arrivals_by_line[line_no].append({
                            "eta_mins": int(eta_seconds) // 60,
                            "eta_secs": int(eta_seconds)
                        })

            # Build result string
            output_parts = [f"<b>Dolasci za: {station_name}</b>\n"]
            
            # Sort active lines by their nearest arrival
            sorted_lines = sorted(
                arrivals_by_line.keys(),
                key=lambda x: min(a["eta_secs"] for a in arrivals_by_line[x])
            )
            
            for line in sorted_lines:
                output_parts.append(f"<b>Linija {line}</b>")
                # Sort arrivals for this specific line
                line_arrivals = sorted(arrivals_by_line[line], key=lambda x: x["eta_secs"])
                for arr in line_arrivals:
                    output_parts.append(f"• Stiže za {arr['eta_mins']} min ({arr['eta_secs']} sek)")
                output_parts.append("") # Spacer
            
            # Handle missing lines requested by user
            if use_filter:
                missing_lines = actual_targets.difference(found_lines)
                if missing_lines:
                    for line in sorted(list(missing_lines)):
                        output_parts.append(f"Linija {line} - Jos uvek nije krenula")
            
            # If no data at all and no specific filter
            if not sorted_lines and not (use_filter and actual_targets):
                return f"Trenutno nema zakazanih autobusa ili dostupnog praćenja za stanicu {station_name}."
                
            return "\n".join(output_parts).strip()
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

