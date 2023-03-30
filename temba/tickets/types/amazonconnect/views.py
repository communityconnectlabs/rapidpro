import requests

from django.conf import settings
from django.contrib import messages
from django.utils.translation import gettext_lazy as _

from temba.tickets.models import Ticketer
from temba.tickets.views import BaseConnectView
from temba.utils.uuid import uuid4


class ConnectView(BaseConnectView):
    def form_valid(self, form):
        from .type import AmazonConnectType

        config = {
            AmazonConnectType.CONFIG_ENDPOINT_URL: settings.AMAZON_CONNECT_LAMBDA_FUNCTION_URL,
        }

        org_existing_ticketer = Ticketer.objects.filter(
            org=self.org, is_active=True, ticketer_type=AmazonConnectType.slug
        ).first()
        if org_existing_ticketer:
            messages.error(self.request, _("This organization is already connected to Amazon Connect"))
            return super().get(self.request, *self.args, **self.kwargs)

        response = requests.get(f"{settings.AMAZON_CONNECT_LAMBDA_FUNCTION_URL}/healthcheck")
        if response.status_code != 200:
            messages.error(self.request, _("The Amazon Connect lambda function is not available"))
            return super().get(self.request, *self.args, **self.kwargs)

        self.object = Ticketer(
            uuid=uuid4(),
            org=self.org,
            ticketer_type=AmazonConnectType.slug,
            config=config,
            name=AmazonConnectType.name,
            created_by=self.request.user,
            modified_by=self.request.user,
        )
        self.object.save()

        return super().form_valid(form)

    form_class = BaseConnectView.Form
    template_name = "tickets/types/amazonconnect/connect.haml"
