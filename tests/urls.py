from django.urls import path

from harry.views import health

urlpatterns = [
    path("health/", health),
]
