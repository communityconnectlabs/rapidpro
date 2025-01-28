from django.db import models


class CustomerEventConfig(models.Model):
    """
    A customer event is a record of a customer interaction with the system
    """
    org = models.ForeignKey('orgs.Org', on_delete=models.CASCADE, related_name='events')
    created_by = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='events')
    created_on = models.DateTimeField(auto_now_add=True)
    type = models.CharField(max_length=20)
    data = models.JSONField(default=dict)

    class Meta:
        ordering = ['-created_on']

    def __str__(self):
        return f"{self.customer} {self.type} {self.created_on}"
