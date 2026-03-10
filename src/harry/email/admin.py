from django.contrib import admin

from .models import EmailMessage, EmailMessageAttachment


class EmailMessageAttachmentInline(admin.TabularInline):
    model = EmailMessageAttachment
    extra = 0


@admin.register(EmailMessage)
class EmailMessageAdmin(admin.ModelAdmin):
    list_display = ["__str__", "to_email", "status", "created_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["to_email", "to_name", "subject", "message_id"]
    readonly_fields = [
        "uuid",
        "created_at",
        "updated_at",
        "message_id",
        "esp_event",
        "esp_event_at",
    ]
    inlines = [EmailMessageAttachmentInline]
