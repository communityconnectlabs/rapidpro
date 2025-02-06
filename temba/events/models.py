import logging

from typing import Type, Iterable, Union
from abc import ABCMeta, abstractmethod

from django.db import models
from django.db.models import Q
from temba.orgs.models import Org
from django.contrib.auth.models import User
from django.contrib.postgres.fields import ArrayField
from dataclasses import dataclass
from enum import Enum


logger = logging.getLogger(__name__)


@dataclass
class CustomerEvent:
    org: Org
    user: User
    description: str


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
    pass


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


class CustomerEventHandler:
    """
    Factory class for creating handlers based on the handler_type
    """
    class WebhookHandler(HandlerMixin):
        @dataclass
        class WebhookHandlerConfig(HandlerConfigMixin):
            method: HTTPMethod
            url: str
            headers: dict
            body: Union[dict, str]

        handler_type: str = "webhook"
        config: WebhookHandlerConfig = None

        def __init__(self, config: dict):
            self.config = CustomerEventHandler.WebhookHandler.WebhookHandlerConfig(**config)

        def handle(self, event: CustomerEvent):
            logger.error(f"WebhookHandler received event: {event}, but no implementation is provided")

    class EmailHandler(HandlerMixin):
        @dataclass
        class EmailHandlerConfig(HandlerConfigMixin):
            subject: str
            template: str
            to: str
            cc: str

        handler_type: str = "email"
        config: EmailHandlerConfig = None

        def __init__(self, config: dict):
            self.config = CustomerEventHandler.EmailHandler.EmailHandlerConfig(**config)

        def handle(self, event: CustomerEvent):
            logger.error(f"EmailHandler received event: {event}, but no implementation is provided")

    class SlackHandler(HandlerMixin):
        @dataclass
        class SlackHandlerConfig(HandlerConfigMixin):
            token: str
            channel: str
            message: str

        handler_type: str = "slack"
        config: SlackHandlerConfig = None

        def __init__(self, config: dict):
            self.config = CustomerEventHandler.SlackHandler.SlackHandlerConfig(**config)

        def handle(self, event: CustomerEvent):
            logger.error(f"SlackHandler received event: {event}, but no implementation is provided")

    __handlers_map = {
        WebhookHandler.handler_type: WebhookHandler,
        EmailHandler.handler_type: EmailHandler,
        SlackHandler.handler_type: SlackHandler
    }

    @classmethod
    def choices(cls):
        return [(handler, handler.capitalize()) for handler in cls.__handlers_map.keys()]

    @classmethod
    def handlers_for(cls, handler_types: list[str], handlers_configs: dict) -> Iterable[HandlerMixin]:
        for handler_type in handler_types:
            handler_class: Union[Type[HandlerMixin], None] = cls.__handlers_map.get(handler_type)
            if handler_class is not None:
                yield handler_class(config=handlers_configs.get(handler_type, {}))


class CustomerEventConfig(models.Model):
    """
    A model to store the configuration for customer events
    Being used by `CustomerEventMiddleware` to obtain the handler configuration data for the specific event
    """
    system_wide = models.BooleanField(default=False)
    org = models.ForeignKey(
        'orgs.Org',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='customer_events',
    )
    action = models.CharField(max_length=124, help_text="The action that triggers the event")
    action_description = models.CharField(max_length=256, help_text="A description of the action")
    handlers = ArrayField(models.CharField(
        max_length=64,
        choices=CustomerEventHandler.choices()),
        default=list,
        help_text="The handlers that should be called when the event is triggered"
    )
    handlers_configs = models.JSONField(
        default=dict,
        help_text="The configuration for the handlers"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['system_wide', 'org', 'action'], name='unique_action'),
            models.CheckConstraint(
                check=(Q(system_wide=True) | Q(org__isnull=False)),
                name='system_wide_or_org'
            ),
        ]

    def __str__(self):
        return f"{'All Orgs' if self.system_wide else self.org} - {self.action}, handlers: {self.handlers}"

    def handle(self, org: Org, user: User):
        event = CustomerEvent(org=org, user=user, description=self.action_description)
        for handler in CustomerEventHandler.handlers_for(self.handlers, self.handlers_configs):
            handler.handle(event)
