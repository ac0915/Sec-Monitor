import requests
from bs4 import BeautifulSoup
from secmon.config import get_settings

settings = get_settings()
url = 'https://www.sec.gov/Archives/edgar/data/1321655/000184024426000010/0001840244-26-000010-index.htm'

print(f"Fetching: {url}")
response = requests.get(url, timeout=settings.request_timeout_seconds, headers={'User-Agent': settings.sec_user_agent})
soup = BeautifulSoup(response.content, 'html.parser')

table = soup.find('table', class_='tableFile')
if table:
    print('✓ Table found!')
    rows = table.find_all('tr')[1:6]  # Skip header, get first 5 data rows
    for i, row in enumerate(rows):
        cols = row.find_all('td')
        if len(cols) >= 3:
            link = cols[2].find('a')
            if link:
                href = link.get('href', '')
                filename = href.split('/')[-1]
                print(f'  File: {filename}')
else:
    print('✗ Table NOT found with class=tableFile')
    # List all tables
    tables = soup.find_all('table')
    print(f"Total tables found: {len(tables)}")
