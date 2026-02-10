"""Модуль автоматической замены карт в клубе с новыми условиями замены."""

import time
from typing import Optional
import requests
from boost import get_boost_card_info, replace_club_card
from trade import cancel_all_sent_trades
from daily_stats import DailyStatsManager
from utils import print_section, print_success, print_warning, print_info
from config import OUTPUT_DIR


class CardReplacementManager:
    """Менеджер автоматической замены карт с новыми условиями."""
    
    def __init__(
        self,
        session: requests.Session,
        boost_url: str,
        stats_manager: DailyStatsManager
    ):
        """
        Инициализация менеджера.
        
        Args:
            session: Сессия requests
            boost_url: URL страницы буста
            stats_manager: Менеджер статистики
        """
        self.session = session
        self.boost_url = boost_url
        self.stats_manager = stats_manager
    

    def should_replace_card(self, boost_card: dict) -> bool:
        """
        Проверяет, нужно ли заменить карту по новым условиям.
        """
        owners_count = boost_card.get('owners_count', 0)
        wanters_count = boost_card.get('wanters_count', 0)
        
        # 🔧 ДОБАВЛЯЕМ ДЕТАЛЬНОЕ ЛОГИРОВАНИЕ
        print_section("🔍 ПРОВЕРКА УСЛОВИЙ АВТОЗАМЕНЫ", char="-")
        print(f"   Владельцев: {owners_count} (тип: {type(owners_count).__name__})")
        print(f"   Желающих: {wanters_count} (тип: {type(wanters_count).__name__})")
        print(f"   Card ID: {boost_card.get('card_id')}")
        print(f"   Название: {boost_card.get('name', 'Неизвестно')}")
        print()
        
        if owners_count <= 0:
            print_info("❌ Нет данных о владельцах карты (owners_count <= 0)")
            print("-" * 60 + "\n")
            return False
        
        # Условие 1: 0-108 владельцев = ВСЕГДА автозамена
        condition1 = 0 < owners_count <= 108
        print(f"   Условие 1 (0 < {owners_count} <= 108): {condition1}")
        if condition1:
            print_warning(f"✅ ЗАМЕНА! Владельцев {owners_count} <= 108")
            print("-" * 60 + "\n")
            return True
        
        # Условие 2: 109-216 владельцев при 121+ желающих
        condition2_range = 109 <= owners_count <= 216
        condition2_wanters = wanters_count >= 121
        print(f"   Условие 2 (109 <= {owners_count} <= 216): {condition2_range}")
        if condition2_range:
            print(f"      └─ Желающих {wanters_count} >= 121: {condition2_wanters}")
            if condition2_wanters:
                print_warning(f"✅ ЗАМЕНА! Владельцев {owners_count}, желающих {wanters_count} >= 121")
                print("-" * 60 + "\n")
                return True
            else:
                print_info(f"❌ НЕТ ЗАМЕНЫ. Желающих {wanters_count} < 121")
                print("-" * 60 + "\n")
                return False
        
        # Условие 3: 217-360 владельцев при 181+ желающих
        condition3_range = 217 <= owners_count <= 360
        condition3_wanters = wanters_count >= 181
        print(f"   Условие 3 (217 <= {owners_count} <= 360): {condition3_range}")
        if condition3_range:
            print(f"      └─ Желающих {wanters_count} >= 181: {condition3_wanters}")
            if condition3_wanters:
                print_warning(f"✅ ЗАМЕНА! Владельцев {owners_count}, желающих {wanters_count} >= 181")
                print("-" * 60 + "\n")
                return True
            else:
                print_info(f"❌ НЕТ ЗАМЕНЫ. Желающих {wanters_count} < 181")
                print("-" * 60 + "\n")
                return False
        
        # Условие 4: 361-540 владельцев при 300+ желающих
        condition4_range = 361 <= owners_count <= 540
        condition4_wanters = wanters_count >= 300
        print(f"   Условие 4 (361 <= {owners_count} <= 540): {condition4_range}")
        if condition4_range:
            print(f"      └─ Желающих {wanters_count} >= 300: {condition4_wanters}")
            if condition4_wanters:
                print_warning(f"✅ ЗАМЕНА! Владельцев {owners_count}, желающих {wanters_count} >= 300")
                print("-" * 60 + "\n")
                return True
            else:
                print_info(f"❌ НЕТ ЗАМЕНЫ. Желающих {wanters_count} < 300")
                print("-" * 60 + "\n")
                return False
        
        # Все остальные случаи - замена не требуется
        print_info(f"❌ НЕТ ЗАМЕНЫ. Владельцев {owners_count} не попадает под условия (>540)")
        print("-" * 60 + "\n")
        return False
    
    def can_replace(self) -> bool:
        """
        Проверяет лимит замен с обновлением с сервера.
        
        Returns:
            True если лимит не достигнут
        """
        if not self.stats_manager.can_replace(force_refresh=True):
            print_warning(f"⛔ Достигнут дневной лимит замен карт!")
            self.stats_manager.print_stats()
            return False
        
        return True
    
    def force_replace_card(self, boost_card: dict, reason: str = "Принудительная замена") -> Optional[dict]:
        """
        Принудительная замена карты БЕЗ проверки условий.
        
        Args:
            boost_card: Текущая карта
            reason: Причина замены (для логов)
        
        Returns:
            Информация о новой карте или None
        """
        if not self.can_replace():
            return None
        
        print_section(f"🔄 {reason.upper()}", char="=")
        
        old_card_id = boost_card.get('card_id')
        old_card_name = boost_card.get('name', 'Неизвестно')
        owners = boost_card.get('owners_count', '?')
        wanters = boost_card.get('wanters_count', '?')
        
        print(f"   Текущая карта: {old_card_name} (ID: {old_card_id})")
        print(f"   Владельцев: {owners} | Желающих: {wanters}")
        
        replacements_left = self.stats_manager.get_replacements_left(force_refresh=True)
        print(f"   Замен осталось сегодня: {replacements_left}\n")
        
        print("1️⃣ Отменяем все отправленные обмены...")
        cancel_all_sent_trades(self.session, debug=False)
        time.sleep(1)
        
        if not self.stats_manager.can_replace(force_refresh=True):
            print_warning("⛔ Лимит замен достигнут перед отправкой!")
            print("=" * 60 + "\n")
            return None
        
        print("2️⃣ Отправляем запрос на замену карты...")
        success = replace_club_card(self.session)
        
        if not success:
            print_warning("❌ Не удалось заменить карту")
            print("=" * 60 + "\n")
            return None
        
        print_success("✅ Запрос на замену отправлен!")
        
        print("3️⃣ Ожидание обновления данных (3 сек)...")
        time.sleep(3)
        
        print("4️⃣ Обновляем статистику с сервера...")
        self.stats_manager.refresh_stats()
        
        print("5️⃣ Загружаем информацию о новой карте...")
        new_boost_card = get_boost_card_info(self.session, self.boost_url)
        
        if not new_boost_card:
            print_warning("❌ Не удалось получить информацию о новой карте")
            print("=" * 60 + "\n")
            return None
        
        new_card_id = new_boost_card.get('card_id')
        new_card_name = new_boost_card.get('name', 'Неизвестно')
        new_owners = new_boost_card.get('owners_count', '?')
        new_wanters = new_boost_card.get('wanters_count', '?')
        
        if new_card_id != old_card_id:
            print_success(f"✅ Карта успешно заменена!")
            print(f"\n   Старая: {old_card_name} (ID: {old_card_id}, владельцев: {owners}, желающих: {wanters})")
            print(f"   Новая: {new_card_name} (ID: {new_card_id}, владельцев: {new_owners}, желающих: {new_wanters})\n")
            
            self.stats_manager.print_stats(force_refresh=True)
            print("=" * 60 + "\n")
            
            return new_boost_card
        else:
            print_warning(f"⚠️  Карта не изменилась (ID: {old_card_id})")
            print("   Возможно, замена не сработала или вернулась та же карта\n")
            
            self.stats_manager.print_stats(force_refresh=True)
            print("=" * 60 + "\n")
            return None
    
    def perform_replacement(self, boost_card: dict) -> Optional[dict]:
        """
        Выполняет замену карты С ПРОВЕРКОЙ новых условий.
        
        Args:
            boost_card: Текущая карта
        
        Returns:
            Информация о новой карте или None
        """
        if not self.should_replace_card(boost_card):
            return None
        
        if not self.can_replace():
            return None
        
        print_section("🔄 АВТОМАТИЧЕСКАЯ ЗАМЕНА КАРТЫ", char="=")
        
        old_card_id = boost_card.get('card_id')
        old_card_name = boost_card.get('name', 'Неизвестно')
        owners = boost_card.get('owners_count', '?')
        wanters = boost_card.get('wanters_count', '?')
        
        print(f"   Текущая карта: {old_card_name} (ID: {old_card_id})")
        print(f"   Владельцев: {owners} | Желающих: {wanters}")
        
        replacements_left = self.stats_manager.get_replacements_left(force_refresh=True)
        print(f"   Замен осталось сегодня: {replacements_left}\n")
        
        print("1️⃣ Отменяем все отправленные обмены...")
        cancel_all_sent_trades(self.session, debug=False)
        time.sleep(1)
        
        if not self.stats_manager.can_replace(force_refresh=True):
            print_warning("⛔ Лимит замен достигнут перед отправкой!")
            print("=" * 60 + "\n")
            return None
        
        print("2️⃣ Отправляем запрос на замену карты...")
        success = replace_club_card(self.session)
        
        if not success:
            print_warning("❌ Не удалось заменить карту")
            print("=" * 60 + "\n")
            return None
        
        print_success("✅ Запрос на замену отправлен!")
        
        print("3️⃣ Ожидание обновления данных (3 сек)...")
        time.sleep(3)
        
        print("4️⃣ Обновляем статистику с сервера...")
        self.stats_manager.refresh_stats()
        
        print("5️⃣ Загружаем информацию о новой карте...")
        new_boost_card = get_boost_card_info(self.session, self.boost_url)
        
        if not new_boost_card:
            print_warning("❌ Не удалось получить информацию о новой карте")
            print("=" * 60 + "\n")
            return None
        
        new_card_id = new_boost_card.get('card_id')
        new_card_name = new_boost_card.get('name', 'Неизвестно')
        new_owners = new_boost_card.get('owners_count', '?')
        new_wanters = new_boost_card.get('wanters_count', '?')
        
        if new_card_id != old_card_id:
            print_success(f"✅ Карта успешно заменена!")
            print(f"\n   Старая: {old_card_name} (ID: {old_card_id}, владельцев: {owners}, желающих: {wanters})")
            print(f"   Новая: {new_card_name} (ID: {new_card_id}, владельцев: {new_owners}, желающих: {new_wanters})\n")
            
            self.stats_manager.print_stats(force_refresh=True)
            print("=" * 60 + "\n")
            
            return new_boost_card
        else:
            print_warning(f"⚠️  Карта не изменилась (ID: {old_card_id})")
            print("   Возможно, замена не сработала или вернулась та же карта\n")
            
            self.stats_manager.print_stats(force_refresh=True)
            print("=" * 60 + "\n")
            return None


def check_and_replace_if_needed(
    session: requests.Session,
    boost_url: str,
    boost_card: dict,
    stats_manager: DailyStatsManager
) -> Optional[dict]:
    """
    Проверяет карту и заменяет её если нужно и возможно.
    """
    manager = CardReplacementManager(session, boost_url, stats_manager)
    return manager.perform_replacement(boost_card)


def force_replace_card(
    session: requests.Session,
    boost_url: str,
    boost_card: dict,
    stats_manager: DailyStatsManager,
    reason: str = "Принудительная замена"
) -> Optional[dict]:
    """
    Принудительная замена карты БЕЗ проверки условий.
    """
    manager = CardReplacementManager(session, boost_url, stats_manager)
    return manager.force_replace_card(boost_card, reason)