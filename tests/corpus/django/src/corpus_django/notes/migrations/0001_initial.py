from typing import ClassVar

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies: ClassVar[list] = []
    operations: ClassVar[list] = [
        migrations.CreateModel(
            name="Note",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=80)),
                ("body", models.TextField(blank=True)),
            ],
        ),
    ]
