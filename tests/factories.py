# from datetime import timedelta
# from django.utils import timezone
from django.contrib.auth import get_user_model
from faker import Faker
import harry.email.services

fake = Faker()

User = get_user_model()


def user_create(**kwargs):
    # org = kwargs.pop("org", None)

    first_name = fake.first_name()
    last_name = fake.last_name()

    defaults = dict(
        username=first_name,
        first_name=first_name,
        last_name=last_name,
        email=f"{first_name}.{last_name}@example.com".lower(),
        password="goodpass",
    )
    params = defaults | kwargs
    user = User.objects.create_user(**params)

    # If an org was passed, add the user to it.
    # FIXME
    # if org:
    #     org.users.add(user)

    return user


def email_message_create(**kwargs):
    defaults = dict(
        created_by=user_create(),
        template_context=dict(),
        template_prefix="core/email/base",
        to_email=fake.email(),
        sender_email=fake.email(),
    )
    params = defaults | kwargs

    return harry.email.services.email_message_create(save=True, **params)


def email_message_webhook_create(**kwargs):
    default_body = {
        "RecordType": "some_type",
        "MessageID": "id-abc123",
    }

    default_headers = {
        "X-Some-Header": "id-xyz456",
    }

    defaults = dict(body=default_body, headers=default_headers)
    params = defaults | kwargs

    return harry.email.services.email_message_webhook_create(**params)


# FIXME
# def org_create(**kwargs):
#     defaults = dict(
#         name=fake.company(),
#         owner=user_create(),
#         domain="testserver",
#         primary_plan=plan_create(),
#         default_plan=plan_create(),
#         current_period_end=timezone.now() + timedelta(days=10),
#     )
#     params = defaults | kwargs

#     return services.org_create(**params)


# def plan_create():
#     name = f"Plan {fake.word()} {fake.word()}"
#     return services.plan_create(name=name)
