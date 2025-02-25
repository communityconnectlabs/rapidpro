from django.urls import resolve
from temba.events.models import CustomerEventConfig


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

        config.handle(request, request.org, request.user)
        return response
