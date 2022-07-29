from unittest.mock import patch

from requests.exceptions import Timeout

from django.urls import reverse

from temba.tests import MockResponse
from temba.tests.base import TembaTest
from temba.tickets.models import Ticketer

from .type import AmazonConnectType


class AmazonConnectTypeTest(TembaTest):
    def test_is_available_to(self):
        self.assertTrue(AmazonConnectType().is_available_to(self.admin))


class AmazonconnectMixin(TembaTest):
    def setUp(self):
        super().setUp()
        self.connect_url = reverse("tickets.types.amazonconnect.connect")


class TwilioflexViewTest(AmazonconnectMixin):
    def check_exceptions(self, mock_choices, mock_request, timeout_msg, exception_msg):
        self.client.force_login(self.admin)
        check = [(Timeout(), timeout_msg), (Exception(), exception_msg)]
        for err, msg in check:

            def side_effect(*arg, **kwargs):
                raise err

            mock_request.side_effect = side_effect
            data = {"endpoint_url": "https://aws.lambda.com"}
            response = self.client.post(self.connect_url, data=data)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.context["messages"]), 1)
            self.assertEqual([f"{m}" for m in response.context["messages"]][0], msg)

    @patch("requests.get")
    def test_form_valid(self, mock_request):
        self.client.force_login(self.admin)

        mock_request.return_value = MockResponse(200, '[{"result":"OK"}]')

        data = {"endpoint_url": "https://aws.lambda.com"}
        response = self.client.post(self.connect_url, data=data)

        mock_request.return_value = MockResponse(302, None)
        self.assertEqual(response.status_code, 302)

        ticketer = Ticketer.objects.order_by("id").last()
        self.assertEqual("Amazon Connect", ticketer.name)

        self.assertRedirect(response, reverse("tickets.ticket_list"))
