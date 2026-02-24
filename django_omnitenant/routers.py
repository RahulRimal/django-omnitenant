from django.apps import apps
from django.db import connections
from .conf import settings
from .utils import get_custom_apps
from .tenant_context import TenantContext
from django_omnitenant.models import BaseTenant
from django_omnitenant.utils import TenantScope


class TenantRouter:
    def _get_scope(self, model):
        """Helper to get the scope from Model, then AppConfig, then Default."""
        if model._meta.app_label not in get_custom_apps():
            return TenantScope.TENANT  # Default for external apps

        # 1. Check Model Attribute
        scope = getattr(model, "tenant_scope", None)
        if isinstance(scope, TenantScope):
            return scope

        # 2. Check AppConfig Attribute
        try:
            app_config = apps.get_app_config(model._meta.app_label)
            scope = getattr(app_config, "tenant_scope", TenantScope.TENANT)
            return scope
        except LookupError:
            return TenantScope.TENANT

    def db_for_read(self, model, **hints):
        scope = self._get_scope(model)

        if scope == TenantScope.MASTER:
            return settings.MASTER_DB_ALIAS

        if scope == TenantScope.SHARED:
            # If a tenant is active, use it; otherwise fallback to master
            return TenantContext.get_db_alias() or settings.MASTER_DB_ALIAS

        return TenantContext.get_db_alias()

    def db_for_write(self, model, **hints):
        return self.db_for_read(model, **hints)

    def allow_relation(self, obj1, obj2, **hints):
        return self.db_for_read(obj1) == self.db_for_read(obj2)

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        # 1. Fast-track non-custom apps
        if app_label not in get_custom_apps():
            return True

        # 2. Identify the Model Scope
        if model_name:
            try:
                model = apps.get_model(app_label, model_name)
                scope = self._get_scope(model)
            except LookupError:
                return None
        else:
            # Fallback to AppConfig scope if model_name isn't provided
            app_config = apps.get_app_config(app_label)
            scope = getattr(app_config, "tenant_scope", TenantScope.TENANT)

        # We check the database alias and the schema search_path from settings
        is_master_db = db == settings.MASTER_DB_ALIAS

        connection = connections[db]
        # Check if the connection is currently set to a specific tenant schema
        # In django-omnitenant, this is usually managed via the 'options' in settings
        search_path = connection.settings_dict.get("OPTIONS", {}).get("options", "")
        is_public_schema = settings.DEFAULT_SCHEMA_NAME in search_path or "search_path" not in search_path

        # 4. Routing Logic Matrix
        if scope == TenantScope.MASTER:
            return is_master_db and is_public_schema

        if scope == TenantScope.TENANT:
            tenant = TenantContext.get_tenant()
            is_schema_iso = tenant and tenant.isolation_type == BaseTenant.IsolationType.SCHEMA

            if is_schema_iso:
                return is_master_db and not is_public_schema
            return not is_master_db and is_public_schema

        if scope == TenantScope.SHARED:
            # This model is allowed in BOTH Master and Tenant locations
            # We simply return True if it matches either valid Master or valid Tenant criteria
            is_master_loc = is_master_db and is_public_schema

            tenant = TenantContext.get_tenant()
            is_schema_iso = tenant and tenant.isolation_type == BaseTenant.IsolationType.SCHEMA
            is_tenant_loc = (is_master_db and not is_public_schema) if is_schema_iso else (not is_master_db)

            return is_master_loc or is_tenant_loc

        return False
