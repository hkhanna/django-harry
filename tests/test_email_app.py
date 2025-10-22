import pytest
from django.apps import apps


@pytest.mark.django_db
class TestEmailAppConfig:
    """Test that the harry.email app is properly configured."""

    def test_app_config(self) -> None:
        """Test that the EmailConfig is loaded correctly."""
        app_config = apps.get_app_config("email")
        assert app_config.name == "harry.email"
        assert app_config.default_auto_field == "django.db.models.BigAutoField"
