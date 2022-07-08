from django.utils.translation import ugettext_lazy as _

from temba.tickets.models import TicketerType
from temba.tickets.types.twilioflex.views import ConnectView


class TwilioFlexType(TicketerType):
    """
    Type for using Twilio Flex as a ticketer
    """

    CONFIG_ACCOUNT_SID = "account_sid"
    CONFIG_AUTH_TOKEN = "auth_token"
    CONFIG_CHAT_SERVICE_SID = "chat_service_sid"
    CONFIG_FLEX_FLOW_SID = "flex_flow_sid"
    CONFIG_WORKSPACE_SID = "workspace_sid"

    name = "Twilio Flex"
    slug = "twilioflex"
    icon = "icon-twilio_original"

    connect_view = ConnectView
    connect_blurb = _(
        f"%(link)s is the world’s most flexible contact center platform and can be connected as ticket service."
    ) % {"link": '<a href="https://www.twilio.com/flex">Twilio Flex</a>'}

    def is_available_to(self, user):
        return True
