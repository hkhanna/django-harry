import pytest

from . import factories


@pytest.fixture
def user():
    user = factories.user_create()
    return user


# FIXME
# @pytest.fixture
# def org(user):
#     # User should be a non-owner member of the Org
#     org = factories.org_create()
#     services.org_user_create(org=org, user=user)
#     return org


# @pytest.fixture
# def ou(user, org):
#     return user.org_users.get(org=org)


@pytest.fixture(autouse=True)
def enable_db_access_for_all_tests(db):
    pass
