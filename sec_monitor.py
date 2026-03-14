import time
import requests
import re
import feedparser
from bs4 import BeautifulSoup
import google.generativeai as genai
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timezone


# ==========================================
# 1. API Keys & Database Setup
# ==========================================
# IMPORTANT: The SEC requires you to declare your name and email in the headers!
SEC_HEADERS = {'User-Agent': 'antonychan915@gmail.com'}


# Initialize Gemini
GEMINI_API_KEY = "AIzaSyBtv9EzsYDPMRyjrdX5OQjY-HD4CXF2IXI"
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')


# Initialize Firebase
from firebase_admin import db 


# Initialize Firebase
CREDENTIALS_FILE = "serviceAccountKey.json" 
try:
    cred = credentials.Certificate(CREDENTIALS_FILE)
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://stock-f54bd-default-rtdb.firebaseio.com/'
    })
    print("Firebase connected successfully.")
except Exception as e:
    print(f"Warning: Firebase not connected. Error: {e}")


seen_filings = set()


# ==========================================
# 2. Watchlist 
# ==========================================
WATCHLIST = [
    "DNN", "SMR", "UUUU", "UAMY", "USAR",
    "AMPX", "LCID", "RKLB", "QBTS",
    "NVDA", "AMD", "INTC", "ARM", "AVGO",
    "MRVL", "NVTS", "SMCI", "ASML", "MU",
    "TSM", "ON", "AMAT", "NOK", "SNDK",
    "MSFT", "GOOGL", "IBM", "ORCL", "AAPL",
    "VRT", "CLS", "TMUS", "PLTR",
    "FCX", "AG", "SLB", "CRML", "MTRN",
    "VOO", "SPY", "QQQ", "EQT", "ONDS",
]


# ==========================================
# 3. Ticker → Company Name Map
# ==========================================
TICKER_NAMES = {
    "AAPL":  "Apple", "NVDA":  "NVIDIA", "MSFT":  "Microsoft", "GOOGL": "Alphabet",
    "AMD":   "Advanced Micro Devices", "INTC":  "Intel", "TSLA":  "Tesla", "AVGO":  "Broadcom",
    "ARM":   "ARM Holdings", "ASML":  "ASML", "MU":    "Micron Technology",
    "TSM":   "Taiwan Semiconductor", "AMAT":  "Applied Materials", "MRVL":  "Marvell Technology",
    "SMCI":  "Super Micro Computer", "NVTS":  "Navitas Semiconductor", "SNDK":  "SanDisk",
    "NOK":   "Nokia", "ON":    "ON Semiconductor", "IBM":   "International Business Machines",
    "ORCL":  "Oracle", "PLTR":  "Palantir", "TMUS":  "T-Mobile", "VRT":   "Vertiv",
    "CLS":   "Celestica", "LCID":  "Lucid Group", "AMPX":  "Amprius", "RKLB":  "Rocket Lab",
    "QBTS":  "D-Wave Quantum", "DNN":   "Denison Mines", "SMR":   "NuScale Power",
    "UUUU":  "Energy Fuels", "UAMY":  "United States Antimony", "USAR":  "US Nuclear",
    "FCX":   "Freeport-McMoRan", "AG":    "First Majestic Silver", "SLB":   "Schlumberger",
    "CRML":  "Critical Metals", "MTRN":  "Materion", "EQT":   "EQT Corp", "ONDS":  "Ondas Holdings",
    "VOO":   "Vanguard 500", "SPY":   "SPDR S&P 500", "QQQ":   "Invesco QQQ"
}


# ==========================================
# 4. Terminal Colors & Tiers
# ==========================================
COLOR_RED = "\033[91m"    # Tier 1
COLOR_YELLOW = "\033[93m" # Tier 2
COLOR_GREEN = "\033[92m"  # Tier 3
COLOR_RESET = "\033[0m"


def get_tier(items_list, form_type):
    """Determines the alert level based on SEC Item numbers OR Form Type."""
    tier1_codes = ["1.03", "4.02", "8.01", "1.04", "2.01"] 
    tier2_codes = ["1.01", "3.01", "5.02", "1.02", "3.02", "5.01"]

    # Check 8-K Items first
    for item in items_list:
        if item in tier1_codes: return 1
    for item in items_list:
        if item in tier2_codes: return 2
        
    # If no items (like a 10-K or Form 4), check the Form Type!
    if "10-K" in form_type or "10-Q" in form_type:
        return 1  # Earnings reports are Tier 1!
    if form_type in ["4", "3", "5", "S-1", "S-3"]:
        return 2  # Insider trading and stock offerings are Tier 2!
        
    return 3 


# ==========================================
# Cantonese Translation 
# ==========================================
SEC_ITEM_NOTE_YUE = {
    "1.01": "簽大單/併購/融資",
    "1.02": "取消合約/大單告吹",
    "1.03": "🚨 破產/被接管",
    "1.04": "換核數師 (留意有冇嘈交)",
    "2.01": "正式完成收購/賣盤",
    "2.02": "出業績/更新指引",
    "3.01": "⚠️ 收到退市警告",
    "3.02": "配股/發債 (提防攤薄)",
    "4.02": "🚨 舊份業績有問題/要重列",
    "5.01": "大股東易手",
    "5.02": "CEO/高層大執位",
    "5.07": "股東大會投票結果",
    "7.01": "出Presentation/對外通訊",
    "8.01": "其他突發大事 (要睇內文)",
    "9.01": "淨係畀附件/新聞稿"
}


def explain_items_yue(items_list, form_type):
    notes = []
    for it in items_list:
        if it in SEC_ITEM_NOTE_YUE:
            notes.append(f"• {it}: {SEC_ITEM_NOTE_YUE[it]}")
            
    # If there are no 8-K items, explain the Form Type instead
    if not notes:
        if "10-K" in form_type: return "• 年度業績報告 (全年大結算)"
        if "10-Q" in form_type: return "• 季度業績報告"
        if form_type == "4": return "• 內幕人士/高層買賣股票 (留意係買定沽)"
        if form_type == "3": return "• 新高層/大股東初次申報持股"
        if "S-1" in form_type or "S-3" in form_type: return "• 發售新股/集資文件 (提防攤薄)"
        return f"• 表格類型: {form_type} (一般例行披露)"

    return "\n".join(notes)


# ==========================================
# 5. Core Functions
# ==========================================
def fetch_primary_document(index_url):
    """Scrapes the SEC Index page, ignores raw .txt/.xml, and returns ONLY the main HTML text."""
    try:
        response = requests.get(index_url, headers=SEC_HEADERS, timeout=30)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        table = soup.find('table', class_='tableFile')
        if not table: return None

        for row in table.find_all('tr')[1:]:
            cols = row.find_all('td')
            if len(cols) > 2:
                file_link = cols[2].find('a')
                if file_link:
                    href = file_link['href']
                    file_name = href.split('/')[-1].lower()
                    
                    if (file_name.endswith('.htm') or file_name.endswith('.html')) and "xbrl" not in file_name and "_pre" not in file_name:
                        doc_url = f"https://www.sec.gov{href}"
                        
                        doc_url = doc_url.replace("/ix?doc=", "")
                        doc_url = doc_url.replace("/ixviewer/ix.html?doc=", "")

                        doc_resp = requests.get(doc_url, headers=SEC_HEADERS, timeout=10)
                        doc_soup = BeautifulSoup(doc_resp.content, 'html.parser')
                        
                        clean_text = doc_soup.get_text(separator=' ', strip=True)
                        return clean_text[:30000] 
                        
    except Exception as e:
        print(f"Error fetching document: {e}")
        return None


def analyze_with_gemini(document_text, form_type):
    """Bulletproof Gemini AI function returning Traditional Chinese analysis."""
    if not document_text:
        return "No readable HTML document found."
        
    prompt = f"""
    You are an expert financial analyst. Your job is to read English SEC filings and extract the most critical information that an investor would care about. 

    Read the following SEC {form_type} filing text and provide a concise, structured summary IN TRADITIONAL CHINESE (繁體中文).

    Focus strictly on:
    1. Impact on Future Financials & Strategy (Earnings, Revenues, Debt).
    2. Leadership & Governance Changes (or Insider Trading amounts if Form 4).
    3. Material Risks or Opportunities.

    Analyze the overall tone of the filing and explicitly state if it is 利好 (Bullish), 利空 (Bearish), or 中性 (Neutral).

    Format your response EXACTLY like this (use these exact bold headers):
    **影響 (Impact):** [利好 / 利空 / 中性]
    **總結 (Summary):** (1-2 sentences summarizing the main event and why you chose the impact rating)
    **投資者重點 (Key Takeaways):**
    - (Bullet point 1)
    - (Bullet point 2)
    - (Bullet point 3)

    Ignore standard legal boilerplate and generic signatures.

    Here is the filing text:
    -------------------------
    {document_text}
    """

    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return "AI analysis is temporarily unavailable."


def push_to_firebase(ticker, company, url, tier, items_str, ai_summary, published_time):
    """Pushes the payload to your Firebase Realtime Database."""
    try:
        payload = {
            "ticker": ticker,
            "companyName": company,
            "filingUrl": url,
            "tier": tier,
            "secItems": items_str,
            "summary": ai_summary,
            "publishedAt": published_time,
            "timestamp": time.time()
        }
        
        ref = db.reference('news')
        ref.push(payload)
        
    except Exception as e:
        print(f"Failed to push to Firebase: {e}")


def send_telegram_alert(ticker, company, tier, tier_name, items_str, ai_summary, url, items_list, form_type):
    """Sends a formatted message to your Telegram app."""
    
    TELEGRAM_BOT_TOKEN = "8638265428:AAEvL-gw0UEbRnrrFHAGY3dPVMwxmyW0Nss"
    TELEGRAM_CHAT_ID = "6108164674"
    
    if tier == 1:
        emoji = "🚨🔴 [EMERGENCY]"
    elif tier == 2:
        emoji = "⚠️🟡 [MYSTERY BOX]"
    else:
        emoji = "📄🟢 [ROUTINE]"

    items_note_yue = explain_items_yue(items_list, form_type)

    message = (
        f"{emoji} <b>{ticker} ({company})</b>\n\n"
        f"<b>SEC Form / Items:</b> {items_str}\n"
        f"<b>備註:</b>\n{items_note_yue}\n\n"
        f"<b>🤖 AI Summary:</b>\n{ai_summary}\n\n"
        f"<a href='{url}'>🔗 View Full SEC Filing</a>"
    )
    
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    try:
        response = requests.post(api_url, data=payload, timeout=10)
        if response.status_code != 200:
            print(f"Telegram Error: {response.text}")
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")


# ==========================================
# 6. Main Loop
# ==========================================
def main():
    print(f"\n{COLOR_GREEN}Starting SEC Monitor for {len(WATCHLIST)} tickers...{COLOR_RESET}")
    print("Checking SEC EDGAR RSS feed every 3 minutes. Press Ctrl+C to stop.\n")
    
    # 🚨 FIX: Removed '&type=8-k' and increased count to 100 to catch ALL SEC filings!
    rss_url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&CIK=&type=&company=&dateb=&owner=include&start=0&count=100&output=atom"

    while True:
        try:
            # Fetch RSS feed
            response = requests.get(rss_url, headers=SEC_HEADERS, timeout=10)
            feed = feedparser.parse(response.content)

            # --- HEARTBEAT PRINT ---
            current_time = datetime.now().strftime("%H:%M:%S")
            print(f"[{current_time}] Checked SEC feed. Found {len(feed.entries)} recent filings.")

            for entry in feed.entries:
                title = entry.title
                link = entry.link
                published = entry.updated if hasattr(entry, 'updated') else datetime.now(timezone.utc).isoformat()
                
                if link in seen_filings:
                    continue

                summary_text = entry.summary if hasattr(entry, 'summary') else title
                
                # Extract the Form Type from the Title (e.g., "10-K - APPLE INC")
                form_type = title.split(' - ')[0] if ' - ' in title else "Unknown Form"

                matched_ticker = None
                
                # 1. Match exact company name
                for ticker, name in TICKER_NAMES.items():
                    if name.lower() in title.lower():
                        matched_ticker = ticker
                        break
                
                # 2. Match exact ticker (with \b regex to prevent false alarms)
                if not matched_ticker:
                    for ticker in WATCHLIST:
                        if re.search(rf'\b{re.escape(ticker)}\b', title, re.IGNORECASE):
                            matched_ticker = ticker
                            break

                if matched_ticker:
                    company_name = TICKER_NAMES.get(matched_ticker, matched_ticker)
                    
                    # Extract 8-K Items if they exist
                    found_items = re.findall(r'Item\s*(\d\.\d{2})', summary_text, re.IGNORECASE)
                    found_items = list(set(found_items))
                    
                    # If it's an 8-K with items, list the items. If it's a 10-K/Form 4, just say the Form name.
                    items_str = ", ".join(found_items) if found_items else f"Form {form_type}"
                    
                    # Determine Tier using the new smart logic
                    tier = get_tier(found_items, form_type)
                    
                    if tier == 1:
                        color = COLOR_RED
                        tier_name = "TIER 1 - EMERGENCY"
                    elif tier == 2:
                        color = COLOR_YELLOW
                        tier_name = "TIER 2 - MYSTERY BOX"
                    else:
                        color = COLOR_GREEN
                        tier_name = "TIER 3 - ROUTINE"

                    print(f"{color}[{tier_name}] {matched_ticker} ({company_name}) filed: {items_str}{COLOR_RESET}")
                    
                    ai_summary = "Routine filing logged. No AI summary generated to save API costs."
                    
                    if tier in [1, 2]:
                        print("Fetching main HTML document...")
                        doc_text = fetch_primary_document(link)
                        print("Analyzing with Gemini...")
                        ai_summary = analyze_with_gemini(doc_text, form_type)
                        
                    print(f"Summary: {ai_summary}\n")

                    # Push to website
                    push_to_firebase(matched_ticker, company_name, link, tier, items_str, ai_summary, published)
                    
                    # Send to Telegram
                    if tier in [1, 2]:
                        send_telegram_alert(matched_ticker, company_name, tier, tier_name, items_str, ai_summary, link, found_items, form_type)
                    
                    seen_filings.add(link)

        except Exception as e:
            print(f"Error in main loop: {e}")

        # Sleep for 3 minutes (180 seconds)
        time.sleep(180)


if __name__ == "__main__":
    main()
