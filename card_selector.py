"""Селектор карт для обмена с ПРИОРИТЕТОМ непропарсенных карт и немедленным возвратом."""

import random
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

from config import (
    OUTPUT_DIR,
    MAX_CARD_SELECTION_ATTEMPTS,
    CACHE_VALIDITY_HOURS,
    MAX_WANTERS_FOR_TRADE,
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

    Карты с 0-5 желающими приравниваются друг к другу (возвращают 0).
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
        used_cards: Optional[Set[int]] = None,
    ):
        self.session = session
        self.inventory_manager = InventoryManager(output_dir)
        self.locked_cards = locked_cards or set()
        self.used_cards = used_cards or set()
        self.logger = get_logger()
        self.cards_parsed_count = 0
        self.cards_saved_count = 0

    def is_card_available(self, instance_id: int) -> bool:
        if instance_id in self.locked_cards:
            return False
        if instance_id in self.used_cards:
            return False
        return True

    def mark_card_used(self, instance_id: int) -> None:
        self.used_cards.add(instance_id)

    def reset_used_cards(self) -> None:
        self.used_cards.clear()

    def parse_and_cache_card(
        self,
        card: Dict[str, Any],
        parsed_inventory: Dict[str, Dict[str, Any]],
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

        self.logger.debug(f"Парсинг карты: {card_data['name']} (ID: {card_id_str})")
        print(f"      🔍 Парсинг: {card_data['name']}...", end="", flush=True)

        wanters_count = count_wants(self.session, card_id_str, force_accurate=False)

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
            "card_id":       card_data["card_id"],
            "name":          card_data["name"],
            "rank":          card_data["rank"],
            "wanters_count": wanters_count,
            "timestamp":     time.time(),
            "cached_at":     datetime.now().isoformat(),
            "instance_id":   instance_id,
        }

        parsed_inventory[card_id_str] = parsed_card
        self.cards_parsed_count += 1

        if self.cards_parsed_count % 5 == 0:
            self.inventory_manager.save_parsed_inventory(parsed_inventory)
            self.cards_saved_count += 1
            self.logger.debug(f"Сохранено {self.cards_parsed_count} пропарсенных карт")

        return parsed_card

    def filter_cards_by_rank(
        self,
        inventory: List[Dict[str, Any]],
        target_rank: str,
    ) -> List[Dict[str, Any]]:
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
        max_attempts: int = MAX_CARD_SELECTION_ATTEMPTS,
    ) -> Optional[Dict[str, Any]]:
        """
        Приоритет 1: карта с желающих <= target → немедленный возврат.
        Приоритет 2: ближайшая к target если приоритет 1 не найден.
        """
        random.shuffle(available_cards)
        normalized_target = normalize_wanters(target_wanters)

        self.logger.info(
            f"Начало парсинга непропарсенных карт "
            f"(target: {target_wanters}, normalized: {normalized_target})"
        )
        print(f"   🔍 Парсинг карт (приоритет: <= {target_wanters} желающих, карты с 0-5 приравнены)...")

        cards_checked = 0
        best_alternative = None

        while available_cards and cards_checked < max_attempts:
            cards_checked += 1
            random_card = available_cards.pop(0)
            self.inventory_manager.remove_card(random_card)

            parsed_card = self.parse_and_cache_card(random_card, parsed_inventory)
            if not parsed_card:
                continue

            wanters = parsed_card["wanters_count"]
            normalized_wanters = normalize_wanters(wanters)

            if normalized_wanters <= normalized_target:
                self.logger.info(
                    f"✅ ПРИОРИТЕТ 1: {parsed_card['name']} "
                    f"({wanters} желающих) после {cards_checked} проверок"
                )
                print(
                    f"   ⚡ НАЙДЕНО (приоритет 1): {wanters} желающих "
                    f"(норм: {normalized_wanters} <= {normalized_target}) "
                    f"после {cards_checked} проверок!"
                )
                if self.cards_parsed_count > 0:
                    self.inventory_manager.save_parsed_inventory(parsed_inventory)
                return parsed_card

            if wanters > target_wanters:
                if best_alternative is None or wanters < best_alternative["wanters_count"]:
                    best_alternative = parsed_card
                    self.logger.debug(
                        f"Альтернатива обновлена: {parsed_card['name']} ({wanters})"
                    )

        if best_alternative:
            self.logger.info(
                f"✅ ПРИОРИТЕТ 2: {best_alternative['name']} "
                f"({best_alternative['wanters_count']} желающих)"
            )
            print(
                f"   ⚡ НАЙДЕНО (приоритет 2): ближайшая к {target_wanters} "
                f"— {best_alternative['wanters_count']} желающих"
            )

        if available_cards and best_alternative:
            self.logger.info(f"Продолжаем поиск лучшей альтернативы...")
            print(f"   📦 Продолжаем парсинг (проверено {cards_checked})...")

            while available_cards:
                random_card = available_cards.pop(0)
                self.inventory_manager.remove_card(random_card)

                parsed_card = self.parse_and_cache_card(random_card, parsed_inventory)
                if not parsed_card:
                    continue

                wanters = parsed_card["wanters_count"]
                normalized_wanters = normalize_wanters(wanters)

                if normalized_wanters <= normalized_target:
                    self.logger.info(
                        f"✅ Найдена карта с <= target: {parsed_card['name']} ({wanters})"
                    )
                    if self.cards_parsed_count > 0:
                        self.inventory_manager.save_parsed_inventory(parsed_inventory)
                    return parsed_card

                if wanters > target_wanters and wanters < best_alternative["wanters_count"]:
                    best_alternative = parsed_card

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
        exclude_instances: Optional[Set[int]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Приоритет 1: карты с <= target желающих (с нормализацией).
        Приоритет 2: ближайшая к target среди остальных.
        """
        exclude_instances = exclude_instances or set()
        normalized_target = normalize_wanters(target_wanters)

        self.logger.debug(
            f"Поиск в пропарсенном инвентаре: "
            f"target={target_wanters} (norm={normalized_target}), rank={target_rank}"
        )

        suitable_priority1 = []
        suitable_priority2 = []

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

            if normalize_wanters(wanters) <= normalized_target:
                suitable_priority1.append(card_data)
            else:
                suitable_priority2.append(card_data)

        if suitable_priority1:
            selected = random.choice(suitable_priority1)
            self.logger.info(
                f"✅ ПРИОРИТЕТ 1: {selected['name']} ({selected['wanters_count']} желающих)"
            )
            return selected

        if suitable_priority2:
            suitable_priority2.sort(key=lambda x: x["wanters_count"])
            selected = suitable_priority2[0]
            self.logger.info(
                f"✅ ПРИОРИТЕТ 2: {selected['name']} "
                f"({selected['wanters_count']} — ближайшая к {target_wanters})"
            )
            return selected

        self.logger.debug("Подходящих карт в пропарсенном инвентаре не найдено")
        return None

    def select_best_card(
        self,
        target_rank: str,
        target_wanters: int,
        exclude_instances: Optional[Set[int]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Выбирает лучшую карту:
        1. Сначала непропарсенные (немедленный возврат при нахождении).
        2. Затем пропарсенные (запасной вариант).
        """
        self.logger.info(
            f"Начало выбора карты: rank={target_rank}, "
            f"target_wanters={target_wanters} (норм: {normalize_wanters(target_wanters)})"
        )

        inventory = self.inventory_manager.load_inventory()
        parsed_inventory = self.inventory_manager.load_parsed_inventory()

        if not inventory and not parsed_inventory:
            self.logger.warning("Инвентарь пуст!")
            print("   ⚠️  Инвентарь пуст!")
            return None

        available_cards = self.filter_cards_by_rank(inventory, target_rank)

        self.logger.info(
            f"Доступно непропарсенных карт ранга {target_rank}: {len(available_cards)}"
        )
        print(f"   📦 Доступно непропарсенных карт ранга {target_rank}: {len(available_cards)}")
        print(f"   🎯 Цель: <= {target_wanters} желающих (карты 0-5 приравнены)")

        if available_cards:
            print("   🔍 Парсинг непропарсенных карт...")
            selected_card = self.select_from_unparsed(available_cards, target_wanters, parsed_inventory)

            if selected_card:
                wanters = selected_card["wanters_count"]
                norm_w = normalize_wanters(wanters)
                norm_t = normalize_wanters(target_wanters)
                if norm_w <= norm_t:
                    print(f"   ✅ Приоритет 1: {selected_card['name']} ({wanters} желающих)")
                else:
                    print(
                        f"   ✅ Приоритет 2: {selected_card['name']} "
                        f"({wanters} — ближайшая к {target_wanters})"
                    )
                return selected_card
            else:
                print("   ⚠️  В непропарсенных картах не найдено подходящих")
        else:
            print("   ℹ️  Нет непропарсенных карт")

        if parsed_inventory:
            print(f"   🔄 Проверка пропарсенного инвентаря ({len(parsed_inventory)} карт)...")
            selected_card = self.select_from_parsed(
                parsed_inventory, target_rank, target_wanters, exclude_instances
            )

            if selected_card:
                wanters = selected_card["wanters_count"]
                norm_w = normalize_wanters(wanters)
                norm_t = normalize_wanters(target_wanters)
                if norm_w <= norm_t:
                    print(f"   ✅ Приоритет 1 (из кэша): {selected_card['name']} ({wanters} желающих)")
                else:
                    print(
                        f"   ✅ Приоритет 2 (из кэша): {selected_card['name']} "
                        f"({wanters} — ближайшая к {target_wanters})"
                    )
                return selected_card
            else:
                print("   ⚠️  В пропарсенном инвентаре нет подходящих карт")

        self.logger.error(f"Не найдено подходящих карт ранга {target_rank}")
        print(f"   ❌ Не найдено подходящих карт ранга {target_rank}")
        return None


# ---------------------------------------------------------------------------
# Публичные функции
# ---------------------------------------------------------------------------

def select_trade_card(
    session,
    boost_card: Dict[str, Any],
    output_dir: str = OUTPUT_DIR,
    trade_manager=None,
    exclude_instances: Optional[Set[int]] = None,
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


def parse_all_unparsed_cards(
    session,
    output_dir: str = OUTPUT_DIR,
    save_interval: int = 10,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, int]:
    """
    Парсит все непропарсенные карты инвентаря.

    Вызывается перед уходом в сон (когда лимиты вкладов исчерпаны),
    чтобы следующий день начинался с готовым кэшем желающих.

    «Непропарсенная» карта — присутствует в inventory.json,
    но её card_id отсутствует в parsed_inventory.json.

    Карты с wanters_count > MAX_WANTERS_FOR_TRADE тоже сохраняются в кэш
    (чтобы не проверять их снова), но считаются «пропущенными».

    Args:
        session:       Авторизованная сессия requests
        output_dir:    Директория с файлами инвентаря
        save_interval: Сохранять parsed_inventory каждые N карт
        on_progress:   Опциональный колбэк (current_idx, total_count)

    Returns:
        dict: {parsed, skipped, errors, total}
    """
    logger = get_logger()
    inventory_manager = InventoryManager(output_dir)

    inventory        = inventory_manager.load_inventory()
    parsed_inventory = inventory_manager.load_parsed_inventory()

    if not inventory:
        logger.info("parse_all_unparsed_cards: инвентарь пуст")
        print("   ℹ️  Инвентарь пуст — нечего парсить")
        return {"parsed": 0, "skipped": 0, "errors": 0, "total": 0}

    # Собираем карты, которых нет в кэше
    unparsed: List[Dict[str, Any]] = []
    parsed_card_ids = set(parsed_inventory.keys())

    for card in inventory:
        card_data = extract_card_data(card)
        if card_data is None:
            continue
        if str(card_data["card_id"]) not in parsed_card_ids:
            unparsed.append(card)

    total = len(unparsed)
    if total == 0:
        logger.info("parse_all_unparsed_cards: все карты уже пропарсены")
        print("   ✅ Все карты инвентаря уже пропарсены")
        return {"parsed": 0, "skipped": 0, "errors": 0, "total": 0}

    logger.info(f"parse_all_unparsed_cards: найдено {total} непропарсенных карт")
    print(f"\n📦 Найдено непропарсенных карт: {total}")
    print(f"   Сохранение каждые {save_interval} карт\n")

    parsed_count  = 0
    skipped_count = 0
    error_count   = 0

    try:
        for idx, card in enumerate(unparsed, 1):
            card_data = extract_card_data(card)
            if not card_data:
                error_count += 1
                continue

            card_id_str = str(card_data["card_id"])
            name        = card_data["name"] or f"card_{card_id_str}"

            print(
                f"   [{idx}/{total}] {name} (ранг {card_data['rank']})...",
                end="",
                flush=True,
            )

            wanters_count = count_wants(session, card_id_str, force_accurate=False)

            if wanters_count < 0:
                print(" ❌ ошибка запроса")
                logger.warning(f"Не удалось получить желающих для {card_id_str}")
                error_count += 1
                continue

            # Сохраняем в кэш в любом случае — даже если превышен лимит желающих,
            # чтобы при следующем запуске не парсить эту карту снова
            parsed_inventory[card_id_str] = {
                "card_id":       card_data["card_id"],
                "name":          name,
                "rank":          card_data["rank"],
                "wanters_count": wanters_count,
                "timestamp":     time.time(),
                "cached_at":     datetime.now().isoformat(),
                "instance_id":   card_data["instance_id"],
            }

            if wanters_count > MAX_WANTERS_ALLOWED:
                print(f" ⏭️  пропуск ({wanters_count} > {MAX_WANTERS_ALLOWED} желающих)")
                skipped_count += 1
            else:
                print(f" ✅ {wanters_count} желающих")
                parsed_count += 1

            if on_progress:
                on_progress(idx, total)

            # Периодическое сохранение
            if idx % save_interval == 0:
                inventory_manager.save_parsed_inventory(parsed_inventory)
                logger.debug(f"Промежуточное сохранение: {idx}/{total}")
                print(f"   💾 Сохранено ({idx}/{total})")

    except KeyboardInterrupt:
        print("\n\n⚠️  Парсинг прерван пользователем")
        logger.warning("parse_all_unparsed_cards: прерван пользователем (Ctrl+C)")

    # Финальное сохранение
    inventory_manager.save_parsed_inventory(parsed_inventory)
    logger.info(
        f"parse_all_unparsed_cards завершён: "
        f"пропарсено={parsed_count}, пропущено={skipped_count}, "
        f"ошибок={error_count}, всего={total}"
    )

    done = parsed_count + skipped_count + error_count
    print(f"\n   ✅ Парсинг завершён: {done}/{total} обработано")
    print(
        f"      ├─ добавлено в кэш:  {parsed_count}\n"
        f"      ├─ пропущено (>70♥): {skipped_count}\n"
        f"      └─ ошибок:           {error_count}"
    )

    return {
        "parsed":  parsed_count,
        "skipped": skipped_count,
        "errors":  error_count,
        "total":   total,
    }