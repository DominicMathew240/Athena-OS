from django.apps import AppConfig


class ProjectsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "projects"

    def ready(self) -> None:
        from django.contrib.auth import get_user_model
        from django.db.models.signals import m2m_changed, post_delete

        from . import access_admin
        from .models import OperationRecording
        from .operations.recordings import delete_storage_file

        post_delete.connect(delete_storage_file, sender=OperationRecording, dispatch_uid="athena_delete_operation_recording")

        # Role changes made by any other tool (a shell, a user-admin add-on) are written down too.
        m2m_changed.connect(
            access_admin.record_group_change,
            sender=get_user_model().groups.through,
            dispatch_uid="athena_record_group_change",
        )
