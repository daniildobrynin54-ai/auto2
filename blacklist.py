"""Модуль управления черным списком пользователей с горячей перезагрузкой."""

import os
import re
import json
import threading
import time
from typing import Set, List, Optional
from datetime import datetime

BLACKLIST_FILE = "blacklist.json"


class BlacklistManager:
    """
    Менеджер черного списка с автоматической перезагрузкой.
    
    Поддерживает:
    - Горячую перезагрузку при изменении файла
    - Различные форматы ссылок
    - Комментарии и причины блокировки
    """
    
    def __init__(
        self,
        blacklist_file: str = BLACKLIST_FILE,
        auto_reload: bool = True,
        check_interval: int = 5
    ):
        """
        Args:
            blacklist_file: Путь к файлу черного списка
            auto_reload: Автоматически перезагружать при изменении
            check_interval: Интервал проверки файла (секунды)
        """
        self.blacklist_file = blacklist_file
        self.auto_reload = auto_reload
        self.check_interval = check_interval
        
        self.blacklisted_ids: Set[str] = set()
        self.blacklist_data: dict = {}
        self.last_modified: float = 0
        
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        
        # Создаем файл если не существует
        self._ensure_file_exists()
        
        # Загружаем начальные данные
        self.reload()
        
        # Запускаем авто-перезагрузку
        if auto_reload:
            self.start_auto_reload()
    
    def _ensure_file_exists(self) -> None:
        """Создает файл черного списка с примером если не существует."""
        if not os.path.exists(self.blacklist_file):
            example_data = {
                "_comment": "Формат: user_id или полная ссылка",
                "_examples": [
                    "123456",
                    "https://mangabuff.ru/users/789012",
                    "/users/345678"
                ],
                "blacklist": []
            }
            
            try:
                with open(self.blacklist_file, 'w', encoding='utf-8') as f:
                    json.dump(example_data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
    
    def _extract_user_id(self, entry: str) -> Optional[str]:
        """
        Извлекает user_id из различных форматов.
        
        Поддерживаемые форматы:
        - 123456
        - https://mangabuff.ru/users/123456
        - /users/123456
        - mangabuff.ru/users/123456
        
        Args:
            entry: Строка с ID или ссылкой
        
        Returns:
            user_id или None
        """
        entry = entry.strip()
        
        # Чистый ID
        if entry.isdigit():
            return entry
        
        # Ссылка с /users/
        match = re.search(r'/users/(\d+)', entry)
        if match:
            return match.group(1)
        
        return None
    
    def _get_file_mtime(self) -> float:
        """Возвращает время последнего изменения файла."""
        try:
            return os.path.getmtime(self.blacklist_file)
        except OSError:
            return 0
    
    def _load_from_file(self) -> dict:
        """Загружает данные из файла."""
        try:
            with open(self.blacklist_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data
        except FileNotFoundError:
            return {"blacklist": []}
        except json.JSONDecodeError:
            return {"blacklist": []}
        except Exception:
            return {"blacklist": []}
    
    def reload(self) -> bool:
        """
        Перезагружает черный список из файла.
        
        Returns:
            True если данные изменились
        """
        current_mtime = self._get_file_mtime()
        
        # Проверяем изменился ли файл
        if current_mtime <= self.last_modified and self.blacklisted_ids:
            return False
        
        data = self._load_from_file()
        blacklist_entries = data.get("blacklist", [])
        
        new_ids = set()
        
        for entry in blacklist_entries:
            # Поддержка простых строк
            if isinstance(entry, str):
                user_id = self._extract_user_id(entry)
                if user_id:
                    new_ids.add(user_id)
            
            # Поддержка объектов с метаданными
            elif isinstance(entry, dict):
                url = entry.get("url") or entry.get("user_id")
                if url:
                    user_id = self._extract_user_id(str(url))
                    if user_id:
                        new_ids.add(user_id)
        
        # Обновляем с блокировкой
        with self.lock:
            old_count = len(self.blacklisted_ids)
            self.blacklisted_ids = new_ids
            self.blacklist_data = data
            self.last_modified = current_mtime
            new_count = len(new_ids)
        
        if old_count != new_count:
            print(f"✅ Черный список обновлен: {old_count} → {new_count} пользователей")
            return True
        
        return False
    
    def is_blacklisted(self, user_id: str) -> bool:
        """
        Проверяет находится ли пользователь в черном списке.
        
        Args:
            user_id: ID пользователя
        
        Returns:
            True если в черном списке
        """
        with self.lock:
            return str(user_id) in self.blacklisted_ids
    
    def filter_owners(self, owners: List) -> List:
        """
        Фильтрует список владельцев, удаляя тех кто в черном списке.
        
        Args:
            owners: Список объектов Owner с атрибутом .id
        
        Returns:
            Отфильтрованный список
        """
        if not self.blacklisted_ids:
            return owners
        
        original_count = len(owners)
        
        filtered = [
            owner for owner in owners
            if not self.is_blacklisted(owner.id)
        ]
        
        removed_count = original_count - len(filtered)
        
        if removed_count > 0:
            print(f"🚫 Отфильтровано {removed_count} пользователей из черного списка")
        
        return filtered
    
    def add_to_blacklist(
        self,
        user_id: str,
        reason: Optional[str] = None,
        added_by: Optional[str] = None
    ) -> bool:
        """
        Добавляет пользователя в черный список.
        
        Args:
            user_id: ID пользователя или ссылка
            reason: Причина блокировки
            added_by: Кто добавил
        
        Returns:
            True если успешно
        """
        extracted_id = self._extract_user_id(user_id)
        
        if not extracted_id:
            return False
        
        # Проверяем не добавлен ли уже
        if self.is_blacklisted(extracted_id):
            return True
        
        # Загружаем текущие данные
        data = self._load_from_file()
        blacklist = data.get("blacklist", [])
        
        # Создаем запись
        entry = {
            "user_id": extracted_id,
            "url": f"https://mangabuff.ru/users/{extracted_id}",
            "added_at": datetime.now().isoformat(),
            "reason": reason or "Не указана",
            "added_by": added_by or "system"
        }
        
        blacklist.append(entry)
        data["blacklist"] = blacklist
        
        # Сохраняем
        try:
            with open(self.blacklist_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            # Перезагружаем
            self.reload()
            return True
            
        except Exception:
            return False
    
    def remove_from_blacklist(self, user_id: str) -> bool:
        """
        Удаляет пользователя из черного списка.
        
        Args:
            user_id: ID пользователя или ссылка
        
        Returns:
            True если успешно
        """
        extracted_id = self._extract_user_id(user_id)
        
        if not extracted_id:
            return False
        
        if not self.is_blacklisted(extracted_id):
            return True
        
        # Загружаем текущие данные
        data = self._load_from_file()
        blacklist = data.get("blacklist", [])
        
        # Удаляем записи
        blacklist = [
            entry for entry in blacklist
            if self._extract_user_id(
                entry if isinstance(entry, str) else entry.get("user_id", "")
            ) != extracted_id
        ]
        
        data["blacklist"] = blacklist
        
        # Сохраняем
        try:
            with open(self.blacklist_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            # Перезагружаем
            self.reload()
            return True
            
        except Exception:
            return False
    
    def get_blacklist_info(self) -> dict:
        """Возвращает информацию о черном списке."""
        with self.lock:
            return {
                "count": len(self.blacklisted_ids),
                "ids": sorted(self.blacklisted_ids),
                "last_modified": datetime.fromtimestamp(self.last_modified).isoformat() if self.last_modified else None
            }
    
    def _auto_reload_loop(self) -> None:
        """Цикл автоматической перезагрузки."""
        while self.running:
            try:
                self.reload()
            except Exception:
                pass
            
            time.sleep(self.check_interval)
    
    def start_auto_reload(self) -> None:
        """Запускает автоматическую перезагрузку."""
        if self.running:
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._auto_reload_loop, daemon=True)
        self.thread.start()
    
    def stop_auto_reload(self) -> None:
        """Останавливает автоматическую перезагрузку."""
        if not self.running:
            return
        
        self.running = False
        
        if self.thread:
            self.thread.join(timeout=self.check_interval + 1)
    
    def print_stats(self) -> None:
        """Выводит статистику черного списка."""
        info = self.get_blacklist_info()
        
        print(f"\n📋 Черный список:")
        print(f"   Файл: {self.blacklist_file}")
        print(f"   Пользователей: {info['count']}")
        
        if info['last_modified']:
            print(f"   Последнее изменение: {info['last_modified']}")
        
        if info['count'] > 0 and info['count'] <= 10:
            print(f"   IDs: {', '.join(info['ids'])}")
        
        print()


# Глобальный экземпляр
_blacklist_manager: Optional[BlacklistManager] = None


def get_blacklist_manager() -> BlacklistManager:
    """Возвращает глобальный экземпляр менеджера черного списка."""
    global _blacklist_manager
    
    if _blacklist_manager is None:
        _blacklist_manager = BlacklistManager()
    
    return _blacklist_manager


def is_blacklisted(user_id: str) -> bool:
    """Удобная функция для проверки."""
    return get_blacklist_manager().is_blacklisted(user_id)


def filter_owners(owners: List) -> List:
    """Удобная функция для фильтрации."""
    return get_blacklist_manager().filter_owners(owners)
