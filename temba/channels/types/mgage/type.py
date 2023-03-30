from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView


class MGageType(ChannelType):
    """
    A mGage SMS channel that's based on SMPP
    """

    code = "MGA"
    category = ChannelType.Category.PHONE

    name = "mGage Channel"
    show_config_page = False

    claim_blurb = _("Add a mGage phone number.")
    claim_view = ClaimView

    schemes = [URN.TEL_SCHEME]
    max_length = 1600
    attachment_support = False
    free_sending = False

    def activate(self, channel):
        channel.is_active = True
        channel.save()

    def deactivate(self, channel):
        channel.is_active = False
        channel.save()
