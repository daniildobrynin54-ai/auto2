"""Точка входа MangaBuff v2.8.1."""

import argparse
import sys

from logger import setup_logging, get_logger
from app import MangaBuffApp


def create_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MangaBuff v2.8.1"
    )

    parser.add_argument("--email", required=True, help="Email")
    parser.add_argument("--password", required=True, help="Пароль")
    parser.add_argument("--user_id", required=True, help="ID пользователя")
    parser.add_argument("--boost_url", help="URL буста")
    parser.add_argument("--proxy", help="URL прокси (опционально, используется из config)")

    parser.add_argument("--skip_inventory", action="store_true", help="Пропустить инвентарь")
    parser.add_argument("--only_list_owners", action="store_true", help="Только список владельцев")
    parser.add_argument("--enable_monitor", action="store_true", help="Включить мониторинг")
    parser.add_argument("--dry_run", action="store_true", help="Тестовый режим")
    parser.add_argument("--debug", action="store_true", help="Отладка")

    parser.add_argument(
        "--extra_donations", type=int, default=0, metavar="N",
        help="Дополнительные вклады сверх лимита сайта (например, --extra_donations 10)"
    )

    parser.add_argument(
        "--log_level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Уровень логирования"
    )
    parser.add_argument("--no_console_log", action="store_true", help="Отключить вывод логов в консоль")

    return parser


def main():
    print("=" * 70)
    print("MangaBuff v2.8.1")
    print("=" * 70)
    print()

    parser = create_argument_parser()
    args = parser.parse_args()

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
    logger.info(f"Debug mode: {args.debug} | Dry run: {args.dry_run}")
    if args.extra_donations:
        logger.info(f"Дополнительные вклады: +{args.extra_donations} сверх лимита сайта")

    if args.debug:
        print("🔧 DEBUG MODE ENABLED")

    if args.extra_donations > 0:
        print(f"➕ Дополнительные вклады: +{args.extra_donations} сверх лимита сайта")

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