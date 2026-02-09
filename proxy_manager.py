"""Упрощенный менеджер прокси для requests с поддержкой SOCKS5."""

from typing import Optional, Dict
from urllib.parse import urlparse

from config import PROXY_ENABLED, PROXY_URL


class ProxyManager:
    """Упрощенный менеджер для настройки SOCKS5/HTTP прокси."""
    
    def __init__(self, proxy_url: Optional[str] = None):
        """
        Инициализация менеджера прокси.
        
        Args:
            proxy_url: URL прокси (по умолчанию из config.PROXY_URL)
        """
        self.proxy_url = proxy_url or PROXY_URL
        self.enabled = PROXY_ENABLED and bool(self.proxy_url)
    
    def get_proxies(self) -> Optional[Dict[str, str]]:
        """
        Возвращает словарь прокси для requests.
        
        Returns:
            Словарь с прокси или None если прокси не используется
        """
        if not self.enabled or not self.proxy_url:
            return None
        
        try:
            parsed = urlparse(self.proxy_url)
            
            # Проверяем что URL корректный
            if not parsed.scheme or not parsed.hostname:
                return None
            
            # Для SOCKS5 нужна библиотека requests[socks]
            if parsed.scheme in ('socks5', 'socks5h'):
                return {
                    'http': self.proxy_url,
                    'https': self.proxy_url
                }
            # Для HTTP/HTTPS
            elif parsed.scheme in ('http', 'https'):
                return {
                    'http': self.proxy_url,
                    'https': self.proxy_url
                }
            else:
                return None
                
        except Exception as e:
            print(f"⚠️ Ошибка получения прокси: {e}")
            return None
    
    def is_enabled(self) -> bool:
        """Проверяет, включен ли прокси."""
        return self.enabled
    
    def get_info(self) -> str:
        """Возвращает информацию о прокси."""
        if not self.enabled:
            return "Proxy: Disabled"
        
        try:
            parsed = urlparse(self.proxy_url)
            
            if parsed.password:
                safe_url = f"{parsed.scheme}://{parsed.username}:***@{parsed.hostname}:{parsed.port}"
            else:
                safe_url = self.proxy_url
            
            return f"Proxy: {safe_url}"
        except Exception:
            return f"Proxy: {self.proxy_url}"


def create_proxy_manager(proxy_url: Optional[str] = None) -> ProxyManager:
    """
    Фабричная функция для создания ProxyManager.
    
    Args:
        proxy_url: URL прокси (опционально, иначе из config)
    
    Returns:
        ProxyManager
    """
    manager = ProxyManager(proxy_url)
    
    if manager.is_enabled():
        print(f"🔗 {manager.get_info()}")
    else:
        print("📡 Proxy: Disabled")
    
    return manager