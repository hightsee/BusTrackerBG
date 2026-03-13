import os
import json
import time

from typing import Dict, Any

# Mock constants from bus_bot.py
USERS_FILE = "users_test.json"

def load_users() -> Dict[str, Any]:
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_users(users_data: Dict[str, Any]):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users_data, f, indent=4, ensure_ascii=False)

def test_user_tracking():
    # Cleanup
    if os.path.exists(USERS_FILE):
        os.remove(USERS_FILE)
    
    # Simulate first user
    user_id = "12345"
    username = "testuser"
    current_date = time.strftime("%Y-%m-%d %H:%M:%S")
    
    users: Dict[str, Any] = load_users()
    if user_id not in users:
        users[user_id] = {
            "username": username,
            "first_started": current_date
        }
        save_users(users)
    
    # Verify save
    users = load_users()
    assert user_id in users
    assert users[user_id]["username"] == username
    print("Test User Tracking: Passed")

    # Simulate existing user (should not update date)
    old_date = users[user_id]["first_started"]
    time.sleep(1)
    
    users = load_users()
    if user_id not in users:
        users[user_id] = {
            "username": username,
            "first_started": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        save_users(users)
    
    users = load_users()
    assert users[user_id]["first_started"] == old_date
    print("Test Existing User: Passed")
    
    # Cleanup
    if os.path.exists(USERS_FILE):
        os.remove(USERS_FILE)

if __name__ == "__main__":
    test_user_tracking()
