import abc
from functools import lru_cache
from typing import Any

from twilio.rest import Client

from django.conf import settings
from django.contrib.auth.models import User


@lru_cache(maxsize=1)
def get_verification_service():
    client = Client(
        settings.TW_VERIFY_ACCOUNT_SID,
        settings.TW_VERIFY_AUTH_TOKEN,
    )
    service = client.verify.v2.services(settings.TW_VERIFY_APP_ID)
    return service


class VerificationMeta(type):
    """Metaclass that registers verification classes and creates instances based on verification type"""

    _registry = {}

    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)

        # Register subclasses that have VERIFICATION_METHOD attribute
        if "VERIFICATION_METHOD" in namespace:
            verification_method = namespace["VERIFICATION_METHOD"]
            mcs._registry[verification_method] = cls

        return cls

    def __call__(cls, user: User, user_settings: Any, **kwargs):
        """Create an instance of the appropriate verification class based on verification_type"""
        assert user_settings is not None, "User settings cannot be None"
        assert hasattr(user_settings, "verification_type"), "User settings must have verification_type attribute"

        verification_type = user_settings.verification_type

        # If calling Verification base class, select the appropriate subclass from registry
        if cls.__name__ == "Verification":
            if verification_type not in cls._registry:
                raise ValueError(
                    f"Unknown verification type: {verification_type}. "
                    f"Available types: {list(cls._registry.keys())}"
                )
            actual_class = cls._registry[verification_type]
            return actual_class(user, user_settings, **kwargs)

        # If calling a specific subclass directly, just create the instance normally
        instance = super(VerificationMeta, cls).__call__(user, user_settings, **kwargs)
        return instance


class Verification(metaclass=VerificationMeta):
    """Base class for all verification types"""

    @abc.abstractmethod  # noqa
    def __init__(self, user: User, user_settings: Any):
        pass

    @abc.abstractmethod  # noqa
    def start_verification(self):
        pass

    @abc.abstractmethod  # noqa
    def complete_verification(self, verification_code: str) -> bool:
        pass


class PhoneVerification(Verification):
    VERIFICATION_METHOD = settings.VERIFICATION_TYPES.PHONE

    def __init__(self, _: User, user_settings: Any):
        self.user_settings = user_settings
        self.verification_service = get_verification_service()

    def start_verification(self):
        self.verification_service.verifications.create(
            to=self.user_settings.tel,
            channel="sms",
        )

    def complete_verification(self, verification_code: str) -> bool:
        result = self.verification_service.verification_checks.create(
            to=self.user_settings.tel,
            code=verification_code,
        )
        return result.status == "approved"


class EmailVerification(Verification):
    VERIFICATION_METHOD = settings.VERIFICATION_TYPES.EMAIL

    def __init__(self, user: User, _: Any):
        self.user = user
        self.verification_service = get_verification_service()

    def start_verification(self):
        self.verification_service.verifications.create(
            to=self.user.email,
            channel="email",
        )

    def complete_verification(self, verification_code: str) -> bool:
        result = self.verification_service.verification_checks.create(
            to=self.user.email,
            code=verification_code,
        )
        return result.status == "approved"


class TotpVerification(Verification):
    VERIFICATION_METHOD = settings.VERIFICATION_TYPES.TOTP

    def __init__(self, user: User, user_settings: Any):
        self.user = user
        self.user_settings = user_settings

    def start_verification(self):
        pass

    def complete_verification(self, verification_code: str) -> bool:
        return self.user.verify_2fa(otp=verification_code)  # noqa


class SecretCodeVerification(Verification):
    VERIFICATION_METHOD = settings.VERIFICATION_TYPES.SECRET_CODE

    def __init__(self, _: User, __: Any):
        pass

    def start_verification(self):
        pass

    def complete_verification(self, verification_code: str) -> bool:
        return verification_code == settings.TWO_FACTOR_MAGIC_PASS


def start_user_verification(user: User):
    verification = Verification(user=user, user_settings=user.get_settings())  # noqa
    verification.start_verification()


def complete_user_verification(user: User, verification_code: str) -> bool:
    verification = Verification(user=user, user_settings=user.get_settings())  # noqa
    return verification.complete_verification(verification_code=verification_code)
