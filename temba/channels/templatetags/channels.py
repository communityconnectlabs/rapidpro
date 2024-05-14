from django import template

from temba.contacts.models import ContactURN

register = template.Library()


@register.filter
def channel_icon(channel):
    return channel.type.icon


@register.simple_tag(takes_context=True)
def channellog_url(context, log, *args, **kwargs):
    return log.get_url_display(context["user"], ContactURN.ANON_MASK)


@register.simple_tag(takes_context=True)
def channellog_request(context, log, *args, **kwargs):
    return log.get_request_display(context["user"], ContactURN.ANON_MASK)


@register.simple_tag(takes_context=True)
def channellog_response(context, log, *args, **kwargs):
    if not log.response:
        return log.description

    return log.get_response_display(context["user"], ContactURN.ANON_MASK)


@register.filter
def adapt_for_widget(text):
    return text.replace("'", "\\'").replace("\n", "\\n").replace("\r", "\\r")


@register.filter
def adapt_for_widget_bool(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(bool(value and value == "true")).lower()


@register.filter
def adapt_for_widget_list(_list):
    return _list if _list else []
