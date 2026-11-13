"""
Application Constants
All magic numbers and configuration values in one place
"""

# Database Configuration
# SEC-07: credentials are never hardcoded. They come from the environment
# (DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD) or the settings store.
import os as _os
DEFAULT_DB_HOST = _os.environ.get('DB_HOST', 'localhost')
DEFAULT_DB_PORT = int(_os.environ.get('DB_PORT', '5432'))
DEFAULT_DB_NAME = _os.environ.get('DB_NAME', 'analysis')
DEFAULT_DB_USER = _os.environ.get('DB_USER', 'postgres')
DEFAULT_DB_PASSWORD = _os.environ.get('DB_PASSWORD', '')

# Connection Pool Settings
MIN_POOL_CONNECTIONS = 5
MAX_POOL_CONNECTIONS = 20
CONNECTION_TIMEOUT = 10

# Word Processing Limits
MAX_REGULAR_WORD_LENGTH = 100
MAX_EMAIL_LENGTH = 320
MAX_URL_LENGTH = 2000
MAX_DOMAIN_LENGTH = 255
MAX_DATE_LENGTH = 50

# Cache Settings
WORD_CACHE_MAX_SIZE = 50000
CACHE_TTL_SECONDS = 3600

# Batch Analysis
BATCH_SIZE = 2000
LARGE_BATCH_THRESHOLD = 1000

# Data File Settings
SUPPORTED_FORMATS = ['.xlsx', '.csv', '.json']
DEFAULT_DATA_FILE = 'domain_data.xlsx'
DATA_FILE_SEARCH_PATHS = [
    'classification/',
    './',
]

# Logging
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

# Database Tables
TABLE_WORDS = 'words'
TABLE_CATEGORIES = 'categorys'
TABLE_KEYWORDS = 'keywords'
TABLE_WORDS_CATEGORIES = 'words_categorys'
TABLE_KEYWORDS_PATHS = 'keywords_hashs'
TABLE_WORDS_PATHS = 'words_hashs'

