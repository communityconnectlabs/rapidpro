import ast
import base64
import hashlib
import hmac
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta

import nexmo
import phonenumbers
import pycountry
import pytz
import regex
import requests
import twilio.base.exceptions
from dateutil.relativedelta import relativedelta
from rest_framework import status
from smartmin.views import (
    SmartCRUDL,
    SmartFormView,
    SmartListView,
    SmartModelActionView,
    SmartReadView,
    SmartTemplateView,
    SmartUpdateView,
)
from twilio.base.exceptions import TwilioRestException

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Sum
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt

from temba.contacts.models import URN
from temba.msgs.models import Msg, SystemLabel
from temba.msgs.views import InboxView
from temba.orgs.models import Org
from temba.orgs.views import AnonMixin, DependencyDeleteModal, MenuMixin, ModalMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.utils import analytics, countries, get_image_size, json
from temba.utils.fields import SelectMultipleWidget, SelectWidget
from temba.utils.models import patch_queryset_count
from temba.utils.views import ComponentFormMixin, SpaMixin

from .models import (
    Alert,
    Channel,
    ChannelConnection,
    ChannelCount,
    ChannelEvent,
    ChannelLog,
    SyncEvent,
    UnsupportedAndroidChannelError,
)

logger = logging.getLogger(__name__)

ALL_COUNTRIES = countries.choices()


def get_channel_read_url(channel):
    return reverse("channels.channel_read", args=[channel.uuid])


def channel_status_processor(request):
    status = dict()
    user = request.user

    if user.is_superuser or user.is_anonymous:
        return status

    # from the logged in user get the channel
    org = user.get_org()

    allowed = False
    if org:
        allowed = user.has_org_perm(org, "channels.channel_claim")

    if allowed:
        # only care about channels that are older than an hour
        cutoff = timezone.now() - timedelta(hours=1)
        send_channel = org.get_send_channel()
        call_channel = org.get_call_channel()

        status["send_channel"] = send_channel
        status["call_channel"] = call_channel
        status["has_outgoing_channel"] = send_channel or call_channel

        channels = org.channels.filter(is_active=True)
        for channel in channels:

            if channel.created_on > cutoff:
                continue

            if not channel.is_new():
                # delayed out going messages
                if channel.get_delayed_outgoing_messages().exists():
                    status["unsent_msgs"] = True

                # see if it hasn't synced in a while
                if not channel.get_recent_syncs().exists():
                    status["delayed_syncevents"] = True

                # don't have to keep looking if they've both failed
                if "delayed_syncevents" in status and "unsent_msgs" in status:
                    break

    return status


def get_commands(channel, commands, sync_event=None):
    """
    Generates sync commands for all queued messages on the given channel
    """
    msgs = Msg.objects.filter(
        status__in=(Msg.STATUS_PENDING, Msg.STATUS_QUEUED, Msg.STATUS_WIRED),
        channel=channel,
        direction=Msg.DIRECTION_OUT,
    )

    if sync_event:
        pending_msgs = sync_event.get_pending_messages()
        retry_msgs = sync_event.get_retry_messages()
        msgs = msgs.exclude(id__in=pending_msgs).exclude(id__in=retry_msgs)

    commands += Msg.get_sync_commands(msgs=msgs)

    # TODO: add in other commands for the channel
    # We need a queueable model similar to messages for sending arbitrary commands to the client

    return commands


@csrf_exempt
def sync(request, channel_id):
    start = time.time()

    if request.method != "POST":
        return HttpResponse(status=500, content="POST Required")

    commands = []
    channel = Channel.objects.filter(id=channel_id, is_active=True).first()
    if not channel:
        return JsonResponse(dict(cmds=[dict(cmd="rel", relayer_id=channel_id)]))

    request_time = request.GET.get("ts", "")
    request_signature = force_bytes(request.GET.get("signature", ""))

    if not channel.secret:
        return JsonResponse({"error_id": 4, "error": "Can't sync unclaimed channel", "cmds": []}, status=401)

    # check that the request isn't too old (15 mins)
    now = time.time()
    if abs(now - int(request_time)) > 60 * 15:
        return JsonResponse({"error_id": 3, "error": "Old Request", "cmds": []}, status=401)

    # sign the request
    signature = hmac.new(
        key=force_bytes(str(channel.secret + request_time)), msg=force_bytes(request.body), digestmod=hashlib.sha256
    ).digest()

    # base64 and url sanitize
    signature = base64.urlsafe_b64encode(signature).strip()

    if request_signature != signature:
        return JsonResponse(
            {"error_id": 1, "error": "Invalid signature: '%(request)s'" % {"request": request_signature}, "cmds": []},
            status=401,
        )

    # update our last seen on our channel if we haven't seen this channel in a bit
    if not channel.last_seen or timezone.now() - channel.last_seen > timedelta(minutes=5):
        channel.last_seen = timezone.now()
        channel.save(update_fields=["last_seen"])

    sync_event = None

    # Take the update from the client
    cmds = []
    if request.body:
        body_parsed = json.loads(request.body)

        # all valid requests have to begin with a FCM command
        if "cmds" not in body_parsed or len(body_parsed["cmds"]) < 1 or body_parsed["cmds"][0]["cmd"] != "fcm":
            return JsonResponse({"error_id": 4, "error": "Missing FCM command", "cmds": []}, status=401)

        cmds = body_parsed["cmds"]

    if not channel.org and channel.uuid == cmds[0].get("uuid"):
        # Unclaimed channel with same UUID resend the registration commmands
        cmd = dict(
            cmd="reg", relayer_claim_code=channel.claim_code, relayer_secret=channel.secret, relayer_id=channel.id
        )
        return JsonResponse(dict(cmds=[cmd]))
    elif not channel.org:
        return JsonResponse({"error_id": 4, "error": "Can't sync unclaimed channel", "cmds": []}, status=401)

    unique_calls = set()

    for cmd in cmds:
        handled = False
        extra = None

        if "cmd" in cmd:
            keyword = cmd["cmd"]

            # catchall for commands that deal with a single message
            if "msg_id" in cmd:

                # make sure the negative ids are converted to long
                msg_id = cmd["msg_id"]
                if msg_id < 0:
                    msg_id = 4294967296 + msg_id

                msg = Msg.objects.filter(id=msg_id, org=channel.org).first()
                if msg:
                    if msg.direction == Msg.DIRECTION_OUT:
                        handled = msg.update(cmd)
                    else:
                        handled = True

            # creating a new message
            elif keyword == "mo_sms":
                date = datetime.fromtimestamp(int(cmd["ts"]) // 1000).replace(tzinfo=pytz.utc)

                # it is possible to receive spam SMS messages from no number on some carriers
                tel = cmd["phone"] if cmd["phone"] else "empty"
                try:
                    urn = URN.normalize(URN.from_tel(tel), channel.country.code)

                    if "msg" in cmd:
                        msg = Msg.create_relayer_incoming(channel.org, channel, urn, cmd["msg"], date)
                        extra = dict(msg_id=msg.id)
                except ValueError:
                    pass

                handled = True

            # phone event
            elif keyword == "call":
                call_tuple = (cmd["ts"], cmd["type"], cmd["phone"])
                date = datetime.fromtimestamp(int(cmd["ts"]) // 1000).replace(tzinfo=pytz.utc)

                duration = 0
                if cmd["type"] != "miss":
                    duration = cmd["dur"]

                # Android sometimes will pass us a call from an 'unknown number', which is null
                # ignore these events on our side as they have no purpose and break a lot of our
                # assumptions
                if cmd["phone"] and call_tuple not in unique_calls:
                    urn = URN.from_tel(cmd["phone"])
                    try:
                        ChannelEvent.create_relayer_event(
                            channel, urn, cmd["type"], date, extra={"duration": duration}
                        )
                    except ValueError:
                        # in some cases Android passes us invalid URNs, in those cases just ignore them
                        pass

                    unique_calls.add(call_tuple)
                handled = True

            elif keyword == "fcm":
                # update our fcm and uuid

                config = channel.config
                config.update({Channel.CONFIG_FCM_ID: cmd["fcm_id"]})
                channel.config = config
                channel.uuid = cmd.get("uuid", None)
                channel.save(update_fields=["uuid", "config"])

                # no acking the fcm
                handled = False

            elif keyword == "reset":
                # release this channel
                channel.release(channel.modified_by, trigger_sync=False)
                channel.save()

                # ack that things got handled
                handled = True

            elif keyword == "status":
                sync_event = SyncEvent.create(channel, cmd, cmds)
                Alert.check_power_alert(sync_event)

                # tell the channel to update its org if this channel got moved
                if channel.org and "org_id" in cmd and channel.org.pk != cmd["org_id"]:
                    commands.append(dict(cmd="claim", org_id=channel.org.pk))

                # we don't ack status messages since they are always included
                handled = False

        # is this something we can ack?
        if "p_id" in cmd and handled:
            ack = dict(p_id=cmd["p_id"], cmd="ack")
            if extra:
                ack["extra"] = extra

            commands.append(ack)

    outgoing_cmds = get_commands(channel, commands, sync_event)
    result = dict(cmds=outgoing_cmds)

    if sync_event:
        sync_event.outgoing_command_count = len([_ for _ in outgoing_cmds if _["cmd"] != "ack"])
        sync_event.save()

    # keep track of how long a sync takes
    analytics.gauge("temba.relayer_sync", time.time() - start)

    return JsonResponse(result)


@csrf_exempt
def register(request):
    """
    Endpoint for Android devices registering with this server
    """
    if request.method != "POST":
        return HttpResponse(status=500, content=_("POST Required"))

    client_payload = json.loads(force_str(request.body))
    cmds = client_payload["cmds"]

    try:
        # look up a channel with that id
        channel = Channel.get_or_create_android(cmds[0], cmds[1])
        cmd = dict(
            cmd="reg", relayer_claim_code=channel.claim_code, relayer_secret=channel.secret, relayer_id=channel.id
        )
    except UnsupportedAndroidChannelError:
        cmd = dict(cmd="reg", relayer_claim_code="*********", relayer_secret="0" * 64, relayer_id=-1)

    return JsonResponse(dict(cmds=[cmd]))


class ClaimViewMixin(SpaMixin, OrgPermsMixin, ComponentFormMixin):
    permission = "channels.channel_claim"
    channel_type = None

    class Form(forms.Form):
        def __init__(self, **kwargs):
            self.request = kwargs.pop("request")
            self.channel_type = kwargs.pop("channel_type")
            super().__init__(**kwargs)

    def __init__(self, channel_type):
        self.channel_type = channel_type
        super().__init__()

    def get_template_names(self):
        return (
            [self.template_name]
            if self.template_name
            else ["channels/types/%s/claim.html" % self.channel_type.slug, "channels/channel_claim_form.html"]
        )

    def derive_title(self):
        return _("Connect %(channel_type)s") % {"channel_type": self.channel_type.name}

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        kwargs["channel_type"] = self.channel_type
        return kwargs

    def get_success_url(self):
        if self.channel_type.show_edit_page:
            return reverse("channels.channel_update", args=[self.object.id])
        elif self.channel_type.show_config_page:
            return reverse("channels.channel_configuration", args=[self.object.uuid])
        else:
            return reverse("channels.channel_read", args=[self.object.uuid])


class AuthenticatedExternalClaimView(ClaimViewMixin, SmartFormView):
    form_blurb = _("You can connect your number by entering your credentials here.")
    username_label = _("Username")
    username_help = _("The username provided by the provider to use their API")
    password_label = _("Password")
    password_help = _("The password provided by the provider to use their API")

    def __init__(self, **kwargs):
        self.form_blurb = kwargs.pop("form_blurb", self.form_blurb)
        self.username_label = kwargs.pop("username_label", self.username_label)
        self.username_help = kwargs.pop("username_help", self.username_help)
        self.password_label = kwargs.pop("password_label", self.password_label)
        self.password_help = kwargs.pop("password_help", self.password_help)

        super().__init__(**kwargs)

    class Form(ClaimViewMixin.Form):
        country = forms.ChoiceField(
            choices=ALL_COUNTRIES,
            widget=SelectWidget(attrs={"searchable": True}),
            label=_("Country"),
            help_text=_("The country this phone number is used in"),
        )
        number = forms.CharField(
            max_length=14,
            min_length=1,
            label=_("Number"),
            help_text=_("The phone number or short code you are connecting with country code. ex: +250788123124"),
        )
        username = forms.CharField(
            label=_("Username"), help_text=_("The username provided by the provider to use their API")
        )
        password = forms.CharField(
            label=_("Password"), help_text=_("The password provided by the provider to use their API")
        )

        def clean_number(self):
            number = self.data["number"]

            # number is a shortcode, accept as is
            if len(number) > 0 and len(number) < 7:
                return number

            # otherwise, try to parse into an international format
            if number and number[0] != "+":
                number = "+" + number

            try:
                cleaned = phonenumbers.parse(number, None)
                return phonenumbers.format_number(cleaned, phonenumbers.PhoneNumberFormat.E164)
            except Exception:  # pragma: needs cover
                raise forms.ValidationError(
                    _("Invalid phone number, please include the country code. ex: +250788123123")
                )

    form_class = Form

    def lookup_field_label(self, context, field, default=None):
        if field == "password":
            return self.password_label

        elif field == "username":
            return self.username_label

        return super().lookup_field_label(context, field, default=default)

    def lookup_field_help(self, field, default=None):
        if field == "password":
            return self.password_help

        elif field == "username":
            return self.username_help

        return super().lookup_field_help(field, default=default)

    def get_submitted_country(self, data):
        return data["country"]

    def get_channel_config(self, org, data):
        """
        Subclasses can override this method to add in other channel config variables
        """
        return {}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_blurb"] = self.form_blurb
        return context

    def form_valid(self, form):
        org = self.request.user.get_org()

        data = form.cleaned_data
        extra_config = self.get_channel_config(org, data)
        self.object = Channel.add_authenticated_external_channel(
            org,
            self.request.user,
            self.get_submitted_country(data),
            data["number"],
            data["username"],
            data["password"],
            self.channel_type,
            data.get("url"),
            extra_config=extra_config,
        )

        return super().form_valid(form)


class AuthenticatedExternalCallbackClaimView(AuthenticatedExternalClaimView):
    def get_channel_config(self, org, data):
        return {Channel.CONFIG_CALLBACK_DOMAIN: org.get_brand_domain()}


class BaseClaimNumberMixin(ClaimViewMixin):
    def pre_process(self, *args, **kwargs):  # pragma: needs cover
        return None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        org = self.request.user.get_org()

        try:
            context["account_numbers"] = self.get_existing_numbers(org)
        except Exception as e:
            context["account_numbers"] = []
            context["error"] = str(e)

        context["search_url"] = self.get_search_url()
        context["claim_url"] = self.get_claim_url()

        context["search_countries"] = self.get_search_countries()
        context["supported_country_iso_codes"] = self.get_supported_country_iso_codes()

        return context

    def get_search_countries(self):
        search_countries = []

        for country in self.get_search_countries_tuple():
            search_countries.append(dict(key=country[0], label=country[1]))

        return search_countries

    def get_supported_country_iso_codes(self):
        supported_country_iso_codes = []

        for country in self.get_supported_countries_tuple():
            supported_country_iso_codes.append(country[0])

        return supported_country_iso_codes

    def get_search_countries_tuple(self):  # pragma: no cover
        raise NotImplementedError(
            'method "get_search_countries_tuple" should be overridden in %s.%s'
            % (self.crudl.__class__.__name__, self.__class__.__name__)
        )

    def get_supported_countries_tuple(self):  # pragma: no cover
        raise NotImplementedError(
            'method "get_supported_countries_tuple" should be overridden in %s.%s'
            % (self.crudl.__class__.__name__, self.__class__.__name__)
        )

    def get_search_url(self):  # pragma: no cover
        raise NotImplementedError(
            'method "get_search_url" should be overridden in %s.%s'
            % (self.crudl.__class__.__name__, self.__class__.__name__)
        )

    def get_claim_url(self):  # pragma: no cover
        raise NotImplementedError(
            'method "get_claim_url" should be overridden in %s.%s'
            % (self.crudl.__class__.__name__, self.__class__.__name__)
        )

    def get_existing_numbers(self, org):  # pragma: no cover
        raise NotImplementedError(
            'method "get_existing_numbers" should be overridden in %s.%s'
            % (self.crudl.__class__.__name__, self.__class__.__name__)
        )

    def is_valid_country(self, calling_code: int) -> bool:  # pragma: no cover
        raise NotImplementedError(
            'method "is_valid_country" should be overridden in %s.%s'
            % (self.crudl.__class__.__name__, self.__class__.__name__)
        )

    def is_messaging_country(self, country_code: str) -> bool:  # pragma: no cover
        raise NotImplementedError(
            'method "is_messaging_country" should be overridden in %s.%s'
            % (self.crudl.__class__.__name__, self.__class__.__name__)
        )

    def claim_number(self, user, phone_number, country, role):  # pragma: no cover
        raise NotImplementedError(
            'method "claim_number" should be overridden in %s.%s'
            % (self.crudl.__class__.__name__, self.__class__.__name__)
        )

    def remove_api_credentials_from_session(self):
        pass

    def form_valid(self, form, *args, **kwargs):

        # must have an org
        org = self.request.user.get_org()
        if not org:  # pragma: needs cover
            form._errors["upgrade"] = True
            form._errors["phone_number"] = form.error_class(
                [
                    _(
                        "Sorry, you need to have a workspace to add numbers. "
                        "You can still test things out for free using an Android phone."
                    )
                ]
            )
            return self.form_invalid(form)

        data = form.cleaned_data

        # no number parse for short codes
        if len(data["phone_number"]) > 6:
            phone = phonenumbers.parse(data["phone_number"])
            if not self.is_valid_country(phone.country_code):  # pragma: needs cover
                form._errors["phone_number"] = form.error_class(
                    [
                        _(
                            "Sorry, the number you chose is not supported. "
                            "You can still deploy in any country using your "
                            "own SIM card and an Android phone."
                        )
                    ]
                )
                return self.form_invalid(form)

        # don't add the same number twice to the same account
        existing = org.channels.filter(is_active=True, address=data["phone_number"]).first()
        if existing:  # pragma: needs cover
            form._errors["phone_number"] = form.error_class(
                [_("That number is already connected (%s)" % data["phone_number"])]
            )
            return self.form_invalid(form)

        existing = Channel.objects.filter(is_active=True, address=data["phone_number"]).first()
        if existing:  # pragma: needs cover
            form._errors["phone_number"] = form.error_class(
                [
                    _(
                        "That number is already connected to another account - %(org)s (%(user)s)"
                        % dict(org=existing.org, user=existing.created_by.username)
                    )
                ]
            )
            return self.form_invalid(form)

        error_message = None

        # try to claim the number
        try:
            role = Channel.ROLE_CALL + Channel.ROLE_ANSWER
            if self.is_messaging_country(data["country"]):
                role += Channel.ROLE_SEND + Channel.ROLE_RECEIVE
            self.claim_number(self.request.user, data["phone_number"], data["country"], role)
            self.remove_api_credentials_from_session()

            return HttpResponseRedirect("%s?success" % reverse("public.public_welcome"))

        except (
            nexmo.AuthenticationError,
            nexmo.ClientError,
            twilio.base.exceptions.TwilioRestException,
        ) as e:  # pragma: no cover
            logger.warning(f"Unable to claim a number: {str(e)}", exc_info=True)
            error_message = form.error_class([str(e)])

        except Exception as e:  # pragma: needs cover
            logger.error(f"Unable to claim a number: {str(e)}", exc_info=True)

            message = str(e)
            if message:
                error_message = form.error_class([message])
            else:
                error_message = form.error_class(
                    [
                        _(
                            "An error occurred connecting your Twilio number, try removing your "
                            "Twilio account, reconnecting it and trying again."
                        )
                    ]
                )

        if error_message is not None:
            form._errors["phone_number"] = error_message

        return self.form_invalid(form)


class UpdateChannelForm(forms.ModelForm):
    name = forms.CharField(
        label=_("Name"), max_length=64, required=True, help_text=_("Descriptive name for this channel.")
    )

    def __init__(self, *args, **kwargs):
        self.object = kwargs["object"]
        del kwargs["object"]

        super().__init__(*args, **kwargs)

        self.config_fields = []

        if URN.TEL_SCHEME in self.object.schemes:
            self.add_config_field(
                Channel.CONFIG_ALLOW_INTERNATIONAL,
                forms.BooleanField(required=False, help_text=_("Allow sending to and calling international numbers.")),
                default=False,
            )

        if Channel.ROLE_CALL in self.object.role:
            self.add_config_field(
                Channel.CONFIG_MACHINE_DETECTION,
                forms.BooleanField(
                    required=False, help_text=_("Perform answering machine detection and hangup if machine detected.")
                ),
                default=False,
            )

    def add_config_field(self, config_key: str, field, *, default):
        field.initial = self.instance.config.get(config_key, default)

        self.fields[config_key] = field
        self.config_fields.append(config_key)

    class Meta:
        model = Channel
        fields = ("name", "alert_email")
        config_fields = []
        readonly = ()
        labels = {}
        helps = {}


class UpdateWebChatForm(UpdateChannelForm):
    name = forms.CharField(
        label=_("Name"),
        help_text=_("Descriptive label for this channel"),
        widget=forms.TextInput(attrs={"required": "", "maxlength": "64"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.add_extra_fields()

        self.fields["theme"].choices = [
            (theme, settings.WIDGET_THEMES[theme]["name"]) for theme in list(settings.WIDGET_THEMES.keys())
        ]

        self.fields["logo_style"].choices = [("circle", _("Circle")), ("rectangle", _("Rectangle"))]

        self.fields["auto_open"].choices = [("false", _("Disable")), ("true", _("Enable"))]

        self.fields["side_of_screen"].choices = [("right", _("Right")), ("left", _("Left"))]

        if self.instance.config:
            config = self.instance.config
            default_theme = settings.WIDGET_THEMES.get(settings.WIDGET_DEFAULT_THEME, {})
            self.fields["theme"].initial = config.get("theme", "")
            self.fields["title"].initial = config.get("title", "")
            self.fields["logo_style"].initial = config.get("logo_style", "circle")
            self.fields["widget_bg_color"].initial = config.get("widget_bg_color", default_theme.get("widget_bg"))
            self.fields["chat_header_bg_color"].initial = config.get(
                "chat_header_bg_color", default_theme.get("header_bg")
            )
            self.fields["chat_header_text_color"].initial = config.get(
                "chat_header_text_color", default_theme.get("header_txt")
            )
            self.fields["automated_chat_bg"].initial = config.get(
                "automated_chat_bg", default_theme.get("automated_chat_bg")
            )
            self.fields["automated_chat_txt"].initial = config.get(
                "automated_chat_txt", default_theme.get("automated_chat_txt")
            )
            self.fields["user_chat_bg"].initial = config.get("user_chat_bg", default_theme.get("user_chat_bg"))
            self.fields["user_chat_txt"].initial = config.get("user_chat_txt", default_theme.get("user_chat_txt"))

            self.fields["width"].initial = config.get("width", "400")
            self.fields["height"].initial = config.get("height", "550")
            self.fields["chat_button_height"].initial = config.get("chat_button_height", "64")
            self.fields["side_padding"].initial = config.get("side_padding", "20")
            self.fields["bottom_padding"].initial = config.get("bottom_padding", "20")
            self.fields["side_of_screen"].initial = config.get("side_of_screen", "right")

            self.fields["action_type"].initial = "update_and_generate_code_snippet"

            self.fields["welcome_message_default"].initial = config.get("welcome_message_default", "")

            self.fields["welcome_msg_quick_replies"].initial = config.get("welcome_msg_quick_replies", [])

            self.fields["inputtext_placeholder_default"].initial = config.get("inputtext_placeholder_default", "")

            self.fields["store_history"].initial = config.get("store_history", False)
            self.fields["allow_attachments"].initial = config.get("allow_attachments", False)
            self.fields["allow_multi_language"].initial = config.get("allow_multi_language", False)
            self.fields["allow_fab_text"].initial = config.get("allow_fab_text", False)
            self.fields["fab_text"].initial = config.get("fab_text", "")

            response_fonts = requests.get(
                f"https://www.googleapis.com/webfonts/v1/webfonts?key={settings.GOOGLE_FONT_API_KEY}"
            )

            if response_fonts.status_code == 200:
                font_list_json = response_fonts.json()
                self.fields["google_font"].choices = [("", "")] + [
                    (item.get("family"), item.get("family")) for item in font_list_json.get("items", [])
                ]

            self.fields["google_font"].initial = config.get("google_font", "")

            for lang in self.object.org.flow_languages:
                lang_alpha = pycountry.languages.get(alpha_3=lang)
                if not hasattr(lang_alpha, "alpha_2"):
                    continue

                self.fields[f"welcome_message_{lang_alpha.alpha_2}"].initial = config.get(
                    f"welcome_message_{lang_alpha.alpha_2}", ""
                )

                self.fields[f"inputtext_placeholder_{lang_alpha.alpha_2}"].initial = config.get(
                    f"inputtext_placeholder_{lang_alpha.alpha_2}", ""
                )

    def clean_name(self):
        org = self.object.org
        name = self.cleaned_data["name"]

        if not regex.match(r"^[A-Za-z0-9_.\-*() ]+$", name, regex.V0):
            raise forms.ValidationError(
                "Please make sure the WebChat name only contains "
                "alphanumeric characters [0-9a-zA-Z], hyphens, and underscores"
            )

        # does a ws channel already exists on this account with that name
        existing = Channel.objects.filter(
            org=org, is_active=True, channel_type=self.object.channel_type, name=name
        ).first()

        if existing and existing != self.object:
            raise ValidationError(_("A WebChat channel for this name already exists on your account."))

        return name

    def clean_width(self):
        width = self.cleaned_data["width"]

        if str(width).endswith("px"):
            width = str(width).replace("px", "")

        if int(width) < 250:
            raise ValidationError(_("The minimum width is 250px."))

        return width

    def clean_height(self):
        height = self.cleaned_data["height"]

        if str(height).endswith("px"):
            height = str(height).replace("px", "")

        if int(height) < 300:
            raise ValidationError(_("The minimum height is 300px."))

        return height

    def clean_title(self):
        title = self.cleaned_data["title"]

        if len(title) > 40:
            raise ValidationError(
                _("Oops, the maximum length for a title is only 40 characters, " "your title has %s." % len(title))
            )

        return title

    def clean_logo(self):
        channel = self.instance
        org = channel.org
        logo = self.cleaned_data.get("logo")
        config = channel.config

        logo_media = config.get(Channel.CONFIG_WCH_LOGO, None)

        if logo and len(logo) > 0 and not str(logo).startswith("http"):
            if logo.size > 1000000:
                raise ValidationError(_("Too big logo for the Widget, it does not accept more than 1MB"))

            extension = logo.name.split(".")[-1]
            if extension not in ["png", "jpg", "jpeg", "gif"]:
                raise ValidationError(
                    _("Please, upload a logo using one of the following formats: PNG, JPG, " "JPEG or GIF")
                )

            logo_media = org.save_media(logo, extension)

        return logo_media

    def clean_welcome_msg_quick_replies(self):
        quick_replies = self.cleaned_data.get("welcome_msg_quick_replies", "")
        quick_replies = ast.literal_eval(quick_replies if quick_replies else "[]")
        return [item for item in quick_replies if item != ""]

    def add_extra_fields(self):

        self.add_config_field(
            "logo",
            forms.FileField(
                label=_("Logo"), required=False, help_text=_("We recommend to upload an image with 64x64px")
            ),
            default=None,
        )

        self.add_config_field(
            "logo_style",
            forms.ChoiceField(
                label=_("Logo Style"),
                help_text=_("This is related to how we will display the widget when it's closed"),
            ),
            default=None,
        )

        self.add_config_field(
            "title",
            forms.CharField(
                label=_("Chat Title"),
                help_text=_("It will appear on the header of the WebChat"),
                widget=forms.TextInput(attrs={"required": "", "maxlength": 40}),
            ),
            default=None,
        )

        self.add_config_field(
            "welcome_message_default",
            forms.CharField(
                label=_("Welcome Message"), widget=forms.Textarea(attrs={"style": "height: 110px", "required": ""})
            ),
            default=None,
        )

        org = self.object.org
        for lang in org.flow_languages:
            lang_alpha = pycountry.languages.get(alpha_3=lang)
            if not hasattr(lang_alpha, "alpha_2"):
                continue

            self.add_config_field(
                f"welcome_message_{lang_alpha.alpha_2}",
                forms.CharField(
                    label=_(f"Welcome Message {lang_alpha.name}"),
                    required=False,
                    widget=forms.Textarea(attrs={"style": "height: 110px"}),
                ),
                default="",
            )

        self.add_config_field(
            "welcome_msg_quick_replies",
            forms.CharField(
                required=False,
                label=_("Welcome Message Quick Replies"),
                widget=SelectMultipleWidget(
                    attrs={
                        "widget_only": False,
                        "searchable": True,
                        "tags": True,
                        "space_select": False,
                        "placeholder": _("Quick Replies"),
                    }
                ),
            ),
            default=None,
        )

        self.add_config_field(
            "inputtext_placeholder_default",
            forms.CharField(
                label=_("Input Text Placeholder"),
                widget=forms.Textarea(attrs={"style": "height: 110px", "required": ""}),
            ),
            default=None,
        )

        # Unfortunately, duplicated "for" here because of the frontend implementation
        for lang in org.flow_languages:
            lang_alpha = pycountry.languages.get(alpha_3=lang)
            if not hasattr(lang_alpha, "alpha_2"):
                continue

            self.add_config_field(
                f"inputtext_placeholder_{lang_alpha.alpha_2}",
                forms.CharField(
                    label=_(f"Input Text Placeholder {lang_alpha.name}"),
                    required=False,
                    widget=forms.Textarea(attrs={"style": "height: 110px"}),
                ),
                default="",
            )

        self.add_config_field(
            "google_font",
            forms.ChoiceField(
                label=_("Google Font"),
                required=False,
                widget=SelectWidget(attrs={"searchable": True, "clearable": True}),
            ),
            default=None,
        )

        self.add_config_field("theme", forms.ChoiceField(label=_("Theme"), required=False), default=None)

        self.add_config_field(
            "widget_bg_color",
            forms.CharField(label=_("Widget Background Color"), widget=forms.TextInput()),
            default=None,
        )

        self.add_config_field(
            "chat_header_bg_color",
            forms.CharField(label=_("Chat Header Background Color"), widget=forms.TextInput()),
            default=None,
        )

        self.add_config_field(
            "chat_header_text_color",
            forms.CharField(label=_("Chat Header Text Color"), widget=forms.TextInput()),
            default=None,
        )

        self.add_config_field(
            "automated_chat_bg",
            forms.CharField(label=_("Automated Chat Background"), widget=forms.TextInput()),
            default=None,
        )

        self.add_config_field(
            "automated_chat_txt",
            forms.CharField(label=_("Automated Chat Text"), widget=forms.TextInput()),
            default=None,
        )

        self.add_config_field(
            "user_chat_bg",
            forms.CharField(label=_("User Chat Background"), widget=forms.TextInput()),
            default=None,
        )

        self.add_config_field(
            "user_chat_txt",
            forms.CharField(label=_("User Chat Text"), widget=forms.TextInput()),
            default=None,
        )

        self.add_config_field(
            "width",
            forms.CharField(
                label=_("Width (in pixels)"),
                widget=forms.NumberInput(),
                help_text=_("Default: 400 | Minimum: 250"),
            ),
            default=None,
        )

        self.add_config_field(
            "height",
            forms.CharField(
                label=_("Height (in pixels)"),
                widget=forms.NumberInput(),
                help_text=_("Default: 550 | Minimum: 300"),
            ),
            default=None,
        )

        self.add_config_field(
            "chat_button_height",
            forms.CharField(label=_("Chat Button Height (in pixels)"), widget=forms.NumberInput()),
            default=None,
        )

        self.add_config_field(
            "side_padding",
            forms.CharField(label=_("Side Padding (# of Pixels)"), widget=forms.NumberInput()),
            default=None,
        )

        self.add_config_field(
            "bottom_padding",
            forms.CharField(label=_("Bottom Padding (# of Pixels)"), widget=forms.NumberInput()),
            default=None,
        )

        self.add_config_field(
            "side_of_screen",
            forms.ChoiceField(
                label=_("Side of Screen"),
                help_text=_("This is related to the side of the screen that we will display the widget"),
            ),
            default=None,
        )

        self.add_config_field(
            "auto_open",
            forms.ChoiceField(
                label=_("Auto Open"), help_text=_("Whether the chatbox should open when the website page is loaded")
            ),
            default=None,
        )

        self.add_config_field(
            "action_type", forms.CharField(widget=forms.HiddenInput()), default="update_and_generate_code_snippet"
        )

        self.add_config_field(
            "store_history",
            forms.BooleanField(
                label=_("Store History"),
                help_text=_(
                    "Keeps chat identifier in cookies so the history can be accessible after the page refresh"
                ),
                required=False,
            ),
            default=False,
        )

        self.add_config_field(
            "allow_attachments",
            forms.BooleanField(
                label=_("Allow Attachments"),
                help_text=_("Check this box whether the attachment icon is available for this WebChat"),
                required=False,
            ),
            default=False,
        )

        self.add_config_field(
            "allow_multi_language",
            forms.BooleanField(
                label=_("Allow Language Select"),
                help_text=_("Check this box whether the language selection is available for this WebChat"),
                required=False,
            ),
            default=False,
        )

        self.add_config_field(
            "allow_fab_text",
            forms.BooleanField(
                label=_("Use a text button"),
                help_text=_(
                    "Whether you want to use a text button instead of icon "
                    "(you can specify text you want once you checking this box)"
                ),
                required=False,
            ),
            default=False,
        )

        self.add_config_field(
            "fab_text", forms.CharField(label=_("Text for the button"), max_length=64, required=False), default=False
        )

        unlisted_fields = ["name", "alert_email"]

        for field in list(self.fields):
            if field in unlisted_fields:
                continue
            self.Meta.config_fields.append(field)

    class Meta(UpdateChannelForm.Meta):
        readonly = []


class UpdateTelChannelForm(UpdateChannelForm):
    class Meta(UpdateChannelForm.Meta):
        helps = {"address": _("Phone number of this channel")}


class ChannelCRUDL(SmartCRUDL):
    model = Channel
    actions = (
        "list",
        "claim",
        "claim_all",
        "menu",
        "update",
        "read",
        "delete",
        "configuration",
        "bulk_sender_options",
        "create_bulk_sender",
        "create_caller",
        "facebook_whitelist",
    )
    permissions = True

    class Menu(MenuMixin, OrgPermsMixin, SmartTemplateView):  # pragma: no cover
        def derive_menu(self):
            org = self.request.user.get_org()

            menu = []
            if self.has_org_perm("channels.channel_read"):
                from temba.channels.views import get_channel_read_url

                channels = Channel.objects.filter(org=org, is_active=True, parent=None).order_by("-role")
                for channel in channels:
                    icon = channel.get_type().icon.replace("icon-", "")
                    icon = icon.replace("power-cord", "box")

                    menu.append(
                        self.create_menu_item(
                            menu_id=channel.uuid,
                            name=channel.name,
                            href=get_channel_read_url(channel),
                            icon=icon,
                        )
                    )

            menu.append(self.create_menu_item(menu_id="claim", name=_("Add Channel"), href="channels.channel_claim"))

            return menu

    class Read(SpaMixin, OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"
        exclude = ("id", "is_active", "created_by", "modified_by", "modified_on")

        def get_queryset(self):
            return Channel.objects.filter(is_active=True)

        def get_gear_links(self):
            links = []

            extra_links = self.object.get_type().extra_links
            if extra_links:
                for extra in extra_links:
                    links.append(dict(title=extra["name"], href=reverse(extra["link"], args=[self.object.uuid])))

            if self.object.parent:
                links.append(
                    dict(
                        title=_("Android Channel"),
                        style="button-primary",
                        href=reverse("channels.channel_read", args=[self.object.parent.uuid]),
                    )
                )

            if self.object.get_type().show_config_page:
                links.append(
                    dict(title=_("Settings"), href=reverse("channels.channel_configuration", args=[self.object.uuid]))
                )

            if not self.object.is_android():
                sender = self.object.get_sender()
                caller = self.object.get_caller()

                if sender:
                    links.append(
                        dict(title=_("Channel Log"), href=reverse("channels.channellog_list", args=[sender.uuid]))
                    )
                elif Channel.ROLE_RECEIVE in self.object.role:
                    links.append(
                        dict(title=_("Channel Log"), href=reverse("channels.channellog_list", args=[self.object.uuid]))
                    )

                if caller and caller != sender:
                    links.append(
                        dict(
                            title=_("Call Log"),
                            href=f"{reverse('channels.channellog_list', args=[caller.uuid])}?sessions=1",
                        )
                    )

            if self.has_org_perm("channels.channel_update"):
                edit_link = dict(
                    id="update-channel",
                    title=_("Edit"),
                    href=reverse("channels.channel_update", args=[self.object.id]),
                    modax=_("Edit Channel"),
                )
                if self.object.channel_type == "WCH":
                    del edit_link["modax"]

                links.append(edit_link)

                if self.object.is_android() or (self.object.parent and self.object.parent.is_android()):

                    sender = self.object.get_sender()
                    if sender and sender.is_delegate_sender():
                        links.append(
                            dict(
                                id="disable-sender",
                                title=_("Disable Bulk Sending"),
                                modax=_("Disable Bulk Sending"),
                                href=reverse("channels.channel_delete", args=[sender.uuid]),
                            )
                        )
                    elif self.object.is_android():
                        links.append(
                            dict(
                                title=_("Enable Bulk Sending"),
                                href="%s?channel=%d"
                                % (reverse("channels.channel_bulk_sender_options"), self.object.id),
                            )
                        )

                    caller = self.object.get_caller()
                    if caller and caller.is_delegate_caller():
                        links.append(
                            dict(
                                id="disable-voice",
                                title=_("Disable Voice Calling"),
                                modax=_("Disable Voice Calling"),
                                href=reverse("channels.channel_delete", args=[caller.uuid]),
                            )
                        )
                    elif self.object.org.is_connected_to_twilio():
                        links.append(
                            dict(
                                id="enable-voice",
                                title=_("Enable Voice Calling"),
                                js_class="posterize",
                                href=f"{reverse('channels.channel_create_caller')}?channel={self.object.id}",
                            )
                        )

            if self.has_org_perm("channels.channel_delete"):
                links.append(
                    dict(
                        id="delete-channel",
                        title=_("Delete Channel"),
                        modax=_("Delete Channel"),
                        href=reverse("channels.channel_delete", args=[self.object.uuid]),
                    )
                )

            if self.object.channel_type == "FB" and self.has_org_perm("channels.channel_facebook_whitelist"):
                links.append(
                    dict(
                        id="fb-whitelist",
                        title=_("Whitelist Domain"),
                        modax=_("Whitelist Domain"),
                        href=reverse("channels.channel_facebook_whitelist", args=[self.object.uuid]),
                    )
                )

            user = self.get_user()
            if user.is_superuser or user.is_staff:
                links.append(
                    dict(
                        title=_("Service"),
                        posterize=True,
                        href=f'{reverse("orgs.org_service")}?organization={self.object.org_id}&redirect_url={reverse("channels.channel_read", args=[self.object.uuid])}',
                    )
                )

            return links

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            channel = self.object

            sync_events = SyncEvent.objects.filter(channel=channel.id).order_by("-created_on")
            context["last_sync"] = sync_events.first()

            if "HTTP_X_FORMAX" in self.request.META:  # no additional data needed if request is only for formax
                return context

            if not channel.is_active:  # pragma: needs cover
                raise Http404("No active channel with that id")

            context["msg_count"] = channel.get_msg_count()
            context["ivr_count"] = channel.get_ivr_count()

            # power source stats data
            source_stats = [
                [event["power_source"], event["count"]]
                for event in sync_events.order_by("power_source")
                .values("power_source")
                .annotate(count=Count("power_source"))
            ]
            context["source_stats"] = source_stats

            # network connected to stats
            network_stats = [
                [event["network_type"], event["count"]]
                for event in sync_events.order_by("network_type")
                .values("network_type")
                .annotate(count=Count("network_type"))
            ]
            context["network_stats"] = network_stats

            total_network = 0
            network_share = []

            for net in network_stats:
                total_network += net[1]

            total_share = 0
            for net_stat in network_stats:
                share = int(round((100 * net_stat[1]) / float(total_network)))
                net_name = net_stat[0]

                if net_name != "NONE" and net_name != "UNKNOWN" and share > 0:
                    network_share.append([net_name, share])
                    total_share += share

            other_share = 100 - total_share
            if other_share > 0:
                network_share.append(["OTHER", other_share])

            context["network_share"] = sorted(network_share, key=lambda _: _[1], reverse=True)

            # add to context the latest sync events to display in a table
            context["latest_sync_events"] = sync_events[:10]

            # delayed sync event
            if not channel.is_new():
                if sync_events:
                    latest_sync_event = sync_events[0]
                    interval = timezone.now() - latest_sync_event.created_on
                    seconds = interval.seconds + interval.days * 24 * 3600
                    if seconds > 3600:
                        context["delayed_sync_event"] = latest_sync_event

                # unsent messages
                unsent_msgs = channel.get_delayed_outgoing_messages()

                if unsent_msgs:
                    context["unsent_msgs_count"] = unsent_msgs.count()

            end_date = (timezone.now() + timedelta(days=1)).date()
            start_date = end_date - timedelta(days=30)

            context["start_date"] = start_date
            context["end_date"] = end_date

            message_stats = []

            # build up the channels we care about for outgoing messages
            channels = [channel]
            for sender in Channel.objects.filter(parent=channel):
                channels.append(sender)

            msg_in = []
            msg_out = []
            ivr_in = []
            ivr_out = []

            message_stats.append(dict(name=_("Incoming Text"), data=msg_in))
            message_stats.append(dict(name=_("Outgoing Text"), data=msg_out))

            if context["ivr_count"]:
                message_stats.append(dict(name=_("Incoming Voice Messages"), data=ivr_in))
                message_stats.append(dict(name=_("Outgoing Voice Messages"), data=ivr_out))

            # get all our counts for that period
            daily_counts = list(
                ChannelCount.objects.filter(channel__in=channels, day__gte=start_date)
                .filter(
                    count_type__in=[
                        ChannelCount.INCOMING_MSG_TYPE,
                        ChannelCount.OUTGOING_MSG_TYPE,
                        ChannelCount.INCOMING_IVR_TYPE,
                        ChannelCount.OUTGOING_IVR_TYPE,
                    ]
                )
                .values("day", "count_type")
                .order_by("day", "count_type")
                .annotate(count_sum=Sum("count"))
            )

            current = start_date
            while current <= end_date:
                # for every date we care about
                while daily_counts and daily_counts[0]["day"] == current:
                    daily_count = daily_counts.pop(0)
                    if daily_count["count_type"] == ChannelCount.INCOMING_MSG_TYPE:
                        msg_in.append(dict(date=daily_count["day"], count=daily_count["count_sum"]))
                    elif daily_count["count_type"] == ChannelCount.OUTGOING_MSG_TYPE:
                        msg_out.append(dict(date=daily_count["day"], count=daily_count["count_sum"]))
                    elif daily_count["count_type"] == ChannelCount.INCOMING_IVR_TYPE:
                        ivr_in.append(dict(date=daily_count["day"], count=daily_count["count_sum"]))
                    elif daily_count["count_type"] == ChannelCount.OUTGOING_IVR_TYPE:
                        ivr_out.append(dict(date=daily_count["day"], count=daily_count["count_sum"]))

                current = current + timedelta(days=1)

            context["message_stats"] = message_stats
            context["has_messages"] = len(msg_in) or len(msg_out) or len(ivr_in) or len(ivr_out)

            message_stats_table = []

            # we'll show totals for every month since this channel was started (limited to 12 months ago)
            month_start = channel.created_on.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

            now = timezone.now()
            twelve_months_ago = now + relativedelta(months=-12)
            twelve_months_ago = twelve_months_ago.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

            if month_start < twelve_months_ago:
                month_start = twelve_months_ago

            # get our totals grouped by month
            monthly_totals = list(
                ChannelCount.objects.filter(channel=channel, day__gte=month_start)
                .filter(
                    count_type__in=[
                        ChannelCount.INCOMING_MSG_TYPE,
                        ChannelCount.OUTGOING_MSG_TYPE,
                        ChannelCount.INCOMING_IVR_TYPE,
                        ChannelCount.OUTGOING_IVR_TYPE,
                    ]
                )
                .extra({"month": "date_trunc('month', day)"})
                .values("month", "count_type")
                .order_by("month", "count_type")
                .annotate(count_sum=Sum("count"))
            )

            context["total_incoming_messages_count"] = 0
            context["total_outgoing_messages_count"] = 0
            context["total_incoming_ivr_count"] = 0
            context["total_outgoing_ivr_count"] = 0

            # calculate our summary table for last 12 months
            while month_start < now:
                msg_in = 0
                msg_out = 0
                ivr_in = 0
                ivr_out = 0

                while monthly_totals and monthly_totals[0]["month"] == month_start:
                    monthly_total = monthly_totals.pop(0)
                    if monthly_total["count_type"] == ChannelCount.INCOMING_MSG_TYPE:
                        msg_in = monthly_total["count_sum"]
                    elif monthly_total["count_type"] == ChannelCount.OUTGOING_MSG_TYPE:
                        msg_out = monthly_total["count_sum"]
                    elif monthly_total["count_type"] == ChannelCount.INCOMING_IVR_TYPE:
                        ivr_in = monthly_total["count_sum"]
                    elif monthly_total["count_type"] == ChannelCount.OUTGOING_IVR_TYPE:
                        ivr_out = monthly_total["count_sum"]

                message_stats_table.append(
                    dict(
                        month_start=month_start,
                        incoming_messages_count=msg_in,
                        outgoing_messages_count=msg_out,
                        incoming_ivr_count=ivr_in,
                        outgoing_ivr_count=ivr_out,
                    )
                )

                context["total_incoming_messages_count"] += msg_in
                context["total_outgoing_messages_count"] += msg_out
                context["total_incoming_ivr_count"] += ivr_in
                context["total_outgoing_ivr_count"] += ivr_out

                month_start = (month_start + timedelta(days=32)).replace(day=1)

            # reverse our table so most recent is first
            message_stats_table.reverse()
            context["message_stats_table"] = message_stats_table

            return context

    class FacebookWhitelist(ComponentFormMixin, ModalMixin, OrgObjPermsMixin, SmartModelActionView):
        class DomainForm(forms.Form):
            whitelisted_domain = forms.URLField(
                required=True,
                initial="https://",
                help_text="The domain to whitelist for Messenger extensions ex: https://yourdomain.com",
            )

        slug_url_kwarg = "uuid"
        success_url = "uuid@channels.channel_read"
        form_class = DomainForm

        def get_queryset(self):
            return Channel.objects.filter(is_active=True, org=self.request.user.get_org(), channel_type="FB")

        def execute_action(self):
            # curl -X POST -H "Content-Type: application/json" -d '{
            #  "setting_type" : "domain_whitelisting",
            #  "whitelisted_domains" : ["https://petersfancyapparel.com"],
            #  "domain_action_type": "add"
            # }' "https://graph.facebook.com/v3.3/me/thread_settings?access_token=PAGE_ACCESS_TOKEN"
            access_token = self.object.config[Channel.CONFIG_AUTH_TOKEN]
            response = requests.post(
                "https://graph.facebook.com/v3.3/me/thread_settings?access_token=" + access_token,
                json=dict(
                    setting_type="domain_whitelisting",
                    whitelisted_domains=[self.form.cleaned_data["whitelisted_domain"]],
                    domain_action_type="add",
                ),
            )

            if response.status_code != 200:
                response_json = response.json()
                default_error = dict(message=_("An error occured contacting the Facebook API"))
                raise ValidationError(response_json.get("error", default_error)["message"])

    class Delete(DependencyDeleteModal):
        cancel_url = "uuid@channels.channel_read"
        success_message = _("Your channel has been removed.")
        success_message_twilio = _(
            "We have disconnected your Twilio number. "
            "If you do not need this number you can delete it from the Twilio website."
        )

        def get_success_url(self):
            # if we're deleting a child channel, redirect to parent afterwards
            channel = self.get_object()
            if channel.parent:
                return reverse("channels.channel_read", args=[channel.parent.uuid])

            return reverse("orgs.org_home")

        def derive_submit_button_name(self):
            channel = self.get_object()

            if channel.is_delegate_caller():
                return _("Disable Voice Calling")
            if channel.is_delegate_sender():
                return _("Disable Bulk Sending")

            return super().derive_submit_button_name()

        def post(self, request, *args, **kwargs):
            channel = self.get_object()

            try:
                channel.release(request.user)
            except TwilioRestException as e:
                messages.error(
                    request,
                    _(
                        f"Twilio reported an error removing your channel (error code {e.code}). Please try again later."
                    ),
                )

                response = HttpResponse()
                response["Temba-Success"] = self.cancel_url
                return response

            # override success message for Twilio channels
            if channel.channel_type == "T" and not channel.is_delegate_sender():
                messages.info(request, self.success_message_twilio)
            else:
                messages.info(request, self.success_message)

            response = HttpResponse()
            response["Temba-Success"] = self.get_success_url()
            return response

    class Update(OrgObjPermsMixin, ComponentFormMixin, ModalMixin, SmartUpdateView):
        success_message = ""
        submit_button_name = _("Save Changes")

        def set_submit_button_name(self, name):
            self.submit_button_name = name

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            if self.object.channel_type == "WCH":
                context["customable_fields"] = [
                    "#id_title",
                    "#id_widget_bg_color",
                    "#id_chat_header_bg_color",
                    "#id_chat_header_text_color",
                    "#id_automated_chat_bg",
                    "#id_automated_chat_txt",
                    "#id_user_chat_bg",
                    "#id_user_chat_txt",
                    "#id_welcome_message_default",
                    "#id_side_padding",
                    "#id_bottom_padding",
                    "#id_chat_button_height",
                    "#id_width",
                    "#id_height",
                ]
                context["hostname"] = settings.HOSTNAME
                context["websocket_url"] = settings.WEBSOCKET_SERVER_URL
                logo_img = self.object.config.get("logo")
                context["wch_logo_size"] = get_image_size(logo_img=logo_img)

                context["widget_compiled_file"] = settings.WIDGET_COMPILED_FILE

                context_languages = []
                for lang in self.object.org.flow_languages:
                    lang_alpha = pycountry.languages.get(alpha_3=lang)
                    if not hasattr(lang_alpha, "alpha_2"):
                        continue
                    context_languages.append(dict(name=lang_alpha.name, iso_code=lang_alpha.alpha_2))
                context["languages"] = context_languages

            return context

        def derive_title(self):
            if self.object.channel_type == "WCH":
                channel_type_display = self.object.get_channel_type_display()
                self.set_submit_button_name(_("Save and Generate Code Snippet"))
            else:
                channel_type_display = f"{self.object.get_channel_type_display()} Channel"
            return _("%s") % channel_type_display

        def derive_readonly(self):
            return self.form.Meta.readonly if hasattr(self, "form") else []

        def lookup_field_label(self, context, field, default=None):
            if field in self.form.Meta.labels:
                return self.form.Meta.labels[field]
            return super().lookup_field_label(context, field, default=default)

        def lookup_field_help(self, field, default=None):
            if field in self.form.Meta.helps:
                return self.form.Meta.helps[field]
            return super().lookup_field_help(field, default=default)

        def get_success_url(self):
            has_reload = self.request.POST.get("action_type", None) == "update_and_reload"
            if has_reload:
                viewname = "channels.channel_update"
                arg = self.object.id
            else:
                viewname = (
                    "channels.channel_configuration" if self.object.channel_type == "WCH" else "channels.channel_read"
                )
                arg = self.object.uuid
            return reverse(viewname=viewname, args=[arg])

        def get_form_class(self):
            return Channel.get_type_from_code(self.object.channel_type).get_update_form()

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["object"] = self.object
            return kwargs

        def derive_initial(self):
            initial = super().derive_initial()
            initial["role"] = [char for char in self.object.role]
            return initial

        def pre_save(self, obj):
            for field in self.form.config_fields:
                try:
                    if field == "action_type":
                        continue
                    obj.config[field] = self.form.cleaned_data[field]
                except KeyError:
                    pass
            return obj

        def post_save(self, obj):
            # update our delegate channels with the new number
            if not obj.parent and URN.TEL_SCHEME in obj.schemes:
                e164_phone_number = None
                try:
                    parsed = phonenumbers.parse(obj.address, None)
                    e164_phone_number = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164).strip(
                        "+"
                    )
                except Exception:  # pragma: needs cover
                    pass
                for channel in obj.get_delegate_channels():  # pragma: needs cover
                    channel.address = obj.address
                    channel.bod = e164_phone_number
                    channel.save(update_fields=("address", "bod"))
            return obj

    class Claim(SpaMixin, OrgPermsMixin, SmartTemplateView):
        def channel_types_groups(self):
            user = self.request.user

            # fetch channel types, sorted by category and name
            types_by_category = defaultdict(list)
            recommended_channels = []
            for ch_type in list(Channel.get_types()):
                region_aware_visible, region_ignore_visible = ch_type.is_available_to(user)

                if ch_type.is_recommended_to(user):
                    recommended_channels.append(ch_type)
                elif region_ignore_visible and region_aware_visible and ch_type.category:
                    types_by_category[ch_type.category.name].append(ch_type)

            return recommended_channels, types_by_category, True

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            user = self.request.user

            org = user.get_org()
            context["org_timezone"] = str(org.timezone)
            context["brand"] = org.get_branding()

            # fetch channel types, sorted by category and name
            recommended_channels, types_by_category, only_regional_channels = self.channel_types_groups()

            context["recommended_channels"] = recommended_channels
            context["channel_types"] = types_by_category
            context["only_regional_channels"] = only_regional_channels
            return context

    class ClaimAll(Claim):
        def channel_types_groups(self):
            user = self.request.user

            types_by_category = defaultdict(list)
            recommended_channels = []
            for ch_type in list(Channel.get_types()):
                region_aware_visible, region_ignore_visible = ch_type.is_available_to(user)
                if ch_type.is_recommended_to(user):
                    recommended_channels.append(ch_type)
                elif region_ignore_visible and ch_type.category:
                    types_by_category[ch_type.category.name].append(ch_type)

            return recommended_channels, types_by_category, False

    class BulkSenderOptions(OrgPermsMixin, SmartTemplateView):
        pass

    class CreateBulkSender(OrgPermsMixin, SmartFormView):
        class BulkSenderForm(forms.Form):
            connection = forms.CharField(max_length=2, widget=forms.HiddenInput, required=False)
            channel = forms.IntegerField(widget=forms.HiddenInput, required=False)

            def __init__(self, *args, **kwargs):
                self.org = kwargs["org"]
                del kwargs["org"]
                super().__init__(*args, **kwargs)

            def clean_connection(self):
                connection = self.cleaned_data["connection"]
                if connection == "NX" and not self.org.is_connected_to_vonage():
                    raise forms.ValidationError(_("A connection to a Vonage account is required"))
                return connection

            def clean_channel(self):
                channel = self.cleaned_data["channel"]
                channel = self.org.channels.filter(pk=channel).first()
                if not channel:
                    raise forms.ValidationError("Can't add sender for that number")
                return channel

        form_class = BulkSenderForm
        fields = ("connection", "channel")

        def get_form_kwargs(self, *args, **kwargs):
            form_kwargs = super().get_form_kwargs(*args, **kwargs)
            form_kwargs["org"] = Org.objects.get(pk=self.request.user.get_org().pk)
            return form_kwargs

        def form_valid(self, form):
            user = self.request.user

            channel = form.cleaned_data["channel"]
            Channel.add_vonage_bulk_sender(user, channel)
            return super().form_valid(form)

        def form_invalid(self, form):
            return super().form_invalid(form)

        def get_success_url(self):
            channel = self.form.cleaned_data["channel"]
            return reverse("channels.channel_read", args=[channel.uuid])

    class CreateCaller(OrgPermsMixin, SmartFormView):
        class CallerForm(forms.Form):
            connection = forms.CharField(max_length=2, widget=forms.HiddenInput, required=False)
            channel = forms.IntegerField(widget=forms.HiddenInput, required=False)

            def __init__(self, *args, **kwargs):
                self.org = kwargs["org"]
                del kwargs["org"]
                super().__init__(*args, **kwargs)

            def clean_connection(self):
                connection = self.cleaned_data["connection"]
                if connection == "T" and not self.org.is_connected_to_twilio():
                    raise forms.ValidationError(_("A connection to a Twilio account is required"))
                return connection

            def clean_channel(self):
                channel = self.cleaned_data["channel"]
                channel = self.org.channels.filter(pk=channel).first()
                if not channel:
                    raise forms.ValidationError(_("A caller cannot be added for that number"))
                if channel.get_caller():
                    raise forms.ValidationError(_("A caller has already been added for that number"))
                return channel

        form_class = CallerForm
        fields = ("connection", "channel")

        def get_form_kwargs(self, *args, **kwargs):
            form_kwargs = super().get_form_kwargs(*args, **kwargs)
            form_kwargs["org"] = Org.objects.get(pk=self.request.user.get_org().pk)
            return form_kwargs

        def form_valid(self, form):
            user = self.request.user
            org = user.get_org()

            channel = form.cleaned_data["channel"]
            Channel.add_call_channel(org, user, channel)
            return super().form_valid(form)

        def form_invalid(self, form):
            return super().form_invalid(form)

        def get_success_url(self):
            channel = self.form.cleaned_data["channel"]
            return reverse("channels.channel_read", args=[channel.uuid])

    class Configuration(SpaMixin, OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"

        def get_gear_links(self):
            links = []

            if self.has_org_perm("channels.channel_update") and self.object.channel_type == "WCH":
                links.append(
                    dict(
                        title=_("Edit"),
                        style="button-primary",
                        href=reverse("channels.channel_update", args=[self.object.id]),
                    )
                )

            return links

        def has_permission_view_objects(self):
            channel = Channel.objects.filter(org=self.request.user.get_org(), uuid=self.kwargs.get("uuid")).first()
            if not channel:
                raise PermissionDenied()
            return None

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["domain"] = self.object.callback_domain
            context["ip_addresses"] = settings.IP_ADDRESSES

            context_dict = dict(widget_compiled_file=settings.WIDGET_COMPILED_FILE)

            # populate with our channel type
            channel_type = Channel.get_type_from_code(self.object.channel_type)
            context["configuration_blurb"] = channel_type.get_configuration_blurb(self.object)
            context["configuration_urls"] = channel_type.get_configuration_urls(self.object)
            context["show_public_addresses"] = channel_type.show_public_addresses

            if self.object.channel_type == "WCH":
                config = self.object.config
                logo_img = config.get("logo", None)
                if logo_img:
                    context_dict["wch_logo_size"] = get_image_size(logo_img=logo_img)
                context_dict["hostname"] = settings.HOSTNAME

                welcome_message_i18n = {}
                input_placeholder_i18n = {}

                for lang in self.object.org.flow_languages:
                    lang_alpha = pycountry.languages.get(alpha_3=lang)
                    if not hasattr(lang_alpha, "alpha_2"):
                        continue

                    welcome_message_i18n[f"{lang_alpha.alpha_2}"] = self.object.config.get(
                        f"welcome_message_{lang_alpha.alpha_2}", ""
                    )

                    input_placeholder_i18n[f"{lang_alpha.alpha_2}"] = self.object.config.get(
                        f"inputtext_placeholder_{lang_alpha.alpha_2}", ""
                    )

                context_dict["welcome_message_i18n"] = welcome_message_i18n
                context_dict["input_placeholder_i18n"] = input_placeholder_i18n

            context["configuration_template"] = channel_type.get_configuration_template(
                self.object, context=context_dict
            )

            return context

    class List(OrgPermsMixin, SmartListView):
        title = _("Channels")
        fields = ("name", "address", "last_seen")
        search_fields = ("name", "address", "org__created_by__email")

        def lookup_field_link(self, context, field, obj):
            return reverse("channels.channel_read", args=[obj.uuid])

        def get_queryset(self, **kwargs):
            queryset = super().get_queryset(**kwargs)

            # org users see channels for their org, superuser sees all
            if not self.request.user.is_superuser:
                org = self.request.user.get_org()
                queryset = queryset.filter(org=org)

            return queryset.filter(is_active=True)

        def pre_process(self, *args, **kwargs):
            # superuser sees things as they are
            if self.request.user.is_superuser:
                return super().pre_process(*args, **kwargs)

            # everybody else goes to a different page depending how many channels there are
            org = self.request.user.get_org()
            channels = list(Channel.objects.filter(org=org, is_active=True))

            if len(channels) == 0:
                return HttpResponseRedirect(reverse("channels.channel_claim"))
            elif len(channels) == 1:
                return HttpResponseRedirect(reverse("channels.channel_read", args=[channels[0].uuid]))
            else:
                return super().pre_process(*args, **kwargs)

        def get_name(self, obj):
            return obj.get_name()

        def get_address(self, obj):
            return obj.address if obj.address else _("Unknown")


class ChannelEventCRUDL(SmartCRUDL):
    model = ChannelEvent
    actions = ("calls",)

    class Calls(InboxView):
        title = _("Calls")
        fields = ("contact", "event_type", "channel", "occurred_on")
        default_order = ("-occurred_on",)
        search_fields = ("contact__urns__path__icontains", "contact__name__icontains")
        system_label = SystemLabel.TYPE_CALLS
        select_related = ("contact", "channel")

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^calls/$"

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["actions"] = []
            return context


class ChannelLogCRUDL(SmartCRUDL):
    model = ChannelLog
    actions = ("list", "read", "connection")

    class List(SpaMixin, OrgPermsMixin, SmartListView):
        fields = ("channel", "description", "created_on")
        link_fields = ("channel", "description", "created_on")
        paginate_by = 50

        FOLDER_MESSAGES = "messages"
        FOLDER_CALLS = "calls"
        FOLDER_OTHERS = "others"
        FOLDER_ERRORS = "errors"

        @property
        def folder(self) -> str:
            if self.request.GET.get("calls") or self.request.GET.get("connections"):
                return self.FOLDER_CALLS
            elif self.request.GET.get("others"):
                return self.FOLDER_OTHERS
            elif self.request.GET.get("errors"):
                return self.FOLDER_ERRORS
            else:
                return self.FOLDER_MESSAGES

        def get_gear_links(self):
            list_url = reverse("channels.channellog_list", args=[self.channel.uuid])
            links = []

            if self.folder != self.FOLDER_MESSAGES:
                links.append(dict(title=_("Messages"), href=list_url))
            if self.folder != self.FOLDER_CALLS and self.channel.supports_ivr():
                links.append(dict(title=_("Calls"), href=f"{list_url}?calls=1"))
            if self.folder != self.FOLDER_OTHERS:
                links.append(dict(title=_("Other Interactions"), href=f"{list_url}?others=1"))
            if self.folder != self.FOLDER_ERRORS:
                links.append(dict(title=_("Errors"), href=f"{list_url}?errors=1"))

            return links

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/(?P<channel_uuid>[^/]+)/$" % path

        def get_template_names(self):
            if self.folder == self.FOLDER_CALLS:
                return ("channels/channellog_calls.haml",)
            else:
                return super().get_template_names()

        @cached_property
        def channel(self):
            return get_object_or_404(Channel, uuid=self.kwargs["channel_uuid"])

        def derive_org(self):
            return self.channel.org

        def derive_queryset(self, **kwargs):
            if self.folder == self.FOLDER_CALLS:
                logs = self.channel.logs.exclude(connection=None).values_list("connection_id", flat=True)
                events = ChannelConnection.objects.filter(id__in=logs).order_by("-created_on")

            elif self.folder == self.FOLDER_OTHERS:
                events = self.channel.logs.filter(connection=None, msg=None).order_by("-created_on")

            else:
                if self.folder == self.FOLDER_ERRORS:
                    logs = self.channel.logs.filter(connection=None, is_error=True)
                else:
                    logs = self.channel.logs.filter(connection=None).exclude(msg=None)

                events = logs.order_by("-created_on").select_related(
                    "msg", "msg__contact", "msg__contact_urn", "channel", "channel__org"
                )

                if self.request.GET.get("errors"):
                    patch_queryset_count(events, self.channel.get_error_log_count)
                else:
                    patch_queryset_count(events, self.channel.get_non_ivr_log_count)

            return events

        def has_permission_view_objects(self):
            channel = Channel.objects.filter(
                org=self.request.user.get_org(), uuid=self.kwargs.get("channel_uuid")
            ).first()
            if not channel:
                raise PermissionDenied()
            return None

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["channel"] = self.channel
            context["statuses"] = [
                (key, getattr(status, key, None)) for key in filter(lambda x: x.startswith("HTTP_"), dir(status))
            ]
            return context

        def paginate_queryset(self, queryset, page_size):
            paginator = self.get_paginator(
                queryset, page_size, orphans=self.get_paginate_orphans(), allow_empty_first_page=self.get_allow_empty()
            )

            page_kwarg = self.page_kwarg
            page = self.kwargs.get(page_kwarg) or self.request.GET.get(page_kwarg) or 1
            try:
                page_number = int(page)
            except ValueError:
                if page == "last":
                    page_number = paginator.num_pages
                else:
                    raise Http404(_("Page is not 'last', nor can it be converted to an int."))

            from django.core.paginator import InvalidPage

            try:
                paginator.count = len(queryset)
                page = paginator.page(page_number)
                return (paginator, page, page.object_list, page.has_other_pages())
            except InvalidPage as e:
                raise Http404(
                    _("Invalid page (%(page_number)s): %(message)s") % {"page_number": page_number, "message": str(e)}
                )

    class Connection(AnonMixin, SmartReadView):
        model = ChannelConnection

        def get_gear_links(self):
            return [
                dict(
                    title=_("More Calls"),
                    style="button-light",
                    href=reverse("channels.channellog_list", args=[self.get_object().channel.uuid]) + "?connections=1",
                )
            ]

    class Read(SpaMixin, OrgObjPermsMixin, SmartReadView):
        fields = ("description", "created_on")
        slug_url_kwarg = "pk"

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<channel_uuid>[0-9a-f-]+)/(?P<pk>\d+)/$" % (path, action)

        def get_gear_links(self):
            return [
                dict(
                    title=_("Channel Log"),
                    style="button-light",
                    href=reverse("channels.channellog_list", args=[self.get_object().channel.uuid]),
                )
            ]

        def get_object_org(self):
            return self.get_object().channel.org

        def derive_queryset(self, **kwargs):
            queryset = super().derive_queryset(**kwargs)
            return queryset.order_by("-created_on")
