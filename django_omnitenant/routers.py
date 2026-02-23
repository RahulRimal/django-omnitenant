from django.apps import apps

from django_omnitenant.models import BaseTenant
from .tenant_context import TenantContext
from .constants import constants
from .conf import settings

from .utils import get_custom_apps


from django.db import connection


class TenantRouter:
    def _is_tenant_managed_model(self, model):
        if model._meta.app_label not in get_custom_apps():
            return True  # treat non-custom apps as tenant-managed by default

        # 1. Check AppConfig
        app_config = apps.get_app_config(model._meta.app_label)
        if hasattr(app_config, "tenant_managed"):
            return getattr(app_config, "tenant_managed", True)

        # 2. Check Model
        return getattr(model, "tenant_managed", True)

    def _is_master_managed_model(self, model):
        if model._meta.app_label not in get_custom_apps():
            return True  # treat non-custom apps as tenant-managed by default
        # Check AppConfig
        app_config = apps.get_app_config(model._meta.app_label)
        if hasattr(app_config, "master_managed"):
            return getattr(app_config, "master_managed", False)

        # Check Model
        return getattr(model, "master_managed", False)

    def db_for_read(self, model, **hints):
        if self._is_master_managed_model(model):
            return settings.MASTER_DB_ALIAS
        elif self._is_tenant_managed_model(model):
            return TenantContext.get_db_alias()
        return (
            settings.PUBLIC_DB_ALIAS
        )  # If not master tenant managed or tenant-managed then it must be a default or public tenant managed model

    def db_for_write(self, model, **hints):
        if self._is_master_managed_model(model):
            return settings.MASTER_DB_ALIAS
        elif self._is_tenant_managed_model(model):
            return TenantContext.get_db_alias()
        return settings.PUBLIC_DB_ALIAS

    def allow_relation(self, obj1, obj2, **hints):
        return len({self.db_for_read(obj1), self.db_for_read(obj2)}) == 1

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """
        Determine if migrations for a given app/model should be applied to the specified database.

        This method is tenant-aware and schema-aware. It ensures that:
            - Non-custom apps are always migrated.
            - Tenant-managed apps/models are migrated either to the tenant's DB or tenant schema.
            - Non-tenant-managed apps/models are migrated only to the default DB / public schema.

        Args:
            db (str): The database alias being considered for migration.
            app_label (str): The Django app label of the model/app being migrated.
            model_name (str, optional): Specific model name being migrated.
            **hints: Additional migration hints (not used here).

        Returns:
            bool: True if migration should be applied to the given DB/schema, False otherwise.
        """
        # Allow migrations for apps not considered "custom"
        if app_label not in get_custom_apps():
            return True

        # Get the current tenant context
        tenant: BaseTenant = TenantContext.get_tenant()  # type: ignore
        is_schema_tenant = tenant and tenant.isolation_type == BaseTenant.IsolationType.SCHEMA

        # Attempt to get the current schema from the database
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_schema();")
                selected_schema = cursor.fetchone()[0]  # type: ignore
        except Exception:
            # If schema detection fails, mark as unknown
            selected_schema = "unknown"

        # Determine if the migration is for a tenant-managed app/model
        if not model_name:
            try:
                app_config = apps.get_app_config(app_label)
            except LookupError:
                return None
            is_tenant_managed = getattr(app_config, "tenant_managed", True)
        else:
            try:
                model = apps.get_model(app_label, model_name)
            except LookupError:
                return None
            is_tenant_managed = self._is_tenant_managed_model(model)

        if self._is_master_managed_model(model):
            return db == settings.MASTER_DB_ALIAS and selected_schema == settings.DEFAULT_SCHEMA_NAME

        if is_tenant_managed:
            if is_schema_tenant:
                # Schema-managed tenant: migrate to tenant schema on master DB
                return db == settings.MASTER_DB_ALIAS and selected_schema != settings.DEFAULT_SCHEMA_NAME

            # DB-managed tenant: migrate to tenant DB (non-default)
            return db != settings.MASTER_DB_ALIAS and selected_schema == settings.DEFAULT_SCHEMA_NAME

        return False
