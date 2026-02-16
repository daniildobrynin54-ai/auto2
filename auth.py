"""Модуль авторизации с поддержкой прокси и обновления токенов."""

from typing import Optional
import requests
from bs4 import BeautifulSoup

from config import BASE_URL, USER_AGENT, REQUEST_TIMEOUT
from rate_limiter import RateLimitedSession
from proxy_manager import ProxyManager


class AuthenticationError(Exception):
    """Ошибка аутентификации."""
    pass


def get_csrf_token(session: requests.Session) -> Optional[str]:
    """Получает CSRF токен со страницы логина."""
    try:
        response = session.get(f"{BASE_URL}/login", timeout=REQUEST_TIMEOUT)
        
        if response.status_code != 200:
            return None
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Пробуем найти токен в meta теге
        token_meta = soup.select_one('meta[name="csrf-token"]')
        if token_meta:
            token = token_meta.get("content", "").strip()
            if token:
                return token
        
        # Пробуем найти токен в input поле
        token_input = soup.find("input", {"name": "_token"})
        if token_input:
            token = token_input.get("value", "").strip()
            if token:
                return token
        
        return None
        
    except requests.RequestException:
        return None


def create_session(proxy_manager: Optional[ProxyManager] = None) -> requests.Session:
    """
    Создает настроенную сессию requests с прокси.
    
    Args:
        proxy_manager: Менеджер прокси
    
    Returns:
        Настроенная сессия с rate limiting
    """
    session = requests.Session()
    
    # Настраиваем прокси
    if proxy_manager and proxy_manager.is_enabled():
        proxies = proxy_manager.get_proxies()
        if proxies:
            session.proxies.update(proxies)
    
    # Настраиваем заголовки
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru,en;q=0.8",
    })
    
    # Оборачиваем в RateLimitedSession
    return RateLimitedSession(session)


def login(
    email: str,
    password: str,
    proxy_manager: Optional[ProxyManager] = None
) -> Optional[RateLimitedSession]:
    """
    Выполняет вход в аккаунт.
    
    Args:
        email: Email пользователя
        password: Пароль
        proxy_manager: Менеджер прокси
    
    Returns:
        Авторизованная сессия или None при ошибке
    
    Raises:
        AuthenticationError: При ошибке аутентификации
    """
    session = create_session(proxy_manager)
    
    csrf_token = get_csrf_token(session)
    if not csrf_token:
        print("⚠️  Не удалось получить CSRF токен")
        return None
    
    headers = {
        "Referer": f"{BASE_URL}/login",
        "Origin": BASE_URL,
        "Content-Type": "application/x-www-form-urlencoded",
        "X-CSRF-TOKEN": csrf_token,
    }
    
    data = {
        "email": email,
        "password": password,
        "_token": csrf_token
    }
    
    try:
        response = session.post(
            f"{BASE_URL}/login",
            data=data,
            headers=headers,
            allow_redirects=True,
            timeout=REQUEST_TIMEOUT
        )
        
        # Проверяем успешность входа по наличию cookie сессии
        if "mangabuff_session" not in session.cookies:
            print("⚠️  Авторизация не удалась: нет cookie сессии")
            return None
        
        # Обновляем заголовки для последующих запросов
        session.headers.update({
            "X-CSRF-TOKEN": csrf_token,
            "X-Requested-With": "XMLHttpRequest"
        })
        
        return session
        
    except requests.RequestException as e:
        print(f"⚠️  Ошибка при авторизации: {e}")
        return None


def refresh_session_token(session: requests.Session) -> bool:
    """
    🔧 НОВОЕ: Обновляет CSRF токен в существующей сессии.
    
    Args:
        session: Существующая сессия
    
    Returns:
        True если успешно
    """
    try:
        print("🔄 Обновление CSRF токена...")
        
        response = session.get(f"{BASE_URL}/trades/offers", timeout=REQUEST_TIMEOUT)
        
        if response.status_code != 200:
            print(f"⚠️  Не удалось загрузить страницу: {response.status_code}")
            return False
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Ищем токен в meta теге
        token_meta = soup.select_one('meta[name="csrf-token"]')
        if token_meta:
            token = token_meta.get("content", "").strip()
            if token:
                # Обновляем заголовки сессии
                if isinstance(session, RateLimitedSession):
                    session._session.headers.update({
                        "X-CSRF-TOKEN": token,
                        "X-Requested-With": "XMLHttpRequest"
                    })
                else:
                    session.headers.update({
                        "X-CSRF-TOKEN": token,
                        "X-Requested-With": "XMLHttpRequest"
                    })
                
                print(f"✅ CSRF токен обновлен: {token[:10]}...")
                return True
        
        # Пробуем найти в input поле
        token_input = soup.find("input", {"name": "_token"})
        if token_input:
            token = token_input.get("value", "").strip()
            if token:
                if isinstance(session, RateLimitedSession):
                    session._session.headers.update({
                        "X-CSRF-TOKEN": token,
                        "X-Requested-With": "XMLHttpRequest"
                    })
                else:
                    session.headers.update({
                        "X-CSRF-TOKEN": token,
                        "X-Requested-With": "XMLHttpRequest"
                    })
                
                print(f"✅ CSRF токен обновлен: {token[:10]}...")
                return True
        
        print("⚠️  Не удалось найти CSRF токен на странице")
        return False
        
    except Exception as e:
        print(f"❌ Ошибка обновления токена: {e}")
        return False


def logout(session: requests.Session) -> bool:
    """
    Выполняет выход из аккаунта.
    
    Args:
        session: Сессия для выхода
    
    Returns:
        True если успешно
    """
    try:
        # Пытаемся выполнить запрос на logout
        response = session.get(f"{BASE_URL}/logout", timeout=REQUEST_TIMEOUT)
        
        # Очищаем cookies
        if isinstance(session, RateLimitedSession):
            session._session.cookies.clear()
        else:
            session.cookies.clear()
        
        # Удаляем CSRF токен из заголовков
        headers_to_delete = ["X-CSRF-TOKEN", "X-Requested-With"]
        
        for header in headers_to_delete:
            if isinstance(session, RateLimitedSession):
                if header in session._session.headers:
                    del session._session.headers[header]
            else:
                if header in session.headers:
                    del session.headers[header]
        
        return True
        
    except requests.RequestException as e:
        print(f"⚠️  Ошибка при выходе: {e}")
        # Очищаем cookies даже при ошибке
        if isinstance(session, RateLimitedSession):
            session._session.cookies.clear()
        else:
            session.cookies.clear()
        return False


def is_authenticated(session: requests.Session) -> bool:
    """
    Проверяет, авторизована ли сессия.
    
    Args:
        session: Сессия для проверки
    
    Returns:
        True если сессия авторизована
    """
    # Для RateLimitedSession нужно обращаться к _session
    if isinstance(session, RateLimitedSession):
        return "mangabuff_session" in session._session.cookies
    else:
        return "mangabuff_session" in session.cookies