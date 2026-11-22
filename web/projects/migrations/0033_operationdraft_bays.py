from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0032_operationexecution_progress_checkpoint'),
    ]

    operations = [
        migrations.AddField(
            model_name='operationdraft',
            name='bays',
            field=models.JSONField(blank=True, default=list, help_text='Named four-corner bays, each with a stable ID so renaming keeps its identity.'),
        ),
    ]
