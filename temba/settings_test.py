from .settings import *  # noqa

# Rewrite the MIDDLEWARE setting to skip event logger in tests
MIDDLEWARE = list(filter(lambda m: m != "temba.events.middleware.CustomerEventMiddleware", MIDDLEWARE))
