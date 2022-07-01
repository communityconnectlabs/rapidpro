import time

import jwt
from django.conf import settings
from django.db import models
from django.utils.translation import ugettext_lazy as _


class EmbeddedBoard(models.Model):
    TYPE_PUBLIC = "P"
    TYPE_METABASE = "M"
    EMBEDDING_TYPES = (
        (TYPE_PUBLIC, _("Public link to be embedded")),
        (TYPE_METABASE, _("Metabase dashboard")),
    )

    title = models.CharField(max_length=256, blank=True)
    embedding_type = models.CharField(max_length=1, choices=EMBEDDING_TYPES, default=TYPE_PUBLIC)
    metabase_id = models.PositiveIntegerField(null=True, blank=True)
    url = models.URLField(blank=True)

    def __str__(self):
        if self.title:
            return self.title
        return f"Embedded board #{self.id}"

    @property
    def dashboard_link(self):
        if self.embedding_type == "M" and all([settings.METABASE_SITE_URL, settings.METABASE_SECRET_KEY]):
            payload = {
                "resource": {"dashboard": self.metabase_id},
                "exp": round(time.time()) + (60 * 10),  # 10 minute expiration
                "params": {},
            }
            token = jwt.encode(payload, settings.METABASE_SECRET_KEY, algorithm="HS256").decode()
            iframe_url = f"{settings.METABASE_SITE_URL}/embed/dashboard/{token}#bordered=true&titled=true"
            print(iframe_url)
            return iframe_url
        return self.url
