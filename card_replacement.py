"""Модуль автоматической замены карт в клубе."""

import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

from boost import get_boost_card_info, replace_club_card
from trade import cancel_all_sent_trades
from daily_stats import DailyStatsManager
from utils import print_section, print_success, print_warning, print_info
from config import BASE_URL, REQUEST_TIMEOUT


# ---------------------------------------------------------------------------
# Вспомогательная функция: получить текущий card_id со страницы буста
# ---------------------------------------------------------------------------

def fetch_current_card_id(session: requests.Session, boost_url: str) -> Optional[int]:
    """
    Лёгкий запрос к странице буста — возвращает только card_id текущей карты.

    Используется для проверки перед заменой: убедиться что карта
    не была сменена кем-то другим пока скрипт работал.

    Returns:
        card_id (int) или None при ошибке
    """
    if not boost_url.startswith("http"):
        boost_url = f"{BASE_URL}{boost_url}"

    try:
        response = session.get(boost_url, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, "html.parser")
        card_link = soup.select_one('a.button.button--block[href*="/cards/"]')
        if not card_link:
            return None

        match = re.search(r"/cards/(\d+)", card_link.get("href", ""))
        return int(match.group(1)) if match else None

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Менеджер замены карт
# ---------------------------------------------------------------------------

class CardReplacementManager:
    """Менеджер автоматической замены карт."""

    def __init__(
        self,
        session: requests.Session,
        boost_url: str,
        stats_manager: DailyStatsManager,
    ):
        self.session = session
        self.boost_url = boost_url
        self.stats_manager = stats_manager

    # ------------------------------------------------------------------
    # Условия замены
    # ------------------------------------------------------------------

    def should_replace_card(self, boost_card: dict) -> bool:
        """Проверяет нужно ли заменить карту по условиям владельцев/желающих."""
        owners_count  = boost_card.get("owners_count", 0)
        wanters_count = boost_card.get("wanters_count", 0)

        print_section("🔍 ПРОВЕРКА УСЛОВИЙ АВТОЗАМЕНЫ", char="-")
        print(f"   Владельцев: {owners_count}")
        print(f"   Желающих:   {wanters_count}")
        print(f"   Card ID:    {boost_card.get('card_id')}")
        print(f"   Название:   {boost_card.get('name', 'Неизвестно')}")
        print()

        if owners_count <= 0:
            print_info("❌ Нет данных о владельцах (owners_count <= 0)")
            print("-" * 60 + "\n")
            return False

        # Условие 1: 0–108 владельцев → всегда замена
        if 0 < owners_count <= 108:
            print_warning(f"✅ ЗАМЕНА! Владельцев {owners_count} <= 108")
            print("-" * 60 + "\n")
            return True

        # Условие 2: 109–216 владельцев при 121+ желающих
        if 109 <= owners_count <= 216:
            if wanters_count >= 121:
                print_warning(f"✅ ЗАМЕНА! Владельцев {owners_count}, желающих {wanters_count} >= 121")
                print("-" * 60 + "\n")
                return True
            print_info(f"❌ НЕТ ЗАМЕНЫ. Желающих {wanters_count} < 121")
            print("-" * 60 + "\n")
            return False

        # Условие 3: 217–360 владельцев при 181+ желающих
        if 217 <= owners_count <= 360:
            if wanters_count >= 181:
                print_warning(f"✅ ЗАМЕНА! Владельцев {owners_count}, желающих {wanters_count} >= 181")
                print("-" * 60 + "\n")
                return True
            print_info(f"❌ НЕТ ЗАМЕНЫ. Желающих {wanters_count} < 181")
            print("-" * 60 + "\n")
            return False

        # Условие 4: 361–540 владельцев при 300+ желающих
        if 361 <= owners_count <= 540:
            if wanters_count >= 300:
                print_warning(f"✅ ЗАМЕНА! Владельцев {owners_count}, желающих {wanters_count} >= 300")
                print("-" * 60 + "\n")
                return True
            print_info(f"❌ НЕТ ЗАМЕНЫ. Желающих {wanters_count} < 300")
            print("-" * 60 + "\n")
            return False

        print_info(f"❌ НЕТ ЗАМЕНЫ. Владельцев {owners_count} > 540")
        print("-" * 60 + "\n")
        return False

    def can_replace(self) -> bool:
        """Проверяет лимит замен."""
        if not self.stats_manager.can_replace(force_refresh=True):
            print_warning("⛔ Достигнут дневной лимит замен карт!")
            self.stats_manager.print_stats()
            return False
        return True

    # ------------------------------------------------------------------
    # Ключевая проверка: карта на странице == карта в boost_card?
    # ------------------------------------------------------------------

    def _verify_card_not_changed(self, boost_card: dict) -> tuple[bool, Optional[dict]]:
        """
        Проверяет что карта на странице буста не сменилась сторонним образом.

        Делает лёгкий запрос и сравнивает card_id.

        Returns:
            (карта актуальна, новая карта если сменилась или None)
        """
        expected_id = boost_card.get("card_id")

        print(f"🔎 Проверка актуальности карты перед заменой...")
        print(f"   Ожидаемый card_id: {expected_id}")

        current_id = fetch_current_card_id(self.session, self.boost_url)

        if current_id is None:
            # Не удалось получить — действуем осторожно, не заменяем
            print_warning("   ⚠️  Не удалось получить текущий card_id со страницы")
            print_warning("   ⏭️  Пропускаем замену во избежание ошибки")
            return False, None

        print(f"   Текущий card_id на странице: {current_id}")

        if current_id != expected_id:
            print_warning(
                f"   ⚡ Карта уже сменена кем-то другим!\n"
                f"      Было: {expected_id} → Стало: {current_id}"
            )
            print("   📥 Загружаем информацию о новой карте...")
            new_card = get_boost_card_info(self.session, self.boost_url)
            return False, new_card

        print(f"   ✅ Карта актуальна (ID: {current_id})\n")
        return True, None

    # ------------------------------------------------------------------
    # Общий приватный метод выполнения замены
    # ------------------------------------------------------------------

    def _do_replace(self, boost_card: dict, section_title: str) -> Optional[dict]:
        """
        Выполняет замену карты:
        1. Проверяет лимит
        2. Проверяет что карта на странице ещё та же
        3. Отменяет обмены
        4. Ещё раз проверяет card_id прямо перед отправкой
        5. Отправляет запрос на замену
        6. Загружает новую карту

        Returns:
            Новая карта, уже загруженная карта (если сменили снаружи),
            или None при ошибке/отмене.
        """
        print_section(section_title, char="=")

        old_id   = boost_card.get("card_id")
        old_name = boost_card.get("name", "Неизвестно")
        owners   = boost_card.get("owners_count", "?")
        wanters  = boost_card.get("wanters_count", "?")

        print(f"   Текущая карта: {old_name} (ID: {old_id})")
        print(f"   Владельцев: {owners} | Желающих: {wanters}")
        replacements_left = self.stats_manager.get_replacements_left(force_refresh=True)
        print(f"   Замен осталось сегодня: {replacements_left}\n")

        # ── Шаг 1: проверка лимита ──────────────────────────────────────
        if not self.can_replace():
            return None

        # ── Шаг 2: первичная проверка card_id ───────────────────────────
        card_ok, externally_changed = self._verify_card_not_changed(boost_card)
        if not card_ok:
            # Карту уже сменили снаружи — возвращаем новую без замены
            return externally_changed

        # ── Шаг 3: отмена обменов ───────────────────────────────────────
        print("1️⃣  Отменяем все отправленные обмены...")
        cancel_all_sent_trades(self.session, debug=False)
        time.sleep(1)

        # ── Шаг 4: повторная проверка card_id прямо перед запросом ──────
        #    За время отмены обменов карту могут успеть сменить снаружи
        print("2️⃣  Повторная проверка card_id перед отправкой запроса...")
        card_ok, externally_changed = self._verify_card_not_changed(boost_card)
        if not card_ok:
            return externally_changed

        # ── Шаг 5: проверяем лимит ещё раз (мог измениться за время ────
        #    отмены обменов)
        if not self.stats_manager.can_replace(force_refresh=True):
            print_warning("⛔ Лимит замен достигнут перед отправкой!")
            print("=" * 60 + "\n")
            return None

        # ── Шаг 6: отправляем запрос на замену ──────────────────────────
        print("3️⃣  Отправляем запрос на замену карты...")
        success = replace_club_card(self.session)

        if not success:
            print_warning("❌ Не удалось заменить карту")
            print("=" * 60 + "\n")
            return None

        print_success("✅ Запрос на замену отправлен!")

        print("4️⃣  Ожидание обновления данных (3 сек)...")
        time.sleep(3)

        print("5️⃣  Обновляем статистику с сервера...")
        self.stats_manager.refresh_stats()

        print("6️⃣  Загружаем информацию о новой карте...")
        new_card = get_boost_card_info(self.session, self.boost_url)

        if not new_card:
            print_warning("❌ Не удалось получить информацию о новой карте")
            print("=" * 60 + "\n")
            return None

        new_id      = new_card.get("card_id")
        new_name    = new_card.get("name", "Неизвестно")
        new_owners  = new_card.get("owners_count", "?")
        new_wanters = new_card.get("wanters_count", "?")

        if new_id != old_id:
            print_success("✅ Карта успешно заменена!")
            print(
                f"\n   Старая: {old_name} (ID: {old_id}, "
                f"владельцев: {owners}, желающих: {wanters})"
            )
            print(
                f"   Новая:  {new_name} (ID: {new_id}, "
                f"владельцев: {new_owners}, желающих: {new_wanters})\n"
            )
        else:
            print_warning(f"⚠️  Карта не изменилась (ID: {old_id})")
            print("   Возможно, замена не сработала или вернулась та же карта\n")
            new_card = None

        self.stats_manager.print_stats(force_refresh=True)
        print("=" * 60 + "\n")
        return new_card

    # ------------------------------------------------------------------
    # Публичные методы
    # ------------------------------------------------------------------

    def perform_replacement(self, boost_card: dict) -> Optional[dict]:
        """Замена карты С ПРОВЕРКОЙ условий (owners/wanters)."""
        if not self.should_replace_card(boost_card):
            return None
        return self._do_replace(boost_card, "🔄 АВТОМАТИЧЕСКАЯ ЗАМЕНА КАРТЫ")

    def force_replace_card(
        self,
        boost_card: dict,
        reason: str = "Принудительная замена",
    ) -> Optional[dict]:
        """Принудительная замена карты БЕЗ проверки условий."""
        return self._do_replace(boost_card, f"🔄 {reason.upper()}")


# ---------------------------------------------------------------------------
# Публичные функции-обёртки
# ---------------------------------------------------------------------------

def check_and_replace_if_needed(
    session: requests.Session,
    boost_url: str,
    boost_card: dict,
    stats_manager: DailyStatsManager,
) -> Optional[dict]:
    """Проверяет карту и заменяет если нужно и возможно."""
    manager = CardReplacementManager(session, boost_url, stats_manager)
    return manager.perform_replacement(boost_card)


def force_replace_card(
    session: requests.Session,
    boost_url: str,
    boost_card: dict,
    stats_manager: DailyStatsManager,
    reason: str = "Принудительная замена",
) -> Optional[dict]:
    """Принудительная замена карты БЕЗ проверки условий."""
    manager = CardReplacementManager(session, boost_url, stats_manager)
    return manager.force_replace_card(boost_card, reason)