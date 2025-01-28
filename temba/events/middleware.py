from pprint import pprint


class CustomerEventMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, *args, **kwargs):
        request = args[0]
        print("CustomerEventMiddleware", request.method, request.path, request.org, request.user)
        # todo: add logic to check if event is in the list of events that have handlers and call the handler
        response = self.get_response(*args, **kwargs)
        return response
