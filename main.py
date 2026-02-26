import argparse
import sys
import time
import os
from typing import Optional

from config import (
    OUTPUT_DIR,
    BOOST_CARD_FILE,
    WAIT_AFTER_ALL_OWNERS,
    WAIT_CHECK_INTERVAL,
    HISTORY_CHECK_INTERVAL
)
from logger import setup_logging, get_logger
from auth import login, logout, is_authenticated, refresh_session_token
from inventory import get_user_inventory, InventoryManager
from boost import get_boost_card_info
from card_selector import select_trade_card
from owners_parser import process_owners_page_by_page, OwnersProcessor
from monitor import (
    start_boost_monitor,
    MONITOR_CHECK_INTERVAL
)
from trade import (
    send_trade_to_owner,
    cancel_all_sent_trades,
    TradeHistoryMonitor
)
from card_replacement import check_and_replace_if_needed, force_replace_card
from daily_stats import create_stats_manager
from proxy_manager import create_proxy_manager
from rate_limiter import get_rate_limiter
from utils import (
    ensure_dir_exists,
    save_json,
    load_json,
    format_card_info,
    print_section,
    print_success,
    print_error,
    print_warning,
    print_info
)

class MangaBuffApp:
    """Главное приложение MangaBuff v2.8.1 - ИСПРАВЛЕНА ПРОБЛЕМА СО СМЕНОЙ КАРТЫ."""
    
    MAX_FAILED_CYCLES = 3
    
    def __init__(self, args: argparse.Namespace):
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
    
    def setup(self) -> bool:
        self.logger.info("=" * 70)
        self.logger.info("Инициализация приложения MangaBuff v2.8.1")
        self.logger.info("=" * 70)
        
        ensure_dir_exists(self.output_dir)
        self.logger.debug(f"Output directory: {self.output_dir}")
        
        # Упрощенная инициализация прокси (только URL из аргумента или config)
        self.proxy_manager = create_proxy_manager(proxy_url=self.args.proxy)
        self.logger.info(f"Rate Limiting: {self.rate_limiter.max_requests} req/min")
        
        print(f"⏱️  Rate Limiting: {self.rate_limiter.max_requests} req/min")
        
        self.logger.info("Вход в аккаунт...")
        print("\n🔐 Вход в аккаунт...")
        self.session = login(
            self.args.email,
            self.args.password,
            self.proxy_manager
        )
        
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
        
        self.logger.info("Инициализация менеджера статистики...")
        print("📊 Инициализация менеджера статистики...")
        self.stats_manager = create_stats_manager(
            self.session,
            self.args.boost_url
        )
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
                select_card_func=select_trade_card,
                send_trade_func=send_trade_to_owner,
                dry_run=self.args.dry_run,
                debug=self.args.debug
            )
    
    def load_inventory(self) -> Optional[list]:
        if self.args.skip_inventory:
            self.logger.info("Пропуск загрузки инвентаря (--skip_inventory)")
            return []
        
        print(f"   🔍 Фильтрация: только ранги E, D, C | без заблокированных карт")
        inventory = get_user_inventory(self.session, self.args.user_id)  # уже отфильтрован
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
        
        self.logger.info(f"Буст-карта загружена: {boost_card.get('name')} (ID: {boost_card.get('card_id')})")
        print_success("Карточка для вклада:")
        print(f"   {format_card_info(boost_card)}")
        
        # 🔧 ДОБАВЛЯЕМ ЛОГИРОВАНИЕ ПЕРЕД ПРОВЕРКОЙ
        self.logger.info("="*70)
        self.logger.info("ПРОВЕРКА АВТОЗАМЕНЫ ПРИ ЗАГРУЗКЕ КАРТЫ")
        self.logger.info(f"Карта: {boost_card.get('name')} (ID: {boost_card.get('card_id')})")
        self.logger.info(f"Владельцев: {boost_card.get('owners_count')}, Желающих: {boost_card.get('wanters_count')}")
        
        # Проверяем новые условия автозамены
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
    
    def start_monitoring(self, boost_card: dict):
        if not self.args.enable_monitor:
            self.logger.debug("Мониторинг отключен (--enable_monitor не указан)")
            return
        
        self.logger.info("Запуск монитора буста...")
        self.monitor = start_boost_monitor(
            self.session,
            self.args.boost_url,
            self.stats_manager,
            self.output_dir
        )
        
        self.monitor.current_card_id = boost_card['card_id']
        self.logger.info(f"Монитор запущен для карты ID: {boost_card['card_id']}")
    
    def recreate_all_objects(self) -> bool:
        """
        🔧 НОВОЕ: Универсальный метод пересоздания всех объектов.
        """
        try:
            self.logger.info("=" * 70)
            self.logger.info("ПЕРЕСОЗДАНИЕ ВСЕХ ОБЪЕКТОВ С НОВОЙ СЕССИЕЙ")
            
            # 1. Менеджер статистики
            if self.args.boost_url:
                print("📊 Пересоздание менеджера статистики...")
                self.stats_manager = create_stats_manager(self.session, self.args.boost_url)
                self.stats_manager.print_stats(force_refresh=True)
            
            # 2. Монитор истории
            if not self.args.skip_inventory:
                print("📊 Пересоздание монитора истории...")
                if self.history_monitor and self.history_monitor.running:
                    self.history_monitor.stop()
                self.init_history_monitor()
            
            # 3. Процессор
            print("🔄 Пересоздание процессора...")
            self.processor = OwnersProcessor(
                session=self.session,
                select_card_func=select_trade_card,
                send_trade_func=send_trade_to_owner,
                dry_run=self.args.dry_run,
                debug=self.args.debug
            )
            
            # 4. Монитор буста
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
        """🔧 НОВОЕ: Проверяет валидность сессии."""
        if not is_authenticated(self.session):
            print_error("❌ Сессия истекла!")
            
            # Попытка обновить токен
            if refresh_session_token(self.session):
                if is_authenticated(self.session):
                    print_success("✅ Сессия восстановлена")
                    return True
            
            # Полный повторный вход
            print_warning("Повторный вход...")
            self.session = login(self.args.email, self.args.password, self.proxy_manager)
            
            if not self.session:
                return False
            
            return self.recreate_all_objects()
        
        return True

    def wait_for_boost_or_timeout(
        self,
        card_id: int,
        timeout: int = WAIT_AFTER_ALL_OWNERS
    ) -> bool:
        """
        Ожидает буст или таймаут с активным мониторингом страницы вклада.
        """
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
    
    def sleep_until_reset(self) -> bool:
        """
        Режим сна до смены суток (00:00 MSK).
        
        Returns:
            True если успешно дождались и вошли заново
        """
        self.logger.info("Переход в режим сна (лимиты исчерпаны)")
        print_section("💤 РЕЖИМ СНА", char="=")
        print("   ⛔ Вклады на сегодня исчерпаны")
        print("   💤 Выход из аккаунта и ожидание смены суток...\n")
        
        # Отменяем обмены перед выходом
        if not self.args.dry_run and self.processor and self.processor.trade_manager:
            self.logger.info("Отмена всех обменов перед выходом...")
            print("🔄 Отменяем все обмены перед выходом...")
            success = cancel_all_sent_trades(
                self.session,
                self.processor.trade_manager,
                self.history_monitor,
                self.args.debug
            )
            if success:
                self.logger.info("Обмены успешно отменены")
                print_success("✅ Обмены отменены\n")
        
        # Останавливаем мониторы
        if self.monitor and self.monitor.is_running():
            self.logger.info("Остановка монитора буста...")
            print("🛑 Остановка монитора буста...")
            self.monitor.stop()
            self.monitor = None  # 🔧 НОВОЕ: Очищаем ссылку
        
        if self.history_monitor and self.history_monitor.running:
            self.logger.info("Остановка монитора истории...")
            print("🛑 Остановка монитора истории...")
            self.history_monitor.stop()
            self.history_monitor = None  # 🔧 НОВОЕ: Очищаем ссылку
        
        # Выход из аккаунта
        self.logger.info("Выход из аккаунта...")
        print("\n🚪 Выход из аккаунта...")
        logout_success = logout(self.session)
        if logout_success:
            self.logger.info("Выход выполнен успешно")
            print_success("✅ Выход выполнен\n")
        else:
            self.logger.warning("Ошибка при выходе из аккаунта")
            print_warning("⚠️  Ошибка выхода, но продолжаем...\n")
        
        # Вычисляем время до сброса
        if not self.stats_manager:
            self.logger.error("Нет менеджера статистики!")
            print_error("Нет менеджера статистики!")
            return False
        
        seconds_until_reset = self.stats_manager._seconds_until_reset()
        reset_time_formatted = self.stats_manager._format_time_until_reset()
        
        self.logger.info(f"Время до сброса лимитов: {reset_time_formatted}")
        print(f"⏰ Сброс лимитов через: {reset_time_formatted}")
        print(f"💤 Переход в режим ожидания...")
        print("   Нажмите Ctrl+C для завершения\n")
        
        # Ожидание с периодическими обновлениями
        check_interval = 60  # Проверяем каждую минуту
        elapsed = 0
        
        while elapsed < seconds_until_reset:
            remaining = seconds_until_reset - elapsed
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            
            if minutes % 10 == 0 or remaining < 600:  # Выводим каждые 10 минут или в последние 10 минут
                self.logger.debug(f"Режим сна: осталось {hours}ч {minutes}м")
                print(f"💤 Режим сна: осталось {hours}ч {minutes}м до сброса")
            
            sleep_time = min(check_interval, remaining)
            time.sleep(sleep_time)
            elapsed += sleep_time
        
        # ═══════════════════════════════════════════════════════════════════
        # 🔧 КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ НАЧИНАЕТСЯ ЗДЕСЬ
        # ═══════════════════════════════════════════════════════════════════
        
        self.logger.info("=" * 70)
        self.logger.info("СМЕНА СУТОК - ПОВТОРНЫЙ ВХОД")
        self.logger.info("=" * 70)
        print_success("\n✅ Смена суток! Повторный вход в аккаунт...")
        
        # Повторный вход
        self.session = login(
            self.args.email,
            self.args.password,
            self.proxy_manager
        )
        
        if not self.session:
            self.logger.error("❌ Не удалось войти в аккаунт после режима сна")
            print_error("❌ Не удалось войти в аккаунт!")
            return False
        
        self.logger.info("✅ Авторизация после режима сна успешна")
        print_success("✅ Авторизация успешна!")
        
        # 🔧 КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: ВСЕГДА пересоздаем ВСЕ объекты
        self.logger.info("Пересоздание всех объектов после режима сна...")
        print("\n" + "=" * 70)
        print("ПЕРЕСОЗДАНИЕ ВСЕХ ОБЪЕКТОВ С НОВОЙ СЕССИЕЙ")
        print("=" * 70 + "\n")
        
        if not self.recreate_all_objects():
            self.logger.error("❌ Не удалось пересоздать объекты после сна")
            print_error("❌ Ошибка пересоздания объектов")
            return False
        
        # Сбрасываем счетчик неудачных циклов
        self.failed_cycles_count = 0
        self.logger.info("Счетчик неудачных циклов сброшен")
        
        self.logger.info("=" * 70)
        self.logger.info("✅ СИСТЕМА ПОЛНОСТЬЮ ПЕРЕЗАПУЩЕНА")
        self.logger.info("=" * 70)
        print_success("✅ Система полностью перезапущена!\n")
        
        return True
    
    def attempt_auto_replacement(self, current_boost_card: dict, reason: str = "АВТОЗАМЕНА ПОСЛЕ 3 НЕУДАЧНЫХ ЦИКЛОВ") -> Optional[dict]:
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
        else:
            self.logger.warning("Замена карты не удалась")
            print_warning("❌ Замена не удалась\n")
            return None
    
        # ============================================================================
        # 🔧 ИСПРАВЛЕНИЕ КРИТИЧЕСКОЙ ОШИБКИ: Смена карты
        # ============================================================================
    # ИЗМЕНЕНИЯ В run_processing_mode - добавить сразу после process_owners_page_by_page

    def run_processing_mode(self, boost_card: dict):
        self.init_processor()
        self.logger.info("Запуск режима обработки владельцев")
        
        current_boost_card = boost_card
        
        while True:
            # Проверяем лимит вкладов
            if not self.stats_manager.can_donate(force_refresh=True):
                self.logger.warning("Лимит вкладов достигнут")
                print_warning("\n⛔ Лимит вкладов достигнут!")
                
                sleep_success = self.sleep_until_reset()
                
                if not sleep_success:
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
            
            current_card_id = current_boost_card['card_id']
            
            if self.failed_cycles_count >= self.MAX_FAILED_CYCLES:
                self.logger.warning(f"Достигнуто {self.MAX_FAILED_CYCLES} неудачных циклов")
                print_warning(f"\n⚠️  Достигнуто {self.MAX_FAILED_CYCLES} неудачных ПОЛНЫХ циклов!")
                
                new_card = self.attempt_auto_replacement(
                    current_boost_card,
                    reason="АВТОЗАМЕНА ПОСЛЕ 3 НЕУДАЧНЫХ ЦИКЛОВ"
                )
                
                if new_card:
                    current_boost_card = new_card
                    current_card_id = new_card['card_id']
                    
                    save_json(f"{self.output_dir}/{BOOST_CARD_FILE}", new_card)
                    self.logger.info(f"✅ Новая карта сохранена в {BOOST_CARD_FILE}")
                    print(f"💾 Новая карта ID={new_card['card_id']} сохранена в файл")
                    
                    if self.monitor:
                        self.monitor.current_card_id = current_card_id
                    
                    self.processor.reset_state()
                    continue
                else:
                    self.failed_cycles_count = 0
                    self.logger.info("Продолжаем работу с текущей картой")
                    print_info("ℹ️  Продолжаем работу с текущей картой")

            # Проверяем новые условия автозамены
            self.logger.info("="*70)
            self.logger.info("ПРОВЕРКА АВТОЗАМЕНЫ В ЦИКЛЕ")
            self.logger.info(f"Карта: {current_boost_card.get('name')} (ID: {current_boost_card.get('card_id')})")
            self.logger.info(f"Владельцев: {current_boost_card.get('owners_count')}, Желающих: {current_boost_card.get('wanters_count')}")

            new_card = check_and_replace_if_needed(
                self.session,
                self.args.boost_url,
                current_boost_card,
                self.stats_manager
            )

            if new_card:
                self.logger.info(f"Карта заменена автоматически: {new_card.get('card_id')}")
                
                current_boost_card = new_card
                current_card_id = new_card['card_id']
                
                save_json(f"{self.output_dir}/{BOOST_CARD_FILE}", new_card)
                self.logger.info(f"✅ Новая карта сохранена в {BOOST_CARD_FILE}")
                print(f"💾 Новая карта ID={new_card['card_id']} сохранена в файл")
                
                if self.monitor:
                    self.monitor.current_card_id = current_card_id
                
                self.processor.reset_state()
                self.failed_cycles_count = 0
            
            if self.monitor:
                self.monitor.card_changed = False
            
            self.logger.info(f"Обработка карты: {current_boost_card['name']} (ID: {current_card_id})")
            print(f"\n🎯 Обработка: {current_boost_card['name']} (ID: {current_card_id})")
            
            current_rate = self.rate_limiter.get_current_rate()
            self.logger.debug(f"Текущий rate: {current_rate}/{self.rate_limiter.max_requests}")
            print(f"📊 Текущий rate: {current_rate}/{self.rate_limiter.max_requests} req/min\n")
            
            if not self.stats_manager.can_donate(force_refresh=True):
                self.logger.warning("Лимит вкладов достигнут во время обработки")
                print_warning("⛔ Лимит вкладов достигнут!")
                continue
            
            boost_happened_this_cycle = False
            
            self.logger.info(f"Начало обработки владельцев карты {current_card_id}")
            total = process_owners_page_by_page(
                session=self.session,
                card_id=str(current_card_id),
                boost_card=current_boost_card,
                output_dir=self.output_dir,
                select_card_func=select_trade_card,
                send_trade_func=send_trade_to_owner,
                monitor_obj=self.monitor,
                processor=self.processor,
                dry_run=self.args.dry_run,
                debug=self.args.debug
            )
            
            # ====================================================================
            # 🔧 КРИТИЧЕСКОЕ ДОБАВЛЕНИЕ: Проверка прерывания СРАЗУ после обработки
            # ====================================================================
            if self.monitor and self.monitor.should_interrupt():
                reason = self.monitor.get_interrupt_reason()
                self.logger.info(f"⚡ Обнаружено прерывание: {reason}")
                print(f"\n⚡ ОБНАРУЖЕНО ПРЕРЫВАНИЕ: {reason}!")
                
                if self.monitor.boost_available:
                    self.logger.info("🎁 Буст был внесён во время обработки!")
                    print("🎁 Буст был внесён во время обработки владельцев!")
                    print("💎 Монитор уже внёс карту, загружаем новую...\n")
                    boost_happened_this_cycle = True
                
                if self.monitor.card_changed:
                    self.logger.info("🔄 Карта изменилась во время обработки!")
                    print("🔄 Карта изменилась во время обработки владельцев!\n")
                
                # Загружаем новую карту
                self.logger.info("📥 Загрузка новой карты после прерывания...")
                print("📥 Загрузка новой карты...")
                current_boost_card = self.load_boost_card()
                
                if not current_boost_card:
                    self.logger.error("❌ Не удалось загрузить карту после прерывания")
                    print_error("❌ Не удалось загрузить карту!")
                    break
                
                # КРИТИЧЕСКИ ВАЖНО: Сбрасываем флаги!
                self.logger.info("🚩 Сброс флагов прерывания...")
                self.monitor.reset_interruption_flags()
                
                # Сбрасываем состояние процессора
                self.processor.reset_state()
                self.failed_cycles_count = 0
                
                self.logger.info("🔄 Перезапуск обработки с новой картой")
                print_success(f"✅ Новая карта загружена: {current_boost_card['name']} (ID: {current_boost_card['card_id']})")
                print("🔄 Перезапуск обработки...\n")
                
                time.sleep(1)
                continue
            
            # Обычная обработка результатов
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
            
            # Проверка флага card_changed от старой логики (совместимость)
            if self._should_restart():
                boost_happened_this_cycle = True
                self.processor.reset_state()
                self.failed_cycles_count = 0
                self.logger.info("Буст произошел - перезапуск с новой картой")
                print_success("✅ Буст произошел - счетчик неудачных циклов сброшен")
                self._prepare_restart()
                
                self.logger.info("Загрузка новой карты после буста...")
                current_boost_card = self.load_boost_card()
                if not current_boost_card:
                    self.logger.error("Не удалось загрузить карту после буста")
                    print_error("❌ Не удалось загрузить карту после буста")
                    break
                
                time.sleep(1)
                continue
            
            if self.monitor and self.monitor.is_running() and total > 0:
                boost_occurred = self.wait_for_boost_or_timeout(current_card_id)
                
                if boost_occurred:
                    boost_happened_this_cycle = True
                    self.processor.reset_state()
                    self.failed_cycles_count = 0
                    self.logger.info("Буст произошел во время ожидания")
                    print_success("✅ Буст произошел - счетчик неудачных циклов сброшен")
                    self._prepare_restart()
                    
                    self.logger.info("Загрузка новой карты после буста...")
                    current_boost_card = self.load_boost_card()
                    if not current_boost_card:
                        self.logger.error("Не удалось загрузить карту после буста")
                        print_error("❌ Не удалось загрузить карту после буста")
                        break
                    
                    time.sleep(1)
                    continue
                else:
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
                    
                    if not boost_happened_this_cycle:
                        self.failed_cycles_count += 1
                        self.logger.warning(f"Неудачный цикл #{self.failed_cycles_count}/{self.MAX_FAILED_CYCLES}")
                        print_warning(
                            f"⚠️  ПОЛНЫЙ цикл #{self.failed_cycles_count}/{self.MAX_FAILED_CYCLES} "
                            f"завершен БЕЗ вклада (таймаут ожидания)"
                        )
                    
                    print_section("🔄 ПЕРЕЗАПУСК с той же картой", char="=")
                    time.sleep(1)
                    continue
            
            if total == 0:
                self.failed_cycles_count += 1
                self.logger.warning(f"Неудачный цикл #{self.failed_cycles_count}/{self.MAX_FAILED_CYCLES} (нет владельцев)")
                print_warning(
                    f"⚠️  ПОЛНЫЙ цикл #{self.failed_cycles_count}/{self.MAX_FAILED_CYCLES} "
                    f"завершен БЕЗ вклада (нет владельцев)"
                )
                print_section("🔄 ПЕРЕЗАПУСК с той же картой", char="=")
                time.sleep(1)
                continue

    # ЭТО НЕ ПОЛНЫЙ ФАЙЛ - ТОЛЬКО ИЗМЕНЁННЫЙ МЕТОД!
    # Вставьте этот метод в ваш main.py, заменив старый run_processing_mode
    
    def _load_current_boost_card(self, default: dict) -> dict:
        """
        УСТАРЕЛО: Этот метод больше не используется в run_processing_mode.
        Оставлен для совместимости с другими частями кода.
        """
        path = f"{self.output_dir}/{BOOST_CARD_FILE}"
        current = load_json(path, default=default)
        return current if current else default
    
    def _should_restart(self) -> bool:
        return (
            self.monitor and
            self.monitor.is_running() and
            self.monitor.card_changed
        )
    
    def _prepare_restart(self):
        self.logger.info("Подготовка к перезапуску с новой картой")
        print_section("🔄 ПЕРЕЗАПУСК с новой картой", char="=")
    
    def wait_for_monitor(self):
        if not self.monitor or not self.monitor.is_running():
            return
        
        try:
            self.logger.info("Мониторинг активен. Ожидание завершения...")
            print_section("Мониторинг активен. Ctrl+C для выхода", char="=")
            
            while self.monitor.is_running():
                time.sleep(1)
                
        except KeyboardInterrupt:
            self.logger.info("Прерывание пользователем")
            print("\n\n⚠️  Прерывание...")
            self.monitor.stop()
            if self.history_monitor:
                self.history_monitor.stop()
    
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
            
            inventory = self.load_inventory()
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


def create_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MangaBuff v2.8.1 - ИСПРАВЛЕНА ПРОБЛЕМА СО СМЕНОЙ КАРТЫ"
    )
    
    parser.add_argument("--email", required=True, help="Email")
    parser.add_argument("--password", required=True, help="Пароль")
    parser.add_argument("--user_id", required=True, help="ID пользователя")
    parser.add_argument("--boost_url", help="URL буста")
    
    # Упрощенная работа с прокси - только URL (используется config.PROXY_URL по умолчанию)
    parser.add_argument("--proxy", help="URL прокси (опционально, используется из config)")
    
    parser.add_argument("--skip_inventory", action="store_true", help="Пропустить инвентарь")
    parser.add_argument("--only_list_owners", action="store_true", help="Только список владельцев")
    parser.add_argument("--enable_monitor", action="store_true", help="Включить мониторинг")
    parser.add_argument("--dry_run", action="store_true", help="Тестовый режим")
    parser.add_argument("--debug", action="store_true", help="Отладка")
    
    # Новые аргументы для логирования
    parser.add_argument("--log_level", default="INFO", 
                       choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                       help="Уровень логирования")
    parser.add_argument("--no_console_log", action="store_true", 
                       help="Отключить вывод логов в консоль")
    
    return parser


def main():
    print("=" * 70)
    print("MangaBuff v2.8.1 - ИСПРАВЛЕНА ПРОБЛЕМА СО СМЕНОЙ КАРТЫ")
    print("=" * 70)
    print()
    
    parser = create_argument_parser()
    args = parser.parse_args()
    
    # Настройка логирования
    log_level = getattr(__import__('logging'), args.log_level)
    setup_logging(
        name="mangabuff",
        base_dir="logs",
        level=log_level,
        console_output=not args.no_console_log
    )
    
    logger = get_logger()
    logger.info("=" * 70)
    logger.info("MangaBuff v2.8.1 - Запуск приложения")
    logger.info("=" * 70)
    logger.info(f"Уровень логирования: {args.log_level}")
    logger.info(f"Console output: {not args.no_console_log}")
    logger.info(f"Debug mode: {args.debug}")
    logger.info(f"Dry run: {args.dry_run}")
    
    if args.debug:
        print("🔧 DEBUG MODE ENABLED")
        logger.debug("Debug mode enabled")
    
    app = MangaBuffApp(args)
    
    try:
        exit_code = app.run()
        if exit_code == 0:
            logger.info("Программа завершена успешно")
            print("\n✅ Программа завершена успешно")
        else:
            logger.error(f"Программа завершена с кодом ошибки: {exit_code}")
            print("\n❌ Программа завершена с ошибками")
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.warning("Прервано пользователем (Ctrl+C)")
        print("\n\n⚠️  Прервано пользователем")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()