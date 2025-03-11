from django import template

register = template.Library()


@register.filter
def call_method(obj, method_with_args):
    """
    Calls a method of an object with parameters in Django templates.
    Usage: {{ my_object|call_method:"method_name,arg1,arg2" }}
    """
    try:
        method_name, *args = method_with_args.split(",")
        method = getattr(obj, method_name, None)

        if callable(method):
            return method(*args)
    except Exception as e:
        return f"Error: {e}"
    return f"Method '{method_name}' not found."
