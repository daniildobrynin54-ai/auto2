"""Монитор истории обменов с отслеживанием статусов."""

import re
import time
import threading
from typing import Any, Dict, List, Optional, Set

import requests
from bs4 import BeautifulSoup

from config import BASE_URL, REQUEST_TIMEOUT


class TradeHistoryMonitor:
    """Монитор истории обменов с отслеживанием статусов обменов."""

    def __init__(
        self,
        session,
        user_id: int,
        inventory_manager,
        debug: bool = False,
    ):
        self.session = session
        self.user_id = user_id
        self.inventory_manager = inventory_manager
        self.debug = debug
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.trade_statuses: Dict[int, str] = {}
        self.traded_away_cards: Set[int] = set()

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log(self, message: str) -> None:
        if self.debug:
            print(f"[HISTORY] {message}")

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_trade_status(self, trade_elem) -> str:
        """
        Определяет статус обмена.

        Returns:
            'completed' | 'cancelled' | 'pending'
        """
        if trade_elem.select_one(".history__item--completed"):
            return "completed"
        if trade_elem.select_one(".history__item--cancelled"):
            return "cancelled"

        status_elem = trade_elem.select_one(".history__status")
        if status_elem:
            status_text = status_elem.get_text().lower()
            if "отменен" in status_text or "отклонен" in status_text:
                return "cancelled"
            if "завершен" in status_text or "принят" in status_text:
                return "completed"

        return "pending"

    def fetch_recent_trades(self) -> List[Dict[str, Any]]:
        """Загружает последние обмены с их статусами."""
        url = f"{BASE_URL}/users/{self.user_id}/trades"
        try:
            response = self.session.get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code != 200:
                self._log(f"Ошибка загрузки истории: {response.status_code}")
                return []

            soup = BeautifulSoup(response.text, "html.parser")
            trades = []

            for trade_elem in soup.select(".history__item"):
                trade_id_elem = trade_elem.get("data-id")
                if not trade_id_elem:
                    continue

                trade_id = int(trade_id_elem)
                status = self._parse_trade_status(trade_elem)

                def _extract_card_ids(selector: str) -> List[int]:
                    ids = []
                    for elem in trade_elem.select(selector):
                        href = elem.get("href", "")
                        m = re.search(r"/cards/(\d+)", href)
                        if m:
                            ids.append(int(m.group(1)))
                    return ids

                lost_cards = _extract_card_ids(".history__body--lost .history__body-item")
                gained_cards = _extract_card_ids(".history__body--gained .history__body-item")

                if lost_cards:
                    trades.append(
                        {
                            "trade_id": trade_id,
                            "status": status,
                            "lost_cards": lost_cards,
                            "gained_cards": gained_cards,
                        }
                    )

            return trades

        except Exception as e:
            self._log(f"Ошибка парсинга истории: {e}")
            return []

    # ------------------------------------------------------------------
    # Business logic
    # ------------------------------------------------------------------

    def check_and_remove_traded_cards(self) -> int:
        """Проверяет историю с учётом статусов обменов."""
        trades = self.fetch_recent_trades()
        if not trades:
            self._log("Нет записей в истории")
            return 0

        removed_count = 0
        restored_count = 0
        self._log(f"Проверка истории: найдено {len(trades)} записей")

        for trade in trades:
            trade_id = trade["trade_id"]
            current_status = trade["status"]
            previous_status = self.trade_statuses.get(trade_id)

            if previous_status is None and current_status == "completed":
                self._log(f"Новый завершённый обмен: ID {trade_id}")
                for card_id in trade["lost_cards"]:
                    if card_id not in self.traded_away_cards:
                        self._log(f"  Отдана карта: {card_id}")
                        if self._remove_card_from_inventory(card_id):
                            removed_count += 1
                            self.traded_away_cards.add(card_id)
                            print(f"🗑️  Карта {card_id} удалена из инвентаря")
                        else:
                            self._log(f"  Не удалось удалить карту {card_id}")
                self.trade_statuses[trade_id] = "completed"

            elif previous_status == "completed" and current_status == "cancelled":
                self._log(f"⚠️  Обмен {trade_id} отменён! Возвращаем карты")
                for card_id in trade["lost_cards"]:
                    if card_id in self.traded_away_cards:
                        self._log(f"  Карта {card_id} возвращена в инвентарь")
                        self.traded_away_cards.discard(card_id)
                        restored_count += 1
                        print(f"♻️  Карта {card_id} возвращена (обмен отменён)")
                self.trade_statuses[trade_id] = "cancelled"

            elif previous_status != current_status:
                self._log(f"Обмен {trade_id}: {previous_status} -> {current_status}")
                self.trade_statuses[trade_id] = current_status
            else:
                if previous_status is None:
                    self._log(f"Обмен {trade_id}: начальный статус = {current_status}")
                    self.trade_statuses[trade_id] = current_status
                else:
                    self._log(f"Обмен {trade_id} уже обработан (статус: {current_status})")

        if removed_count > 0:
            self._log(f"✅ Удалено карт: {removed_count}")
        if restored_count > 0:
            self._log(f"♻️  Возвращено карт: {restored_count}")
        if removed_count == 0 and restored_count == 0:
            self._log("Нет изменений в истории")

        return removed_count

    def _remove_card_from_inventory(self, card_id: int) -> bool:
        """Удаляет карту из инвентаря по card_id."""
        try:
            self._log(f"Попытка удаления карты {card_id} из инвентаря...")
            inventory = self.inventory_manager.load_inventory()

            if not inventory:
                self._log("Инвентарь пуст или не загружен")
                return False

            self._log(f"Загружен инвентарь: {len(inventory)} карт")

            cards_to_remove = []
            for card in inventory:
                c_id = card.get("card_id")
                if not c_id and isinstance(card.get("card"), dict):
                    c_id = card["card"].get("id")
                if c_id == card_id:
                    cards_to_remove.append(card)
                    self._log(f"Найдена карта для удаления: card_id={card_id}")

            if not cards_to_remove:
                self._log(f"Карта {card_id} не найдена в инвентаре")
                return False

            self._log(f"Найдено карт с ID {card_id}: {len(cards_to_remove)}")
            inventory.remove(cards_to_remove[0])
            success = self.inventory_manager.save_inventory(inventory)

            if success:
                self._log(f"✅ Карта {card_id} удалена ({len(inventory)} осталось)")
            else:
                self._log("❌ Не удалось сохранить инвентарь после удаления")

            return success

        except Exception as e:
            self._log(f"Ошибка удаления карты {card_id}: {e}")
            if self.debug:
                import traceback
                traceback.print_exc()
            return False

    # ------------------------------------------------------------------
    # Thread management
    # ------------------------------------------------------------------

    def monitor_loop(self, check_interval: int = 10) -> None:
        """Основной цикл мониторинга."""
        self._log(f"Запущен мониторинг истории (каждые {check_interval}с)")

        initial_trades = self.fetch_recent_trades()
        for trade in initial_trades:
            self.trade_statuses[trade["trade_id"]] = trade["status"]
        self._log(f"Начальное состояние: {len(self.trade_statuses)} обменов")

        check_count = 0
        while self.running:
            try:
                check_count += 1
                self._log(f"Проверка истории #{check_count}")
                removed = self.check_and_remove_traded_cards()
                if removed > 0:
                    print(f"[HISTORY] ✅ Обработано изменений: {removed}")
            except Exception as e:
                self._log(f"Ошибка в цикле: {e}")
                if self.debug:
                    import traceback
                    traceback.print_exc()

            time.sleep(check_interval)

    def start(self, check_interval: int = 10) -> None:
        """Запускает мониторинг в фоновом потоке."""
        if self.running:
            self._log("Мониторинг уже запущен")
            return

        self.running = True
        self.thread = threading.Thread(
            target=self.monitor_loop,
            args=(check_interval,),
            daemon=True,
        )
        self.thread.start()
        print("📊 Мониторинг истории запущен")

    def stop(self) -> None:
        """Останавливает мониторинг."""
        if not self.running:
            return

        self._log("Остановка мониторинга...")
        self.running = False

        if self.thread:
            self.thread.join(timeout=5)

        print("📊 Мониторинг истории остановлен")

    def force_check(self) -> int:
        """Принудительная проверка истории."""
        self._log("🔍 Принудительная проверка истории обменов...")
        removed = self.check_and_remove_traded_cards()
        if removed > 0:
            print(f"[HISTORY] ✅ Принудительная проверка: обработано {removed} изменений")
        else:
            self._log("Принудительная проверка: изменений нет")
        return removed