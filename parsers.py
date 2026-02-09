"""Парсеры для подсчета владельцев и желающих."""

import time
from typing import Tuple
import requests
from bs4 import BeautifulSoup
from config import (
    BASE_URL,
    REQUEST_TIMEOUT,
    PARSE_DELAY,
    OWNERS_PER_PAGE,
    WANTS_PER_PAGE,
    OWNERS_APPROXIMATE_THRESHOLD,
    WANTS_APPROXIMATE_THRESHOLD,
    OWNERS_LAST_PAGE_ESTIMATE,
    WANTS_LAST_PAGE_ESTIMATE
)


def parse_max_page_number(soup: BeautifulSoup) -> int:
    """
    Извлекает максимальный номер страницы из пагинации.
    
    Args:
        soup: Объект BeautifulSoup со страницей
    
    Returns:
        Максимальный номер страницы (минимум 1)
    """
    selectors = [
        '.pagination__button',
        '.pagination > li > a',
        '.pagination > li',
        '.paginator a'
    ]
    
    page_numbers = []
    
    for selector in selectors:
        elements = soup.select(selector)
        for element in elements:
            text = element.get_text(strip=True)
            try:
                number = int(text)
                if number > 0:
                    page_numbers.append(number)
            except ValueError:
                continue
    
    return max(page_numbers) if page_numbers else 1


def count_elements_on_page(soup: BeautifulSoup, selector: str) -> int:
    """
    Подсчитывает количество элементов на странице по селектору.
    
    Args:
        soup: Объект BeautifulSoup со страницей
        selector: CSS селектор
    
    Returns:
        Количество найденных элементов
    """
    return len(soup.select(selector))


def fetch_last_page(
    session: requests.Session,
    url: str,
    max_page: int
) -> Tuple[bool, BeautifulSoup]:
    """
    Загружает последнюю страницу.
    
    Args:
        session: Сессия requests
        url: Базовый URL
        max_page: Номер последней страницы
    
    Returns:
        Кортеж (успешность, BeautifulSoup объект)
    """
    time.sleep(PARSE_DELAY)
    
    last_page_url = f"{url}?page={max_page}"
    
    try:
        response = session.get(last_page_url, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            return False, None
        
        soup = BeautifulSoup(response.text, "html.parser")
        return True, soup
        
    except requests.RequestException:
        return False, None


def count_owners(
    session: requests.Session,
    card_id: str,
    force_accurate: bool = False
) -> int:
    """
    Подсчитывает владельцев карты с оптимизацией.
    
    Использует приближенный подсчет для карт с большим количеством страниц.
    
    Args:
        session: Сессия requests
        card_id: ID карты
        force_accurate: Принудительный точный подсчет
    
    Returns:
        Количество владельцев или -1 при ошибке
    """
    url = f"{BASE_URL}/cards/{card_id}/users"
    
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            return -1
        
        soup = BeautifulSoup(response.text, "html.parser")
        max_page = parse_max_page_number(soup)
        
        # Одна страница - точный подсчет
        if max_page == 1:
            return count_elements_on_page(soup, '.card-show__owner')
        
        # Приближенный подсчет для больших списков
        if max_page >= OWNERS_APPROXIMATE_THRESHOLD and not force_accurate:
            return (max_page - 1) * OWNERS_PER_PAGE + OWNERS_LAST_PAGE_ESTIMATE
        
        # Точный подсчет - загружаем последнюю страницу
        success, last_soup = fetch_last_page(session, url, max_page)
        
        if not success:
            return (max_page - 1) * OWNERS_PER_PAGE + OWNERS_LAST_PAGE_ESTIMATE
        
        last_page_count = count_elements_on_page(last_soup, '.card-show__owner')
        return (max_page - 1) * OWNERS_PER_PAGE + last_page_count
        
    except requests.RequestException:
        return -1


def count_wants(
    session: requests.Session,
    card_id: str,
    force_accurate: bool = False
) -> int:
    """
    Подсчитывает желающих карту с оптимизацией.
    
    Использует приближенный подсчет для карт с большим количеством страниц.
    
    Args:
        session: Сессия requests
        card_id: ID карты
        force_accurate: Принудительный точный подсчет
    
    Returns:
        Количество желающих или -1 при ошибке
    """
    url = f"{BASE_URL}/cards/{card_id}/offers/want"
    
    # CSS селекторы для поиска пользователей
    user_selectors = '.profile__friends-item, .users-list__item, .user-card'
    
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            return -1
        
        soup = BeautifulSoup(response.text, "html.parser")
        max_page = parse_max_page_number(soup)
        
        # Одна страница - точный подсчет
        if max_page == 1:
            return count_elements_on_page(soup, user_selectors)
        
        # Приближенный подсчет для больших списков
        if max_page >= WANTS_APPROXIMATE_THRESHOLD and not force_accurate:
            return (max_page - 1) * WANTS_PER_PAGE + WANTS_LAST_PAGE_ESTIMATE
        
        # Точный подсчет - загружаем последнюю страницу
        success, last_soup = fetch_last_page(session, url, max_page)
        
        if not success:
            return (max_page - 1) * WANTS_PER_PAGE + WANTS_LAST_PAGE_ESTIMATE
        
        last_page_count = count_elements_on_page(last_soup, user_selectors)
        return (max_page - 1) * WANTS_PER_PAGE + last_page_count
        
    except requests.RequestException:
        return -1
