from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel


class MGageChannelTypeTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        url = reverse("channels.types.mgage.claim")
        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context["form"].initial

        post_data["phone_number"] = "+18889999999"

        response = self.client.post(url, post_data)
        self.assertEqual(302, response.status_code)

        channel = Channel.objects.get()

        self.assertEqual("MGA", channel.channel_type)
        self.assertEqual(post_data["phone_number"], channel.address)

        Channel.objects.all().delete()

        response = self.client.get(url)
        post_data = response.context["form"].initial

        post_data["phone_number"] = "+18889999999"

        response = self.client.post(url, post_data)
        self.assertEqual(302, response.status_code)

        channel = Channel.objects.get()

        self.assertEqual("MGA", channel.channel_type)
        self.assertEqual(post_data["phone_number"], channel.address)
