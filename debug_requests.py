"""
python debug_requests.py
"""
import sys, os, traceback, re
sys.path.insert(0, os.path.dirname(__file__))

try:
    import requests
    from bs4 import BeautifulSoup
    from urllib.parse import unquote
    print("✅ Импорты OK")
except Exception as e:
    print(f"❌ {e}"); sys.exit(1)

BASE_URL = "https://mangabuff.ru"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")

EMAIL    = "danpaganela@gmail.com"
PASSWORD = "CLUBTARO"
USER_ID  = "826513"
BOOST_URL = "https://mangabuff.ru/clubs/klub-taro-2/boost"

# ─── Сессия (без прокси) ─────────────────────────────────────────────
s = requests.Session()
s.headers["User-Agent"] = UA

# Шаг 1
s.get(BASE_URL, timeout=20)
print(f"GET / куки: {[c.name for c in s.cookies]}")

# Шаг 2
r_login = s.get(f"{BASE_URL}/login", timeout=20)
csrf_meta = BeautifulSoup(r_login.text, "html.parser").select_one('meta[name="csrf-token"]')
csrf = csrf_meta.get("content") if csrf_meta else None
xsrf_raw = next((c.value for c in s.cookies if c.name == "XSRF-TOKEN"), None)
xsrf = unquote(xsrf_raw) if xsrf_raw else csrf
print(f"CSRF: {csrf[:20] if csrf else None}...")

# Шаг 3: AJAX POST
print("\n" + "="*60)
print("POST /login как AJAX (XHR)")
print("="*60)

r_post = s.post(
    f"{BASE_URL}/login",
    data={"login": EMAIL, "password": PASSWORD, "_token": csrf},
    headers={
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRF-TOKEN": xsrf,
        "X-XSRF-TOKEN": xsrf,
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/login",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    },
    allow_redirects=True,
    timeout=20,
)

print(f"Status: {r_post.status_code}")
print(f"Content-Type: {r_post.headers.get('content-type')}")
print(f"URL: {r_post.url}")
print(f"Body: {r_post.text[:500]}")

# Проверка isAuth
r_main = s.get(BASE_URL, timeout=20)
m = re.search(r'window\.isAuth\s*=\s*(\d+)', r_main.text)
m2 = re.search(r'window\.user_id\s*=\s*(\d+)', r_main.text)
print(f"\nwindow.isAuth = {m.group(1) if m else '?'}")
print(f"window.user_id = {m2.group(1) if m2 else '?'}")

# Тесты API
print("\n" + "="*60)
print("ТЕСТ: POST availableCardsLoad")
print("="*60)
r_inv = s.post(
    f"{BASE_URL}/trades/{USER_ID}/availableCardsLoad",
    headers={
        "Referer": f"{BASE_URL}/trades/{USER_ID}",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    },
    data={"offset": 0},
    timeout=20,
)
print(f"Status: {r_inv.status_code}")
print(f"Body: {r_inv.text[:300]}")

print("\n" + "="*60)
print("ТЕСТ: GET boost page")
print("="*60)
r_boost = s.get(BOOST_URL, timeout=20)
print(f"Status: {r_boost.status_code}")
print(f"Body (500): {r_boost.text[:500]}")