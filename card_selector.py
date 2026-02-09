"""Селектор карт для обмена с ПРИОРИТЕТОМ непропарсенных карт и немедленным возвратом."""

import random
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from config import (
    OUTPUT_DIR,
    MAX_CARD_SELECTION_ATTEMPTS,
    CACHE_VALIDITY_HOURS,
    MAX_WANTERS_FOR_TRADE
)
from inventory import InventoryManager
from parsers import count_wants
from utils import extract_card_data, is_cache_valid
from logger import get_logger

MAX_WANTERS_ALLOWED = MAX_WANTERS_FOR_TRADE
LOW_WANTERS_THRESHOLD = 5

def normalize_wanters(wanters_count: int) -> int:
    """
    Нормализует количество желающих для карт с малым спросом.
    
    🔧 ИЗМЕНЕНО: Карты с 0-5 желающими приравниваются друг к другу (возвращают 0).
    Это означает что если во вкладе карта с 1 желающим, то карты с 0-5 желающими 
    в инвентаре будут подпадать под первый приоритет выбора.
    """
    if wanters_count <= LOW_WANTERS_THRESHOLD:
        return 0
    return wanters_count

class CardSelector:
    """Селектор для подбора оптимальных карт для обмена."""
    
    def __init__(
        self,
        session,
        output_dir: str = OUTPUT_DIR,
        locked_cards: Optional[Set[int]] = None,
        used_cards: Optional[Set[int]] = None
    ):
        self.session = session
        self.inventory_manager = InventoryManager(output_dir)
        self.locked_cards = locked_cards or set()
        self.used_cards = used_cards or set()
        self.logger = get_logger()
        self.cards_parsed_count = 0
        self.cards_saved_count = 0
    
    def is_card_available(self, instance_id: int) -> bool:
        """Проверяет, доступна ли карта."""
        if instance_id in self.locked_cards:
            return False
        if instance_id in self.used_cards:
            return False
        return True
    
    def mark_card_used(self, instance_id: int) -> None:
        """Помечает карту как использованную."""
        self.used_cards.add(instance_id)
    
    def reset_used_cards(self) -> None:
        """Сбрасывает список использованных карт."""
        self.used_cards.clear()
    
    def parse_and_cache_card(
        self,
        card: Dict[str, Any],
        parsed_inventory: Dict[str, Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Парсит карту и сохраняет в кэш."""
        card_data = extract_card_data(card)
        
        if not card_data:
            return None
        
        instance_id = card_data["instance_id"]
        if not self.is_card_available(instance_id):
            return None
        
        card_id_str = str(card_data["card_id"])
        
        # Проверяем кэш
        if card_id_str in parsed_inventory:
            cached = parsed_inventory[card_id_str]
            if is_cache_valid(cached.get("cached_at", ""), CACHE_VALIDITY_HOURS):
                cached["instance_id"] = instance_id
                self.logger.debug(f"Карта {card_data['name']} взята из кэша")
                return cached
        
        # Парсим
        self.logger.debug(f"Парсинг карты: {card_data['name']} (ID: {card_id_str})")
        print(f"      🔍 Парсинг: {card_data['name']}...", end="", flush=True)
        
        wanters_count = count_wants(
            self.session,
            card_id_str,
            force_accurate=False
        )
        
        if wanters_count < 0:
            print(" ❌ ошибка")
            self.logger.warning(f"Не удалось получить желающих для карты {card_id_str}")
            return None
        
        if wanters_count > MAX_WANTERS_ALLOWED:
            print(f" ⏭️ пропуск ({wanters_count} > {MAX_WANTERS_ALLOWED})")
            self.logger.debug(f"Карта {card_data['name']} пропущена: {wanters_count} желающих")
            return None
        
        print(f" ✅ {wanters_count} желающих")
        
        parsed_card = {
            "card_id": card_data["card_id"],
            "name": card_data["name"],
            "rank": card_data["rank"],
            "wanters_count": wanters_count,
            "timestamp": time.time(),
            "cached_at": datetime.now().isoformat(),
            "instance_id": instance_id
        }
        
        # Сохраняем в память
        parsed_inventory[card_id_str] = parsed_card
        self.cards_parsed_count += 1
        
        # Сохраняем на диск каждые 5 карт
        if self.cards_parsed_count % 5 == 0:
            self.inventory_manager.save_parsed_inventory(parsed_inventory)
            self.cards_saved_count += 1
            self.logger.debug(f"Сохранено {self.cards_parsed_count} пропарсенных карт")
        
        return parsed_card
    
    def filter_cards_by_rank(
        self,
        inventory: List[Dict[str, Any]],
        target_rank: str
    ) -> List[Dict[str, Any]]:
        """Фильтрует карты по рангу."""
        filtered = []
        
        for card in inventory:
            card_data = extract_card_data(card)
            if card_data and card_data["rank"] == target_rank:
                if self.is_card_available(card_data["instance_id"]):
                    filtered.append(card)
        
        return filtered
    
    def select_from_unparsed(
        self,
        available_cards: List[Dict[str, Any]],
        target_wanters: int,
        parsed_inventory: Dict[str, Dict[str, Any]],
        max_attempts: int = MAX_CARD_SELECTION_ATTEMPTS
    ) -> Optional[Dict[str, Any]]:
        """
        🔧 ПРАВИЛЬНАЯ ЛОГИКА с нормализацией 0-5 желающих:
        1. ПРИОРИТЕТ 1: Ищем карту с желающих <= target (0-5 приравниваются) → СРАЗУ возвращаем
        2. ПРИОРИТЕТ 2: Если не нашли после max_attempts - возвращаем ближайшую к target (но <= 70)
        """
        random.shuffle(available_cards)
        
        # 🔧 НОВОЕ: Нормализуем target для корректного сравнения
        normalized_target = normalize_wanters(target_wanters)
        
        self.logger.info(f"Начало парсинга непропарсенных карт (target: {target_wanters} желающих, normalized: {normalized_target})")
        print(f"   🔍 Парсинг карт (приоритет: <= {target_wanters} желающих, карты с 0-5 приравнены)...")
        
        cards_checked = 0
        best_alternative = None  # Лучшая альтернатива если не найдем <= target
        
        while available_cards and cards_checked < max_attempts:
            cards_checked += 1
            random_card = available_cards.pop(0)
            self.inventory_manager.remove_card(random_card)
            
            parsed_card = self.parse_and_cache_card(random_card, parsed_inventory)
            
            if not parsed_card:
                continue
            
            wanters = parsed_card["wanters_count"]
            # 🔧 НОВОЕ: Нормализуем для сравнения
            normalized_wanters = normalize_wanters(wanters)
            
            # ПРИОРИТЕТ 1: Нашли карту с <= target (с учетом нормализации) → НЕМЕДЛЕННЫЙ ВОЗВРАТ!
            if normalized_wanters <= normalized_target:
                self.logger.info(
                    f"✅ ПРИОРИТЕТ 1! Найдена карта: {parsed_card['name']} "
                    f"({wanters} желающих, normalized={normalized_wanters} <= {normalized_target}) после {cards_checked} проверок"
                )
                print(f"   ⚡ НАЙДЕНО (приоритет 1): {wanters} желающих (норм: {normalized_wanters} <= {normalized_target}) после {cards_checked} проверок!")
                
                # Финальное сохранение
                if self.cards_parsed_count > 0:
                    self.inventory_manager.save_parsed_inventory(parsed_inventory)
                
                return parsed_card
            
            # ПРИОРИТЕТ 2: Сохраняем как альтернативу
            # Ищем МИНИМАЛЬНУЮ среди >target (ближайшую к target)
            if wanters > target_wanters:
                if best_alternative is None:
                    best_alternative = parsed_card
                elif wanters < best_alternative["wanters_count"]:
                    # МЕНЬШЕ = ближе к target!
                    best_alternative = parsed_card
                    self.logger.debug(f"Альтернатива обновлена: {parsed_card['name']} ({wanters} ближе к {target_wanters})")
        
        # Если не нашли карту с <= target после max_attempts
        if best_alternative:
            self.logger.info(
                f"✅ ПРИОРИТЕТ 2! Возвращаем ближайшую: {best_alternative['name']} "
                f"({best_alternative['wanters_count']} желающих, ближе всего к {target_wanters})"
            )
            print(f"   ⚡ НАЙДЕНО (приоритет 2): ближайшая к {target_wanters} - {best_alternative['wanters_count']} желающих")
        
        # Продолжаем парсить оставшиеся карты если есть
        if available_cards and best_alternative:
            self.logger.info(f"Проверено {cards_checked} карт, продолжаем поиск лучшей альтернативы...")
            print(f"   📦 Продолжаем парсинг (проверено {cards_checked})...")
            
            while available_cards:
                random_card = available_cards.pop(0)
                self.inventory_manager.remove_card(random_card)
                
                parsed_card = self.parse_and_cache_card(random_card, parsed_inventory)
                
                if not parsed_card:
                    continue
                
                wanters = parsed_card["wanters_count"]
                normalized_wanters = normalize_wanters(wanters)
                
                # Нашли карту с <= target (с нормализацией)!
                if normalized_wanters <= normalized_target:
                    self.logger.info(f"✅ Найдена карта с <= target: {parsed_card['name']} ({wanters}, норм: {normalized_wanters})")
                    
                    if self.cards_parsed_count > 0:
                        self.inventory_manager.save_parsed_inventory(parsed_inventory)
                    
                    return parsed_card
                
                # Обновляем альтернативу если эта БЛИЖЕ (меньше)
                if wanters > target_wanters and wanters < best_alternative["wanters_count"]:
                    best_alternative = parsed_card
        
        # Финальное сохранение
        if self.cards_parsed_count > 0:
            self.inventory_manager.save_parsed_inventory(parsed_inventory)
            self.logger.info(f"Финальное сохранение: {self.cards_parsed_count} карт")
            print(f"   💾 Сохранено {self.cards_parsed_count} пропарсенных карт")
        
        return best_alternative
    
    def select_from_parsed(
        self,
        parsed_inventory: Dict[str, Dict[str, Any]],
        target_rank: str,
        target_wanters: int,
        exclude_instances: Optional[Set[int]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        🔧 ПРАВИЛЬНАЯ ЛОГИКА для пропарсенного инвентаря с нормализацией:
        1. ПРИОРИТЕТ 1: Карты с желающих <= target (0-5 приравниваются)
        2. ПРИОРИТЕТ 2: Если таких нет - ближайшая к target (но <= 70)
        """
        exclude_instances = exclude_instances or set()
        
        # 🔧 НОВОЕ: Нормализуем target
        normalized_target = normalize_wanters(target_wanters)
        
        self.logger.debug(f"Поиск в пропарсенном инвентаре: target={target_wanters} (norm={normalized_target}), rank={target_rank}")
        
        suitable_priority1 = []  # Карты с <= target (ПРИОРИТЕТ 1)
        suitable_priority2 = []  # Карты с > target (ПРИОРИТЕТ 2 - ближе к target = лучше)
        
        for card_data in parsed_inventory.values():
            if card_data["rank"] != target_rank:
                continue
            
            instance_id = card_data.get("instance_id", 0)
            
            if instance_id in exclude_instances:
                continue
            
            if not self.is_card_available(instance_id):
                continue
            
            wanters = card_data["wanters_count"]
            if wanters > MAX_WANTERS_ALLOWED:
                continue
            
            # 🔧 НОВОЕ: Используем нормализацию для сравнения
            normalized_wanters = normalize_wanters(wanters)
            
            # ПРИОРИТЕТ 1: <= target (с нормализацией)
            if normalized_wanters <= normalized_target:
                suitable_priority1.append(card_data)
            # ПРИОРИТЕТ 2: > target (но <= 70)
            else:
                suitable_priority2.append(card_data)
        
        # ПРИОРИТЕТ 1 - карты с <= target желающих (с нормализацией)
        if suitable_priority1:
            # Выбираем случайную из подходящих
            selected = random.choice(suitable_priority1)
            self.logger.info(
                f"✅ ПРИОРИТЕТ 1: {selected['name']} "
                f"({selected['wanters_count']} желающих, norm={normalize_wanters(selected['wanters_count'])} <= {normalized_target})"
            )
            return selected
        
        # ПРИОРИТЕТ 2 - ближайшая к target (минимальная среди > target)
        if suitable_priority2:
            # Сортируем по ВОЗРАСТАНИЮ - берем минимальную = ближайшую к target
            suitable_priority2.sort(key=lambda x: x["wanters_count"])
            selected = suitable_priority2[0]
            self.logger.info(
                f"✅ ПРИОРИТЕТ 2: {selected['name']} "
                f"({selected['wanters_count']} - ближайшая к {target_wanters})"
            )
            return selected
        
        self.logger.debug("Подходящих карт в пропарсенном инвентаре не найдено")
        return None
    
    def select_best_card(
        self,
        target_rank: str,
        target_wanters: int,
        exclude_instances: Optional[Set[int]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        🔧 ИЗМЕНЕНО: Выбирает лучшую карту с НОВЫМ приоритетом:
        1. СНАЧАЛА непропарсенные карты (с немедленным возвратом при нахождении)
        2. ЗАТЕМ пропарсенные карты (если не нашли в непропарсенных)
        
        Теперь карты с 0-5 желающими приравниваются друг к другу.
        """
        self.logger.info(f"Начало выбора карты: rank={target_rank}, target_wanters={target_wanters} (норм: {normalize_wanters(target_wanters)})")
        
        inventory = self.inventory_manager.load_inventory()
        parsed_inventory = self.inventory_manager.load_parsed_inventory()
        
        if not inventory and not parsed_inventory:
            self.logger.warning("Инвентарь пуст!")
            print("   ⚠️  Инвентарь пуст!")
            return None
        
        available_cards = self.filter_cards_by_rank(inventory, target_rank)
        
        self.logger.info(f"Доступно непропарсенных карт ранга {target_rank}: {len(available_cards)}")
        print(f"   📦 Доступно непропарсенных карт ранга {target_rank}: {len(available_cards)}")
        print(f"   🎯 Цель: <= {target_wanters} желающих (карты 0-5 приравнены)")
        
        # 🔧 ИЗМЕНЕНО: ПРИОРИТЕТ 1 - СНАЧАЛА парсим непропарсенные
        if available_cards:
            self.logger.info("Парсинг непропарсенных карт...")
            print(f"   🔍 Парсинг непропарсенных карт...")
            
            selected_card = self.select_from_unparsed(
                available_cards,
                target_wanters,
                parsed_inventory
            )
            
            if selected_card:
                wanters = selected_card['wanters_count']
                norm_wanters = normalize_wanters(wanters)
                norm_target = normalize_wanters(target_wanters)
                
                if norm_wanters <= norm_target:
                    self.logger.info(f"✅ Приоритет 1: {selected_card['name']} ({wanters} желающих, норм: {norm_wanters} <= {norm_target})")
                    print(f"   ✅ Приоритет 1: {selected_card['name']} ({wanters} желающих)")
                else:
                    self.logger.info(f"✅ Приоритет 2: {selected_card['name']} ({wanters} - ближайшая к {target_wanters})")
                    print(f"   ✅ Приоритет 2: {selected_card['name']} ({wanters} - ближайшая к {target_wanters})")
                return selected_card
            else:
                self.logger.info("В непропарсенных картах не найдено подходящих")
                print(f"   ⚠️  В непропарсенных картах не найдено подходящих")
        else:
            self.logger.info("Нет непропарсенных карт")
            print(f"   ℹ️  Нет непропарсенных карт")
        
        # 🔧 ИЗМЕНЕНО: ПРИОРИТЕТ 2 - ЗАТЕМ проверяем пропарсенный инвентарь (запасной вариант)
        if parsed_inventory:
            self.logger.info(f"Проверка пропарсенного инвентаря ({len(parsed_inventory)} карт)...")
            print(f"   🔄 Проверка пропарсенного инвентаря ({len(parsed_inventory)} карт)...")
            
            selected_card = self.select_from_parsed(
                parsed_inventory,
                target_rank,
                target_wanters,
                exclude_instances
            )
            
            if selected_card:
                wanters = selected_card['wanters_count']
                norm_wanters = normalize_wanters(wanters)
                norm_target = normalize_wanters(target_wanters)
                
                if norm_wanters <= norm_target:
                    self.logger.info(f"✅ Приоритет 1: {selected_card['name']} ({wanters} желающих, норм: {norm_wanters} <= {norm_target})")
                    print(f"   ✅ Приоритет 1 (из кэша): {selected_card['name']} ({wanters} желающих)")
                else:
                    self.logger.info(f"✅ Приоритет 2: {selected_card['name']} ({wanters} - ближайшая к {target_wanters})")
                    print(f"   ✅ Приоритет 2 (из кэша): {selected_card['name']} ({wanters} - ближайшая к {target_wanters})")
                return selected_card
            else:
                self.logger.info("В пропарсенном инвентаре нет подходящих карт")
                print(f"   ⚠️  В пропарсенном инвентаре нет подходящих карт")
        
        self.logger.error(f"Не найдено подходящих карт ранга {target_rank}")
        print(f"   ❌ Не найдено подходящих карт ранга {target_rank}")
        return None

def select_trade_card(
    session,
    boost_card: Dict[str, Any],
    output_dir: str = OUTPUT_DIR,
    trade_manager=None,
    exclude_instances: Optional[Set[int]] = None
) -> Optional[Dict[str, Any]]:
    """Главная функция для выбора карты с исключением."""
    target_rank = boost_card.get("rank", "")
    target_wanters = boost_card.get("wanters_count", 0)
    
    if not target_rank:
        return None
    
    locked_cards = set()
    if trade_manager:
        locked_cards = trade_manager.locked_cards
    
    selector = CardSelector(session, output_dir, locked_cards)
    return selector.select_best_card(target_rank, target_wanters, exclude_instances)