import logging

from rest_framework.response import Response as DRFResponse

from django.urls import resolve

from temba.events.models import CustomerEventConfig

logger = logging.getLogger(__name__)


class CustomerEventMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, *args, **kwargs):
        response = self.get_response(*args, **kwargs)
        request = args[0]
        method = request.method
        action = resolve(request.path).url_name or ""
        config: CustomerEventConfig = CustomerEventConfig.objects.filter(method=method, action=action).first()
        if config is None:
            return response

        if not config.system_wide and config.org != request.org:
            return response

        view_context = {}
        if hasattr(response, "context_data"):
            view_context = response.context_data
        elif isinstance(response, DRFResponse):
            view_context = response.data

        try:
            config.handle(request, request.org, request.user, view_context, list(args), kwargs)
        except Exception as e:
            logger.error(f"Error handling customer event: {e}")
        return response
