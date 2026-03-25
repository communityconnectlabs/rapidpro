import json
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase
from django.urls import reverse

from temba.tests import CRUDLTestMixin, TembaTest

from .middleware import CustomerEventMiddleware
from .models import CustomerEvent, CustomerEventConfig, CustomerEventHandler, HTTPMethod
from .views import CustomerEventConfigForm

# =============================================================================
# CustomerEvent dataclass
# =============================================================================


class CustomerEventDataclassTest(TestCase):
    def test_create_and_attributes(self):
        org = MagicMock()
        user = MagicMock()
        request = MagicMock()
        event = CustomerEvent(
            org=org,
            user=user,
            description="Test event",
            request=request,
            view_context={"key": "value"},
            args=["arg1"],
            kwargs={"kwarg1": "val1"},
        )
        self.assertEqual(event.org, org)
        self.assertEqual(event.user, user)
        self.assertEqual(event.description, "Test event")
        self.assertEqual(event.request, request)
        self.assertEqual(event.view_context, {"key": "value"})
        self.assertEqual(event.args, ["arg1"])
        self.assertEqual(event.kwargs, {"kwarg1": "val1"})


# =============================================================================
# HTTPMethod enum
# =============================================================================


class HTTPMethodTest(TestCase):
    def test_values(self):
        self.assertEqual(HTTPMethod.GET.value, "GET")
        self.assertEqual(HTTPMethod.POST.value, "POST")
        self.assertEqual(HTTPMethod.PUT.value, "PUT")
        self.assertEqual(HTTPMethod.PATCH.value, "PATCH")
        self.assertEqual(HTTPMethod.DELETE.value, "DELETE")
        self.assertEqual(HTTPMethod.OPTIONS.value, "OPTIONS")


# =============================================================================
# WebhookHandler
# =============================================================================


class WebhookHandlerTest(TestCase):
    def _make_event(self):
        return CustomerEvent(
            org=MagicMock(),
            user=MagicMock(),
            description="webhook test",
            request=MagicMock(),
            view_context={},
            args=[],
            kwargs={},
        )

    def test_init_sets_config(self):
        handler = CustomerEventHandler.WebhookHandler(
            config={
                "template": "Hello",
                "method": HTTPMethod.POST,
                "url": "http://example.com",
                "headers": {"X-Foo": "bar"},
                "body": {},
            }
        )
        self.assertEqual(handler.config.template, "Hello")
        self.assertEqual(handler.config.method, HTTPMethod.POST)
        self.assertEqual(handler.config.url, "http://example.com")
        self.assertEqual(handler.config.headers, {"X-Foo": "bar"})

    def test_handle_with_dict_body(self):
        handler = CustomerEventHandler.WebhookHandler(
            config={
                "template": "msg: {{ event.description }}",
                "method": HTTPMethod.POST,
                "url": "http://hook.example.com",
                "headers": {},
                "body": {"key": "val"},
            }
        )
        event = self._make_event()
        with patch("temba.events.models.requests.request") as mock_req:
            handler.handle(event)
            mock_req.assert_called_once_with(
                method="POST",
                url="http://hook.example.com",
                headers={},
                json={"key": "val"},
                data=None,
            )

    def test_handle_with_string_body(self):
        handler = CustomerEventHandler.WebhookHandler(
            config={
                "template": "raw",
                "method": HTTPMethod.GET,
                "url": "http://hook.example.com",
                "headers": {},
                "body": "raw body string",
            }
        )
        event = self._make_event()
        with patch("temba.events.models.requests.request") as mock_req:
            handler.handle(event)
            mock_req.assert_called_once_with(
                method="GET",
                url="http://hook.example.com",
                headers={},
                json=None,
                data="raw",
            )

    def test_handle_renders_template(self):
        handler = CustomerEventHandler.WebhookHandler(
            config={
                "template": "Event: {{ event.description }}",
                "method": HTTPMethod.POST,
                "url": "http://hook.example.com",
                "headers": {},
                "body": "rendered",
            }
        )
        event = self._make_event()
        with patch("temba.events.models.requests.request") as mock_req:
            handler.handle(event)
            _, kwargs = mock_req.call_args
            self.assertEqual(kwargs["data"], "Event: webhook test")

    def test_get_config_class(self):
        config_class = CustomerEventHandler.WebhookHandler.get_config_class()
        self.assertIs(config_class, CustomerEventHandler.WebhookHandler.WebhookHandlerConfig)

    def test_handler_type(self):
        self.assertEqual(CustomerEventHandler.WebhookHandler.handler_type, "webhook")


# =============================================================================
# EmailHandler
# =============================================================================


class EmailHandlerTest(TestCase):
    def _make_event(self):
        return CustomerEvent(
            org=MagicMock(),
            user=MagicMock(),
            description="email test",
            request=MagicMock(),
            view_context={},
            args=[],
            kwargs={},
        )

    def test_init_sets_config(self):
        handler = CustomerEventHandler.EmailHandler(
            config={
                "template": "Hello",
                "subject": "Test Subject",
                "to": "from@example.com",
                "cc": "a@example.com,b@example.com",
            }
        )
        self.assertEqual(handler.config.template, "Hello")
        self.assertEqual(handler.config.subject, "Test Subject")
        self.assertEqual(handler.config.to, "from@example.com")
        self.assertEqual(handler.config.cc, "a@example.com,b@example.com")

    def test_handle_sends_mail(self):
        handler = CustomerEventHandler.EmailHandler(
            config={
                "template": "Body text",
                "subject": "Subject",
                "to": "from@example.com",
                "cc": "a@example.com,b@example.com",
            }
        )
        event = self._make_event()
        with patch("temba.events.models.send_mail") as mock_send:
            handler.handle(event)
            mock_send.assert_called_once_with(
                "Subject", "Body text", "from@example.com", ["a@example.com", "b@example.com"]
            )

    def test_handle_renders_template(self):
        handler = CustomerEventHandler.EmailHandler(
            config={
                "template": "{{ event.description }}",
                "subject": "Sub",
                "to": "from@example.com",
                "cc": "recipient@example.com",
            }
        )
        event = self._make_event()
        with patch("temba.events.models.send_mail") as mock_send:
            handler.handle(event)
            args = mock_send.call_args[0]
            self.assertEqual(args[1], "email test")

    def test_get_config_class(self):
        config_class = CustomerEventHandler.EmailHandler.get_config_class()
        self.assertIs(config_class, CustomerEventHandler.EmailHandler.EmailHandlerConfig)

    def test_handler_type(self):
        self.assertEqual(CustomerEventHandler.EmailHandler.handler_type, "email")


# =============================================================================
# SlackHandler
# =============================================================================


class SlackHandlerTest(TestCase):
    def _make_event(self):
        return CustomerEvent(
            org=MagicMock(),
            user=MagicMock(),
            description="slack test",
            request=MagicMock(),
            view_context={},
            args=[],
            kwargs={},
        )

    def test_init_sets_config(self):
        handler = CustomerEventHandler.SlackHandler(
            config={"template": "Hello Slack", "webhook_url": "https://hooks.slack.com/xxx"}
        )
        self.assertEqual(handler.config.template, "Hello Slack")
        self.assertEqual(handler.config.webhook_url, "https://hooks.slack.com/xxx")

    def test_handle_posts_to_slack(self):
        handler = CustomerEventHandler.SlackHandler(
            config={
                "template": "Msg: {{ event.description }}",
                "webhook_url": "https://hooks.slack.com/xxx",
            }
        )
        event = self._make_event()
        with patch("temba.events.models.requests.post") as mock_post:
            handler.handle(event)
            mock_post.assert_called_once_with(
                "https://hooks.slack.com/xxx",
                json={"text": "Msg: slack test"},
                headers={"Content-Type": "application/json"},
            )

    def test_handle_renders_template(self):
        handler = CustomerEventHandler.SlackHandler(
            config={"template": "{{ event.description }}", "webhook_url": "https://hooks.slack.com/xxx"}
        )
        event = self._make_event()
        with patch("temba.events.models.requests.post") as mock_post:
            handler.handle(event)
            _, kwargs = mock_post.call_args
            self.assertEqual(kwargs["json"]["text"], "slack test")

    def test_get_config_class(self):
        config_class = CustomerEventHandler.SlackHandler.get_config_class()
        self.assertIs(config_class, CustomerEventHandler.SlackHandler.SlackHandlerConfig)

    def test_handler_type(self):
        self.assertEqual(CustomerEventHandler.SlackHandler.handler_type, "slack")


# =============================================================================
# CustomerEventHandler factory
# =============================================================================


class CustomerEventHandlerFactoryTest(TestCase):
    def test_choices_contains_all_handlers(self):
        choices = CustomerEventHandler.choices()
        handler_types = [c[0] for c in choices]
        self.assertIn("webhook", handler_types)
        self.assertIn("email", handler_types)
        self.assertIn("slack", handler_types)

    def test_choices_labels_are_capitalized(self):
        for choice_value, choice_label in CustomerEventHandler.choices():
            self.assertEqual(choice_label, choice_value.capitalize())

    def test_handlers_for_webhook(self):
        handlers = list(
            CustomerEventHandler.handlers_for(
                handlers_types=["webhook"],
                handlers_configs={
                    "webhook": {
                        "method": HTTPMethod.GET,
                        "url": "http://example.com",
                        "headers": {},
                        "body": {},
                    }
                },
                template="Hello",
            )
        )
        self.assertEqual(len(handlers), 1)
        self.assertIsInstance(handlers[0], CustomerEventHandler.WebhookHandler)
        self.assertEqual(handlers[0].config.template, "Hello")

    def test_handlers_for_email(self):
        handlers = list(
            CustomerEventHandler.handlers_for(
                handlers_types=["email"],
                handlers_configs={"email": {"subject": "Sub", "to": "from@example.com", "cc": ""}},
                template="body",
            )
        )
        self.assertEqual(len(handlers), 1)
        self.assertIsInstance(handlers[0], CustomerEventHandler.EmailHandler)
        self.assertEqual(handlers[0].config.template, "body")

    def test_handlers_for_slack(self):
        handlers = list(
            CustomerEventHandler.handlers_for(
                handlers_types=["slack"],
                handlers_configs={"slack": {"webhook_url": "https://hooks.slack.com/xxx"}},
                template="msg",
            )
        )
        self.assertEqual(len(handlers), 1)
        self.assertIsInstance(handlers[0], CustomerEventHandler.SlackHandler)

    def test_handlers_for_multiple(self):
        handlers = list(
            CustomerEventHandler.handlers_for(
                handlers_types=["webhook", "slack"],
                handlers_configs={
                    "webhook": {"method": HTTPMethod.GET, "url": "http://example.com", "headers": {}, "body": {}},
                    "slack": {"webhook_url": "https://hooks.slack.com/xxx"},
                },
                template="tpl",
            )
        )
        self.assertEqual(len(handlers), 2)
        self.assertIsInstance(handlers[0], CustomerEventHandler.WebhookHandler)
        self.assertIsInstance(handlers[1], CustomerEventHandler.SlackHandler)

    def test_handlers_for_unknown_type_skipped(self):
        handlers = list(
            CustomerEventHandler.handlers_for(
                handlers_types=["unknown_type"],
                handlers_configs={},
                template="tpl",
            )
        )
        self.assertEqual(len(handlers), 0)

    def test_handlers_for_missing_config_uses_defaults(self):
        # When handlers_configs doesn't have the key, defaults from the config dataclass are used
        handlers = list(
            CustomerEventHandler.handlers_for(
                handlers_types=["slack"],
                handlers_configs={},  # no slack config
                template="tpl",
            )
        )
        self.assertEqual(len(handlers), 1)
        self.assertIsInstance(handlers[0], CustomerEventHandler.SlackHandler)
        self.assertEqual(handlers[0].config.webhook_url, "")

    def test_get_configs_for_webhook(self):
        config_class = CustomerEventHandler.get_configs_for("webhook")
        self.assertIs(config_class, CustomerEventHandler.WebhookHandler.WebhookHandlerConfig)

    def test_get_configs_for_email(self):
        config_class = CustomerEventHandler.get_configs_for("email")
        self.assertIs(config_class, CustomerEventHandler.EmailHandler.EmailHandlerConfig)

    def test_get_configs_for_slack(self):
        config_class = CustomerEventHandler.get_configs_for("slack")
        self.assertIs(config_class, CustomerEventHandler.SlackHandler.SlackHandlerConfig)

    def test_get_configs_for_unknown_raises(self):
        with self.assertRaises(AssertionError):
            CustomerEventHandler.get_configs_for("nonexistent_handler")


# =============================================================================
# CustomerEventConfig model
# =============================================================================


class CustomerEventConfigModelTest(TembaTest):
    def test_str_system_wide(self):
        config = CustomerEventConfig(
            system_wide=True,
            action="orgs.org_list",
            action_description="List orgs",
            handlers=["email"],
        )
        result = str(config)
        self.assertIn("All Orgs", result)
        self.assertIn("orgs.org_list", result)
        self.assertIn("email", result)

    def test_str_org_specific(self):
        config = CustomerEventConfig(
            system_wide=False,
            org=self.org,
            action="contacts.contact_list",
            action_description="List contacts",
            handlers=["slack"],
        )
        result = str(config)
        self.assertIn("contacts.contact_list", result)
        self.assertIn("slack", result)

    def test_handle_invokes_slack_handler(self):
        config = CustomerEventConfig.objects.create(
            system_wide=True,
            action="orgs.org_list",
            action_description="List orgs",
            notification_template="{{ event.description }}",
            handlers=["slack"],
            handlers_configs={"slack": {"webhook_url": "https://hooks.slack.com/xxx"}},
        )
        request = MagicMock()
        with patch("temba.events.models.requests.post") as mock_post:
            config.handle(request, self.org, self.admin, {}, [], {})
            mock_post.assert_called_once()
            _, kwargs = mock_post.call_args
            self.assertEqual(kwargs["json"]["text"], "List orgs")

    def test_handle_invokes_email_handler(self):
        config = CustomerEventConfig.objects.create(
            system_wide=True,
            action="orgs.org_list",
            action_description="Test action",
            notification_template="Hello {{ event.description }}",
            handlers=["email"],
            handlers_configs={"email": {"subject": "Sub", "to": "from@example.com", "cc": "to@example.com"}},
        )
        request = MagicMock()
        with patch("temba.events.models.send_mail") as mock_send:
            config.handle(request, self.org, self.admin, {}, [], {})
            mock_send.assert_called_once()
            args = mock_send.call_args[0]
            self.assertEqual(args[0], "Sub")
            self.assertIn("Test action", args[1])

    def test_handle_invokes_multiple_handlers(self):
        config = CustomerEventConfig.objects.create(
            system_wide=True,
            action="orgs.org_list",
            action_description="Multi handler event",
            notification_template="msg",
            handlers=["slack", "email"],
            handlers_configs={
                "slack": {"webhook_url": "https://hooks.slack.com/xxx"},
                "email": {"subject": "Sub", "to": "from@example.com", "cc": "to@example.com"},
            },
        )
        request = MagicMock()
        with patch("temba.events.models.requests.post") as mock_post:
            with patch("temba.events.models.send_mail") as mock_send:
                config.handle(request, self.org, self.admin, {}, [], {})
                mock_post.assert_called_once()
                mock_send.assert_called_once()

    def test_handle_creates_event_with_correct_fields(self):
        config = CustomerEventConfig.objects.create(
            system_wide=True,
            action="orgs.org_list",
            action_description="Event description",
            notification_template="",
            handlers=["slack"],
            handlers_configs={"slack": {"webhook_url": "https://hooks.slack.com/xxx"}},
        )
        request = MagicMock()
        view_context = {"foo": "bar"}
        args = ["a"]
        kwargs = {"k": "v"}

        captured_events = []

        def capture_handle(self_handler, event):
            captured_events.append(event)

        with patch.object(CustomerEventHandler.SlackHandler, "handle", capture_handle):
            config.handle(request, self.org, self.admin, view_context, args, kwargs)

        self.assertEqual(len(captured_events), 1)
        event = captured_events[0]
        self.assertEqual(event.org, self.org)
        self.assertEqual(event.user, self.admin)
        self.assertEqual(event.description, "Event description")
        self.assertEqual(event.request, request)
        self.assertEqual(event.view_context, view_context)
        self.assertEqual(event.args, args)
        self.assertEqual(event.kwargs, kwargs)

    def test_action_choices_returns_list_of_tuples(self):
        CustomerEventConfig.action_choices.cache_clear()
        choices = CustomerEventConfig.action_choices()
        self.assertIsInstance(choices, list)
        self.assertGreater(len(choices), 0)
        for action, label in choices:
            self.assertEqual(action, label)
            self.assertIsInstance(action, str)

    def test_action_choices_filters_expected_prefixes(self):
        CustomerEventConfig.action_choices.cache_clear()
        choices = CustomerEventConfig.action_choices()
        action_names = [c[0] for c in choices]
        expected_prefixes = (
            "api.v2.",
            "msgs.",
            "orgs.",
            "contacts.",
            "flows.",
            "triggers.",
            "schedules.",
            "labels.",
            "channels.",
        )
        for action in action_names:
            self.assertTrue(
                any(action.startswith(prefix) for prefix in expected_prefixes),
                f"Action '{action}' does not start with any expected prefix",
            )

    def test_action_choices_is_sorted(self):
        CustomerEventConfig.action_choices.cache_clear()
        choices = CustomerEventConfig.action_choices()
        action_names = [c[0] for c in choices]
        self.assertEqual(action_names, sorted(action_names))


# =============================================================================
# CustomerEventConfigForm
# =============================================================================


class CustomerEventConfigFormTest(TembaTest):
    def _get_valid_action(self):
        CustomerEventConfig.action_choices.cache_clear()
        choices = CustomerEventConfig.action_choices()
        return choices[0][0] if choices else "orgs.org_list"

    def test_clean_handlers_configs_valid_slack(self):
        action = self._get_valid_action()
        form = CustomerEventConfigForm(
            data={
                "method": "GET",
                "action": action,
                "action_description": "Test",
                "handlers": ["slack"],
                "system_wide": True,
                "org": "",
                "notification_template": "",
                "handlers_configs": json.dumps({"slack": {"webhook_url": "https://hooks.slack.com/xxx"}}),
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["handlers_configs"],
            {"slack": {"webhook_url": "https://hooks.slack.com/xxx"}},
        )

    def test_clean_handlers_configs_missing_raises_error(self):
        action = self._get_valid_action()
        form = CustomerEventConfigForm(
            data={
                "method": "GET",
                "action": action,
                "action_description": "Test",
                "handlers": ["slack"],
                "system_wide": True,
                "org": "",
                "notification_template": "",
                "handlers_configs": json.dumps({}),
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("handlers_configs", form.errors)

    def test_clean_handlers_configs_empty_handlers_fails_required(self):
        # handlers is a required MultipleChoiceField; submitting empty fails before clean_handlers_configs runs
        action = self._get_valid_action()
        form = CustomerEventConfigForm(
            data={
                "method": "GET",
                "action": action,
                "action_description": "Test",
                "handlers": [],
                "system_wide": True,
                "org": "",
                "notification_template": "",
                "handlers_configs": json.dumps({}),
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("handlers", form.errors)

    def test_clean_handlers_configs_strips_template_key(self):
        action = self._get_valid_action()
        form = CustomerEventConfigForm(
            data={
                "method": "GET",
                "action": action,
                "action_description": "Test",
                "handlers": ["email"],
                "system_wide": True,
                "org": "",
                "notification_template": "",
                "handlers_configs": json.dumps(
                    {"email": {"template": "should be removed", "subject": "Sub", "to": "f@e.com", "cc": "t@e.com"}}
                ),
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn("template", form.cleaned_data["handlers_configs"].get("email", {}))


# =============================================================================
# CustomerEventMiddleware
# =============================================================================


class CustomerEventMiddlewareTest(TembaTest):
    def _make_request(self, method="GET", path="/org/list/", org=None, user=None):
        factory = RequestFactory()
        request = factory.generic(method, path)
        request.org = org if org is not None else self.org
        request.user = user if user is not None else self.admin
        return request

    def _make_middleware(self, response=None):
        mock_response = response or MagicMock()
        get_response = MagicMock(return_value=mock_response)
        middleware = CustomerEventMiddleware(get_response)
        return middleware, get_response, mock_response

    def test_no_config_match_passes_through(self):
        middleware, get_response, mock_response = self._make_middleware()
        request = self._make_request()
        with patch("temba.events.middleware.resolve") as mock_resolve:
            mock_resolve.return_value.url_name = "no_such_action"
            response = middleware(request)
        get_response.assert_called_once_with(request)
        self.assertEqual(response, mock_response)

    def test_org_mismatch_skips_handle(self):
        CustomerEventConfig.objects.create(
            system_wide=False,
            org=self.org,
            action="org_list_mismatch",
            action_description="List orgs",
            notification_template="",
            handlers=[],
        )
        middleware, get_response, mock_response = self._make_middleware()
        # request.org is self.org2 — does not match config.org (self.org)
        request = self._make_request(org=self.org2)
        with patch("temba.events.middleware.resolve") as mock_resolve:
            mock_resolve.return_value.url_name = "org_list_mismatch"
            with patch.object(CustomerEventConfig, "handle") as mock_handle:
                response = middleware(request)
                mock_handle.assert_not_called()
        self.assertEqual(response, mock_response)

    def test_system_wide_config_calls_handle(self):
        CustomerEventConfig.objects.create(
            system_wide=True,
            action="system_wide_action",
            action_description="System wide",
            method="GET",
            notification_template="",
            handlers=["slack"],
            handlers_configs={"slack": {"webhook_url": "https://hooks.slack.com/xxx"}},
        )
        middleware, _, _ = self._make_middleware()
        request = self._make_request()
        with patch("temba.events.middleware.resolve") as mock_resolve:
            mock_resolve.return_value.url_name = "system_wide_action"
            with patch("temba.events.models.requests.post") as mock_post:
                middleware(request)
                mock_post.assert_called_once()

    def test_org_match_calls_handle(self):
        CustomerEventConfig.objects.create(
            system_wide=False,
            org=self.org,
            action="org_match_action",
            action_description="Org match",
            method="GET",
            notification_template="",
            handlers=["slack"],
            handlers_configs={"slack": {"webhook_url": "https://hooks.slack.com/xxx"}},
        )
        middleware, _, _ = self._make_middleware()
        request = self._make_request(org=self.org)
        with patch("temba.events.middleware.resolve") as mock_resolve:
            mock_resolve.return_value.url_name = "org_match_action"
            with patch("temba.events.models.requests.post") as mock_post:
                middleware(request)
                mock_post.assert_called_once()

    def test_context_data_passed_to_handle(self):
        CustomerEventConfig.objects.create(
            system_wide=True,
            action="ctx_action",
            action_description="Context test",
            method="GET",
            notification_template="",
            handlers=[],
        )
        context_data = {"some_key": "some_value"}
        mock_response = MagicMock(spec=["context_data"])
        mock_response.context_data = context_data

        middleware, _, _ = self._make_middleware(response=mock_response)
        request = self._make_request()

        with patch("temba.events.middleware.resolve") as mock_resolve:
            mock_resolve.return_value.url_name = "ctx_action"
            with patch.object(CustomerEventConfig, "handle") as mock_handle:
                middleware(request)
                mock_handle.assert_called_once()
                call_args = mock_handle.call_args[0]
                # view_context is the 4th positional argument
                self.assertEqual(call_args[3], context_data)

    def test_drf_response_context_data_takes_precedence(self):
        # DRFResponse inherits from SimpleTemplateResponse, which always sets context_data=None.
        # Because the middleware checks `hasattr(response, "context_data")` first, the
        # `elif isinstance(response, DRFResponse)` branch is never reached; view_context
        # comes from context_data (None) rather than response.data.
        from rest_framework.response import Response as DRFResponse

        CustomerEventConfig.objects.create(
            system_wide=True,
            action="drf_action",
            action_description="DRF test",
            method="GET",
            notification_template="",
            handlers=[],
        )
        drf_response = DRFResponse(data={"result": "ok"})

        middleware, _, _ = self._make_middleware(response=drf_response)
        request = self._make_request()

        with patch("temba.events.middleware.resolve") as mock_resolve:
            mock_resolve.return_value.url_name = "drf_action"
            with patch.object(CustomerEventConfig, "handle") as mock_handle:
                middleware(request)
                mock_handle.assert_called_once()
                call_args = mock_handle.call_args[0]
                # context_data is None (set by SimpleTemplateResponse.__init__)
                self.assertIsNone(call_args[3])

    def test_plain_response_passes_empty_context(self):
        from django.http import HttpResponse

        CustomerEventConfig.objects.create(
            system_wide=True,
            action="plain_action",
            action_description="Plain response test",
            method="GET",
            notification_template="",
            handlers=[],
        )
        plain_response = HttpResponse("OK")

        middleware, _, _ = self._make_middleware(response=plain_response)
        request = self._make_request()

        with patch("temba.events.middleware.resolve") as mock_resolve:
            mock_resolve.return_value.url_name = "plain_action"
            with patch.object(CustomerEventConfig, "handle") as mock_handle:
                middleware(request)
                mock_handle.assert_called_once()
                call_args = mock_handle.call_args[0]
                self.assertEqual(call_args[3], {})

    def test_handle_exception_is_logged_and_response_returned(self):
        CustomerEventConfig.objects.create(
            system_wide=True,
            action="error_action",
            action_description="Error test",
            method="GET",
            notification_template="",
            handlers=["slack"],
            handlers_configs={"slack": {"webhook_url": "https://hooks.slack.com/xxx"}},
        )
        middleware, get_response, mock_response = self._make_middleware()
        request = self._make_request()

        with patch("temba.events.middleware.resolve") as mock_resolve:
            mock_resolve.return_value.url_name = "error_action"
            with patch("temba.events.models.requests.post", side_effect=Exception("network error")):
                with patch("temba.events.middleware.logger") as mock_logger:
                    response = middleware(request)
                    mock_logger.error.assert_called_once()
                    error_msg = mock_logger.error.call_args[0][0]
                    self.assertIn("network error", error_msg)
        self.assertEqual(response, mock_response)

    def test_resolve_url_name_none_treated_as_empty_string(self):
        """When resolve returns url_name=None, no config should match and response passes through."""
        middleware, get_response, mock_response = self._make_middleware()
        request = self._make_request()
        with patch("temba.events.middleware.resolve") as mock_resolve:
            mock_resolve.return_value.url_name = None
            response = middleware(request)
        get_response.assert_called_once_with(request)
        self.assertEqual(response, mock_response)


# =============================================================================
# CustomerEventsCRUDL views
# =============================================================================


class CustomerEventsCRUDLTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()
        self.login(self.superuser)
        self.config1 = CustomerEventConfig.objects.create(
            system_wide=True,
            action="orgs.org_list",
            action_description="List all orgs",
            notification_template="",
            handlers=["slack"],
            handlers_configs={"slack": {"webhook_url": "https://hooks.slack.com/xxx"}},
        )
        self.config2 = CustomerEventConfig.objects.create(
            system_wide=False,
            org=self.org,
            action="contacts.contact_list",
            action_description="List contacts",
            notification_template="",
            handlers=["email"],
            handlers_configs={"email": {"subject": "Sub", "to": "from@example.com", "cc": "to@example.com"}},
        )

    def test_list(self):
        list_url = reverse("events.customereventconfig_list")
        response = self.client.get(list_url)
        self.assertEqual(200, response.status_code)
        object_list = list(response.context["object_list"])
        self.assertIn(self.config1, object_list)
        self.assertIn(self.config2, object_list)

    def test_list_search(self):
        # SmartMin search uses exact match per term (Q(field=term)), so we must use the full field value
        list_url = reverse("events.customereventconfig_list")
        response = self.client.get(list_url + "?search=orgs.org_list")
        self.assertEqual(200, response.status_code)
        object_list = list(response.context["object_list"])
        self.assertIn(self.config1, object_list)
        self.assertNotIn(self.config2, object_list)

    def test_create_fetch(self):
        create_url = reverse("events.customereventconfig_create")
        response = self.client.get(create_url)
        self.assertEqual(200, response.status_code)
        form = response.context["form"]
        for field in (
            "method",
            "action",
            "action_description",
            "handlers",
            "system_wide",
            "org",
            "notification_template",
            "handlers_configs",
        ):
            self.assertIn(field, form.fields)

    def test_create_submit_valid(self):
        CustomerEventConfig.action_choices.cache_clear()
        action = CustomerEventConfig.action_choices()[0][0]
        create_url = reverse("events.customereventconfig_create")
        response = self.client.post(
            create_url,
            {
                "method": "POST",
                "action": action,
                "action_description": "A newly created event",
                "handlers": ["slack"],
                "system_wide": True,
                "org": "",
                "notification_template": "",
                "handlers_configs": json.dumps({"slack": {"webhook_url": "https://hooks.slack.com/newone"}}),
            },
        )
        self.assertEqual(302, response.status_code)
        self.assertTrue(
            CustomerEventConfig.objects.filter(action=action, action_description="A newly created event").exists()
        )

    def test_create_submit_missing_handler_config(self):
        CustomerEventConfig.action_choices.cache_clear()
        action = CustomerEventConfig.action_choices()[0][0]
        create_url = reverse("events.customereventconfig_create")
        response = self.client.post(
            create_url,
            {
                "method": "GET",
                "action": action,
                "action_description": "Missing config event",
                "handlers": ["slack"],
                "system_wide": True,
                "org": "",
                "notification_template": "",
                "handlers_configs": json.dumps({}),
            },
        )
        self.assertEqual(200, response.status_code)
        self.assertFormError(response, "form", "handlers_configs", "Configuration for handler 'slack' is missing")

    def test_update_fetch(self):
        update_url = reverse("events.customereventconfig_update", args=[self.config1.id])
        response = self.client.get(update_url)
        self.assertEqual(200, response.status_code)
        form = response.context["form"]
        self.assertIn("action_description", form.fields)
        self.assertIn("handlers", form.fields)

    def test_update_submit_valid(self):
        CustomerEventConfig.action_choices.cache_clear()
        action = CustomerEventConfig.action_choices()[0][0]
        update_url = reverse("events.customereventconfig_update", args=[self.config2.id])
        response = self.client.post(
            update_url,
            {
                "method": "GET",
                "action": action,
                "action_description": "Updated description",
                "handlers": ["slack"],
                "system_wide": False,
                "org": self.org.id,
                "notification_template": "",
                "handlers_configs": json.dumps({"slack": {"webhook_url": "https://hooks.slack.com/updated"}}),
            },
        )
        self.assertEqual(302, response.status_code)
        self.config2.refresh_from_db()
        self.assertEqual(self.config2.action_description, "Updated description")

    def test_delete_fetch(self):
        delete_url = reverse("events.customereventconfig_delete", args=[self.config1.id])
        response = self.client.get(delete_url)
        self.assertEqual(200, response.status_code)

    def test_delete_submit(self):
        delete_url = reverse("events.customereventconfig_delete", args=[self.config2.id])
        response = self.client.post(delete_url)
        self.assertFalse(CustomerEventConfig.objects.filter(id=self.config2.id).exists())
        self.assertRedirects(response, reverse("events.customereventconfig_list"), fetch_redirect_response=False)
