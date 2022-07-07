import time
from datetime import datetime, timedelta

import requests
from django import forms
from django.conf import settings
from django.urls import reverse
from django_redis import get_redis_connection
from smartmin.views import SmartTemplateView, SmartCRUDL, SmartCreateView, SmartUpdateView

from django.db.models import Q, Sum
from django.http import JsonResponse
from django.utils import timezone

from temba.channels.models import Channel, ChannelCount
from temba.orgs.models import Org
from temba.orgs.views import OrgPermsMixin, ModalMixin
from temba.dashboard.models import EmbeddedBoard
from django.utils.translation import ugettext_lazy as _

from temba.utils.fields import InputWidget, SelectWidget


class Home(OrgPermsMixin, SmartTemplateView):
    """
    The main dashboard view
    """

    permission = "orgs.org_dashboard"
    template_name = "dashboard/home.haml"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["embedded_boards"] = EmbeddedBoard.objects.all()
        return context

    def get_gear_links(self):
        links = [
            dict(
                id="new_dashboards",
                title=_("New Dashboards"),
                modax=_("New Dashboards"),
                style="button-primary",
                href=reverse("dashboard.embeddedboard_create"),
            )
        ]
        return links


class MessageHistory(OrgPermsMixin, SmartTemplateView):
    """
    Endpoint to expose message history since the dawn of time by day as JSON blob
    """

    permission = "orgs.org_dashboard"

    def render_to_response(self, context, **response_kwargs):
        orgs = []
        org = self.derive_org()
        if org:
            orgs = Org.objects.filter(Q(id=org.id) | Q(parent=org))

        # get all our counts for that period
        daily_counts = ChannelCount.objects.filter(
            count_type__in=[
                ChannelCount.INCOMING_MSG_TYPE,
                ChannelCount.OUTGOING_MSG_TYPE,
                ChannelCount.INCOMING_IVR_TYPE,
                ChannelCount.OUTGOING_IVR_TYPE,
            ]
        )

        daily_counts = daily_counts.filter(day__gt="2013-02-01").filter(day__lte=timezone.now())

        if orgs or not self.request.user.is_support():
            daily_counts = daily_counts.filter(channel__org__in=orgs)

        daily_counts = list(
            daily_counts.values("day", "count_type").order_by("day", "count_type").annotate(count_sum=Sum("count"))
        )

        msgs_in = []
        msgs_out = []
        epoch = datetime(1970, 1, 1)

        def get_timestamp(count_dict):
            """
            Gets a unix time that is highcharts friendly for a given day
            """
            count_date = datetime.fromtimestamp(time.mktime(count_dict["day"].timetuple()))
            return int((count_date - epoch).total_seconds() * 1000)

        def record_count(counts, day, count):
            """
            Records a count in counts list which is an ordered list of day, count tuples
            """
            is_new = True

            # if we have seen this one before, increment it
            if len(counts):
                last = counts[-1]
                if last and last[0] == day:
                    last[1] += count["count_sum"]
                    is_new = False

            # otherwise add it as a new count
            if is_new:
                counts.append([day, count["count_sum"]])

        msgs_total = []
        for count in daily_counts:
            direction = count["count_type"][0]
            day = get_timestamp(count)

            if direction == "I":
                record_count(msgs_in, day, count)
            elif direction == "O":
                record_count(msgs_out, day, count)

            # we create one extra series that is the combination of both in and out
            # so we can use that inside our navigator
            record_count(msgs_total, day, count)

        return JsonResponse(
            [
                dict(name="Incoming", type="column", data=msgs_in, showInNavigator=False),
                dict(name="Outgoing", type="column", data=msgs_out, showInNavigator=False),
                dict(
                    name="Total",
                    type="column",
                    data=msgs_total,
                    showInNavigator=True,
                    showInLegend=False,
                    visible=False,
                ),
            ],
            safe=False,
        )


class RangeDetails(OrgPermsMixin, SmartTemplateView):
    """
    Intercooler snippet to show detailed information for a specific range
    """

    permission = "orgs.org_dashboard"
    template_name = "dashboard/range_details.haml"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        end = timezone.now()
        begin = end - timedelta(days=30)
        begin = self.request.GET.get("begin", datetime.strftime(begin, "%Y-%m-%d"))
        end = self.request.GET.get("end", datetime.strftime(end, "%Y-%m-%d"))

        direction = self.request.GET.get("direction", "IO")

        if begin and end:
            orgs = []
            org = self.derive_org()
            if org:
                orgs = Org.objects.filter(Q(id=org.id) | Q(parent=org))

            count_types = []
            if "O" in direction:
                count_types = [ChannelCount.OUTGOING_MSG_TYPE, ChannelCount.OUTGOING_IVR_TYPE]

            if "I" in direction:
                count_types += [ChannelCount.INCOMING_MSG_TYPE, ChannelCount.INCOMING_IVR_TYPE]

            # get all our counts for that period
            daily_counts = (
                ChannelCount.objects.filter(count_type__in=count_types)
                .filter(day__gte=begin)
                .filter(day__lte=end)
                .exclude(channel__org=None)
            )
            if orgs:
                daily_counts = daily_counts.filter(channel__org__in=orgs)

            context["orgs"] = list(
                daily_counts.values("channel__org", "channel__org__name")
                .order_by("-count_sum")
                .annotate(count_sum=Sum("count"))[:12]
            )

            channel_types = (
                ChannelCount.objects.filter(count_type__in=count_types)
                .filter(day__gte=begin)
                .filter(day__lte=end)
                .exclude(channel__org=None)
            )

            if orgs or not self.request.user.is_support():
                channel_types = channel_types.filter(channel__org__in=orgs)

            channel_types = list(
                channel_types.values("channel__channel_type").order_by("-count_sum").annotate(count_sum=Sum("count"))
            )

            # populate the channel names
            pie = []
            for channel_type in channel_types[0:6]:
                channel_type["channel__name"] = Channel.get_type_from_code(channel_type["channel__channel_type"]).name
                pie.append(channel_type)

            other_count = 0
            for channel_type in channel_types[6:]:
                other_count += channel_type["count_sum"]

            if other_count:
                pie.append(dict(channel__name="Other", count_sum=other_count))

            context["channel_types"] = pie

            context["begin"] = datetime.strptime(begin, "%Y-%m-%d").date()
            context["end"] = datetime.strptime(end, "%Y-%m-%d").date()
            context["direction"] = direction

        return context


class EmbeddedBoardCRUDL(SmartCRUDL):
    actions = ("create", "delete")
    model = EmbeddedBoard

    class Create(ModalMixin, SmartCreateView):
        class Form(forms.ModelForm):
            embedding_type = forms.ChoiceField(choices=EmbeddedBoard.EMBEDDING_TYPES, widget=SelectWidget)
            url = forms.URLField(label="Link to the new embedded dashboard", widget=InputWidget, required=False)
            metabase_dashboard = forms.IntegerField(label="Metabase Dashboard", required=False)
            title = forms.CharField(label="Title", widget=InputWidget)

            class Meta:
                model = EmbeddedBoard
                fields = "__all__"

        form_class = Form
        fields = ("embedding_type", "title", "url", "metabase_dashboard")
        success_message = ""
        success_url = "@dashboard.dashboard_home"
        template_name = "dashboard/embeddedboard_create.haml"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            if all([settings.METABASE_SITE_URL, settings.METABASE_USERNAME, settings.METABASE_PASSWORD]):
                metabase_token = self._metabase_session_token
                if not metabase_token:
                    return context
                dashboards = self._metabase_dashboards(metabase_token)
                if dashboards:
                    context["metabase_boards"] = dashboards
            return context

        @property
        def _metabase_session_token(self):
            r = get_redis_connection()
            key_name = "metabase_auth_token"
            cached = r.get(key_name)
            if cached:
                if isinstance(cached, (bytes, bytearray)):
                    return cached.decode()
                return cached

            auth_response = requests.post(
                f"{settings.METABASE_SITE_URL}/api/session",
                json={
                    "username": settings.METABASE_USERNAME,
                    "password": settings.METABASE_PASSWORD,
                },
            )
            if auth_response.ok:
                auth_response_json = auth_response.json()
                metabase_api_token = auth_response_json.get("id", "")
                if metabase_api_token:
                    r.set(key_name, metabase_api_token, ex=1123200)
                return metabase_api_token
            return ""

        @staticmethod
        def _metabase_dashboards(token):
            dashboards = requests.get(
                f"{settings.METABASE_SITE_URL}/api/dashboard",
                headers={
                    "X-Metabase-Session": token,
                },
            )
            dashboards = filter(lambda x: x["enable_embedding"], dashboards.json())
            dashboards = [(dashboard.get("id"), dashboard.get("name")) for dashboard in dashboards]
            dashboards = list(sorted(dashboards, key=lambda x: x[1]))
            return dashboards

    class Delete(ModalMixin, SmartUpdateView):
        submit_button_name = _("Delete")
        fields = ()
        success_message = ""
        default_template = "smartmin/delete_confirm.html"
        success_url = "@dashboard.dashboard_home"

        def save(self, obj):
            obj.delete()
            return obj
