"""Модуль для отслеживания дневной статистики с учетом часового пояса MSK."""

import re
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta
import requests
from bs4 import BeautifulSoup
from config import (
    BASE_URL,
    REQUEST_TIMEOUT,
    MAX_DAILY_DONATIONS,
    MAX_DAILY_REPLACEMENTS,
    TIMEZONE_OFFSET
)

class DailyStatsManager:
    """Менеджер дневной статистики с учетом MSK и сброса лимитов."""
    
    def __init__(self, session: requests.Session, boost_url: str):
        """
        Инициализация менеджера статистики.
        
        Args:
            session: Сессия requests
            boost_url: URL страницы буста клуба
        """
        self.session = session
        self.boost_url = boost_url
        self._cached_stats = None
        self._last_refresh = None
    
    def _get_msk_time(self) -> datetime:
        """Возвращает текущее время в MSK (UTC+3)."""
        utc_now = datetime.now(timezone.utc)
        msk_time = utc_now + timedelta(hours=TIMEZONE_OFFSET)
        return msk_time
    
    def _seconds_until_reset(self) -> int:
        """
        Вычисляет количество секунд до сброса лимитов (00:00 MSK).
        
        Returns:
            Количество секунд до полуночи MSK
        """
        msk_now = self._get_msk_time()
        
        # Следующая полночь MSK
        next_midnight = msk_now.replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + timedelta(days=1)
        
        diff = next_midnight - msk_now
        return int(diff.total_seconds())
    
    def _format_time_until_reset(self) -> str:
        """Форматирует время до сброса в читаемый вид."""
        seconds = self._seconds_until_reset()
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}ч {minutes}м"
    
    def _parse_replacements_from_page(self, soup: BeautifulSoup) -> Optional[tuple[int, int]]:
        """Парсит количество использованных замен со страницы."""
        try:
            change_block = soup.select_one('.club-boost__change > div')
            
            if not change_block:
                return None
            
            text = change_block.get_text(strip=True)
            match = re.search(r'(\d+)\s*/\s*(\d+)', text)
            
            if match:
                used = int(match.group(1))
                maximum = int(match.group(2))
                return used, maximum
            
            return None
            
        except Exception as e:
            return None
    
    def _parse_donations_limit(self, soup: BeautifulSoup) -> Optional[tuple[int, int]]:
        """Парсит лимит пожертвований из правил."""
        try:
            rules = soup.select('.club-boost__rules li')
            
            for rule in rules:
                text = rule.get_text()
                match = re.search(r'до\s+(\d+)/(\d+)\s+карт', text)
                if match:
                    used = int(match.group(1))
                    maximum = int(match.group(2))
                    return used, maximum
            
            return None
            
        except Exception as e:
            return None
    
    def fetch_stats_from_page(self) -> Optional[Dict[str, Any]]:
        """Загружает статистику со страницы клуба."""
        try:
            response = self.session.get(self.boost_url, timeout=REQUEST_TIMEOUT)
            
            if response.status_code != 200:
                return None
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Парсим замены
            replacements_data = self._parse_replacements_from_page(soup)
            
            if replacements_data:
                replacements_used, replacements_max = replacements_data
            else:
                replacements_used = 0
                replacements_max = MAX_DAILY_REPLACEMENTS
            
            # Парсим пожертвования
            donations_data = self._parse_donations_limit(soup)
            
            if donations_data:
                donations_used, donations_max = donations_data
            else:
                donations_used = 0
                donations_max = MAX_DAILY_DONATIONS
            
            stats = {
                "donations_used": donations_used,
                "donations_max": donations_max,
                "replacements_used": replacements_used,
                "replacements_max": replacements_max,
                "donations_left": donations_max - donations_used,
                "replacements_left": replacements_max - replacements_used,
                "time_until_reset": self._seconds_until_reset(),
                "reset_time_formatted": self._format_time_until_reset()
            }
            
            # Кэшируем
            self._cached_stats = stats
            self._last_refresh = datetime.now()
            
            return stats
            
        except requests.RequestException as e:
            return None
        except Exception as e:
            return None
    
    def get_stats(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Получает статистику (из кэша или загружает заново)."""
        if force_refresh or self._cached_stats is None:
            stats = self.fetch_stats_from_page()
            
            if stats is None:
                return {
                    "donations_used": 0,
                    "donations_max": MAX_DAILY_DONATIONS,
                    "replacements_used": 0,
                    "replacements_max": MAX_DAILY_REPLACEMENTS,
                    "donations_left": MAX_DAILY_DONATIONS,
                    "replacements_left": MAX_DAILY_REPLACEMENTS,
                    "time_until_reset": self._seconds_until_reset(),
                    "reset_time_formatted": self._format_time_until_reset()
                }
            
            return stats
        
        # Обновляем время до сброса в кэше
        if self._cached_stats:
            self._cached_stats["time_until_reset"] = self._seconds_until_reset()
            self._cached_stats["reset_time_formatted"] = self._format_time_until_reset()
        
        return self._cached_stats
    
    def can_donate(self, force_refresh: bool = True) -> bool:
        """
        Проверяет, можно ли пожертвовать карту.
        
        Args:
            force_refresh: Обновить данные с сервера
        
        Returns:
            True если лимит не достигнут
        """
        stats = self.get_stats(force_refresh=force_refresh)
        return stats["donations_left"] > 0
    
    def can_replace(self, force_refresh: bool = True) -> bool:
        """
        Проверяет, можно ли заменить карту.
        
        Args:
            force_refresh: Обновить данные с сервера
        
        Returns:
            True если лимит не достигнут
        """
        stats = self.get_stats(force_refresh=force_refresh)
        return stats["replacements_left"] > 0
    
    def get_donations_left(self, force_refresh: bool = False) -> int:
        """Возвращает оставшееся количество пожертвований."""
        stats = self.get_stats(force_refresh=force_refresh)
        return stats["donations_left"]
    
    def get_replacements_left(self, force_refresh: bool = False) -> int:
        """Возвращает оставшееся количество замен."""
        stats = self.get_stats(force_refresh=force_refresh)
        return stats["replacements_left"]
    
    def print_stats(self, force_refresh: bool = False) -> None:
        """Выводит текущую статистику с временем до сброса."""
        stats = self.get_stats(force_refresh=force_refresh)
        
        msk_time = self._get_msk_time().strftime('%H:%M:%S MSK')
        
        print(f"\n📊 Дневная статистика ({msk_time}):")
        print(f"   Пожертвовано: {stats['donations_used']}/{stats['donations_max']}")
        print(f"   Замен карты: {stats['replacements_used']}/{stats['replacements_max']}")
        print(f"   Осталось пожертвований: {stats['donations_left']}")
        print(f"   Осталось замен: {stats['replacements_left']}")
        print(f"   ⏰ Сброс через: {stats['reset_time_formatted']}\n")
    
    def refresh_stats(self) -> None:
        """Принудительно обновляет статистику с сервера."""
        self.fetch_stats_from_page()
    
    def can_work(self, force_refresh: bool = True) -> bool:
        """
        🔧 НОВОЕ: Проверяет, может ли бот работать (есть ли хотя бы один доступный лимит).
        
        Returns:
            True если можно вкладывать карты ИЛИ заменять карты
        """
        stats = self.get_stats(force_refresh=force_refresh)
        return stats["donations_left"] > 0 or stats["replacements_left"] > 0

def create_stats_manager(
    session: requests.Session,
    boost_url: str
) -> DailyStatsManager:
    """Фабричная функция для создания менеджера статистики."""
    return DailyStatsManager(session, boost_url)