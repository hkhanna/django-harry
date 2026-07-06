import importlib.util

from django.apps import AppConfig


class EmailConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "harry.email"
    verbose_name = "Harry"

    def ready(self):
        if importlib.util.find_spec("anymail") is None:
            raise ImportError(
                "harry.email requires django-anymail, which harry does not install "
                "by default. Install the extra: "
                "uv add 'harry[email] @ git+https://github.com/hkhanna/django-harry'"
            )
        import harry.email.signals  # noqa: F401
