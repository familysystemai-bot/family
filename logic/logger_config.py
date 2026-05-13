import logging
import logging.handlers
import os
from config import DATA_DIR

# إنشاء مجلد السجلات
LOGS_DIR = os.path.join(DATA_DIR, 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)

# أسماء ملفات السجلات
APP_LOG_FILE = os.path.join(LOGS_DIR, 'app.log')
ERROR_LOG_FILE = os.path.join(LOGS_DIR, 'errors.log')
SECURITY_LOG_FILE = os.path.join(LOGS_DIR, 'security.log')

# صيغة السجل
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

def get_logger(name: str, log_type: str = 'app') -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    log_file = {
        'app': APP_LOG_FILE,
        'error': ERROR_LOG_FILE,
        'security': SECURITY_LOG_FILE,
    }.get(log_type, APP_LOG_FILE)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(levelname)s - %(name)s - %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    return logger

app_logger = get_logger('family_system', 'app')
error_logger = get_logger('family_system.errors', 'error')
security_logger = get_logger('family_system.security', 'security')
