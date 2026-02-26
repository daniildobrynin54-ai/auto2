"""Менеджер обменов: поиск карт, создание и отмена обменов, обработка 419."""

import json
import time
from typing import Dict, Optional, Set

import requests
from bs4 import BeautifulSoup

from config import BASE_URL, REQUEST_TIMEOUT, CARD_API_DELAY, CARDS_PER_BATCH
from rate_limiter import get_rate_limiter


class TradeManager:
    """Менеджер обменов с оптимизированным поиском карт и обработкой 419."""

    def __init__(self, session, debug: bool = False):
        self.session = session
        self.debug = debug
        self.sent_trades: Set[tuple[int, int]] = set()
        self.limiter = get_rate_limiter()
        self.locked_cards: Set[int] = set()

        if not self._get_csrf_token():
            self._log("⚠️  CSRF токен отсутствует при создании TradeManager")
            self._refresh_csrf_token()

    # ------------------------------------------------------------------
    # Logging & helpers
    # ------------------------------------------------------------------

    def _log(self, message: str) -> None:
        if self.debug:
            print(f"[TRADE] {message}")

    def _get_csrf_token(self) -> str:
        return self.session.headers.get("X-CSRF-TOKEN", "")

    def _refresh_csrf_token(self) -> bool:
        """Обновляет CSRF токен из страницы предложений обменов."""
        try:
            self._log("🔄 Обновление CSRF токена...")
            response = self.session.get(f"{BASE_URL}/trades/offers", timeout=REQUEST_TIMEOUT)
            if response.status_code != 200:
                self._log("❌ Не удалось загрузить страницу для обновления токена")
                return False

            soup = BeautifulSoup(response.text, "html.parser")
            token_meta = soup.select_one('meta[name="csrf-token"]')
            if token_meta:
                token = token_meta.get("content", "").strip()
                if token:
                    self.session.headers.update({"X-CSRF-TOKEN": token})
                    self._log(f"✅ CSRF токен обновлён: {token[:10]}...")
                    return True

            self._log("⚠️ Не удалось найти CSRF токен на странице")
            return False

        except Exception as e:
            self._log(f"❌ Ошибка обновления токена: {e}")
            return False

    def _prepare_headers(self, receiver_id: int) -> Dict[str, str]:
        headers = {
            "Referer": f"{BASE_URL}/trades/offers/{receiver_id}",
            "Origin": BASE_URL,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        csrf_token = self._get_csrf_token()
        if csrf_token:
            headers["X-CSRF-TOKEN"] = csrf_token
        return headers

    # ------------------------------------------------------------------
    # Response validation
    # ------------------------------------------------------------------

    def _is_success_response(self, response: requests.Response) -> bool:
        if response.status_code == 200:
            return True

        if response.status_code in (301, 302):
            if "/trades/" in response.headers.get("Location", ""):
                return True

        try:
            data = response.json()
            if isinstance(data, dict):
                if data.get("success") or data.get("ok"):
                    return True
                if isinstance(data.get("trade"), dict) and data["trade"].get("id"):
                    return True
                body_text = json.dumps(data).lower()
                if any(w in body_text for w in ["успеш", "отправ", "создан"]):
                    return True
        except ValueError:
            pass

        body = (response.text or "").lower()
        if any(w in body for w in ["успеш", "отправ", "создан"]):
            return True

        return False

    # ------------------------------------------------------------------
    # Card lookup
    # ------------------------------------------------------------------

    def find_partner_card_instance(self, partner_id: int, card_id: int) -> Optional[int]:
        """
        Ищет instance_id карты у партнёра.

        Обрабатывает: 419 (CSRF expired), 429 (rate limit), таймауты.
        """
        self._log(f"🔍 Поиск instance_id карты {card_id} у владельца {partner_id}...")

        csrf_refresh_attempts = 0
        MAX_CSRF_REFRESH = 2
        MAX_CONSECUTIVE_EMPTY = 2
        MAX_TIMEOUT_RETRIES = 2
        max_batches = 5
        min_batches = 2

        url = f"{BASE_URL}/trades/{partner_id}/availableCardsLoad"
        offset = 0
        batch_count = 0
        consecutive_empty = 0

        while batch_count < max_batches:
            self.limiter.wait_and_record()

            headers = self._prepare_headers(partner_id)
            self._log(f"  📦 Диапазон #{batch_count + 1}: offset={offset}")

            response = None
            for timeout_retry in range(MAX_TIMEOUT_RETRIES):
                try:
                    response = self.session.post(
                        url,
                        data={"offset": offset},
                        headers=headers,
                        timeout=(5, 10),
                    )
                    break
                except requests.Timeout:
                    self._log(f"     ⏱️  Таймаут (попытка {timeout_retry + 1}/{MAX_TIMEOUT_RETRIES})")
                    if timeout_retry < MAX_TIMEOUT_RETRIES - 1:
                        time.sleep(1)
                    else:
                        response = None
                except requests.RequestException as e:
                    self._log(f"     ⚠️  Ошибка сети: {e}")
                    if timeout_retry < MAX_TIMEOUT_RETRIES - 1:
                        time.sleep(1)
                    else:
                        response = None

            if response is None:
                self._log("     ⏭️  Пропускаем диапазон")
                offset += CARDS_PER_BATCH
                batch_count += 1
                consecutive_empty += 1
                if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                    self._log(f"     🛑 {MAX_CONSECUTIVE_EMPTY} пустых диапазонов — стоп")
                    break
                continue

            if response.status_code == 419:
                self._log("     ⚠️  Ошибка 419 (CSRF Token Expired)")
                if csrf_refresh_attempts < MAX_CSRF_REFRESH:
                    csrf_refresh_attempts += 1
                    self._log(f"     🔄 Попытка {csrf_refresh_attempts}/{MAX_CSRF_REFRESH}")
                    if self._refresh_csrf_token():
                        time.sleep(2)
                        continue  # повторяем тот же батч
                    return None
                self._log("     ❌ Превышен лимит попыток обновления токена")
                return None

            if response.status_code == 429:
                self._log("     ⚠️  Rate limit 429")
                self.limiter.pause_for_429()
                continue

            if response.status_code != 200:
                self._log(f"     ❌ Ошибка API: {response.status_code}")
                offset += CARDS_PER_BATCH
                batch_count += 1
                consecutive_empty += 1
                if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                    break
                continue

            try:
                data = response.json()
            except ValueError as e:
                self._log(f"     ❌ JSON parse error: {e}")
                offset += CARDS_PER_BATCH
                batch_count += 1
                continue

            cards = data.get("cards", [])
            if not cards:
                self._log("     📭 Диапазон пуст")
                consecutive_empty += 1
                if batch_count >= min_batches - 1 or consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                    self._log(f"     🛑 Останавливаемся ({batch_count + 1} диапазонов)")
                    break
                offset += CARDS_PER_BATCH
                batch_count += 1
                continue

            consecutive_empty = 0
            self._log(f"     📊 Получено {len(cards)} карт")

            for card in cards:
                c_card_id = card.get("card_id")
                if not c_card_id and isinstance(card.get("card"), dict):
                    nested = card["card"]
                    c_card_id = nested.get("id") or nested.get("card_id")

                if c_card_id and int(c_card_id) == card_id:
                    instance_id = card.get("id")
                    if not instance_id:
                        self._log("     ⚠️  Карта найдена, но нет instance_id")
                        continue

                    is_locked = card.get("locked") or card.get("is_locked") or card.get("lock")
                    is_in_trade = card.get("in_trade") or card.get("is_in_trade") or card.get("trading")

                    if is_locked or is_in_trade:
                        self._log(f"     ⚠️  Карта недоступна (locked={is_locked}, in_trade={is_in_trade})")
                        continue

                    self._log(f"     ✅ НАЙДЕНО! instance_id={instance_id}")
                    return int(instance_id)

            offset += CARDS_PER_BATCH
            batch_count += 1
            time.sleep(CARD_API_DELAY)

        self._log(f"❌ Карта {card_id} не найдена после {batch_count} диапазонов")
        return None

    # ------------------------------------------------------------------
    # Trade creation
    # ------------------------------------------------------------------

    def create_trade_direct_api(
        self,
        receiver_id: int,
        my_instance_id: int,
        his_instance_id: int,
    ) -> bool:
        """Создаёт обмен через API."""
        if my_instance_id in self.locked_cards:
            self._log(f"⚠️  Карта {my_instance_id} уже заблокирована!")
            return False

        url = f"{BASE_URL}/trades/create"
        headers = self._prepare_headers(receiver_id)
        data = [
            ("receiver_id", int(receiver_id)),
            ("creator_card_ids[]", int(my_instance_id)),
            ("receiver_card_ids[]", int(his_instance_id)),
        ]

        self._log(f"⚡ Отправка: receiver={receiver_id}, my={my_instance_id}, his={his_instance_id}")

        try:
            self.limiter.wait_and_record()
            response = self.session.post(
                url,
                data=data,
                headers=headers,
                allow_redirects=False,
                timeout=REQUEST_TIMEOUT,
            )
            self._log(f"Response status: {response.status_code}")

            if response.status_code == 429:
                self._log("⚠️  Rate limit (429)")
                self.limiter.pause_for_429()
                return False

            if response.status_code == 422:
                self._log("❌ Карта уже участвует в обмене (422)")
                return False

            if response.status_code == 419:
                self._log("⚠️  CSRF Token Expired (419) — обновляем токен")
                if self._refresh_csrf_token():
                    headers = self._prepare_headers(receiver_id)
                    response = self.session.post(
                        url,
                        data=data,
                        headers=headers,
                        allow_redirects=False,
                        timeout=REQUEST_TIMEOUT,
                    )
                    self._log(f"Повторная попытка: status={response.status_code}")
                else:
                    return False

            if self._is_success_response(response):
                self._log("✅ Обмен успешно создан")
                self.locked_cards.add(my_instance_id)
                self._log(f"🔒 Карта {my_instance_id} заблокирована (всего: {len(self.locked_cards)})")
                return True

            self._log(f"❌ Обмен не удался: {response.status_code}")
            return False

        except requests.RequestException as e:
            self._log(f"❌ Ошибка сети: {e}")
            return False

    # ------------------------------------------------------------------
    # Trade cancellation
    # ------------------------------------------------------------------

    def cancel_all_sent_trades(self, history_monitor=None) -> bool:
        """Отменяет все исходящие обмены."""
        url = f"{BASE_URL}/trades/rejectAll?type_trade=sender"
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": f"{BASE_URL}/trades/offers",
        }

        self._log("Отмена всех обменов...")
        self._log(f"Заблокированных карт до отмены: {len(self.locked_cards)}")

        try:
            response = self.session.get(
                url,
                headers=headers,
                allow_redirects=True,
                timeout=REQUEST_TIMEOUT,
            )
            self._log(f"Response status: {response.status_code}")

            if response.status_code == 200:
                self.clear_sent_trades()
                time.sleep(2)
                if history_monitor:
                    self._log("Проверка истории после отмены...")
                    removed = history_monitor.force_check()
                    if removed > 0:
                        print(f"🗑️  Обработано {removed} изменений в инвентаре")
                return True

            return False

        except requests.RequestException as e:
            self._log(f"Ошибка сети: {e}")
            return False

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def has_trade_sent(self, receiver_id: int, card_id: int) -> bool:
        return (receiver_id, card_id) in self.sent_trades

    def is_my_card_locked(self, instance_id: int) -> bool:
        return instance_id in self.locked_cards

    def mark_trade_sent(self, receiver_id: int, card_id: int) -> None:
        self.sent_trades.add((receiver_id, card_id))
        self._log(f"Обмен помечен: owner={receiver_id}, card_id={card_id}")

    def unlock_card(self, instance_id: int) -> None:
        if instance_id in self.locked_cards:
            self.locked_cards.discard(instance_id)
            self._log(f"🔓 Карта {instance_id} разблокирована (осталось: {len(self.locked_cards)})")

    def clear_sent_trades(self) -> None:
        """Очищает историю обменов и разблокирует карты."""
        count = len(self.sent_trades)
        locked_count = len(self.locked_cards)
        self.sent_trades.clear()
        self.locked_cards.clear()
        self._log(
            f"Список обменов очищен ({count} записей), "
            f"карты разблокированы ({locked_count} шт)"
        )