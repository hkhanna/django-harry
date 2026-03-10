from django.apps import AppConfig


class EmailConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "harry.email"
    verbose_name = "Harry"

    def ready(self):
        import harry.email.signals  # noqa: F401
