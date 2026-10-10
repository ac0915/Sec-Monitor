import requests
import time
import firebase_admin
from firebase_admin import credentials, db

# 1. Initialize Firebase Realtime Database
# Requires serviceAccountKey.json in the same folder
cred = credentials.Certificate("serviceAccountKey.json")

# REPLACE WITH YOUR ACTUAL DATABASE URL FROM FIREBASE
firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://stock-f54bd-default-rtdb.firebaseio.com/    ' 
})

def get_market_weather():
    """Scrapes Cboe VIX, calculates 0-100 risk score, and returns clean data."""
    url = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_VIX.json"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"Error fetching data: HTTP {response.status_code}")
            return None

        data = response.json()
        current_vix = data['data']['current_price']
        
        # Calculate 0-100 Risk Score
        risk_score = int((current_vix / 40) * 100)
        if risk_score > 100: 
            risk_score = 100
            
        # Assign UI elements based on the score
        if risk_score < 35:
            status, color = "EXTREMELY CALM", "🔵"
        elif 35 <= risk_score < 45:
            status, color = "CALM", "🟢"
        elif 45 <= risk_score < 55:
            status, color = "CAUTIOUS", "🟡"
        elif 55 <= risk_score < 70:
            status, color = "HIGH VOLATILITY", "🟠"
        elif 70 <= risk_score < 85:
            status, color = "SEVERE DANGER", "🔴"
        else:
            status, color = "MARKET CRASH", "⚫"

        return {
            "risk_score_0_to_100": risk_score,
            "status": status,
            "color": color,
            "timestamp": time.time()
        }
    except Exception as e:
        print(f"Error scraping VIX: {e}")
        return None

# 2. The Main Loop
print("Starting VIX Radar Backend (Realtime Database)...")

while True:
    weather_data = get_market_weather()
    
    if weather_data:
        try:
            # 3. Push to Firebase Realtime Database
            ref = db.reference('market_weather')
            ref.set(weather_data)
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Pushed to Firebase: {weather_data['risk_score_0_to_100']}/100 - {weather_data['status']}")
        except Exception as e:
            print(f"Firebase Upload Error: {e}")
    
    # Wait 5 minutes (300 seconds) before checking again
    time.sleep(300)