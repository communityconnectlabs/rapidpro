from django.db import models
from django.urls import reverse
from django.utils import timezone

from temba.orgs.models import Org


class Dashboard(models.Model):
    org = models.ForeignKey(Org, verbose_name="Organization", related_name="dashboards", on_delete=models.PROTECT)
    metabase_dashboard_id = models.IntegerField(verbose_name="Metabase Dashboard ID")
    metabase_dashboard_title = models.CharField(verbose_name="Metabase Dashboard Title", max_length=255)
    is_active = models.BooleanField(
        default=True, help_text="Whether this item is active, use this instead of deleting"
    )
    created_on = models.DateTimeField(
        default=timezone.now, editable=False, blank=True, help_text="When this item was originally created"
    )
    modified_on = models.DateTimeField(
        default=timezone.now, editable=False, blank=True, help_text="When this item was last modified"
    )

    def __str__(self):
        return f"{self.metabase_dashboard_id} - {self.org.name}"

    def get_url(self):
        return reverse("dashboard.dashboard_view", args=[self.pk])
