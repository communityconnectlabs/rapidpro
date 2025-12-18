from django.urls import reverse

from temba.channels.models import ChannelLog
from temba.flows.models import FlowSession
from temba.ivr.models import IVRCall
from temba.tests import TembaTest


class IVRCallTest(TembaTest):
    def test_release(self):
        flow = self.get_flow("ivr")
        contact = self.create_contact("Jose", phone="+12065552000")

        call1 = self.create_incoming_call(flow, contact)
        call2 = self.create_incoming_call(flow, contact)

        self.assertEqual(FlowSession.objects.count(), 2)
        self.assertEqual(ChannelLog.objects.filter(connection_id=call1.id).count(), 1)
        self.assertEqual(ChannelLog.objects.filter(connection_id=call2.id).count(), 1)

        call2.release()

        self.assertEqual(FlowSession.objects.count(), 1)

        # call #1 unaffected
        self.assertEqual(ChannelLog.objects.filter(connection_id=call1.id).count(), 1)
        self.assertEqual(ChannelLog.objects.filter(connection_id=call2.id).count(), 0)
        self.assertFalse(IVRCall.objects.filter(id=call2.id).exists())

    def test_mailroom_urls(self):
        response = self.client.get(reverse("mailroom.ivr_handler", args=[self.channel.uuid, "incoming"]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, b"this URL should be mapped to a Mailroom instance")
