"""Конфигурация приложения MangaBuff с упрощенной настройкой прокси."""

# API настройки
BASE_URL = "https://mangabuff.ru"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0"

# Настройки прокси
PROXY_ENABLED = False
PROXY_URL = "socks5://PrsRUS1HZZ1GZ:LTWg4yWH@62.233.39.89:1080"

# Настройки пагинации
OWNERS_PER_PAGE = 36
WANTS_PER_PAGE = 60
CARDS_PER_BATCH = 10000

# Пороги для приближенного подсчета 
OWNERS_APPROXIMATE_THRESHOLD = 11
WANTS_APPROXIMATE_THRESHOLD = 5

# Оценки для последней страницы
OWNERS_LAST_PAGE_ESTIMATE = 18
WANTS_LAST_PAGE_ESTIMATE = 30

# Таймауты запросов
REQUEST_TIMEOUT = (10, 20)

# Rate Limiting
RATE_LIMIT_PER_MINUTE = 70
RATE_LIMIT_RETRY_DELAY = 15
RATE_LIMIT_WINDOW = 60

# Действия, которые считаются в rate limit
RATE_LIMITED_ACTIONS = {
    'send_trade',
    'load_owners_page',
    'load_wants_page',
    'load_user_cards',
}

# Задержки между запросами
DEFAULT_DELAY = 0.3
PAGE_DELAY = 0.6
PARSE_DELAY = 0.9
CARD_API_DELAY = 0.2

# Настройки обменов
MIN_TRADE_DELAY = 11.0
TRADE_RANDOM_DELAY_MIN = 0.5
TRADE_RANDOM_DELAY_MAX = 2.0

# Настройки мониторинга
MONITOR_CHECK_INTERVAL = 2
MONITOR_STATUS_INTERVAL = 30

# Интервал проверки истории обменов (в секундах)
HISTORY_CHECK_INTERVAL = 10  # 10 секунд

# Настройки ожидания после обработки всех владельцев
WAIT_AFTER_ALL_OWNERS = 300
WAIT_CHECK_INTERVAL = 2

# Настройки кэша
CACHE_VALIDITY_HOURS = 72

# Настройки селектора карт
MAX_CARD_SELECTION_ATTEMPTS = 50
MAX_WANTERS_FOR_TRADE = 70  # Максимум желающих для выбора карты

# 🔧 НОВОЕ: Максимум владельцев для карт клуба
MAX_CLUB_CARD_OWNERS = 540  # Максимальное количество владельцев для буст-карты

# Пропуск первых владельцев на первой странице
FIRST_PAGE_SKIP_OWNERS = 6

# Дневные лимиты
MAX_DAILY_DONATIONS = 50
MAX_DAILY_REPLACEMENTS = 10

# Часовой пояс (MSK = UTC+3)
TIMEZONE_OFFSET = 3  # Московское время UTC+3

# Настройки повторных попыток
MAX_RETRIES = 3
RETRY_DELAY = 2

# Директории
OUTPUT_DIR = "created_files"

# Имена файлов
INVENTORY_FILE = "inventory.json"
PARSED_INVENTORY_FILE = "parsed_inventory.json"
BOOST_CARD_FILE = "boost_card.json"
SENT_CARDS_FILE = "sent_cards.json"
