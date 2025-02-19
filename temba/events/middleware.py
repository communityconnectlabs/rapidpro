from django.urls import resolve


class CustomerEventMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, *args, **kwargs):
        request = args[0]
        url_name = resolve(request.path).url_name or ""

        print("Request path: ", url_name)
        response = self.get_response(*args, **kwargs)
        return response
