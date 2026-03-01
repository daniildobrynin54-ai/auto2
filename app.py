"""Ядро приложения MangaBuff."""

from typing import Optional

from config import (
    OUTPUT_DIR,
    BOOST_CARD_FILE,
    HISTORY_CHECK_INTERVAL
)
from logger import get_logger
from auth import login
from inventory import get_user_inventory, InventoryManager
from boost import get_boost_card_info
from card_selector import select_trade_card
from card_replacement import check_and_replace_if_needed
from owners_parser import OwnersProcessor
from monitor import start_boost_monitor
from trade import send_trade_to_owner, TradeHistoryMonitor
from daily_stats import create_stats_manager
from proxy_manager import create_proxy_manager
from rate_limiter import get_rate_limiter
from utils import (
    ensure_dir_exists,
    save_json,
    format_card_info,
    extract_card_data,
    print_success,
    print_error,
    print_warning
)
from app_session import SessionMixin
from app_processing import ProcessingMixin


class MangaBuffApp(SessionMixin, ProcessingMixin):
    """Главное приложение MangaBuff v2.8.1."""

    MAX_FAILED_CYCLES = 3
    ALLOWED_RANKS = {"E", "D", "C"}  # Только эти ранги берём в работу

    def __init__(self, args):
        self.args = args
        self.session = None
        self.monitor = None
        self.history_monitor = None
        self.output_dir = OUTPUT_DIR
        self.inventory_manager = InventoryManager(self.output_dir)
        self.stats_manager = None
        self.processor = None
        self.proxy_manager = None
        self.rate_limiter = get_rate_limiter()
        self.replace_requested = False
        self.failed_cycles_count = 0
        self.logger = get_logger()
        # Функции передаются в processor через init_processor
        self.select_card_func = select_trade_card
        self.send_trade_func = send_trade_to_owner

    # -------------------------------------------------------------------------
    # Инициализация
    # -------------------------------------------------------------------------

    def setup(self) -> bool:
        self.logger.info("=" * 70)
        self.logger.info("Инициализация приложения MangaBuff v2.8.1")
        self.logger.info("=" * 70)

        ensure_dir_exists(self.output_dir)
        self.proxy_manager = create_proxy_manager(proxy_url=self.args.proxy)
        self.logger.info(f"Rate Limiting: {self.rate_limiter.max_requests} req/min")
        print(f"⏱️  Rate Limiting: {self.rate_limiter.max_requests} req/min")

        self.logger.info("Вход в аккаунт...")
        print("\n🔐 Вход в аккаунт...")
        self.session = login(self.args.email, self.args.password, self.proxy_manager)

        if not self.session:
            self.logger.error("Ошибка авторизации")
            print_error("Ошибка авторизации")
            return False

        self.logger.info("Авторизация успешна")
        print_success("Авторизация успешна\n")
        return True

    def init_stats_manager(self) -> bool:
        if not self.args.boost_url:
            self.logger.warning("URL буста не указан")
            print_warning("URL буста не указан")
            return False

        extra = getattr(self.args, 'extra_donations', 0)

        self.logger.info("Инициализация менеджера статистики...")
        print("📊 Инициализация менеджера статистики...")
        self.stats_manager = create_stats_manager(
            self.session,
            self.args.boost_url,
            extra_donations=extra
        )
        if extra > 0:
            self.logger.info(f"Дополнительные вклады: +{extra} сверх лимита сайта")
            print(f"➕ Дополнительные вклады: +{extra} сверх лимита сайта")
        self.stats_manager.print_stats(force_refresh=True)
        return True

    def init_history_monitor(self) -> bool:
        self.logger.info("Инициализация монитора истории обменов...")
        print("📊 Инициализация монитора истории обменов...")

        self.history_monitor = TradeHistoryMonitor(
            session=self.session,
            user_id=int(self.args.user_id),
            inventory_manager=self.inventory_manager,
            debug=self.args.debug
        )
        self.history_monitor.start(check_interval=HISTORY_CHECK_INTERVAL)

        self.logger.info(f"Монитор истории запущен (проверка каждые {HISTORY_CHECK_INTERVAL}с)")
        print_success(f"Монитор истории запущен (проверка каждые {HISTORY_CHECK_INTERVAL}с)\n")
        return True

    def init_processor(self) -> None:
        if not self.processor:
            self.logger.debug("Инициализация OwnersProcessor")
            self.processor = OwnersProcessor(
                session=self.session,
                select_card_func=self.select_card_func,
                send_trade_func=self.send_trade_func,
                dry_run=self.args.dry_run,
                debug=self.args.debug
            )

    # -------------------------------------------------------------------------
    # Загрузка данных
    # -------------------------------------------------------------------------

    def load_inventory(self) -> Optional[list]:
        if self.args.skip_inventory:
            self.logger.info("Пропуск загрузки инвентаря (--skip_inventory)")
            return []

        print(f"   🔍 Фильтрация: только ранги E, D, C | без заблокированных карт")
        raw_inventory = get_user_inventory(self.session, self.args.user_id)
        self.logger.info(f"Загружено карточек с сервера (без фильтра): {len(raw_inventory)}")

        # ── Реальная фильтрация по рангу ─────────────────────────────────────
        inventory = []
        skipped_ranks = {}
        for card in raw_inventory:
            card_data = extract_card_data(card)
            if card_data and card_data["rank"] in self.ALLOWED_RANKS:
                inventory.append(card)
            else:
                rank = card_data["rank"] if card_data else "?"
                skipped_ranks[rank] = skipped_ranks.get(rank, 0) + 1

        skipped_total = len(raw_inventory) - len(inventory)
        self.logger.info(
            f"После фильтрации: {len(inventory)} карт. "
            f"Пропущено {skipped_total}: {skipped_ranks}"
        )
        if skipped_ranks:
            print(f"   ⏭️  Пропущено рангов: {skipped_ranks}")
        # ─────────────────────────────────────────────────────────────────────

        print_success(f"После фильтрации: {len(inventory)} карточек")

        self.logger.info(f"Загружено карточек: {len(inventory)}")
        print_success(f"Всего загружено: {len(inventory)} карточек")

        if self.inventory_manager.save_inventory(inventory):
            self.logger.debug("Инвентарь сохранен в файл")
            print(f"💾 Инвентарь сохранен")

        self.logger.info("Синхронизация инвентаря с пропарсенными данными...")
        print(f"\n🔄 Синхронизация инвентаря с пропарсенными данными...")
        if self.inventory_manager.sync_inventories():
            self.logger.info("Синхронизация завершена успешно")
            print_success("Синхронизация завершена\n")
        else:
            self.logger.warning("Ошибка синхронизации инвентаря")
            print_warning("Ошибка синхронизации инвентаря\n")

        return inventory

    def load_boost_card(self) -> Optional[dict]:
        if not self.args.boost_url:
            self.logger.warning("URL буста не указан")
            return None

        self.logger.info("Загрузка информации о буст-карте...")
        boost_card = get_boost_card_info(self.session, self.args.boost_url)

        if not boost_card:
            self.logger.error("Не удалось получить карту для буста")
            print_error("Не удалось получить карту для буста")
            return None

        self.logger.info(
            f"Буст-карта загружена: {boost_card.get('name')} "
            f"(ID: {boost_card.get('card_id')})"
        )
        print_success("Карточка для вклада:")
        print(f"   {format_card_info(boost_card)}")

        self.logger.info("ПРОВЕРКА АВТОЗАМЕНЫ ПРИ ЗАГРУЗКЕ КАРТЫ")
        self.logger.info(
            f"Владельцев: {boost_card.get('owners_count')}, "
            f"Желающих: {boost_card.get('wanters_count')}"
        )

        new_card = check_and_replace_if_needed(
            self.session,
            self.args.boost_url,
            boost_card,
            self.stats_manager
        )

        if new_card:
            self.logger.info(f"Карта заменена на: {new_card.get('name')} (ID: {new_card.get('card_id')})")
            boost_card = new_card

        save_json(f"{self.output_dir}/{BOOST_CARD_FILE}", boost_card)
        self.logger.debug(f"Буст-карта сохранена в {BOOST_CARD_FILE}")
        print(f"💾 Карточка сохранена\n")

        return boost_card

    # -------------------------------------------------------------------------
    # Мониторинг и запуск
    # -------------------------------------------------------------------------

    def start_monitoring(self, boost_card: dict):
        if not self.args.enable_monitor:
            self.logger.debug("Мониторинг отключен (--enable_monitor не указан)")
            return

        self.logger.info(f"Запуск монитора буста для карты ID: {boost_card['card_id']}...")
        print(f"🔔 Запуск монитора буста (card_id={boost_card['card_id']})...")

        self.monitor = start_boost_monitor(
            self.session,
            self.args.boost_url,
            self.stats_manager,
            self.output_dir,
            current_card_id=boost_card['card_id']
        )
        self.logger.info(f"Монитор запущен для карты ID: {boost_card['card_id']}")

    def wait_for_monitor(self):
        if not self.monitor or not self.monitor.is_running():
            return

        try:
            self.logger.info("Мониторинг активен. Ожидание завершения...")
            from utils import print_section
            print_section("Мониторинг активен. Ctrl+C для выхода", char="=")

            import time
            while self.monitor.is_running():
                time.sleep(1)

        except KeyboardInterrupt:
            self.logger.info("Прерывание пользователем")
            print("\n\n⚠️  Прерывание...")
            self.monitor.stop()
            if self.history_monitor:
                self.history_monitor.stop()

    # -------------------------------------------------------------------------
    # Главный метод запуска
    # -------------------------------------------------------------------------

    def run(self) -> int:
        try:
            if not self.setup():
                return 1

            if self.args.boost_url:
                if not self.init_stats_manager():
                    self.logger.warning("Работа без статистики")
                    print_warning("Работа без статистики")

            if not self.args.skip_inventory:
                self.init_history_monitor()

            self.load_inventory()
            boost_card = self.load_boost_card()

            if not boost_card:
                return 0

            self.start_monitoring(boost_card)

            if not self.args.only_list_owners:
                self.run_processing_mode(boost_card)

            self.wait_for_monitor()

            if self.history_monitor:
                self.history_monitor.stop()

            return 0

        except Exception as e:
            self.logger.exception("Критическая ошибка в run()")
            raise