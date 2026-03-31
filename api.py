import os
import time
import datetime
from functools import wraps
from flask import Flask, request, jsonify
from flask_cors import CORS
import jwt
import bcrypt
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Import existing logic from bus_bot.py
# Note: Since bus_bot.py's polling is protected by if __name__ == '__main__', 
# this import is safe.
from bus_bot import (
    bot_data_manager,
    gtfs_manager,
    fetch_stations_list,
    search_stations,
    get_arrivals,
    find_station_id_by_uid,
    find_station_uid,
    BOT_TOKEN,
    JWT_SECRET,
    ALLOWED_ORIGINS,
    API_HOST,
    API_PORT
)

app = Flask(__name__)
# Restrict CORS to specific origins from configuration
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}})

# Initialize Rate Limiter
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["300 per day", "100 per hour"],
    storage_uri="memory://",
)

# Use the secure JWT secret from config
app.config['SECRET_KEY'] = JWT_SECRET
START_TIME = time.time()

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Token is missing!'}), 401
            
        token = auth_header.split(' ')[1]
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            # Validate user exists
            current_user = bot_data_manager.get_api_user(data['username'])
            if not current_user:
                return jsonify({'error': 'Invalid Token!'}), 401
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token has expired!'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid Token!'}), 401
        except Exception:
            # Avoid leaking raw exception strings to the client
            return jsonify({'error': 'Authentication failed!'}), 401

        return f(current_user, *args, **kwargs)

    return decorated

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.route('/api/health', methods=['GET'])
def health_check():
    uptime = time.time() - START_TIME
    return jsonify({
        'status': 'ok',
        'uptime_seconds': uptime,
        'server_time': datetime.datetime.now().isoformat()
    }), 200

@app.route('/api/register', methods=['POST'])
@limiter.limit("5 per minute")
def register():
    data = request.get_json()
    if not data or not data.get('username') or not data.get('password'):
        return jsonify({'error': 'Username and password required'}), 400

    username = data['username']
    password = data['password']
    
    # Hash password
    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    
    success = bot_data_manager.register_api_user(username, hashed.decode('utf-8'))
    if success:
        return jsonify({'message': 'User registered successfully'}), 201
    else:
        return jsonify({'error': 'Username already taken'}), 409

@app.route('/api/login', methods=['POST'])
@limiter.limit("10 per minute")
def login():
    data = request.get_json()
    if not data or not data.get('username') or not data.get('password'):
        return jsonify({'error': 'Missing credentials'}), 400

    username = data['username']
    password = data['password']
    
    user = bot_data_manager.get_api_user(username)
    if not user:
        return jsonify({'error': 'Invalid username or password'}), 401
        
    if bcrypt.checkpw(password.encode('utf-8'), user['password_hash'].encode('utf-8')):
        # Generate JWT
        token = jwt.encode({
            'user_id': user['id'],
            'username': user['username'],
            'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
        }, app.config['SECRET_KEY'], algorithm="HS256")
        
        return jsonify({'token': token})
    
    return jsonify({'error': 'Invalid username or password'}), 401

@app.route('/api/search', methods=['GET'])
def search():
    query = request.args.get('q')
    if not query:
        return jsonify({'error': 'Query parameter q is required'}), 400
        
    all_stations = fetch_stations_list()
    if not all_stations:
        return jsonify({'error': 'Failed to fetch stations list from upstream'}), 502
        
    matches = search_stations(query, all_stations)
    # Format matches to hide internal keys if needed, but returning full objects is fine
    return jsonify({'matches': matches})

@app.route('/api/arrivals', methods=['GET'])
def arrivals():
    station_id = request.args.get('station_id')
    lines_param = request.args.get('lines')
    
    if not station_id:
        return jsonify({'error': 'station_id is required'}), 400
        
    target_lines = None
    if lines_param:
        target_lines = [line.strip() for line in lines_param.split(',')]
        
    # We must resolve station_id to station_uid to call get_arrivals
    uid = find_station_uid(station_id)
    if not uid:
        return jsonify({'error': 'Station not found'}), 404
        
    result_text = get_arrivals(uid, target_lines)
    
    # Return raw text. Future improvements could parse this into JSON payload
    return jsonify({
        'station_uid': uid,
        'station_id': station_id,
        'arrivals_text': result_text
    })

@app.route('/api/timetable', methods=['GET'])
def timetable():
    line = request.args.get('line')
    if not line:
        return jsonify({'error': 'line parameter is required'}), 400
        
    result_text = gtfs_manager.get_timetable(line)
    return jsonify({
        'line': line,
        'timetable_text': result_text
    })

@app.route('/api/predict/line', methods=['GET'])
def predict_line():
    line = request.args.get('line')
    if not line:
        return jsonify({'error': 'line parameter is required'}), 400
        
    buses = gtfs_manager.predict_bus_position(line)
    return jsonify({
        'line': line,
        'active_buses': buses
    })

@app.route('/api/predict/stop', methods=['GET'])
def predict_stop():
    station_id = request.args.get('station_id')
    lines_param = request.args.get('lines')
    
    if not station_id:
        return jsonify({'error': 'station_id is required'}), 400
        
    target_lines = None
    if lines_param:
        target_lines = [line.strip() for line in lines_param.split(',')]
        
    arrivals = gtfs_manager.predict_arrivals_at_stop(station_id, target_lines)
    return jsonify({
        'station_id': station_id,
        'predicted_arrivals': arrivals
    })

@app.route('/api/route', methods=['GET'])
def get_route():
    line = request.args.get('line')
    if not line:
        return jsonify({'error': 'line parameter is required'}), 400
        
    route_data = gtfs_manager.get_line_route(line)
    return jsonify({
        'line': line,
        'directions': route_data
    })

@app.route('/api/routing', methods=['GET'])
def find_routing():
    origin = request.args.get('from')
    dest = request.args.get('to')
    
    if not origin or not dest:
        return jsonify({'error': 'from and to parameters are required'}), 400
        
    routes = gtfs_manager.find_routes_between_stops(origin, dest)
    return jsonify({
        'from': origin,
        'to': dest,
        'possible_routes': routes
    })

@app.route('/api/stops/nearby', methods=['GET'])
def stops_nearby():
    try:
        lat = float(request.args.get('lat', 0))
        lon = float(request.args.get('lon', 0))
        radius = float(request.args.get('radius', 500))
    except ValueError:
        return jsonify({'error': 'Invalid coordinates or radius'}), 400
        
    stops = gtfs_manager.get_stops_nearby(lat, lon, radius)
    return jsonify({
        'lat': lat,
        'lon': lon,
        'radius': radius,
        'stops': stops
    })

@app.route('/api/favorites', methods=['GET'])
@token_required
def get_favorites(current_user):
    # Prefix api_ to the internal DB ID to separate from Telegram User IDs
    api_user_id = f"api_{current_user['id']}"
    
    favs = bot_data_manager.get_favorites(api_user_id)
    # Format as list
    fav_list = []
    for fav_name, data in favs.items():
        fav_list.append({
            'name': fav_name,
            'station_uid': data['uid'],
            'station_id': data['sid']
        })
        
    return jsonify({'favorites': fav_list})

@app.route('/api/favorites', methods=['POST'])
@token_required
def add_favorite(current_user):
    data = request.get_json()
    if not data or not data.get('name') or not data.get('station_id'):
        return jsonify({'error': 'name and station_id are required'}), 400
        
    fav_name = data['name']
    station_id = data['station_id']
    api_user_id = f"api_{current_user['id']}"
    
    uid = find_station_uid(station_id)
    if not uid:
        return jsonify({'error': f'Station {station_id} not found'}), 404
        
    # Also resolve nice station_sid to display
    all_stations = fetch_stations_list()
    sid = find_station_id_by_uid(uid, all_stations) or station_id
        
    bot_data_manager.save_favorite(api_user_id, fav_name, uid, sid)
    
    return jsonify({'message': 'Favorite saved successfully', 'favorite': {'name': fav_name, 'station_uid': uid, 'station_id': sid}}), 201

@app.route('/api/favorites/<name>', methods=['DELETE'])
@token_required
def remove_favorite(current_user, name):
    api_user_id = f"api_{current_user['id']}"
    success = bot_data_manager.delete_favorite(api_user_id, name)
    if success:
        return jsonify({'message': 'Favorite deleted successfully'})
    else:
        return jsonify({'error': 'Favorite not found'}), 404

if __name__ == '__main__':
    from waitress import serve
    # Use a production-ready server
    print("\n" + "!" * 80)
    print("! SECURITY WARNING: This API is currently running over plain HTTP.")
    print("! For production deployments, you MUST use a reverse proxy (like Nginx/Caddy)")
    print("! with an SSL certificate to enable HTTPS and prevent credential theft.")
    print("!" * 80 + "\n")
    print(f"Starting production server on {API_HOST}:{API_PORT}...")
    serve(app, host=API_HOST, port=API_PORT)
