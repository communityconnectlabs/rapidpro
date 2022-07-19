from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView


class SMPPType(ChannelType):
    """
    A SMPP bot channel
    """

    code = "SMP"
    category = ChannelType.Category.PHONE

    name = "SMPP Channel"
    show_config_page = False

    claim_blurb = _("Add a SMPP channel phone number.")
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
