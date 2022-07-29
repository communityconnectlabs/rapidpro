from django.utils.translation import ugettext_lazy as _

from temba.tickets.models import TicketerType
from temba.tickets.types.amazonconnect.views import ConnectView


class AmazonConnectType(TicketerType):
    """
    Type for using Amazon Connect as a ticketer
    """

    CONFIG_ENDPOINT_URL = "endpoint_url"

    name = "Amazon Connect"
    slug = "amazonconnect"
    icon = "icon-cloud"

    connect_view = ConnectView
    connect_blurb = _(
        f"%(link)s provides superior customer service at a lower cost with an easy-to-use omnichannel "
        f"cloud contact center."
    ) % {"link": '<a href="https://aws.amazon.com/connect/">Amazon Connect</a>'}

    def is_available_to(self, user):
        return True
