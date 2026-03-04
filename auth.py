"""Модуль авторизации с поддержкой прокси и обновления токенов."""

from typing import Optional
from urllib.parse import unquote
import requests
from bs4 import BeautifulSoup

from config import BASE_URL, REQUEST_TIMEOUT
from rate_limiter import RateLimitedSession
from proxy_manager import ProxyManager

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)


class AuthenticationError(Exception):
    pass


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def _get_cookie(jar, name: str) -> Optional[str]:
    for cookie in jar:
        if cookie.name == name and cookie.value:
            return cookie.value
    return None


def _extract_csrf(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.select_one('meta[name="csrf-token"]')
    if meta:
        t = meta.get("content", "").strip()
        if t:
            return t
    inp = soup.find("input", {"name": "_token"})
    if inp:
        t = inp.get("value", "").strip()
        if t:
            return t
    return None


def _apply_ajax_tokens(raw: requests.Session) -> None:
    """Устанавливает X-CSRF-TOKEN и X-XSRF-TOKEN из куки для AJAX-запросов."""
    xsrf_raw = _get_cookie(raw.cookies, "XSRF-TOKEN")
    if xsrf_raw:
        xsrf = unquote(xsrf_raw)
        raw.headers.update({
            "X-CSRF-TOKEN": xsrf,
            "X-XSRF-TOKEN": xsrf,
        })


def _nav_headers(referer: Optional[str] = None, fetch_site: str = "none") -> dict:
    """Заголовки навигационного запроса."""
    h = {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Ch-Ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Site": fetch_site,
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
        "Priority": "u=0, i",
    }
    if referer:
        h["Referer"] = referer
    return h


# ---------------------------------------------------------------------------
# Создание сессии
# ---------------------------------------------------------------------------

def create_session(proxy_manager: Optional[ProxyManager] = None) -> RateLimitedSession:
    raw = requests.Session()
    if proxy_manager and proxy_manager.is_enabled():
        proxies = proxy_manager.get_proxies()
        if proxies:
            raw.proxies.update(proxies)
    raw.headers.update({"User-Agent": USER_AGENT})
    return RateLimitedSession(raw)


# ---------------------------------------------------------------------------
# Авторизация
# ---------------------------------------------------------------------------

def login(
    email: str,
    password: str,
    proxy_manager: Optional[ProxyManager] = None,
) -> Optional[RateLimitedSession]:
    """
    Авторизация на mangabuff.ru.

    Форма логина — <div class="form">, без <form> тега.
    Кнопка 'login-button' отправляет AJAX POST на /login.
    Поэтому нужно слать как XHR: X-Requested-With + Accept: application/json.
    Ответ — JSON: {"success": true, ...} или редирект с Set-Cookie.
    """
    session = create_session(proxy_manager)
    raw = session._session

    # ------------------------------------------------------------------
    # Шаг 1: прогрев — ddos-guard ставит __ddg* куки
    # ------------------------------------------------------------------
    try:
        r0 = raw.get(BASE_URL, headers=_nav_headers(), timeout=REQUEST_TIMEOUT)
        print(f"   [1] GET / → {r0.status_code}, куки: {[c.name for c in raw.cookies]}")
    except requests.RequestException as e:
        print(f"   [1] GET / → ошибка: {e} (продолжаем)")

    # ------------------------------------------------------------------
    # Шаг 2: GET /login — получаем CSRF-токен и XSRF-TOKEN куку
    # ------------------------------------------------------------------
    try:
        r_get = raw.get(
            f"{BASE_URL}/login",
            headers=_nav_headers(referer=f"{BASE_URL}/", fetch_site="same-origin"),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        print(f"   [2] GET /login → ошибка: {e}")
        return None

    print(f"   [2] GET /login → {r_get.status_code}")

    if r_get.status_code != 200:
        print(f"   ❌ Неожиданный статус: {r_get.status_code}")
        return None

    csrf = _extract_csrf(r_get.text)
    if not csrf:
        print("   ❌ CSRF-токен не найден")
        return None

    print(f"   CSRF: {csrf[:30]}...")

    # XSRF-TOKEN из куки — декодируем для заголовка
    xsrf_raw = _get_cookie(raw.cookies, "XSRF-TOKEN")
    xsrf = unquote(xsrf_raw) if xsrf_raw else csrf

    # ------------------------------------------------------------------
    # Шаг 3: POST /login как AJAX (XHR)
    #
    # Форма на сайте — это <div class="form">, не <form>.
    # Кнопка "Войти" (.login-button) обрабатывается JS-ом через fetch/XHR.
    # Сервер ожидает AJAX-запрос, не обычный form submit.
    # ------------------------------------------------------------------
    ajax_headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRF-TOKEN": xsrf,
        "X-XSRF-TOKEN": xsrf,
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/login",
        "Sec-Ch-Ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }

    try:
        r_post = raw.post(
            f"{BASE_URL}/login",
            data={"login": email, "password": password, "_token": csrf},
            headers=ajax_headers,
            allow_redirects=True,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        print(f"   [3] POST /login → ошибка: {e}")
        return None

    print(f"   [3] POST /login → статус {r_post.status_code}, URL: {r_post.url}")
    print(f"   Content-Type ответа: {r_post.headers.get('content-type', '?')}")

    # ------------------------------------------------------------------
    # Шаг 4: разбираем ответ
    # ------------------------------------------------------------------
    ct = r_post.headers.get("content-type", "")

    if "application/json" in ct:
        try:
            j = r_post.json()
            print(f"   JSON ответ: {j}")
        except Exception:
            print(f"   JSON parse error, body: {r_post.text[:200]}")
            return None

        # Проверяем признаки неудачи
        if j.get("errors") or j.get("message") == "Unauthenticated." or j.get("status") == "error":
            print(f"   ❌ Сервер вернул ошибку: {j}")
            return None
    else:
        # HTML ответ
        print(f"   Не JSON ответ, body (500): {r_post.text[:500]}")

    # ------------------------------------------------------------------
    # Шаг 5: проверяем что сессия авторизована
    # GET на закрытую страницу — должен вернуть 200, не 401
    # ------------------------------------------------------------------
    try:
        r_check = raw.get(
            f"{BASE_URL}/users/{email.split('@')[0]}",  # не важно что — нам нужен /api/user или профиль
            timeout=REQUEST_TIMEOUT,
        )
    except Exception:
        r_check = None

    # Более надёжная проверка — GET на API endpoint
    try:
        r_check2 = raw.get(
            f"{BASE_URL}/api/user",
            headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            timeout=REQUEST_TIMEOUT,
        )
        print(f"   GET /api/user → {r_check2.status_code}: {r_check2.text[:200]}")
        if r_check2.status_code == 200:
            print("   ✅ /api/user вернул 200 — авторизованы!")
    except Exception as e:
        print(f"   GET /api/user → ошибка: {e}")

    # Проверяем window.isAuth в HTML любой страницы
    try:
        r_main = raw.get(BASE_URL, timeout=REQUEST_TIMEOUT)
        if "window.isAuth = 1" in r_main.text or 'window.isAuth=1' in r_main.text:
            print("   ✅ window.isAuth=1 — авторизованы!")
            is_auth = True
        else:
            # Ищем user_id
            import re
            m = re.search(r'window\.user_id\s*=\s*(\d+)', r_main.text)
            uid = m.group(1) if m else "0"
            print(f"   window.user_id={uid}, isAuth={'1' if 'isAuth = 1' in r_main.text else '0'}")
            is_auth = uid != "0" and uid != ""
    except Exception as e:
        print(f"   Проверка isAuth → ошибка: {e}")
        is_auth = False

    if not is_auth:
        print("   ❌ Не авторизованы после POST /login")
        print(f"   Куки: {[(c.name, c.value[:25]) for c in raw.cookies]}")
        return None

    # ------------------------------------------------------------------
    # Шаг 6: финальные токены для AJAX
    # ------------------------------------------------------------------
    _apply_ajax_tokens(raw)
    new_csrf = _extract_csrf(r_post.text if "text/html" in ct else r_main.text)
    if new_csrf:
        raw.headers.update({"X-CSRF-TOKEN": new_csrf})

    raw.headers.update({"X-Requested-With": "XMLHttpRequest"})

    print(f"   Итог: CSRF=✓, XSRF=✓, Session=✓")
    return session


# ---------------------------------------------------------------------------
# Обновление CSRF-токена
# ---------------------------------------------------------------------------

def refresh_session_token(session: RateLimitedSession) -> bool:
    raw = session._session
    try:
        print("🔄 Обновление CSRF-токена...")
        r = raw.get(BASE_URL, headers=_nav_headers(), timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return False
        _apply_ajax_tokens(raw)
        csrf = _extract_csrf(r.text)
        if csrf:
            raw.headers.update({"X-CSRF-TOKEN": csrf})
        print("✅ Токены обновлены")
        return True
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False


# ---------------------------------------------------------------------------
# Выход
# ---------------------------------------------------------------------------

def logout(session: RateLimitedSession) -> bool:
    raw = session._session
    try:
        raw.get(
            f"{BASE_URL}/logout",
            headers=_nav_headers(referer=BASE_URL, fetch_site="same-origin"),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        print(f"⚠️  Ошибка выхода: {e}")
    raw.cookies.clear()
    for h in ("X-CSRF-TOKEN", "X-XSRF-TOKEN", "X-Requested-With"):
        raw.headers.pop(h, None)
    return True


# ---------------------------------------------------------------------------
# Проверка авторизации
# ---------------------------------------------------------------------------

def is_authenticated(session) -> bool:
    if isinstance(session, RateLimitedSession):
        return bool(_get_cookie(session._session.cookies, "mangabuff_session"))
    return "mangabuff_session" in session.cookies