"""Основной цикл обработки владельцев карт."""

import time
from typing import Optional

from config import (
    BOOST_CARD_FILE,
    WAIT_AFTER_ALL_OWNERS,
    WAIT_CHECK_INTERVAL
)
from monitor import MONITOR_CHECK_INTERVAL
from owners_parser import process_owners_page_by_page
from trade import cancel_all_sent_trades
from card_replacement import check_and_replace_if_needed, force_replace_card
from utils import (
    save_json,
    load_json,
    print_section,
    print_success,
    print_error,
    print_warning,
    print_info
)


class ProcessingMixin:
    """Миксин с основным циклом обработки и вспомогательными методами."""

    def wait_for_boost_or_timeout(
        self,
        card_id: int,
        timeout: int = WAIT_AFTER_ALL_OWNERS
    ) -> bool:
        """Ожидает буст или таймаут с активным мониторингом."""
        if not self.monitor:
            return False

        self.logger.info(f"Начало ожидания буста для карты {card_id} (таймаут: {timeout}с)")
        print_section(
            f"⏳ ВСЕ ВЛАДЕЛЬЦЫ ОБРАБОТАНЫ - Ожидание {timeout // 60} мин",
            char="="
        )
        print(f"   Текущая карта: ID {card_id}")
        print(f"   🔄 Мониторинг АКТИВЕН - проверяет карту каждые {MONITOR_CHECK_INTERVAL}с")
        print(f"   Отслеживание: буст + смена карты\n")

        if hasattr(self.monitor, 'monitoring_paused'):
            self.monitor.resume_monitoring()

        start_time = time.time()
        check_count = 0

        while time.time() - start_time < timeout:
            check_count += 1

            if self.monitor.card_changed:
                elapsed = int(time.time() - start_time)
                self.logger.info(f"Буст произошел через {elapsed}с")
                print(f"\n✅ БУСТ ПРОИЗОШЕЛ через {elapsed}с!")
                return True

            if check_count % 15 == 0:
                elapsed = int(time.time() - start_time)
                remaining = timeout - elapsed
                self.logger.debug(f"Ожидание буста: {elapsed}с / {remaining}с осталось")
                print(f"⏳ Ожидание: {elapsed}с / {remaining}с осталось (мониторинг активен)")

            time.sleep(WAIT_CHECK_INTERVAL)

        self.logger.warning(f"Таймаут ожидания буста: {timeout // 60} минут")
        print(f"\n⏱️  ТАЙМАУТ: {timeout // 60} минут")
        return False

    def attempt_auto_replacement(
        self,
        current_boost_card: dict,
        reason: str = "АВТОЗАМЕНА ПОСЛЕ 3 НЕУДАЧНЫХ ЦИКЛОВ"
    ) -> Optional[dict]:
        """Попытка принудительной замены карты."""
        self.logger.warning(f"Попытка автозамены карты. Причина: {reason}")
        if not self.stats_manager.can_replace(force_refresh=True):
            self.logger.warning("Лимит замен достигнут")
            print_warning("⛔ Лимит замен достигнут!")
            self.stats_manager.print_stats()
            return None

        new_card = force_replace_card(
            self.session,
            self.args.boost_url,
            current_boost_card,
            self.stats_manager,
            reason=reason
        )

        if new_card:
            self.failed_cycles_count = 0
            self.logger.info("Замена карты выполнена успешно, счетчик сброшен")
            print_success("✅ Замена выполнена! Счетчик неудачных циклов сброшен\n")
            return new_card

        self.logger.warning("Замена карты не удалась")
        print_warning("❌ Замена не удалась\n")
        return None

    def _should_restart(self) -> bool:
        return (
            self.monitor and
            self.monitor.is_running() and
            self.monitor.card_changed
        )

    def _prepare_restart(self):
        self.logger.info("Подготовка к перезапуску с новой картой")
        print_section("🔄 ПЕРЕЗАПУСК с новой картой", char="=")

    def _load_current_boost_card(self, default: dict) -> dict:
        """Устарело: оставлено для совместимости."""
        path = f"{self.output_dir}/{BOOST_CARD_FILE}"
        current = load_json(path, default=default)
        return current if current else default

    def _handle_interruption(self, current_boost_card: dict) -> Optional[dict]:
        """Обрабатывает прерывание от монитора, возвращает новую карту или None."""
        reason = self.monitor.get_interrupt_reason()
        self.logger.info(f"⚡ Обнаружено прерывание: {reason}")
        print(f"\n⚡ ОБНАРУЖЕНО ПРЕРЫВАНИЕ: {reason}!")

        if self.monitor.boost_available:
            self.logger.info("🎁 Буст был внесён во время обработки!")
            print("🎁 Буст был внесён во время обработки владельцев!")
            print("💎 Монитор уже внёс карту, загружаем новую...\n")

        if self.monitor.card_changed:
            self.logger.info("🔄 Карта изменилась во время обработки!")
            print("🔄 Карта изменилась во время обработки владельцев!\n")

        self.logger.info("📥 Загрузка новой карты после прерывания...")
        print("📥 Загрузка новой карты...")
        new_card = self.load_boost_card()

        self.logger.info("🚩 Сброс флагов прерывания...")
        self.monitor.reset_interruption_flags()
        self.processor.reset_state()
        self.failed_cycles_count = 0

        return new_card

    def _handle_timeout(self, current_boost_card: dict) -> None:
        """Обрабатывает таймаут ожидания буста: отменяет обмены, обновляет счётчик."""
        self.logger.info("Таймаут ожидания буста - отмена обменов")
        print("🔄 Отменяем обмены...")
        if not self.args.dry_run:
            success = cancel_all_sent_trades(
                self.session,
                self.processor.trade_manager,
                self.history_monitor,
                self.args.debug
            )
            if success:
                self.logger.info("Обмены отменены успешно")
                print_success("Обмены отменены, история проверена!")
            else:
                self.logger.warning("Не удалось отменить обмены")
                print_warning("Не удалось отменить")

        self.failed_cycles_count += 1
        self.logger.warning(
            f"Неудачный цикл #{self.failed_cycles_count}/{self.MAX_FAILED_CYCLES}"
        )
        print_warning(
            f"⚠️  ПОЛНЫЙ цикл #{self.failed_cycles_count}/{self.MAX_FAILED_CYCLES} "
            f"завершен БЕЗ вклада (таймаут ожидания)"
        )

    def run_processing_mode(self, boost_card: dict):
        """Основной цикл обработки владельцев."""
        self.init_processor()
        self.logger.info("Запуск режима обработки владельцев")

        current_boost_card = boost_card

        while True:
            # --- Проверка лимита вкладов ---
            if not self.stats_manager.can_donate(force_refresh=True):
                self.logger.warning("Лимит вкладов достигнут")
                print_warning("\n⛔ Лимит вкладов достигнут!")

                if not self.sleep_until_reset():
                    self.logger.error("Не удалось перезапустить после режима сна")
                    print_error("❌ Не удалось перезапустить после режима сна")
                    break

                self.logger.info("Загрузка актуальной карты буста после сна...")
                print("\n📦 Загрузка актуальной карты буста...")
                current_boost_card = self.load_boost_card()

                if not current_boost_card:
                    self.logger.error("Не удалось загрузить карту буста после сна")
                    print_error("❌ Не удалось загрузить карту буста")
                    break

                if self.args.enable_monitor:
                    self.start_monitoring(current_boost_card)

                if self.processor:
                    self.processor.reset_state()

                self.failed_cycles_count = 0
                continue

            # --- Автозамена после 3 неудачных циклов ---
            if self.failed_cycles_count >= self.MAX_FAILED_CYCLES:
                self.logger.warning(f"Достигнуто {self.MAX_FAILED_CYCLES} неудачных циклов")
                print_warning(f"\n⚠️  Достигнуто {self.MAX_FAILED_CYCLES} неудачных ПОЛНЫХ циклов!")

                new_card = self.attempt_auto_replacement(
                    current_boost_card,
                    reason="АВТОЗАМЕНА ПОСЛЕ 3 НЕУДАЧНЫХ ЦИКЛОВ"
                )

                if new_card:
                    current_boost_card = new_card
                    save_json(f"{self.output_dir}/{BOOST_CARD_FILE}", new_card)
                    self.logger.info(f"✅ Новая карта сохранена")
                    print(f"💾 Новая карта ID={new_card['card_id']} сохранена в файл")

                    if self.monitor:
                        self.monitor.current_card_id = new_card['card_id']

                    self.processor.reset_state()
                    continue
                else:
                    self.failed_cycles_count = 0
                    self.logger.info("Продолжаем работу с текущей картой")
                    print_info("ℹ️  Продолжаем работу с текущей картой")

            # --- Проверка условий автозамены ---
            self.logger.info("="*70)
            self.logger.info("ПРОВЕРКА АВТОЗАМЕНЫ В ЦИКЛЕ")
            self.logger.info(
                f"Карта: {current_boost_card.get('name')} "
                f"(ID: {current_boost_card.get('card_id')})"
            )

            new_card = check_and_replace_if_needed(
                self.session,
                self.args.boost_url,
                current_boost_card,
                self.stats_manager
            )

            if new_card:
                self.logger.info(f"Карта заменена автоматически: {new_card.get('card_id')}")
                current_boost_card = new_card
                save_json(f"{self.output_dir}/{BOOST_CARD_FILE}", new_card)
                print(f"💾 Новая карта ID={new_card['card_id']} сохранена в файл")

                if self.monitor:
                    self.monitor.current_card_id = new_card['card_id']

                self.processor.reset_state()
                self.failed_cycles_count = 0

            if self.monitor:
                self.monitor.card_changed = False

            current_card_id = current_boost_card['card_id']
            current_rate = self.rate_limiter.get_current_rate()

            self.logger.info(f"Обработка карты: {current_boost_card['name']} (ID: {current_card_id})")
            print(f"\n🎯 Обработка: {current_boost_card['name']} (ID: {current_card_id})")
            print(f"📊 Текущий rate: {current_rate}/{self.rate_limiter.max_requests} req/min\n")

            if not self.stats_manager.can_donate(force_refresh=True):
                self.logger.warning("Лимит вкладов достигнут во время обработки")
                print_warning("⛔ Лимит вкладов достигнут!")
                continue

            boost_happened_this_cycle = False

            # --- Обработка владельцев ---
            self.logger.info(f"Начало обработки владельцев карты {current_card_id}")
            total = process_owners_page_by_page(
                session=self.session,
                card_id=str(current_card_id),
                boost_card=current_boost_card,
                output_dir=self.output_dir,
                select_card_func=self.select_card_func,
                send_trade_func=self.send_trade_func,
                monitor_obj=self.monitor,
                processor=self.processor,
                dry_run=self.args.dry_run,
                debug=self.args.debug
            )

            # --- Проверка прерывания сразу после обработки ---
            if self.monitor and self.monitor.should_interrupt():
                new_card = self._handle_interruption(current_boost_card)

                if not new_card:
                    self.logger.error("❌ Не удалось загрузить карту после прерывания")
                    print_error("❌ Не удалось загрузить карту!")
                    break

                current_boost_card = new_card
                print_success(
                    f"✅ Новая карта загружена: {current_boost_card['name']} "
                    f"(ID: {current_boost_card['card_id']})"
                )
                print("🔄 Перезапуск обработки...\n")
                time.sleep(1)
                continue

            # --- Итоги обработки ---
            if total > 0:
                self.logger.info(f"Обработано владельцев: {total}")
                print_success(f"Обработано {total} владельцев")
                if self.processor.trade_manager:
                    sent_count = len(self.processor.trade_manager.sent_trades)
                    self.logger.info(f"Отправлено обменов: {sent_count}")
                    print_success(f"✅ Отправлено обменов: {sent_count}")
            else:
                self.logger.warning("Нет доступных владельцев")
                print_warning("Нет доступных владельцев")

            # --- Старая логика card_changed (совместимость) ---
            if self._should_restart():
                boost_happened_this_cycle = True
                self.processor.reset_state()
                self.failed_cycles_count = 0
                self.logger.info("Буст произошел - перезапуск с новой картой")
                print_success("✅ Буст произошел - счетчик неудачных циклов сброшен")
                self._prepare_restart()

                current_boost_card = self.load_boost_card()
                if not current_boost_card:
                    self.logger.error("Не удалось загрузить карту после буста")
                    print_error("❌ Не удалось загрузить карту после буста")
                    break

                time.sleep(1)
                continue

            # --- Ожидание буста с монитором ---
            if self.monitor and self.monitor.is_running() and total > 0:
                boost_occurred = self.wait_for_boost_or_timeout(current_card_id)

                if boost_occurred:
                    boost_happened_this_cycle = True
                    self.processor.reset_state()
                    self.failed_cycles_count = 0
                    self.logger.info("Буст произошел во время ожидания")
                    print_success("✅ Буст произошел - счетчик неудачных циклов сброшен")
                    self._prepare_restart()

                    current_boost_card = self.load_boost_card()
                    if not current_boost_card:
                        self.logger.error("Не удалось загрузить карту после буста")
                        print_error("❌ Не удалось загрузить карту после буста")
                        break

                    time.sleep(1)
                    continue
                else:
                    self._handle_timeout(current_boost_card)
                    print_section("🔄 ПЕРЕЗАПУСК с той же картой", char="=")
                    time.sleep(1)
                    continue

            # --- Нет владельцев ---
            if total == 0:
                self.failed_cycles_count += 1
                self.logger.warning(
                    f"Неудачный цикл #{self.failed_cycles_count}/{self.MAX_FAILED_CYCLES} "
                    f"(нет владельцев)"
                )
                print_warning(
                    f"⚠️  ПОЛНЫЙ цикл #{self.failed_cycles_count}/{self.MAX_FAILED_CYCLES} "
                    f"завершен БЕЗ вклада (нет владельцев)"
                )
                print_section("🔄 ПЕРЕЗАПУСК с той же картой", char="=")
                time.sleep(1)
                continue