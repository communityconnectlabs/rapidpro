from django.urls import re_path

from .views import LinkCRUDL, LinkHandler

urlpatterns = [re_path(r"^link/handler/(?P<uuid>[^/]+)/?$", LinkHandler.as_view(), {}, "links.link_handler")]

urlpatterns += LinkCRUDL().as_urlpatterns()
