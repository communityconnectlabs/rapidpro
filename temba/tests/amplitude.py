from django.urls import reverse

from .base import TembaTest


class AmplitudeIntegrationTests(TembaTest):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.amplitude_script = (
            '<script src="https://cdn.amplitude.com/libs/analytics-browser-2.11.1-min.js.gz"></script>'
            '<script src="https://cdn.amplitude.com/libs/plugin-session-replay-browser-1.8.0-min.js.gz"></script>'
        )

    def test_amplitude(self):
        flow = self.create_flow()
        self.assertContains(self.client.get(reverse("msgs.conversation_list")), self.amplitude_script)
        self.assertContains(
            self.client.get(reverse("flows.flow_editor", kwargs={"uuid": flow.uuid})), self.amplitude_script
        )
        self.assertContains(self.client.get(reverse("triggers.trigger_archived")), self.amplitude_script)
        self.assertContains(self.client.get(reverse("contacts.contact_list")), self.amplitude_script)
        self.assertContains(self.client.get(reverse("campaigns.campaign_archived")), self.amplitude_script)
        self.assertContains(self.client.get(reverse("msgs.msg_inbox")), self.amplitude_script)
        self.assertContains(self.client.get(reverse("orgs.org_home")), self.amplitude_script)

        # other templates should not contain the amplitude script
        self.assertNotContains(self.client.get(reverse("contacts.contactimport_create")), self.amplitude_script)
