"""Управление сессией и жизненным циклом приложения."""

import time
from typing import Optional

from auth import login, logout, is_authenticated, refresh_session_token
from owners_parser import OwnersProcessor
from trade import cancel_all_sent_trades
from card_selector import select_trade_card, parse_all_unparsed_cards
from trade import send_trade_to_owner
from daily_stats import create_stats_manager
from utils import print_success, print_error, print_warning, print_section


class SessionMixin:
    """Миксин для управления сессией и перезапуска компонентов."""

    def recreate_all_objects(self) -> bool:
        """Пересоздаёт все объекты с новой сессией."""
        try:
            self.logger.info("=" * 70)
            self.logger.info("ПЕРЕСОЗДАНИЕ ВСЕХ ОБЪЕКТОВ С НОВОЙ СЕССИЕЙ")

            if self.args.boost_url:
                print("📊 Пересоздание менеджера статистики...")
                self.stats_manager = create_stats_manager(self.session, self.args.boost_url)
                self.stats_manager.print_stats(force_refresh=True)

            if not self.args.skip_inventory:
                print("📊 Пересоздание монитора истории...")
                if self.history_monitor and self.history_monitor.running:
                    self.history_monitor.stop()
                self.init_history_monitor()

            print("🔄 Пересоздание процессора...")
            self.processor = OwnersProcessor(
                session=self.session,
                select_card_func=select_trade_card,
                send_trade_func=send_trade_to_owner,
                dry_run=self.args.dry_run,
                debug=self.args.debug,
            )

            if self.args.enable_monitor and self.args.boost_url:
                print("🔄 Пересоздание монитора буста...")
                if self.monitor and self.monitor.is_running():
                    self.monitor.stop()
                boost_card = self.load_boost_card()
                if boost_card:
                    self.start_monitoring(boost_card)

            print_success("✅ Все объекты обновлены\n")
            return True
        except Exception as e:
            self.logger.exception(f"Ошибка: {e}")
            return False

    def check_and_refresh_session(self) -> bool:
        """Проверяет валидность сессии и при необходимости обновляет её."""
        if not is_authenticated(self.session):
            print_error("❌ Сессия истекла!")

            if refresh_session_token(self.session):
                if is_authenticated(self.session):
                    print_success("✅ Сессия восстановлена")
                    return True

            print_warning("Повторный вход...")
            self.session = login(self.args.email, self.args.password, self.proxy_manager)

            if not self.session:
                return False

            return self.recreate_all_objects()

        return True

    def _parse_inventory_before_sleep(self) -> None:
        """
        Парсит все непропарсенные карты инвентаря перед уходом в сон.

        Вызывается пока сессия ещё активна — после отмены обменов,
        но до logout. Результат сохраняется в parsed_inventory.json
        и будет использован сразу после пробуждения.
        """
        if self.args.skip_inventory:
            self.logger.info("Парсинг инвентаря пропущен (--skip_inventory)")
            return

        self.logger.info("=" * 70)
        self.logger.info("ПАРСИНГ НЕПРОПАРСЕННЫХ КАРТ ПЕРЕД СНОМ")
        self.logger.info("=" * 70)

        print_section("📋 ПАРСИНГ ИНВЕНТАРЯ ПЕРЕД СНОМ", char="=")
        print("   Парсим оставшиеся карты пока сессия активна...")
        print("   (результат будет готов к следующему запуску)\n")

        try:
            stats = parse_all_unparsed_cards(
                session=self.session,
                output_dir=self.output_dir,
                save_interval=10,
            )
            self.logger.info(
                f"Парсинг завершён: "
                f"пропарсено={stats['parsed']}, "
                f"пропущено={stats['skipped']}, "
                f"ошибок={stats['errors']}, "
                f"всего={stats['total']}"
            )
        except Exception as e:
            self.logger.exception(f"Ошибка при парсинге инвентаря перед сном: {e}")
            print_warning(f"⚠️  Ошибка парсинга: {e}")

    def sleep_until_reset(self) -> bool:
        """Режим сна до смены суток (00:00 MSK)."""
        self.logger.info("Переход в режим сна (лимиты исчерпаны)")
        print_section("💤 РЕЖИМ СНА", char="=")
        print("   ⛔ Вклады на сегодня исчерпаны")
        print("   💤 Выход из аккаунта и ожидание смены суток...\n")

        # 1. Отменяем все обмены
        if not self.args.dry_run and self.processor and self.processor.trade_manager:
            self.logger.info("Отмена всех обменов перед выходом...")
            print("🔄 Отменяем все обмены перед выходом...")
            success = cancel_all_sent_trades(
                self.session,
                self.processor.trade_manager,
                self.history_monitor,
                self.args.debug,
            )
            if success:
                self.logger.info("Обмены успешно отменены")
                print_success("✅ Обмены отменены\n")

        # 2. Парсим весь непропарсенный инвентарь пока сессия жива
        self._parse_inventory_before_sleep()

        # 3. Останавливаем мониторы
        if self.monitor and self.monitor.is_running():
            self.logger.info("Остановка монитора буста...")
            print("🛑 Остановка монитора буста...")
            self.monitor.stop()
            self.monitor = None

        if self.history_monitor and self.history_monitor.running:
            self.logger.info("Остановка монитора истории...")
            print("🛑 Остановка монитора истории...")
            self.history_monitor.stop()
            self.history_monitor = None

        # 4. Выходим из аккаунта
        self.logger.info("Выход из аккаунта...")
        print("\n🚪 Выход из аккаунта...")
        if logout(self.session):
            self.logger.info("Выход выполнен успешно")
            print_success("✅ Выход выполнен\n")
        else:
            self.logger.warning("Ошибка при выходе из аккаунта")
            print_warning("⚠️  Ошибка выхода, но продолжаем...\n")

        if not self.stats_manager:
            self.logger.error("Нет менеджера статистики!")
            print_error("Нет менеджера статистики!")
            return False

        seconds_until_reset   = self.stats_manager._seconds_until_reset()
        reset_time_formatted  = self.stats_manager._format_time_until_reset()

        self.logger.info(f"Время до сброса лимитов: {reset_time_formatted}")
        print(f"⏰ Сброс лимитов через: {reset_time_formatted}")
        print("💤 Переход в режим ожидания...")
        print("   Нажмите Ctrl+C для завершения\n")

        check_interval = 60
        elapsed = 0

        while elapsed < seconds_until_reset:
            remaining = seconds_until_reset - elapsed
            hours   = remaining // 3600
            minutes = (remaining % 3600) // 60

            if minutes % 10 == 0 or remaining < 600:
                self.logger.debug(f"Режим сна: осталось {hours}ч {minutes}м")
                print(f"💤 Режим сна: осталось {hours}ч {minutes}м до сброса")

            sleep_time = min(check_interval, remaining)
            time.sleep(sleep_time)
            elapsed += sleep_time

        self.logger.info("=" * 70)
        self.logger.info("СМЕНА СУТОК — ПОВТОРНЫЙ ВХОД")
        self.logger.info("=" * 70)
        print_success("\n✅ Смена суток! Повторный вход в аккаунт...")

        self.session = login(self.args.email, self.args.password, self.proxy_manager)

        if not self.session:
            self.logger.error("❌ Не удалось войти в аккаунт после режима сна")
            print_error("❌ Не удалось войти в аккаунт!")
            return False

        self.logger.info("✅ Авторизация после режима сна успешна")
        print_success("✅ Авторизация успешна!")

        print("\n" + "=" * 70)
        print("ПЕРЕСОЗДАНИЕ ВСЕХ ОБЪЕКТОВ С НОВОЙ СЕССИЕЙ")
        print("=" * 70 + "\n")

        if not self.recreate_all_objects():
            self.logger.error("❌ Не удалось пересоздать объекты после сна")
            print_error("❌ Ошибка пересоздания объектов")
            return False

        self.failed_cycles_count = 0
        self.logger.info("Счётчик неудачных циклов сброшен")

        self.logger.info("=" * 70)
        self.logger.info("✅ СИСТЕМА ПОЛНОСТЬЮ ПЕРЕЗАПУЩЕНА")
        self.logger.info("=" * 70)
        print_success("✅ Система полностью перезапущена!\n")

        return True