import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0028_sourcecredential'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SourceViewEstimate',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('person_height_percent', models.FloatField(help_text='How tall a person stands in this view, as a share of the picture height (a user-entered estimate; Athena cannot measure it).')),
                ('notes', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('source', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='view_estimates', to='projects.operationsource')),
            ],
            options={
                'ordering': ('-updated_at', '-id'),
            },
        ),
        migrations.AddConstraint(
            model_name='sourceviewestimate',
            constraint=models.CheckConstraint(condition=models.Q(('person_height_percent__gte', 1), ('person_height_percent__lte', 100)), name='sourceviewestimate_percent_range'),
        ),
    ]
