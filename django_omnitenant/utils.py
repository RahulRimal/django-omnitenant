"""
Utility Functions for django-omnitenant

This module provides a collection of utility functions for common operations in a
multi-tenant Django application, including:
    - Model retrieval (tenant and domain models)
    - Database and cache connection management
    - Schema name validation and normalization
    - Tenant resolution and backend selection
    - Tenant context access

These functions serve as helpers for other modules and can be imported directly
for use in custom code.

Common Imports:
    ```python
    from django_omnitenant.utils import (
        get_tenant_model,
        get_domain_model,
        get_current_tenant,
        reset_db_connection,
        reset_cache_connection,
        convert_to_valid_pgsql_schema_name,
    )
    ```

Thread Safety:
    Most functions are thread-safe. Database and cache connection resets are
    handled carefully with exception handling to prevent race conditions.
"""

import re
from typing import TYPE_CHECKING, Optional

from django.apps import apps
from django.core.cache import caches
from django.db import connections
from django.db.models.base import Model

from enum import Enum, auto

from .conf import settings

if TYPE_CHECKING:
    from django_omnitenant.models import BaseTenant


# Model Retrieval Functions
# ==========================


def get_tenant_model() -> type[Model]:
    """
    Retrieve the Tenant model class configured in settings.

    This function dynamically loads the Tenant model based on the
    TENANT_MODEL setting. The model is specified as a string in
    "app_label.ModelName" format.

    Returns:
        type[Model]: The Tenant model class

    Raises:
        LookupError: If the model specified in TENANT_MODEL setting
                    cannot be found
        RuntimeError: If called before Django apps are ready

    Configuration:
        settings.OMNITENANT_CONFIG[TENANT_MODEL] = "myapp.Tenant"

    Usage:
        ```python
        from django_omnitenant.utils import get_tenant_model

        Tenant = get_tenant_model()
        all_tenants = Tenant.objects.all()
        public_tenant = Tenant.objects.get(
            tenant_id=settings.PUBLIC_TENANT_NAME
        )
        ```

    Note:
        This function is preferred over importing the Tenant model directly
        because it allows custom tenant models to be configured via settings
        without requiring model import changes throughout the codebase.

    Performance:
        Django's apps.get_model() caches the model lookup, so subsequent
        calls are fast.
    """
    return apps.get_model(settings.TENANT_MODEL)


def get_domain_model() -> type[Model]:
    """
    Retrieve the Domain model class configured in settings.

    This function dynamically loads the Domain model based on the
    DOMAIN_MODEL setting. The model is specified as a string in
    "app_label.ModelName" format.

    Returns:
        type[Model]: The Domain model class

    Raises:
        LookupError: If the model specified in DOMAIN_MODEL setting
                    cannot be found
        RuntimeError: If called before Django apps are ready

    Configuration:
        settings.OMNITENANT_CONFIG[DOMAIN_MODEL] = "myapp.Domain"

    Usage:
        ```python
        from django_omnitenant.utils import get_domain_model

        Domain = get_domain_model()
        custom_domains = Domain.objects.filter(is_custom=True)
        domain = Domain.objects.get(name="example.com")
        ```

    Note:
        This function is preferred over importing the Domain model directly
        because it allows custom domain models to be configured via settings.

    Performance:
        Django's apps.get_model() caches the model lookup, so subsequent
        calls are fast.
    """
    return apps.get_model(settings.DOMAIN_MODEL)


# App and Configuration Functions
# ================================


def get_custom_apps() -> list[str]:
    """
    Retrieve a list of custom application names in the project.

    This function identifies custom/local apps (apps created by the developer)
    and excludes Django built-in apps and third-party packages.

    Returns:
        list[str]: List of app labels for custom applications

    Algorithm:
        1. If CUSTOM_APPS is explicitly configured in settings, return it
        2. Otherwise, iterate through all installed apps
        3. Include only apps whose path starts with BASE_DIR
        4. This distinguishes project apps from installed packages

    Configuration:
        Option 1 - Explicit configuration:
        ```python
        OMNITENANT_CONFIG = {
            'CUSTOM_APPS': ['app1', 'app2', 'app3']
        }
        ```

        Option 2 - Automatic detection:
        The function will automatically detect apps in your project directory

    Usage:
        ```python
        from django_omnitenant.utils import get_custom_apps

        custom_apps = get_custom_apps()
        # Returns: ['myapp', 'accounts', 'products']

        # Use for selectively applying migrations
        for app_label in custom_apps:
            run_migrations_for_tenant(tenant, app_label)
        ```

    Returns:
        ```python
        ['myapp', 'accounts', 'api', 'utils']
        ```

    Note:
        - Built-in Django apps (auth, admin, etc.) are excluded
        - Third-party packages (rest_framework, celery, etc.) are excluded
        - Only custom project apps are included

    Use Cases:
        - Identifying which apps should be migrated per-tenant
        - Determining custom models for tenant isolation
        - Discovering custom management commands
        - Building dynamic model lists for specific operations
    """
    if hasattr(settings, "CUSTOM_APPS"):
        # If explicitly configured, return the configured list
        return settings.CUSTOM_APPS

    # Automatically detect custom apps by checking if they're in BASE_DIR
    custom_apps = []
    base_dir_str = str(settings.BASE_DIR)

    # Iterate through all installed apps
    for app_config in apps.get_app_configs():
        # Include only apps whose path is within the project BASE_DIR
        if app_config.path.startswith(base_dir_str):
            custom_apps.append(app_config.name)

    return custom_apps


# Database and Cache Connection Management
# =========================================


def reset_db_connection(alias: str):
    """
    Close and evict a database connection to force re-initialization.

    This function is critical for multi-tenant systems where database connection
    parameters (database name, schema, user) change per-tenant. After updating
    Django's connections settings, this function ensures the old connection is
    discarded and the new one is created on next access.

    Args:
        alias (str): The database alias to reset (e.g., 'default', 'tenant1', 'master')

    Returns:
        Connection: The reset connection object (used for validation that reset succeeded)

    What It Does:
        1. Closes the active connection if one exists
        2. Removes the connection from Django's connection pool
        3. Forces re-initialization on next access

    Process:
        - Try to close the connection gracefully
        - Remove from _connections cache
        - Re-access the connection to verify reset
        - Returns the new connection object

    Exception Handling:
        All exceptions are caught and suppressed to handle edge cases where:
        - Connection is already closed
        - Connection object doesn't have close()
        - Connection is not in the cache
        - Thread-safety issues during deletion

    Usage:
        ```python
        from django_omnitenant.utils import reset_db_connection
        from django.conf import settings

        # After updating database configuration for a tenant
        DATABASES['tenant1'] = {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': 'tenant1_db',
            'USER': 'postgres',
            # ... other settings
        }

        # Reset the connection to use new settings
        reset_db_connection('tenant1')

        # Now queries use the new database
        from django.db import connections
        conn = connections['tenant1']
        ```

    Typical Scenarios:
        1. Dynamic tenant database creation
        2. Switching database routing for a request
        3. Testing (isolating test databases)
        4. Database failover/recovery
        5. Connection pool cleanup

    Thread Safety:
        This function handles thread-safety by:
        - Using try-except blocks around all operations
        - Not raising exceptions on failure
        - Allowing Django to rebuild connections on demand

    Warning:
        - Active queries on the old connection will be interrupted
        - Ensure no pending transactions before calling
        - Best called during request/transaction boundaries
    """
    # Try to close the existing connection
    if alias in connections:
        try:
            # Attempt graceful connection close
            connections[alias].close()
        except Exception:
            # Silently ignore errors (connection may already be closed)
            pass

        try:
            # Remove from Django's connection pool
            # This ensures a fresh connection is created on next access
            del connections._connections.connections[alias]  # type: ignore[attr-defined]
        except Exception:
            # Silently ignore errors (may not exist in pool)
            pass

    # Access the connection to force re-initialization with new settings
    return connections[alias]


def reset_cache_connection(alias: str):
    """
    Close and evict a cache backend to force re-initialization.

    This function is important for multi-tenant systems where cache configuration
    changes per-tenant. After updating Django's cache settings, this function
    ensures the old cache backend is discarded and the new one is created on
    next access.

    Args:
        alias (str): The cache alias to reset (e.g., 'default', 'tenant_cache', 'master')

    Returns:
        BaseMemcachedCache: The reset cache backend object

    What It Does:
        1. Closes the cache backend if it has a close() method
        2. Removes the backend from Django's cache pool
        3. Forces re-initialization on next access

    Exception Handling:
        All exceptions are caught and suppressed to handle:
        - Backend without close() method
        - Backend not in cache pool
        - Thread-safety issues during cleanup
        - Various backend implementations

    Usage:
        ```python
        from django_omnitenant.utils import reset_cache_connection
        from django.conf import settings

        # After updating cache configuration for a tenant
        CACHES['tenant1_cache'] = {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': 'redis://tenant1:6379/1',
        }

        # Reset the cache backend to use new settings
        reset_cache_connection('tenant1_cache')

        # Now cache operations use the new backend
        from django.core.cache import caches
        cache = caches['tenant1_cache']
        cache.set('key', 'value')
        ```

    Typical Scenarios:
        1. Tenant-specific cache backends
        2. Per-tenant Redis instances
        3. Cache pool cleanup during testing
        4. Dynamic cache configuration
        5. Cache backend switching

    Thread Safety:
        This function handles thread-safety by:
        - Using try-except blocks around all operations
        - Not raising exceptions on failure
        - Allowing Django to rebuild backends on demand

    Supported Backends:
        - Database cache
        - File-based cache
        - Memcached
        - Redis (if close() implemented)
        - Locmem (local memory cache)

    Note:
        - Cache data in the old backend may be lost
        - Pending cache operations are interrupted
        - New backend is created with fresh settings
    """
    # Best effort: try to close the existing cache backend
    try:
        # Get the backend from the cache pool if it exists
        backend = caches._caches.caches.get(alias)  # type: ignore[attr-defined]

        # If backend exists and has a close method, call it
        if backend and hasattr(backend, "close"):
            try:
                backend.close()
            except Exception:
                # Some backends may raise on close, that's okay
                pass
    except Exception:
        # If we can't access the cache internals, continue anyway
        pass

    # Remove from Django's cache backend pool
    # This ensures a fresh backend is created on next access
    try:
        caches._caches.caches.pop(alias, None)  # type: ignore[attr-defined]
    except Exception:
        # If we can't remove from pool, continue anyway
        pass

    # Access the cache to force re-initialization with new settings
    return caches[alias]


# PostgreSQL Schema Name Utilities
# ================================


def convert_to_valid_pgsql_schema_name(name: str) -> str:
    """
    Convert a string into a valid PostgreSQL schema name.

    PostgreSQL has strict requirements for schema names. This function normalizes
    any string into a valid schema name following all PostgreSQL rules.

    PostgreSQL Rules for Schema Names:
        - Maximum length: 63 characters
        - Cannot start with 'pg_' (reserved prefix)
        - Only letters (a-z, A-Z), numbers (0-9), and underscores (_)
        - Case-insensitive (stored as lowercase)

    Transformation Steps:
        1. Convert to lowercase
        2. Replace all invalid characters with underscores
        3. Truncate to 63 characters maximum
        4. If starts with 'pg_', prefix with 'x_' to avoid reserved names
        5. If empty after transformation, use 'default_schema'

    Args:
        name (str): The original string to convert (e.g., tenant name, domain name)

    Returns:
        str: A valid PostgreSQL schema name

    Examples:
        ```python
        from django_omnitenant.utils import convert_to_valid_pgsql_schema_name

        # Simple alphanumeric
        convert_to_valid_pgsql_schema_name("tenant1")
        # Returns: "tenant1"

        # With special characters
        convert_to_valid_pgsql_schema_name("my-tenant@2024")
        # Returns: "my_tenant_2024"

        # Reserved prefix
        convert_to_valid_pgsql_schema_name("pg_custom")
        # Returns: "x_custom"

        # Mixed case with spaces
        convert_to_valid_pgsql_schema_name("My Tenant Inc.")
        # Returns: "my_tenant_inc_"

        # Very long name (>63 chars)
        long_name = "a" * 100
        result = convert_to_valid_pgsql_schema_name(long_name)
        # Returns: "aaa...aaa" (63 'a' characters)

        # Empty or invalid input
        convert_to_valid_pgsql_schema_name("!!!@@##")
        # Returns: "default_schema"
        ```

    Use Cases:
        1. Converting tenant names to database schemas
        2. Converting domain names to schemas
        3. User-provided schema name validation
        4. Normalizing custom schema identifiers
        5. Database migration naming

    Performance:
        O(n) where n is the length of the input string
        (mainly regex replacement operation)

    Note:
        This function is PostgreSQL-specific. Other databases have different
        naming rules and may require different validation.
    """
    # Step 1: Convert to lowercase and replace invalid characters
    # Match: any character that's NOT letters, numbers, or underscore
    # Replace with: underscore
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name.lower())

    # Step 2: Ensure length doesn't exceed PostgreSQL's 63 character limit
    name = name[:63]

    # Step 3: Handle reserved 'pg_' prefix
    # PostgreSQL schema names starting with 'pg_' are reserved for system schemas
    if name.startswith("pg_"):
        # Replace "pg_xxx" with "x_xxx" to avoid conflict
        name = f"x_{name[3:]}" or "x"

    # Step 4: Ensure result is not empty
    # If all characters were replaced/removed, use default
    if not name:
        name = "default_schema"

    return name


# Database Schema Query Functions
# ===============================


def get_active_schema_name(connection=None, db_alias: str | None = None) -> str:
    """
    Retrieve the currently active schema name for a database connection.

    In PostgreSQL, each connection has a search_path that determines which
    schemas are searched for tables/objects. This function returns the name
    of the first (primary) schema in that path.

    Args:
        connection (Connection, optional): A Django database connection object.
                                          If not provided, uses db_alias parameter.

        db_alias (str, optional): The database alias (e.g., 'default', 'tenant1').
                                 Only used if connection is None.
                                 Defaults to 'default' if both are None.

    Returns:
        str: The active schema name. Defaults to 'public' if query fails.

    Examples:
        ```python
        from django_omnitenant.utils import get_active_schema_name
        from django.db import connections

        # Using default connection
        schema = get_active_schema_name()
        # Returns: "public"

        # Using specific database alias
        schema = get_active_schema_name(db_alias='tenant1')
        # Returns: "tenant_schema"

        # Using connection object directly
        conn = connections['master']
        schema = get_active_schema_name(connection=conn)
        # Returns: "master_schema"
        ```

    SQL Query:
        ```sql
        SELECT current_schema();
        ```

        This PostgreSQL function returns the name of the schema that is
        currently being used (typically the first in search_path).

    Use Cases:
        1. Verify correct schema is active before queries
        2. Debug schema switching in multi-tenant systems
        3. Validate tenant database/schema setup
        4. Logging and monitoring schema state
        5. Testing schema isolation

    Exception Handling:
        If the query fails for any reason (connection issues, not PostgreSQL, etc.),
        the function returns 'public' as a safe default.

    PostgreSQL-Specific:
        This function uses PostgreSQL's current_schema() function.
        May not work correctly with other database backends.

    Note:
        - Returns the FIRST schema in search_path, not all schemas
        - For testing, run within TenantContext to ensure correct schema is active
        - Useful for validating database routing works correctly
    """
    # If no connection provided, get it from the database connections pool
    if connection is None:
        connection = connections[db_alias or "default"]

    try:
        # Execute PostgreSQL function to get current active schema
        with connection.cursor() as cursor:
            # current_schema() returns the name of the schema in search_path
            cursor.execute("SELECT current_schema();")
            # Fetch result and extract schema name
            result = cursor.fetchone()
            return result[0]  # type: ignore
    except Exception:
        # If query fails (not PostgreSQL, connection issue, etc.), return default
        # 'public' is the default schema in PostgreSQL
        return "public"


# Tenant Backend and Context Functions
# ====================================


def get_tenant_backend(tenant):
    """
    Get the appropriate backend for a tenant based on its isolation type.

    django-omnitenant supports multiple isolation strategies (database-per-tenant,
    schema-per-tenant, etc.). This function returns the correct backend instance
    for the tenant's isolation strategy.

    Args:
        tenant (BaseTenant): The tenant instance to get the backend for

    Returns:
        TenantBackend: Either SchemaTenantBackend or DatabaseTenantBackend
                      depending on tenant.isolation_type

    Isolation Type Mapping:
        BaseTenant.IsolationType.SCHEMA -> SchemaTenantBackend(tenant)
        Other types (DATABASE) -> DatabaseTenantBackend(tenant)

    Examples:
        ```python
        from django_omnitenant.utils import get_tenant_backend
        from django_omnitenant.models import BaseTenant

        # For schema-isolated tenant
        tenant = Tenant.objects.get(tenant_id='tenant1')
        backend = get_tenant_backend(tenant)
        # Returns: SchemaTenantBackend(tenant)

        # For database-isolated tenant
        tenant2 = Tenant.objects.get(tenant_id='tenant2')
        backend2 = get_tenant_backend(tenant2)
        # Returns: DatabaseTenantBackend(tenant2)
        ```

    Use Cases:
        1. Getting backend for database routing
        2. Tenant-specific database operations
        3. Schema management for schema-isolated tenants
        4. Database setup/migration for database-isolated tenants
        5. Tenant provisioning workflows

    Backend Responsibilities:
        SchemaTenantBackend:
        - Manages PostgreSQL schema per tenant
        - Handles schema creation/deletion
        - Routes queries to correct schema
        - Manages migrations within schema

        DatabaseTenantBackend:
        - Manages separate database per tenant
        - Handles database routing
        - Manages database-level isolation
        - Routes queries to tenant database

    Note:
        - This function is typically called internally by the framework
        - Usually accessed through TenantContext or middleware
        - Return type depends on tenant configuration

    See Also:
        - SchemaTenantBackend: Schema-based isolation backend
        - DatabaseTenantBackend: Database-based isolation backend
        - BaseTenant.IsolationType: Isolation strategy enum
    """
    # Import here to avoid circular imports
    from django_omnitenant.models import BaseTenant
    from .backends import DatabaseTenantBackend, SchemaTenantBackend

    # Return appropriate backend based on tenant's isolation strategy
    return (
        # For schema-based isolation, return schema backend
        SchemaTenantBackend(tenant)
        if tenant.isolation_type == BaseTenant.IsolationType.SCHEMA
        # For database-based isolation (or other types), return database backend
        else DatabaseTenantBackend(tenant)
    )


def get_current_tenant() -> Optional["BaseTenant"]:
    """
    Retrieve the current tenant for the running context.

    This function accesses the tenant stored in the current thread-local context.
    It's useful in views, services, and other application code to access the
    currently active tenant without passing it explicitly as a parameter.

    Returns:
        Optional[BaseTenant]: The current tenant, or None if no tenant context
                             is active or it hasn't been set

    When to Use:
        - In Django views to get the current tenant
        - In service/business logic layer
        - In tasks and background jobs
        - Anywhere you need current tenant without explicit passing

    Examples:
        ```python
        from django_omnitenant.utils import get_current_tenant

        def my_view(request):
            # The middleware automatically sets tenant context
            tenant = get_current_tenant()
            if tenant:
                # Perform tenant-scoped operations
                users = User.objects.filter(tenant=tenant)
                return render(request, 'users.html', {'users': users})
            else:
                return HttpResponse("No tenant context", status=400)

        def business_logic():
            tenant = get_current_tenant()
            if not tenant:
                raise ValueError("This operation requires a tenant context")

            # Perform operations for current tenant
            process_tenant_data(tenant)
        ```

    Returns:
        None when:
        - No tenant context has been set
        - Called outside of TenantContext.use_tenant() context
        - Called in non-request code without explicit context
        - Middleware hasn't run yet (e.g., in app initialization)

    Thread Safety:
        This function is thread-safe. Each thread has its own tenant context
        stored in thread-local storage (contextvars).

    Use Cases:
        1. Access current tenant in views without request.tenant
        2. Get tenant in background tasks and celery jobs
        3. Access tenant in middleware or decorators
        4. Debugging - check which tenant is active
        5. Logging - include tenant ID in log context

    Related:
        - TenantContext: For setting/managing tenant context
        - TenantMiddleware: Automatically sets context for requests
        - request.tenant: Alternative way to access tenant in views

    Example - Celery Task:
        ```python
        from celery import shared_task
        from django_omnitenant.utils import get_current_tenant

        @shared_task
        def process_tenant_email():
            tenant = get_current_tenant()
            if tenant:
                send_emails_for_tenant(tenant)
            else:
                logger.warning("No tenant context for email processing")
        ```

    Note:
        For most views, using request.tenant (set by middleware) is preferred
        as it's more explicit and doesn't rely on thread-local state.
    """
    # Import here to avoid circular imports
    from django_omnitenant.tenant_context import TenantContext

    # Retrieve and return the tenant from thread-local context
    return TenantContext.get_tenant()


class TenantScope(Enum):
    def _generate_next_value_(name, start, count, last_values):
        return name.lower()

    MASTER = auto()
    TENANT = auto()
    SHARED = auto()
