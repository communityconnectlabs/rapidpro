from functools import lru_cache

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


def start_user_verification(user: User):
    verification_service = get_verification_service()
    user_settings = user.get_settings()  # noqa
    phone_verification = {"to": user_settings.tel, "channel": "sms"}
    email_verification = {"to": user.email, "channel": "email"}
    use_phone_verification = user_settings.verification_type == settings.VERIFICATION_TYPES.PHONE
    verification_service.verifications.create(**(phone_verification if use_phone_verification else email_verification))


def complete_user_verification(user: User, verification_code: str) -> bool:
    verification_service = get_verification_service()
    user_settings = user.get_settings()  # noqa
    use_phone_verification = user_settings.verification_type == settings.VERIFICATION_TYPES.PHONE
    communication_way = user_settings.tel if use_phone_verification else user.email
    result = verification_service.verification_checks.create(to=communication_way, code=verification_code)
    return result.status == "approved"
