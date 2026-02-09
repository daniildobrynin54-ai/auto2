"""Мониторинг страницы буста клуба."""

import os
import threading
import time
from typing import Optional
import requests
from bs4 import BeautifulSoup
import re
from config import (
    BASE_URL,
    REQUEST_TIMEOUT,
    OUTPUT_DIR,
    BOOST_CARD_FILE,
    MONITOR_CHECK_INTERVAL,
    MONITOR_STATUS_INTERVAL
)
from boost import get_boost_card_info, replace_club_card
from trade import cancel_all_sent_trades, TradeManager
from daily_stats import DailyStatsManager
from utils import save_json, load_json, print_section, print_success, print_warning

class BoostMonitor:
    """Монитор страницы буста клуба с легковесной проверкой."""
    
    def __init__(
        self,
        session: requests.Session,
        club_url: str,
        stats_manager: DailyStatsManager,
        output_dir: str = OUTPUT_DIR
    ):
        self.session = session
        self.club_url = club_url
        self.output_dir = output_dir
        self.stats_manager = stats_manager
        self.running = False
        self.thread = None
        self.boost_available = False
        self.card_changed = False
        self.current_card_id = None
        self.trade_manager = TradeManager(session, debug=False)
        # 🔧 НОВОЕ: Флаг для паузы мониторинга
        self.monitoring_paused = False
    
    def get_current_card_id(self) -> Optional[int]:
        """
        Легковесная проверка - извлекает только card_id со страницы буста.
        
        Returns:
            card_id или None при ошибке
        """
        try:
            response = self.session.get(self.club_url, timeout=REQUEST_TIMEOUT)
            
            if response.status_code != 200:
                return None
            
            soup = BeautifulSoup(response.text, "html.parser")
            
            # Ищем ссылку на карту
            card_link = soup.select_one('a.button.button--block[href*="/cards/"]')
            
            if not card_link:
                return None
            
            href = card_link.get("href", "")
            match = re.search(r"/cards/(\d+)", href)
            
            if match:
                return int(match.group(1))
            
            return None
            
        except Exception as e:
            return None
    
    def check_boost_available(self) -> Optional[str]:
        """Проверяет доступность кнопки пожертвования."""
        try:
            response = self.session.get(self.club_url, timeout=REQUEST_TIMEOUT)
            
            if response.status_code != 200:
                return None
            
            soup = BeautifulSoup(response.text, "html.parser")
            boost_button = self._find_boost_button(soup)
            
            if not boost_button:
                return None
            
            href = boost_button.get('href')
            if href:
                if not href.startswith('http'):
                    return f"{BASE_URL}{href}"
                return href
            
            return self.club_url
            
        except requests.RequestException as e:
            return None
    
    def check_card_changed_lightweight(self) -> Optional[int]:
        """
        Легковесная проверка смены карты - только card_id.
        
        Returns:
            Новый card_id если карта изменилась, иначе None
        """
        if not self.current_card_id:
            return None
        
        new_card_id = self.get_current_card_id()
        
        if new_card_id and new_card_id != self.current_card_id:
            return new_card_id
        
        return None
    
    def _find_boost_button(self, soup: BeautifulSoup):
        """Находит кнопку буста на странице."""
        boost_button = soup.select_one('.club_boost-btn, .club-boost-btn')
        if boost_button:
            return boost_button
        
        for tag in ['button', 'a']:
            boost_button = soup.find(
                tag,
                string=lambda text: text and 'Пожертвовать карту' in text
            )
            if boost_button:
                return boost_button
        
        for elem in soup.find_all(['a', 'button']):
            text = elem.get_text(strip=True)
            if 'Пожертвовать' in text or 'пожертвовать' in text:
                return elem
        
        return None
    
    def contribute_card(self, boost_url: str) -> bool:
        """
        🔧 ОПТИМИЗИРОВАНО: Внесение карты с правильной последовательностью.
        
        Порядок действий:
        1. Получаем instance_id карты (БЫСТРО, пока обмены активны)
        2. Отменяем все обмены
        3. Проверяем лимит
        4. СРАЗУ вносим карту
        5. Обрабатываем результат
        """
        try:
            # ═══════════════════════════════════════════════════════════
            # 1️⃣ СНАЧАЛА получаем instance_id (пока обмены еще активны)
            # ═══════════════════════════════════════════════════════════
            current_boost_card = get_boost_card_info(self.session, boost_url)
            
            if not current_boost_card:
                print_warning("Не удалось получить информацию о карте для буста")
                return False
            
            instance_id = current_boost_card.get('id', 0)
            current_card_id = current_boost_card.get('card_id', 0)
            
            if not instance_id:
                print_warning("Не удалось получить instance_id карты")
                return False
            
            # ═══════════════════════════════════════════════════════════
            # 2️⃣ ТЕПЕРЬ отменяем обмены (уже знаем instance_id)
            # ═══════════════════════════════════════════════════════════
            print("🔄 Отменяем все обмены перед внесением карты...")
            self._cancel_pending_trades()
            time.sleep(0.5)  # Минимальная задержка
            
            # ═══════════════════════════════════════════════════════════
            # 3️⃣ Проверяем лимит (один раз)
            # ═══════════════════════════════════════════════════════════
            if not self.stats_manager.can_donate(force_refresh=True):
                print_warning(f"⛔ Достигнут дневной лимит пожертвований!")
                self.stats_manager.print_stats()
                return False
            
            self._print_card_info(current_boost_card, instance_id, is_new=False)
            
            # ═══════════════════════════════════════════════════════════
            # 4️⃣ СРАЗУ вносим карту (без повторной проверки лимита)
            # ═══════════════════════════════════════════════════════════
            success = self._send_contribute_request(boost_url, instance_id)
            
            if not success:
                print_warning(f"Ошибка внесения карты")
                return False
            
            print_success("✅ Карта успешно внесена в клуб!")
            
            # ═══════════════════════════════════════════════════════════
            # 5️⃣ Обработка результата
            # ═══════════════════════════════════════════════════════════
            print("\n⏳ Ожидание обновления данных (3 сек)...")
            time.sleep(3)
            
            print("🔄 Загружаем информацию о новой карте...")
            new_boost_card = get_boost_card_info(self.session, boost_url)
            
            if not new_boost_card:
                print_warning("Не удалось получить информацию о новой карте")
                self.stats_manager.refresh_stats()
                return False
            
            new_card_id = new_boost_card.get('card_id', 0)
            new_instance_id = new_boost_card.get('id', 0)
            
            if new_card_id != current_card_id:
                print_success(f"✅ Обнаружена новая карта!")
                print(f"   Старая карта ID: {current_card_id}")
                print(f"   Новая карта ID: {new_card_id}\n")
                
                # Отменяем обмены ПОСЛЕ обнаружения новой карты
                print("🔄 Отменяем обмены на старую карту...")
                self._cancel_pending_trades()
                time.sleep(1)
                
                self._print_card_info(new_boost_card, new_instance_id, is_new=True)
                self._save_boost_card(new_boost_card)
                self.current_card_id = new_card_id
                self.card_changed = True
                
                print("🔄 Флаг изменения карты подтвержден. Ожидаем перезапуска обработки...\n")
            else:
                print_warning(f"⚠️  Карта не изменилась (ID: {current_card_id})")
                print("   Возможно, буст закончился или карта та же самая\n")
                self.current_card_id = current_card_id
            
            self.stats_manager.refresh_stats()
            self.stats_manager.print_stats()
            
            return True
            
        except Exception as e:
            print_warning(f"⚠️  Ошибка при внесении карты: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def handle_card_change_without_boost(self, new_card_id: int) -> bool:
        """Обрабатывает изменение карты в клубе без буста."""
        try:
            timestamp = time.strftime('%H:%M:%S')
            print(f"\n🔄 [{timestamp}] КАРТА В КЛУБЕ ИЗМЕНИЛАСЬ!")
            print(f"   Старая карта ID: {self.current_card_id}")
            print(f"   Новая карта ID: {new_card_id}\n")
            self._cancel_pending_trades()
            
            print("⏳ Ожидание обновления данных на сервере (2 сек)...")
            time.sleep(2)
            
            print("🔄 Загружаем информацию о новой карте...")
            new_boost_card = get_boost_card_info(self.session, self.club_url)
            
            if not new_boost_card:
                print_warning("Не удалось получить информацию о новой карте")
                return False
            
            new_instance_id = new_boost_card.get('id', 0)
            
            self._print_card_info(new_boost_card, new_instance_id, is_new=True)
            self._save_boost_card(new_boost_card)
            self.current_card_id = new_card_id
            self.card_changed = True
            
            print("🔄 Флаг изменения карты установлен. Перезапуск обработки...\n")
            return True
            
        except Exception as e:
            print_warning(f"Ошибка при обработке смены карты: {e}")
            return False
    
    def _save_boost_card(self, boost_card: dict) -> None:
        """Сохраняет информацию о буст-карте."""
        filepath = os.path.join(self.output_dir, BOOST_CARD_FILE)
        save_json(filepath, boost_card)
    
    def _print_card_info(self, boost_card: dict, instance_id: int, is_new: bool = False) -> None:
        """Выводит информацию о карте."""
        if is_new:
            print_section("🎁 НОВАЯ КАРТА ДЛЯ ВКЛАДА!")
        else:
            print_section("🎁 ОБНАРУЖЕНА ВОЗМОЖНОСТЬ ВНЕСТИ КАРТУ!")
        
        name = boost_card.get('name', '(не удалось получить)')
        card_id = boost_card.get('card_id', '?')
        rank = boost_card.get('rank', '(не удалось получить)')
        owners = boost_card.get('owners_count', '?')
        wanters = boost_card.get('wanters_count', '?')
        
        print(f"   Название: {name}")
        print(f"   ID карты: {card_id} | Instance ID: {instance_id} | Ранг: {rank}")
        print(f"   Владельцев: {owners} | Желающих: {wanters}")
        
        if is_new:
            filepath = os.path.join(self.output_dir, BOOST_CARD_FILE)
            print(f"💾 Новая карта сохранена в: {filepath}")
        print("=" * 60 + "\n")
    
    def _send_contribute_request(self, boost_url: str, instance_id: int) -> bool:
        """Отправляет запрос на внесение карты."""
        url = f"{BASE_URL}/clubs/boost"
        csrf_token = self.session.headers.get('X-CSRF-TOKEN', '')
        
        data = {
            "card_id": instance_id,
            "_token": csrf_token
        }
        
        headers = {
            "Referer": boost_url,
            "Origin": BASE_URL,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        
        try:
            response = self.session.post(
                url,
                data=data,
                headers=headers,
                timeout=REQUEST_TIMEOUT
            )
            
            return response.status_code == 200
            
        except requests.RequestException:
            return False
    
    def _cancel_pending_trades(self) -> None:
        """Отменяет все обмены через правильный метод."""
        print("🔄 Отменяем все отправленные обмены...")
        success = cancel_all_sent_trades(
            self.session,
            self.trade_manager,
            debug=False
        )
        
        if success:
            print_success("✅ Все отправленные обмены успешно отменены!")
        else:
            print_warning("⚠️  Не удалось отменить обмены (возможно, их не было)")
    
    def pause_monitoring(self) -> None:
        """🔧 НОВОЕ: Приостанавливает мониторинг (но поток продолжает работать)."""
        self.monitoring_paused = True
    
    def resume_monitoring(self) -> None:
        """🔧 НОВОЕ: Возобновляет мониторинг."""
        self.monitoring_paused = False
    
    def monitor_loop(self) -> None:
        """Основной цикл мониторинга."""
        print(f"\n🔄 Запущен мониторинг страницы: {self.club_url}")
        print(f"   Проверка каждые {MONITOR_CHECK_INTERVAL} секунд...")
        print("   Отслеживание: буст + смена карты в клубе")
        print("   Нажмите Ctrl+C для остановки\n")
        self.stats_manager.print_stats(force_refresh=True)
        
        check_count = 0
        
        while self.running:
            # 🔧 НОВОЕ: Пропускаем проверку если мониторинг на паузе
            if self.monitoring_paused:
                time.sleep(MONITOR_CHECK_INTERVAL)
                continue
            
            check_count += 1
            
            # Легковесная проверка смены карты
            new_card_id = self.check_card_changed_lightweight()
            if new_card_id:
                self.handle_card_change_without_boost(new_card_id)
                time.sleep(MONITOR_CHECK_INTERVAL)
                continue
            
            boost_url = self.check_boost_available()
            
            if boost_url:
                timestamp = time.strftime('%H:%M:%S')
                print(f"\n🎯 [{timestamp}] Проверка #{check_count}: БУСТ ДОСТУПЕН!")
                if self.stats_manager.can_donate(force_refresh=True):
                    success = self.contribute_card(boost_url)
                    
                    if success:
                        self.card_changed = True
                        self.boost_available = True
                        print("   ✅ Продолжаем мониторинг для следующего буста...")
                    else:
                        print("   ⚠️  Внесение не удалось, продолжаем мониторинг...")
                else:
                    print(f"⛔ Буст доступен, но достигнут лимит пожертвований!")
                    self.stats_manager.print_stats()
            else:
                # Только периодический вывод
                if check_count == 1 or check_count % MONITOR_STATUS_INTERVAL == 0:
                    timestamp = time.strftime('%H:%M:%S')
                    print(f"[{timestamp}] Проверка #{check_count}: буст не доступен")
            
            time.sleep(MONITOR_CHECK_INTERVAL)
    
    def start(self) -> None:
        """Запускает мониторинг в отдельном потоке."""
        if self.running:
            print_warning("Мониторинг уже запущен")
            return
        
        self.running = True
        self.thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.thread.start()
    
    def stop(self) -> None:
        """Останавливает мониторинг."""
        if not self.running:
            return
        
        print("\n🛑 Остановка мониторинга...")
        self.running = False
        
        if self.thread:
            self.thread.join(timeout=5)
        
        print_success("Мониторинг остановлен")
    
    def is_running(self) -> bool:
        """Проверяет, запущен ли мониторинг."""
        return self.running

def start_boost_monitor(
    session: requests.Session,
    club_url: str,
    stats_manager: DailyStatsManager,
    output_dir: str = OUTPUT_DIR
) -> BoostMonitor:
    """Удобная функция для запуска мониторинга."""
    monitor = BoostMonitor(
        session,
        club_url,
        stats_manager,
        output_dir
    )
    monitor.start()
    return monitor