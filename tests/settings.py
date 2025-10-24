"""Django settings for running tests."""

SECRET_KEY = "test-secret-key-not-for-production"

DEBUG = True

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "harry.email",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [],
        },
    },
]

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.InMemoryStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

# Site Configuration - Refactor this (FIXME)
SITE_CONFIG = {
    "name": "Example App",
    "logo_url_link": "",
    "logo_url": "",
    "default_from_name": "Bob Smith",
    "default_from_email": "bob@example.com",
    "account_from_name": "Account Management",
    "account_from_email": "accounts@example.com",
    "account_reply_to_name": None,
    "account_reply_to_email": None,  # Set to None to not have any reply-to in account-related emails
    "company": "Bob Smith PLLC",
    "company_address": "123 Main Street",  # Set to None to skip having an address.
    "company_city_state_zip": "Washington, DC 20006",
    "contact_email": "bob@example.com",
}

# EMAIL
# This ephemeral locmem backend would be automatically used anyway during testing no matter what was put in this setting.
# See https://docs.djangoproject.com/en/5.2/topics/testing/tools/#topics-testing-email for more details
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
MAX_SUBJECT_LENGTH = 78
# EMAIL_MESSAGE_WEBHOOK_PATH = env(
#     "EMAIL_MESSAGE_WEBHOOK_PATH", default="email_message_webhook/"
# )
