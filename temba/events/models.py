import logging
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Iterable, Type, Union

import requests

from django.contrib.auth.models import User
from django.contrib.postgres.fields import ArrayField
from django.core.mail import send_mail
from django.db import models
from django.db.models import Q
from django.http import HttpRequest
from django.template import Context, Template
from django.urls.resolvers import get_resolver

from temba.orgs.models import Org

logger = logging.getLogger(__name__)


@dataclass
class CustomerEvent:
    org: Org
    user: User
    description: str
    request: HttpRequest


class HTTPMethod(Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    OPTIONS = "OPTIONS"


class HandlerConfigMixin(metaclass=ABCMeta):
    """
    Base class for handler configuration, successor classes should define the required fields for the handler
    """

    template: str = ""


class HandlerMixin(metaclass=ABCMeta):
    """
    Base class for handlers, successor classes should define the handler_type and implement the handle method and config
    """

    handler_type: str = "unknown_handler"
    config: HandlerConfigMixin = None

    @abstractmethod
    def __init__(self, config: dict):
        pass

    @abstractmethod
    def handle(self, event: CustomerEvent):
        pass

    @classmethod
    @abstractmethod
    def get_config_class(cls) -> Type[HandlerConfigMixin]:
        pass


class CustomerEventHandler:
    """
    Factory class for creating handlers based on the handler_type
    """

    class WebhookHandler(HandlerMixin):
        @dataclass
        class WebhookHandlerConfig(HandlerConfigMixin):
            template: str = ""
            method: HTTPMethod = HTTPMethod.GET
            url: str = ""
            headers: dict = dict
            body: Union[dict, str] = dict

        handler_type: str = "webhook"
        config: WebhookHandlerConfig = None

        def __init__(self, config: dict):
            self.config = CustomerEventHandler.WebhookHandler.WebhookHandlerConfig(**config)

        def handle(self, event: CustomerEvent):
            template = Template(self.config.template)
            message = template.render(Context({"event": event}))
            requests.request(
                method=self.config.method.value,
                url=self.config.url,
                headers=self.config.headers,
                json=self.config.body if isinstance(self.config.body, dict) else None,
                data=message if not isinstance(self.config.body, dict) else None,
            )

        @classmethod
        def get_config_class(cls) -> Type[HandlerConfigMixin]:
            return cls.WebhookHandlerConfig

    class EmailHandler(HandlerMixin):
        @dataclass
        class EmailHandlerConfig(HandlerConfigMixin):
            template: str = ""
            subject: str = ""
            to: str = ""
            cc: str = ""

        handler_type: str = "email"
        config: EmailHandlerConfig = None

        def __init__(self, config: dict):
            self.config = CustomerEventHandler.EmailHandler.EmailHandlerConfig(**config)

        def handle(self, event: CustomerEvent):
            template = Template(self.config.template)
            message = template.render(Context({"event": event}))
            send_mail(self.config.subject, message, self.config.to, self.config.cc.split(","))

        @classmethod
        def get_config_class(cls) -> Type[HandlerConfigMixin]:
            return cls.EmailHandlerConfig

    class SlackHandler(HandlerMixin):
        @dataclass
        class SlackHandlerConfig(HandlerConfigMixin):
            template: str = ""
            webhook_url: str = ""

        handler_type: str = "slack"
        config: SlackHandlerConfig = None

        def __init__(self, config: dict):
            self.config = CustomerEventHandler.SlackHandler.SlackHandlerConfig(**config)

        def handle(self, event: CustomerEvent):
            template = Template(self.config.template)
            message = template.render(Context({"event": event}))
            requests.post(
                self.config.webhook_url, json={"text": message}, headers={"Content-Type": "application/json"}
            )

        @classmethod
        def get_config_class(cls) -> Type[HandlerConfigMixin]:
            return cls.SlackHandlerConfig

    __handlers_map = {
        WebhookHandler.handler_type: WebhookHandler,
        EmailHandler.handler_type: EmailHandler,
        SlackHandler.handler_type: SlackHandler,
    }

    @classmethod
    def choices(cls):
        return [(handler, handler.capitalize()) for handler in cls.__handlers_map.keys()]

    @classmethod
    def handlers_for(cls, handlers_types: list[str], handlers_configs: dict, template: str) -> Iterable[HandlerMixin]:
        for handler_type in handlers_types:
            handler_class: Union[Type[HandlerMixin], None] = cls.__handlers_map.get(handler_type)
            if handler_class is not None:
                yield handler_class(config={"template": template, **handlers_configs.get(handler_type, {})})

    @classmethod
    def get_configs_for(cls, handler_type: str):
        config_cls = cls.__handlers_map.get(handler_type)
        assert config_cls is not None, f"Handler type {handler_type} not found"
        return config_cls.get_config_class()


class CustomerEventConfig(models.Model):
    """
    A model to store the configuration for customer events
    Being used by `CustomerEventMiddleware` to obtain the handler configuration data for the specific event
    """

    system_wide = models.BooleanField(default=False)
    org = models.ForeignKey(
        "orgs.Org",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="customer_events",
    )
    method = models.CharField(max_length=16, choices=[(m.value, m.value) for m in HTTPMethod], default=HTTPMethod.GET)
    action = models.CharField(max_length=124, help_text="The action that triggers the event")
    action_description = models.CharField(max_length=256, help_text="A description of the action")
    notification_template = models.TextField(
        blank=True,
        help_text="The template to use for notifications",
        default="",
    )
    handlers = ArrayField(
        models.CharField(max_length=64, choices=CustomerEventHandler.choices()),
        default=list,
        help_text="The handlers that should be called when the event is triggered",
    )
    handlers_configs = models.JSONField(default=dict, help_text="The configuration for the handlers")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["system_wide", "org", "action"], name="unique_action"),
            models.CheckConstraint(check=(Q(system_wide=True) | Q(org__isnull=False)), name="system_wide_or_org"),
        ]

    def __str__(self):
        return f"{'All Orgs' if self.system_wide else self.org} - {self.action}, handlers: {self.handlers}"

    def handle(self, request: HttpRequest, org: Org, user: User):
        event = CustomerEvent(
            request=request,
            org=org,
            user=user,
            description=self.action_description,
        )
        for handler in CustomerEventHandler.handlers_for(
            handlers_types=self.handlers, handlers_configs=self.handlers_configs, template=self.notification_template
        ):
            handler.handle(event)

    @classmethod
    @lru_cache(maxsize=None)
    def action_choices(cls):
        def not_callable(x):
            return not callable(x) and any(
                str(x).startswith(prefix)
                for prefix in [
                    "api.v2.",
                    "msgs.",
                    "orgs.",
                    "contacts.",
                    "flows.",
                    "triggers.",
                    "schedules.",
                    "labels.",
                    "channels.",
                ]
            )

        actions = sorted(filter(not_callable, get_resolver().reverse_dict.keys()))
        return list([(action, action) for action in actions])
