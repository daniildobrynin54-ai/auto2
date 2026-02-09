"""Модуль логирования с ротацией по дням и организацией по месяцам."""

import logging
import os
import sys
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional


class MonthlyDailyRotatingHandler(TimedRotatingFileHandler):
    """Handler с ротацией по дням и организацией файлов по месяцам."""
    
    def __init__(self, base_dir: str = "logs", encoding: str = "utf-8"):
        """
        Инициализация handler'а.
        
        Args:
            base_dir: Базовая директория для логов
            encoding: Кодировка файлов
        """
        self.base_dir = Path(base_dir)
        self.encoding = encoding
        
        # Создаем начальный путь
        log_file = self._get_current_log_path()
        
        # Инициализируем родительский класс без ротации
        # (мы будем управлять ротацией вручную)
        logging.Handler.__init__(self)
        
        self.baseFilename = str(log_file)
        self.mode = 'a'
        self.stream = None
        
        # Открываем файл
        self._open()
    
    def _ensure_dir_exists(self, directory: Path) -> bool:
        """
        Безопасно создает директорию.
        
        Args:
            directory: Путь к директории
        
        Returns:
            True если успешно или уже существует
        """
        try:
            directory.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            # Не прерываем работу если не удалось создать директорию
            print(f"⚠️ Не удалось создать директорию логов {directory}: {e}", file=sys.stderr)
            return False
    
    def _get_current_log_path(self) -> Path:
        """
        Возвращает путь к текущему лог-файлу.
        
        Returns:
            Path объект с путем к файлу
        """
        try:
            now = datetime.now()
            
            # Папка месяца: logs/january_2025/
            # Используем английские названия месяцев для совместимости
            month_name = now.strftime("%B").lower()  # january, february, etc.
            year = now.strftime("%Y")
            month_folder = f"{month_name}_{year}"
            
            month_dir = self.base_dir / month_folder
            
            # Безопасно создаем директорию
            self._ensure_dir_exists(month_dir)
            
            # Имя файла: 2025-01-15.log
            log_filename = now.strftime("%Y-%m-%d.log")
            
            return month_dir / log_filename
        
        except Exception as e:
            # В случае ошибки возвращаем путь к fallback файлу
            print(f"⚠️ Ошибка получения пути к лог-файлу: {e}", file=sys.stderr)
            return self.base_dir / "fallback.log"
    
    def _open(self):
        """Открывает лог-файл."""
        try:
            # Проверяем изменился ли день
            current_log_path = self._get_current_log_path()
            
            # Если путь изменился - закрываем старый файл и открываем новый
            if str(current_log_path) != self.baseFilename:
                if self.stream:
                    self.stream.close()
                    self.stream = None
                
                self.baseFilename = str(current_log_path)
            
            # Открываем файл
            if self.stream is None:
                self.stream = open(self.baseFilename, self.mode, encoding=self.encoding)
        
        except Exception as e:
            # Если не удалось открыть файл - логируем в stderr но не прерываем работу
            print(f"⚠️ Не удалось открыть лог-файл {self.baseFilename}: {e}", file=sys.stderr)
            # Пытаемся использовать stderr как fallback
            self.stream = sys.stderr
    
    def emit(self, record):
        """
        Записывает лог-запись.
        
        Args:
            record: LogRecord для записи
        """
        try:
            # Проверяем не изменился ли день
            current_log_path = self._get_current_log_path()
            
            if str(current_log_path) != self.baseFilename:
                # День изменился - переключаемся на новый файл
                if self.stream:
                    self.stream.close()
                
                self.baseFilename = str(current_log_path)
                self._open()
            
            # Записываем
            if self.stream:
                msg = self.format(record)
                self.stream.write(msg + self.terminator)
                self.flush()
        
        except Exception as e:
            # Не прерываем работу при ошибке логирования
            self.handleError(record)
    
    def close(self):
        """Закрывает файл."""
        try:
            if self.stream:
                self.stream.close()
                self.stream = None
        except Exception:
            pass


class AppLogger:
    """Менеджер логирования приложения."""
    
    def __init__(
        self,
        name: str = "mangabuff",
        base_dir: str = "logs",
        level: int = logging.INFO,
        console_output: bool = True
    ):
        """
        Инициализация логгера.
        
        Args:
            name: Имя логгера
            base_dir: Директория для логов
            level: Уровень логирования
            console_output: Выводить ли логи в консоль
        """
        self.name = name
        self.base_dir = base_dir
        self.level = level
        self.logger = None
        self.console_output = console_output
        
        self._setup_logger()
    
    def _setup_logger(self):
        """Настраивает логгер."""
        # Создаем логгер
        self.logger = logging.getLogger(self.name)
        self.logger.setLevel(self.level)
        
        # Очищаем существующие handlers
        self.logger.handlers.clear()
        
        # Формат логов
        formatter = logging.Formatter(
            fmt='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # File handler с ротацией
        try:
            file_handler = MonthlyDailyRotatingHandler(
                base_dir=self.base_dir,
                encoding='utf-8'
            )
            file_handler.setLevel(self.level)
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
        except Exception as e:
            print(f"⚠️ Не удалось настроить file handler: {e}", file=sys.stderr)
        
        # Console handler (опционально)
        if self.console_output:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(self.level)
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)
        
        # Предотвращаем дублирование в root logger
        self.logger.propagate = False
    
    def get_logger(self) -> logging.Logger:
        """Возвращает настроенный логгер."""
        return self.logger
    
    def debug(self, message: str, *args, **kwargs):
        """Логирование уровня DEBUG."""
        if self.logger:
            self.logger.debug(message, *args, **kwargs)
    
    def info(self, message: str, *args, **kwargs):
        """Логирование уровня INFO."""
        if self.logger:
            self.logger.info(message, *args, **kwargs)
    
    def warning(self, message: str, *args, **kwargs):
        """Логирование уровня WARNING."""
        if self.logger:
            self.logger.warning(message, *args, **kwargs)
    
    def error(self, message: str, *args, **kwargs):
        """Логирование уровня ERROR."""
        if self.logger:
            self.logger.error(message, *args, **kwargs)
    
    def critical(self, message: str, *args, **kwargs):
        """Логирование уровня CRITICAL."""
        if self.logger:
            self.logger.critical(message, *args, **kwargs)
    
    def exception(self, message: str, *args, **kwargs):
        """Логирование исключения с traceback."""
        if self.logger:
            self.logger.exception(message, *args, **kwargs)


# Глобальный экземпляр логгера
_global_logger: Optional[AppLogger] = None


def setup_logging(
    name: str = "mangabuff",
    base_dir: str = "logs",
    level: int = logging.INFO,
    console_output: bool = True
) -> AppLogger:
    """
    Настраивает глобальное логирование.
    
    Args:
        name: Имя логгера
        base_dir: Директория для логов
        level: Уровень логирования
        console_output: Выводить ли в консоль
    
    Returns:
        AppLogger экземпляр
    """
    global _global_logger
    
    if _global_logger is None:
        _global_logger = AppLogger(
            name=name,
            base_dir=base_dir,
            level=level,
            console_output=console_output
        )
    
    return _global_logger


def get_logger() -> logging.Logger:
    """
    Возвращает глобальный логгер.
    
    Returns:
        logging.Logger экземпляр
    """
    global _global_logger
    
    if _global_logger is None:
        _global_logger = setup_logging()
    
    return _global_logger.get_logger()


def log_function_call(func_name: str, **kwargs):
    """
    Логирует вызов функции с параметрами.
    
    Args:
        func_name: Имя функции
        **kwargs: Параметры функции
    """
    logger = get_logger()
    params = ", ".join(f"{k}={v}" for k, v in kwargs.items())
    logger.debug(f"Calling {func_name}({params})")


def log_error_with_context(error: Exception, context: str = ""):
    """
    Логирует ошибку с контекстом.
    
    Args:
        error: Исключение
        context: Контекст ошибки
    """
    logger = get_logger()
    message = f"Error in {context}: {error}" if context else f"Error: {error}"
    logger.exception(message)