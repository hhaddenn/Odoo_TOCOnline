# Generated manually for delete tracking models

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("state", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="DeletionTombstone",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("entity_type", models.CharField(max_length=50)),
                ("system", models.CharField(choices=[("odoo", "odoo"), ("toconline", "toconline")], max_length=20)),
                ("original_id", models.CharField(max_length=255)),
                ("pending_since", models.DateTimeField(auto_now_add=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("confirmation_count", models.IntegerField(default=0)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="state.company")),
            ],
            options={
                "unique_together": {("company", "system", "original_id", "entity_type")},
            },
        ),
        migrations.CreateModel(
            name="EntitySnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("entity_type", models.CharField(max_length=50)),
                ("system", models.CharField(choices=[("odoo", "odoo"), ("toconline", "toconline")], max_length=20)),
                ("entity_ids", models.JSONField(default=list)),
                ("taken_at", models.DateTimeField(auto_now_add=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="state.company")),
            ],
            options={
                "ordering": ["-taken_at"],
                "unique_together": {("company", "system", "entity_type")},
            },
        ),
    ]
