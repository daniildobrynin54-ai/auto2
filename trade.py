"""
Публичное API модуля обменов.

Содержит функции-обёртки и реэкспортирует классы для обратной совместимости
со всеми существующими импортами в проекте.
"""

from typing import Optional

# Реэкспорт классов — все существующие `from trade import ...` продолжат работать
from trade_history import TradeHistoryMonitor
from trade_manager import TradeManager

__all__ = [
    "TradeHistoryMonitor",
    "TradeManager",
    "send_trade_to_owner",
    "cancel_all_sent_trades",
]


def send_trade_to_owner(
    session,
    owner_id: int,
    owner_name: str,
    my_instance_id: int,
    his_card_id: int,
    his_instance_id: Optional[int] = None,   # ← берётся из card_user_id на странице владельцев
    my_card_name: str = "",
    my_wanters: int = 0,
    trade_manager: Optional[TradeManager] = None,
    dry_run: bool = True,
    debug: bool = False,
) -> bool:
    """
    Отправляет обмен владельцу карты.

    Если his_instance_id передан (спарсен из card_user_id в href страницы владельцев),
    поиск instance_id через API пропускается — экономится один сетевой запрос.
    """
    if not my_instance_id:
        if debug:
            print("[TRADE] Отсутствует my_instance_id")
        return False

    if not trade_manager:
        trade_manager = TradeManager(session, debug)

    if not dry_run and trade_manager.has_trade_sent(owner_id, his_card_id):
        if debug:
            print(f"[TRADE] Обмен уже отправлен {owner_name}")
        print(f"⏭️  Обмен уже отправлен → {owner_name}")
        return False

    if dry_run:
        instance_info = f"his_instance_id={his_instance_id}" if his_instance_id else "his_instance_id=нет (нужен поиск)"
        print(f"[DRY-RUN] 📤 Обмен → {owner_name} ({instance_info})")
        return True

    # Если instance_id не пришёл со страницы — ищем через API (запасной вариант)
    if not his_instance_id:
        if debug:
            print(f"[TRADE] card_user_id не найден для {owner_name}, ищем через API...")
        his_instance_id = trade_manager.find_partner_card_instance(owner_id, his_card_id)

    if not his_instance_id:
        print(f"⚠️  Не удалось получить instance_id карты у {owner_name}")
        return False

    success = trade_manager.create_trade_direct_api(owner_id, my_instance_id, his_instance_id)
    if success:
        trade_manager.mark_trade_sent(owner_id, his_card_id)

    return success


def cancel_all_sent_trades(
    session,
    trade_manager: Optional[TradeManager] = None,
    history_monitor: Optional[TradeHistoryMonitor] = None,
    debug: bool = False,
) -> bool:
    """Отменяет все исходящие обмены."""
    if not trade_manager:
        trade_manager = TradeManager(session, debug)

    return trade_manager.cancel_all_sent_trades(history_monitor)