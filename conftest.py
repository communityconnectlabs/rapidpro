from django.conf import settings


def pytest_configure():
    settings.TESTING = True
    settings.COMPRESS_ENABLED = False
    settings.COMPRESS_OFFLINE = False
    settings.COMPRESS_PRECOMPILERS = ()
    settings.PASSWORD_HASHERS = ("django.contrib.auth.hashers.MD5PasswordHasher",)
    settings.DEBUG = False
    settings.STORAGE_ROOT_DIR = "test_orgs"
    settings.REST_HANDLE_EXCEPTIONS = False
    settings.MIDDLEWARE = tuple(
        m for m in settings.MIDDLEWARE if m != "temba.events.middleware.CustomerEventMiddleware"
    )
