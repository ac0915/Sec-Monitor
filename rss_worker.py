import time
import json
import feedparser
import requests
from pathlib import Path

# ==========================================
# 1. 你的「黃金情報」追蹤清單
# ==========================================
FEEDS = {
    "Google Research (技術突破)": "https://blog.research.google/feeds/posts/default",
    "NVIDIA Developer (架構優化)": "https://developer.nvidia.com/blog/feed/",
    "Apple Machine Learning (邊緣AI)": "https://machinelearning.apple.com/rss.xml",
    "ArXiv (量化與壓縮最新論文)": 'http://export.arxiv.org/api/query?search_query=all:"quantization"+OR+all:"KV+cache"+OR+all:"1-bit"&sortBy=submittedDate&sortOrder=descending&max_results=3',
    # DNN 這種傳統公司通常用 PR Newswire 或官網 RSS，這裡放一個範例
    "Denison Mines (鈾礦/核能)": "https://denisonmines.com/rss/" 
}

# 用來記錄「已經推播過的文章網址」，避免重複發送
SEEN_FILE = Path("seen_posts.json")

def load_seen_posts() -> set:
    """讀取已經看過的文章紀錄"""
    if SEEN_FILE.exists():
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_seen_posts(seen_posts: set) -> None:
    """儲存紀錄，下次重開機才不會重複推播"""
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_posts), f, ensure_ascii=False)

def send_telegram_alert(source_name: str, title: str, link: str):
    """
    發送 Telegram 通知 (這裡可以換成你 sec_monitor 裡的發送函數)
    """
    # 這裡替換成你實際的 Telegram Bot Token 和 Chat ID
    # BOT_TOKEN = "你的_BOT_TOKEN"
    # CHAT_ID = "你的_CHAT_ID"
    
    message = f"🚨 **【技術突破警報】 {source_name}**\n\n📌 **標題:** {title}\n🔗 **連結:** {link}"
    print(f"準備發送推播: {message}")
    print("-" * 50)
    
    # 實際發送的 API 呼叫 (如果你要獨立運行的話把註解拿掉)
    # url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    # payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    # requests.post(url, json=payload)

# ==========================================
# 2. 核心爬蟲邏輯
# ==========================================
def run_rss_worker():
    print("啟動技術情報網 RSS Worker... 監控中 🕵️‍♂️")
    seen_posts = load_seen_posts()

    while True:
        try:
            for source_name, feed_url in FEEDS.items():
                # 解析 RSS 或 ArXiv API
                feed = feedparser.parse(feed_url)
                
                # 只檢查最新的 3 篇文章，提高效率
                for entry in feed.entries[:3]:
                    post_link = entry.link
                    post_title = entry.title
                    
                    # 如果這篇文章沒看過，就是「突發新聞」！
                    if post_link not in seen_posts:
                        send_telegram_alert(source_name, post_title, post_link)
                        seen_posts.add(post_link)
            
            # 儲存紀錄
            save_seen_posts(seen_posts)
            
            # 顯示當前狀態
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 巡邏完成，無新文章。等待 15 分鐘...")
            
            # 暫停 15 分鐘 (900秒) 後再次檢查
            time.sleep(900)

        except Exception as e:
            print(f"發生錯誤: {e}")
            time.sleep(60) # 發生錯誤時休息一分鐘再試，防止 AWS 崩潰

if __name__ == "__main__":
    run_rss_worker()
