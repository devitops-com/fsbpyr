from django.db import models


class Note(models.Model):
    title = models.CharField(max_length=80)
    body = models.TextField(blank=True)

    def __str__(self) -> str:
        return self.title
