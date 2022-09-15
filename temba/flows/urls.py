from django.conf.urls import url

from .views import (
    FlowCRUDL,
    FlowImageCRUDL,
    FlowLabelCRUDL,
    FlowRunCRUDL,
    FlowSessionCRUDL,
    FlowStartCRUDL,
    PartialTemplate,
    FlowTemplateCRUDL,
)

urlpatterns = FlowCRUDL().as_urlpatterns()
urlpatterns += FlowImageCRUDL().as_urlpatterns()
urlpatterns += FlowLabelCRUDL().as_urlpatterns()
urlpatterns += FlowRunCRUDL().as_urlpatterns()
urlpatterns += FlowSessionCRUDL().as_urlpatterns()
urlpatterns += FlowStartCRUDL().as_urlpatterns()
urlpatterns += FlowTemplateCRUDL().as_urlpatterns()
urlpatterns += [
    url(r"^partials/(?P<template>[a-z0-9\-_]+)$", PartialTemplate.as_view(), name="flows.partial_template")
]
