from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel


class SMPPChannelTypeTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        url = reverse("channels.types.smpp.claim")
        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context["form"].initial

        post_data["name"] = "Test SMPP Channel"
        post_data["sms_center"] = "smscsim.melroselabs.com:2775"
        post_data["system_id"] = "111111"
        post_data["password"] = "111111"
        post_data["phone_number"] = "00111111"

        response = self.client.post(url, post_data)
        self.assertEqual(302, response.status_code)

        channel = Channel.objects.get()

        self.assertEqual("SMP", channel.channel_type)
        self.assertEqual(post_data["sms_center"], channel.config["sms_center"])
        self.assertEqual(post_data["system_id"], channel.config["system_id"])
        self.assertEqual(post_data["password"], channel.config["password"])
        self.assertEqual(post_data["phone_number"], channel.config["phone_number"])

        Channel.objects.all().delete()

        response = self.client.get(url)
        post_data = response.context["form"].initial

        post_data["name"] = "Test SMPP Channel"
        post_data["sms_center"] = "smscsim.melroselabs.com:2775"
        post_data["system_id"] = "111111"
        post_data["password"] = "111111"
        post_data["phone_number"] = "00111111"

        response = self.client.post(url, post_data)
        self.assertEqual(302, response.status_code)

        channel = Channel.objects.get()

        self.assertEqual("SMP", channel.channel_type)
        self.assertEqual(post_data["sms_center"], channel.config["sms_center"])
        self.assertEqual(post_data["system_id"], channel.config["system_id"])
        self.assertEqual(post_data["password"], channel.config["password"])
        self.assertEqual(post_data["phone_number"], channel.config["phone_number"])
