# Generated to match Django auto index names for OperationLearningExample (T33).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0034_operationlearningexample"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="operationlearningexample",
            new_name="projects_op_operati_800a6d_idx",
            old_name="projects_op_operati_t33a_idx",
        ),
        migrations.RenameIndex(
            model_name="operationlearningexample",
            new_name="projects_op_recordi_c05e8e_idx",
            old_name="projects_op_recordi_t33b_idx",
        ),
        migrations.RenameIndex(
            model_name="operationlearningexample",
            new_name="projects_op_site_id_6b3e33_idx",
            old_name="projects_op_site_id_t33c_idx",
        ),
    ]
