import importlib.util

from django.apps import AppConfig

_INSTALL_HINT = (
    "harry.email requires django-anymail, which harry does not install by default. "
    "Install the extra: pip install 'harry[email]'"
)


class EmailConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "harry.email"
    verbose_name = "Harry"

    def ready(self):
        if importlib.util.find_spec("anymail") is None:
            raise ImportError(_INSTALL_HINT)
        import harry.email.signals  # noqa: F401
