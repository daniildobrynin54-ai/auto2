"""Rate limiter для контроля запросов к API."""

import time
import threading
from collections import deque
from typing import Callable, Optional, Any
from functools import wraps

from config import (
    RATE_LIMIT_PER_MINUTE,
    RATE_LIMIT_WINDOW,
    RATE_LIMIT_RETRY_DELAY,
    MAX_RETRIES,
    RETRY_DELAY
)

class RateLimiter:
    """Rate limiter с поддержкой retry для ошибок 429."""
    
    def __init__(
        self,
        max_requests: int = RATE_LIMIT_PER_MINUTE,
        window_seconds: int = RATE_LIMIT_WINDOW,
        retry_delay: int = RATE_LIMIT_RETRY_DELAY
    ):
        """
        Инициализация rate limiter.
        
        Args:
            max_requests: Максимум запросов в окне
            window_seconds: Размер окна в секундах
            retry_delay: Задержка при 429 ошибке
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.retry_delay = retry_delay
        self.requests = deque()
        self.lock = threading.Lock()
        self.paused_until = 0  # Timestamp когда можно возобновить
    
    def _cleanup_old_requests(self) -> None:
        """Удаляет запросы старше окна."""
        current_time = time.time()
        cutoff = current_time - self.window_seconds
        
        while self.requests and self.requests[0] < cutoff:
            self.requests.popleft()
    
    def _wait_if_needed(self) -> None:
        """Ожидает если лимит превышен."""
        with self.lock:
            # Проверяем паузу от 429
            if self.paused_until > time.time():
                wait_time = self.paused_until - time.time()
                if wait_time > 0:
                    print(f"⏸️  Rate limit pause: {wait_time:.1f}s")
                    time.sleep(wait_time)
                self.paused_until = 0
            
            self._cleanup_old_requests()
            
            # Проверяем лимит
            if len(self.requests) >= self.max_requests:
                oldest = self.requests[0]
                wait_time = (oldest + self.window_seconds) - time.time()
                
                if wait_time > 0:
                    print(f"⏳ Rate limit: waiting {wait_time:.1f}s")
                    time.sleep(wait_time)
                    self._cleanup_old_requests()
    
    def record_request(self) -> None:
        """Записывает выполненный запрос."""
        with self.lock:
            self.requests.append(time.time())
    
    def pause_for_429(self) -> None:
        """Устанавливает паузу после получения 429."""
        with self.lock:
            self.paused_until = time.time() + self.retry_delay
    
    def get_current_rate(self) -> int:
        """Возвращает текущее количество запросов в окне."""
        with self.lock:
            self._cleanup_old_requests()
            return len(self.requests)
    
    def wait_and_record(self) -> None:
        """Ожидает если нужно и записывает запрос."""
        self._wait_if_needed()
        self.record_request()


# Глобальный rate limiter
_global_rate_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    """Возвращает глобальный rate limiter."""
    return _global_rate_limiter


def with_rate_limit(action_type: str):
    """
    Декоратор для применения rate limiting к функции.
    
    Args:
        action_type: Тип действия для логирования
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            limiter = get_rate_limiter()
            limiter.wait_and_record()
            return func(*args, **kwargs)
        return wrapper
    return decorator


def with_retry(
    max_attempts: int = MAX_RETRIES,
    retry_delay: float = RETRY_DELAY,
    handle_429: bool = True
):
    """
    Декоратор для повторных попыток при ошибках.
    
    Args:
        max_attempts: Максимум попыток
        retry_delay: Базовая задержка между попытками
        handle_429: Обрабатывать ли 429 специальным образом
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            limiter = get_rate_limiter()
            last_exception = None
            
            for attempt in range(max_attempts):
                try:
                    result = func(*args, **kwargs)
                    
                    # Проверяем на 429 в результате
                    if handle_429 and hasattr(result, 'status_code'):
                        if result.status_code == 429:
                            limiter.pause_for_429()
                            if attempt < max_attempts - 1:
                                continue
                    
                    return result
                    
                except Exception as e:
                    last_exception = e
                    
                    if attempt < max_attempts - 1:
                        wait_time = retry_delay * (2 ** attempt)  # Exponential backoff
                        print(f"   Retrying in {wait_time}s...")
                        time.sleep(wait_time)
            
            # 🔧 ИСПРАВЛЕНО: Выбрасываем исключение если все попытки исчерпаны
            if last_exception:
                raise last_exception
            
            return None
        
        return wrapper
    return decorator


class RateLimitedSession:
    """Wrapper для requests.Session с rate limiting."""
    
    def __init__(self, session, limiter: Optional[RateLimiter] = None):
        """
        Инициализация.
        
        Args:
            session: requests.Session объект
            limiter: RateLimiter или None для использования глобального
        """
        self._session = session
        self._limiter = limiter or get_rate_limiter()
    
    def _make_request(self, method: str, url: str, **kwargs):
        """Выполняет запрос с rate limiting и retry."""
        for attempt in range(MAX_RETRIES):
            # Ждем если нужно
            self._limiter.wait_and_record()
            
            try:
                response = getattr(self._session, method)(url, **kwargs)
                
                # Обрабатываем 429
                if response.status_code == 429:
                    self._limiter.pause_for_429()
                    
                    if attempt < MAX_RETRIES - 1:
                        continue
                
                return response
                
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    wait = RETRY_DELAY * (2 ** attempt)
                    print(f"   Retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    raise
        
        # 🔧 ИСПРАВЛЕНО: Возвращаем None если все попытки исчерпаны
        return None
    
    def get(self, url: str, **kwargs):
        """GET запрос с rate limiting."""
        return self._make_request('get', url, **kwargs)
    
    def post(self, url: str, **kwargs):
        """POST запрос с rate limiting."""
        return self._make_request('post', url, **kwargs)
    
    # Проксируем остальные атрибуты к оригинальной сессии
    def __getattr__(self, name):
        return getattr(self._session, name)