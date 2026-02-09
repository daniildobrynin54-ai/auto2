"""Вспомогательные функции."""

import json
import os
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta


def ensure_dir_exists(directory: str) -> None:
    """Создает директорию, если она не существует."""
    os.makedirs(directory, exist_ok=True)


def load_json(filepath: str, default: Any = None) -> Any:
    """
    Загружает JSON из файла.
    
    Args:
        filepath: Путь к файлу
        default: Значение по умолчанию при ошибке
    
    Returns:
        Загруженные данные или default
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(filepath: str, data: Any, indent: int = 2) -> bool:
    """
    Сохраняет данные в JSON файл.
    
    Args:
        filepath: Путь к файлу
        data: Данные для сохранения
        indent: Отступ для форматирования
    
    Returns:
        True если успешно, False при ошибке
    """
    try:
        ensure_dir_exists(os.path.dirname(filepath))
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        return True
    except Exception:
        return False


def is_cache_valid(cached_at_str: str, hours: int = 24) -> bool:
    """
    Проверяет актуальность кэша.
    
    Args:
        cached_at_str: Строка с датой кэширования в ISO формате
        hours: Количество часов для валидности кэша
    
    Returns:
        True если кэш актуален, False если устарел
    """
    try:
        cached_time = datetime.fromisoformat(cached_at_str)
        return datetime.now() - cached_time < timedelta(hours=hours)
    except (ValueError, TypeError):
        return False


def extract_card_data(card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Извлекает данные карты из различных форматов API.
    
    Args:
        card: Словарь с данными карты
    
    Returns:
        Нормализованные данные карты или None при ошибке
    """
    card_id = card.get("card_id")
    name = card.get("name") or card.get("title", "")
    rank = card.get("rank") or card.get("grade", "")
    instance_id = card.get("id")
    
    # Проверяем вложенный объект card
    nested_card = card.get("card")
    if isinstance(nested_card, dict):
        if not card_id:
            card_id = nested_card.get("id")
        if not name:
            name = nested_card.get("name") or nested_card.get("title", "")
        if not rank:
            rank = nested_card.get("rank") or nested_card.get("grade", "")
    
    if not card_id or not rank:
        return None
    
    return {
        "card_id": int(card_id),
        "name": name,
        "rank": rank.upper(),
        "instance_id": int(instance_id) if instance_id else 0
    }


def format_card_info(card: Dict[str, Any]) -> str:
    """
    Форматирует информацию о карте для вывода.
    
    Args:
        card: Словарь с данными карты
    
    Returns:
        Отформатированная строка
    """
    name = card.get("name", "Неизвестно")
    card_id = card.get("card_id", "?")
    rank = card.get("rank", "?")
    wanters = card.get("wanters_count", "?")
    owners = card.get("owners_count", "?")
    
    return (
        f"Название: {name}\n"
        f"   ID: {card_id} | Ранг: {rank}\n"
        f"   Владельцев: {owners} | Желающих: {wanters}"
    )


def print_section(title: str, char: str = "=", width: int = 60) -> None:
    """Выводит красивый заголовок секции."""
    print(f"\n{char * width}")
    print(title)
    print(f"{char * width}\n")


def print_success(message: str) -> None:
    """Выводит сообщение об успехе."""
    print(f"✅ {message}")


def print_error(message: str) -> None:
    """Выводит сообщение об ошибке."""
    print(f"❌ {message}")


def print_warning(message: str) -> None:
    """Выводит предупреждение."""
    print(f"⚠️  {message}")


def print_info(message: str) -> None:
    """Выводит информационное сообщение."""
    print(f"ℹ️  {message}")
