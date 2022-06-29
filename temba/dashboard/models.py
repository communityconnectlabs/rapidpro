from django.db import models


class EmbeddedBoard(models.Model):
    title = models.CharField(max_length=256, blank=True)
    url = models.URLField()

    def __str__(self):
        if self.title:
            return self.title
        return f"Embedded board #{self.id}"
