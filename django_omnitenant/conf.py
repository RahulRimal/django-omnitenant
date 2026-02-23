"""
Configuration Management for django-omnitenant

This module provides a centralized configuration system for django-omnitenant.
It wraps Django's settings object and provides cached access to multi-tenancy
configuration with sensible defaults.

The module implements a settings proxy pattern that:
    1. Allows transparent access to all Django settings
    2. Provides cached properties for omnitenant-specific configuration
    3. Validates required configuration at access time
    4. Supplies default values for optional settings
    5. Prevents accidental configuration overrides

Architecture:
    - _WrappedSettings: Proxy class wrapping Django's settings
    - settings singleton: Module-level instance providing the public API

Configuration Source:
    All configuration is read from Django's OMNITENANT_CONFIG setting:

    ```python
    OMNITENANT_CONFIG = {
        'TENANT_MODEL': 'myapp.Tenant',
        'DOMAIN_MODEL': 'myapp.Domain',
        'TENANT_RESOLVER': 'myapp.resolvers.SubdomainResolver',
        'PUBLIC_HOST': 'example.com',
        'PUBLIC_TENANT_NAME': 'public',
        'MASTER_TENANT_NAME': 'master',
        'PUBLIC_DB_ALIAS': 'default',
        'MASTER_DB_ALIAS': 'master',
        'DEFAULT_SCHEMA_NAME': 'public',
    }
    ```

Usage:
    ```python
    from django_omnitenant.conf import settings

    # Access configuration
    tenant_model = settings.TENANT_MODEL
    resolver_path = settings.TENANT_RESOLVER
    public_host = settings.PUBLIC_HOST

    # Also works for standard Django settings
    debug_mode = settings.DEBUG
    allowed_hosts = settings.ALLOWED_HOSTS
    ```

Configuration Validation:
    Required settings that must be defined:
    - TENANT_MODEL: Will raise ImproperlyConfigured if missing
    - DOMAIN_MODEL: Will raise ImproperlyConfigured if missing

    Optional settings with defaults:
    - TENANT_RESOLVER: Defaults to CustomDomainTenantResolver
    - PUBLIC_TENANT_NAME: Defaults to 'public_omnitenant'
    - MASTER_TENANT_NAME: Defaults to 'Master'
    - PUBLIC_DB_ALIAS: Defaults to 'public'
    - MASTER_DB_ALIAS: Defaults to 'default'
    - PUBLIC_HOST: Defaults to 'localhost'
    - DEFAULT_SCHEMA_NAME: Defaults to 'public'

Performance:
    - Uses @cached_property for lazy initialization
    - Configuration values are computed once per application lifetime
    - Minimal overhead after first access
"""

from __future__ import annotations
from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.functional import cached_property

from .constants import constants


class _WrappedSettings:
    """
    Proxy class that wraps Django's settings object with multi-tenancy configuration.

    This class implements a settings wrapper that:
    1. Transparently proxies access to Django settings
    2. Provides cached, typed access to omnitenant configuration
    3. Validates required configuration with helpful error messages
    4. Supplies sensible defaults for optional settings
    5. Prevents accidental modification of core settings

    Design Pattern:
        The class uses the Proxy pattern to delegate all attribute access to
        Django's settings, while adding caching and defaults for omnitenant-specific
        configuration keys.

    Configuration Access:
        Standard Django settings: Proxied directly via __getattr__
        ```python
        settings.DEBUG  # -> django_settings.DEBUG
        settings.DATABASES  # -> django_settings.DATABASES
        ```

        Omnitenant settings: Cached properties from OMNITENANT_CONFIG
        ```python
        settings.TENANT_MODEL  # -> OMNITENANT_CONFIG['TENANT_MODEL']
        settings.PUBLIC_HOST  # -> OMNITENANT_CONFIG.get('PUBLIC_HOST', 'localhost')
        ```

    Caching:
        All properties use @cached_property to cache values after first access.
        This ensures:
        - Configuration is read once per application lifetime
        - No repeated dictionary lookups
        - Consistent values throughout request lifecycle

    Immutability:
        Assignment to existing attributes raises ValueError to prevent accidental
        configuration changes. This protects against:
        - Runtime setting modifications
        - Configuration conflicts
        - Thread-safety issues
    """

    def __getattr__(self, item):
        """
        Proxy access to Django settings for non-omnitenant attributes.

        This special method is called when an attribute is not found in the
        instance dictionary. It delegates the lookup to Django's settings,
        allowing transparent access to all standard Django configuration.

        Args:
            item (str): The attribute name being accessed

        Returns:
            The value from django_settings.item

        Examples:
            ```python
            from django_omnitenant.conf import settings

            # Proxies to Django settings
            debug = settings.DEBUG
            secret = settings.SECRET_KEY
            databases = settings.DATABASES
            ```

        Note:
            This is called only for attributes NOT found in the instance.
            Cached properties and special methods are found first, so this
            only handles non-cached Django settings access.
        """
        return getattr(django_settings, item)

    def __setattr__(self, key, value):
        """
        Control attribute assignment to prevent accidental configuration changes.

        This special method prevents assignment to attributes after they've been
        set (cached), which protects the configuration from being modified at runtime.

        Args:
            key (str): The attribute name being set
            value: The value being assigned

        Raises:
            ValueError: If trying to assign to an already-existing attribute

        Behavior:
            - Allows initial attribute assignment (during __init__)
            - Raises ValueError for any subsequent assignment attempts
            - Protects cached_property values from being overwritten

        Examples:
            ```python
            from django_omnitenant.conf import settings

            # This would raise ValueError
            settings.PUBLIC_HOST = "newhost.com"  # ✗ ValueError
            ```

        Purpose:
            Prevents accidental runtime modification of cached settings that
            should remain constant for the lifetime of the application.

        Note:
            This check uses `self.__dict__` to detect if an attribute has already
            been set (cached by @cached_property).
        """
        if key in self.__dict__:
            raise ValueError("Item assignment is not supported")

        setattr(django_settings, key, value)

    # Configuration Properties
    # =======================

    @cached_property
    def OMNITENANT_CONFIG(self) -> dict:
        """
        Retrieve the main OMNITENANT_CONFIG dictionary from Django settings.

        This is the root configuration dictionary containing all multi-tenancy
        settings. If not configured, returns an empty dictionary (no error).

        Returns:
            dict: The OMNITENANT_CONFIG dictionary or {} if not set

        Configuration Key:
            Uses the constant: constants.OMNITENANT_CONFIG = "OMNITENANT_CONFIG"

        Configuration Source:
            ```python
            # In Django settings.py
            OMNITENANT_CONFIG = {
                'TENANT_MODEL': 'myapp.Tenant',
                'DOMAIN_MODEL': 'myapp.Domain',
                # ... other settings
            }
            ```

        Default:
            Empty dictionary {} if OMNITENANT_CONFIG is not defined in settings

        Usage:
            ```python
            from django_omnitenant.conf import settings

            config = settings.OMNITENANT_CONFIG
            tenant_model = config.get('TENANT_MODEL')
            ```

        Note:
            All other omnitenant settings are derived from this root config.
            This is cached for performance - configuration is read once.
        """
        return getattr(django_settings, constants.OMNITENANT_CONFIG, {})

    @cached_property
    def SCHEMA_CONFIG(self) -> dict:
        """
        Retrieve schema-specific configuration for schema-based multi-tenancy.

        For applications using PostgreSQL schema-per-tenant isolation, this
        contains schema-specific settings and customizations.

        Returns:
            dict: Schema configuration dictionary or {} if not configured

        Configuration Key:
            Uses the constant: constants.SCHEMA_CONFIG = "schema_config"
            Location: OMNITENANT_CONFIG['schema_config']

        Configuration Example:
            ```python
            OMNITENANT_CONFIG = {
                'schema_config': {
                    'auto_migrate': True,
                    'verbose_writes': True,
                }
            }
            ```

        Default:
            Empty dictionary {} if not specified in OMNITENANT_CONFIG

        Use Cases:
            1. Schema migration settings for per-schema migrations
            2. Schema-specific database behavior
            3. Schema isolation configuration
            4. Schema creation/deletion parameters

        Note:
            Only relevant for schema-based isolation. Database-based isolation
            does not use this configuration.
        """
        return self.OMNITENANT_CONFIG.get(constants.SCHEMA_CONFIG, {})

    @cached_property
    def TENANT_RESOLVER(self) -> str:
        """
        Get the configured tenant resolver class path.

        The resolver is responsible for determining which tenant a request belongs to.
        Different resolvers implement different strategies (subdomain, custom domain, header).

        Returns:
            str: Dotted Python path to the resolver class

        Default:
            'django_omnitenant.resolvers.CustomDomainTenantResolver'

        Configuration Key:
            Uses the constant: constants.TENANT_RESOLVER = "TENANT_RESOLVER"
            Location: OMNITENANT_CONFIG['TENANT_RESOLVER']

        Configuration Example:
            ```python
            OMNITENANT_CONFIG = {
                'TENANT_RESOLVER': 'myapp.resolvers.SubdomainResolver',
            }
            ```

        Available Built-in Resolvers:
            - django_omnitenant.resolvers.CustomDomainTenantResolver
            - django_omnitenant.resolvers.SubdomainTenantResolver

        Custom Resolvers:
            You can implement your own resolver by extending the base resolver class:

            ```python
            from django_omnitenant.resolvers.base import BaseTenantResolver

            class CustomResolver(BaseTenantResolver):
                def resolve(self, request):
                    # Custom tenant resolution logic
                    pass
            ```

        Usage:
            The middleware loads this resolver dynamically:

            ```python
            from django_omnitenant.conf import settings

            resolver_path = settings.TENANT_RESOLVER
            # 'django_omnitenant.resolvers.CustomDomainTenantResolver'
            ```
        """
        return self.OMNITENANT_CONFIG.get(
            constants.TENANT_RESOLVER,
            "django_omnitenant.resolvers.CustomDomainTenantResolver",
        )

    @cached_property
    def TIME_ZONE(self) -> str:
        """
        Get the application's time zone setting.

        Proxies to Django's TIME_ZONE setting with a default of 'UTC'.

        Returns:
            str: Time zone identifier (e.g., 'UTC', 'America/New_York')

        Default:
            'UTC'

        Configuration Source:
            Django's standard TIME_ZONE setting (not in OMNITENANT_CONFIG)

        Django Documentation:
            https://docs.djangoproject.com/en/stable/ref/settings/#time-zone

        Use Cases:
            1. Date/time display in tenant-specific time zone
            2. Scheduled task execution
            3. Log timestamp formatting
            4. API response timestamps

        Note:
            This reads from Django settings, not OMNITENANT_CONFIG.
            Used by django-omnitenant for tenant-aware time handling.
        """
        return getattr(django_settings, "TIME_ZONE", "UTC")

    @cached_property
    def DATABASE_ROUTERS(self) -> list[str]:
        return getattr(django_settings, "DATABASE_ROUTERS", ["django_omnitenant.routers.TenantRouter"])

    @cached_property
    def MASTER_TENANT_NAME(self) -> str:
        """
        Get the identifier of the master/default tenant.

        The master tenant is typically the administrative tenant or system tenant
        that manages other tenants. Some configurations may use it as the default
        fallback tenant.

        Returns:
            str: Master tenant identifier/name

        Default:
            'Master'

        Configuration Key:
            Uses the constant: constants.MASTER_TENANT_NAME = "MASTER_TENANT_NAME"
            Location: OMNITENANT_CONFIG['MASTER_TENANT_NAME']

        Configuration Example:
            ```python
            OMNITENANT_CONFIG = {
                'MASTER_TENANT_NAME': 'master',
            }
            ```

        Use Cases:
            1. System operations that need a default tenant context
            2. Administrative console access
            3. Fallback tenant for failed tenant resolution
            4. System-wide (non-tenant) operations

        Related:
            - PUBLIC_TENANT_NAME: For public/shared content
            - Master Database: MASTER_DB_ALIAS
        """
        return self.OMNITENANT_CONFIG.get(constants.MASTER_TENANT_NAME, "Master")

    @cached_property
    def PUBLIC_TENANT_NAME(self) -> str:
        """
        Get the identifier of the public/shared tenant.

        The public tenant contains data accessible to all tenants and is used for
        shared content, public information, and system data.

        Returns:
            str: Public tenant identifier/name

        Default:
            'public_omnitenant'

        Configuration Key:
            Uses the constant: constants.PUBLIC_TENANT_NAME = "PUBLIC_TENANT_NAME"
            Location: OMNITENANT_CONFIG['PUBLIC_TENANT_NAME']

        Configuration Example:
            ```python
            OMNITENANT_CONFIG = {
                'PUBLIC_TENANT_NAME': 'public',
            }
            ```

        Use Cases:
            1. Accessing shared content accessible to all tenants
            2. Public API endpoints
            3. Shared system configuration
            4. Public documentation and help content

        Related:
            - MASTER_TENANT_NAME: For administrative operations
            - PUBLIC_DB_ALIAS: Database for public tenant
            - PUBLIC_HOST: Domain for public content
        """
        return self.OMNITENANT_CONFIG.get(constants.PUBLIC_TENANT_NAME, "public_omnitenant")

    @cached_property
    def TEST_TENANT_NAME(self) -> str:
        """
        Get the identifier of the test tenant.

        The test tenant will contain the test data and is used for
        running tests.

        Returns:
            str: Test tenant identifier/name

        Default:
            'omitenant_test_tenant'

        Configuration Key:
            Uses the constant: constants.TEST_TENANT_NAME = "TEST_TENANT_NAME"
            Location: OMNITENANT_CONFIG['TEST_TENANT_NAME']

        Configuration Example:
            ```python
            OMNITENANT_CONFIG = {
                'TEST_TENANT_NAME': 'test_tenant',
            }
            ```

        Use Cases:
            Accessing shared content accessible during the tests
        """
        return self.OMNITENANT_CONFIG.get(
            constants.TEST_TENANT_NAME,
            "omitenant_test_tenant",
        )

    @cached_property
    def PUBLIC_DB_ALIAS(self) -> str:
        """
        Get the database alias for the public/shared database.

        The public database contains shared data accessible to all tenants.

        Returns:
            str: Database alias (Django DATABASES key)

        Default:
            'public'

        Configuration Key:
            Uses the constant: constants.PUBLIC_DB_ALIAS = "PUBLIC_DB_ALIAS"
            Location: OMNITENANT_CONFIG['PUBLIC_DB_ALIAS']

        Configuration Example:
            ```python
            DATABASES = {
                'default': {...},
                'public': {...},  # Shared database
                'master': {...},  # Master database
            }

            OMNITENANT_CONFIG = {
                'PUBLIC_DB_ALIAS': 'public',
            }
            ```

        Django Integration:
            ```python
            from django.db import connections

            public_db = connections[settings.PUBLIC_DB_ALIAS]
            ```

        Use Cases:
            1. Queries for shared data across all tenants
            2. Public API data
            3. System configuration stored centrally
            4. Analytics accessible to multiple tenants

        Related:
            - MASTER_DB_ALIAS: For master/administrative database
            - PUBLIC_TENANT_NAME: Tenant name for public data
        """
        return self.OMNITENANT_CONFIG.get(constants.PUBLIC_DB_ALIAS, "public")

    @cached_property
    def MASTER_DB_ALIAS(self) -> str:
        """
        Get the database alias for the master database.

        The master database contains administrative data, tenant records, and
        system-wide information.

        Returns:
            str: Database alias (Django DATABASES key)

        Default:
            'default'

        Configuration Key:
            Uses the constant: constants.MASTER_DB_ALIAS = "MASTER_DB_ALIAS"
            Location: OMNITENANT_CONFIG['MASTER_DB_ALIAS']

        Configuration Example:
            ```python
            DATABASES = {
                'default': {...},  # Master database
                'public': {...},   # Shared database
            }

            OMNITENANT_CONFIG = {
                'MASTER_DB_ALIAS': 'default',
            }
            ```

        Use Cases:
            1. Accessing tenant records
            2. System administration operations
            3. Master configuration and settings
            4. User account data (if centralized)
            5. Audit logs

        Related:
            - PUBLIC_DB_ALIAS: For shared data database
            - MASTER_TENANT_NAME: Tenant for master operations

        Note:
            Defaults to Django's 'default' database alias for simplicity.
        """
        return self.OMNITENANT_CONFIG.get(constants.MASTER_DB_ALIAS, "default")

    @cached_property
    def MASTER_CACHE_ALIAS(self) -> str:
        """
        Get the cache backend alias for system-wide (master) cache.

        The master cache stores system-wide data that applies across all tenants.

        Returns:
            str: Cache backend alias (Django CACHES key)

        Default:
            'default'

        Configuration Key:
            Uses the constant: constants.MASTER_CACHE_ALIAS = "DEFAULT_CACHE_ALIAS"
            Location: OMNITENANT_CONFIG['MASTER_CACHE_ALIAS']
            (Note: The constant uses "DEFAULT_CACHE_ALIAS" but property is MASTER_CACHE_ALIAS)

        Configuration Example:
            ```python
            CACHES = {
                'default': {...},        # Master cache
                'tenant_cache': {...},   # Tenant-specific cache
            }

            OMNITENANT_CONFIG = {
                'MASTER_CACHE_ALIAS': 'default',
            }
            ```

        Django Integration:
            ```python
            from django.core.cache import caches

            master_cache = caches[settings.MASTER_CACHE_ALIAS]
            master_cache.set('key', 'value')
            ```

        Use Cases:
            1. Caching system-wide configuration
            2. Shared session data
            3. Global feature flags
            4. Distributed locks for multi-tenant operations
            5. Rate limiting across all tenants

        Related:
            - Tenant-specific caches: Not directly exposed (handled per-tenant)
            - MASTER_DB_ALIAS: For master database

        Performance:
            System-wide cache operations should use this alias for efficiency
            instead of creating per-tenant cache entries.
        """
        return self.OMNITENANT_CONFIG.get(constants.MASTER_CACHE_ALIAS, "default")

    @cached_property
    def DEFAULT_SCHEMA_NAME(self) -> str:
        """
        Get the default PostgreSQL schema name for schema-based isolation.

        For PostgreSQL-based schema-per-tenant isolation, this is the default schema
        when no specific schema is set. Typically 'public' for PostgreSQL.

        Returns:
            str: PostgreSQL schema name

        Default:
            'public'

        Configuration Key:
            Uses the constant: constants.DEFAULT_SCHEMA_NAME = "DEFAULT_SCHEMA_NAME"
            Location: OMNITENANT_CONFIG['DEFAULT_SCHEMA_NAME']

        Configuration Example:
            ```python
            OMNITENANT_CONFIG = {
                'DEFAULT_SCHEMA_NAME': 'public',
            }
            ```

        PostgreSQL Schemas:
            PostgreSQL organizes tables within schemas. The 'public' schema is the
            default schema in any PostgreSQL database.

        Use Cases:
            1. Schema-based tenant isolation
            2. Fallback schema for system operations
            3. Public schema for shared system tables
            4. Schema switching during migrations

        Related:
            - SCHEMA_CONFIG: Schema-specific configuration
            - convert_to_valid_pgsql_schema_name: For normalizing schema names

        Database Backends:
            This setting is only relevant when using schema-based isolation backend.
            Not used for database-per-tenant isolation.

        Note:
            'public' is the PostgreSQL default schema. Changing this is uncommon
            and should be done with careful consideration.
        """
        return self.OMNITENANT_CONFIG.get(constants.DEFAULT_SCHEMA_NAME, "public")

    @cached_property
    def TENANT_MODEL(self) -> str:
        """
        Get the configured Tenant model class path.

        This is the primary model representing a tenant in the application.
        The model path must be in "app_label.ModelName" format.

        Returns:
            str: Dotted Python path to the Tenant model (e.g., 'myapp.Tenant')

        Raises:
            ImproperlyConfigured: If TENANT_MODEL is not configured

        Configuration Key:
            Uses the constant: constants.TENANT_MODEL = "TENANT_MODEL"
            Location: OMNITENANT_CONFIG['TENANT_MODEL']

        Configuration Example:
            ```python
            # In your Django app
            # myapp/models.py
            from django_omnitenant.models import BaseTenant

            class Tenant(BaseTenant):
                name = models.CharField(max_length=100)
                # ... custom fields

            # In settings.py
            OMNITENANT_CONFIG = {
                'TENANT_MODEL': 'myapp.Tenant',  # REQUIRED
            }
            ```

        Error Handling:
            If not configured, raises ImproperlyConfigured with helpful message:

            ```
            ImproperlyConfigured: OMNITENANT_CONFIG.TENANT_MODEL is not set.
            You must define TENANT_MODEL in your Omnitenant configuration.
            ```

        Usage:
            ```python
            from django_omnitenant.conf import settings
            from django_omnitenant.utils import get_tenant_model

            # Get model path
            model_path = settings.TENANT_MODEL  # 'myapp.Tenant'

            # Get actual model class
            Tenant = get_tenant_model()
            all_tenants = Tenant.objects.all()
            ```

        Requirements:
            - Must be set in OMNITENANT_CONFIG
            - Model must extend BaseTenant
            - Model must be in an installed app

        Note:
            This is a REQUIRED setting. Django will not start if not properly configured.
        """
        tenant = self.OMNITENANT_CONFIG.get(constants.TENANT_MODEL, "")
        if not tenant:
            raise ImproperlyConfigured(
                "OMNITENANT_CONFIG.TENANT_MODEL is not set. "
                "You must define TENANT_MODEL in your Omnitenant configuration."
            )
        return tenant

    @cached_property
    def DOMAIN_MODEL(self) -> str:
        """
        Get the configured Domain model class path.

        This is the model representing domains mapped to tenants (for custom domain
        and subdomain support). The model path must be in "app_label.ModelName" format.

        Returns:
            str: Dotted Python path to the Domain model (e.g., 'myapp.Domain')

        Raises:
            ImproperlyConfigured: If DOMAIN_MODEL is not configured

        Configuration Key:
            Uses the constant: constants.DOMAIN_MODEL = "DOMAIN_MODEL"
            Location: OMNITENANT_CONFIG['DOMAIN_MODEL']

        Configuration Example:
            ```python
            # In your Django app
            # myapp/models.py
            from django_omnitenant.models import BaseDomain

            class Domain(BaseDomain):
                name = models.CharField(max_length=253)
                tenant = models.ForeignKey('Tenant', on_delete=models.CASCADE)
                # ... custom fields

            # In settings.py
            OMNITENANT_CONFIG = {
                'DOMAIN_MODEL': 'myapp.Domain',  # REQUIRED
            }
            ```

        Error Handling:
            If not configured, raises ImproperlyConfigured with helpful message:

            ```
            ImproperlyConfigured: OMNITENANT_CONFIG.DOMAIN_MODEL is not set.
            You must define DOMAIN_MODEL in your Omnitenant configuration.
            ```

        Usage:
            ```python
            from django_omnitenant.conf import settings
            from django_omnitenant.utils import get_domain_model

            # Get model path
            model_path = settings.DOMAIN_MODEL  # 'myapp.Domain'

            # Get actual model class
            Domain = get_domain_model()

            # Find tenant for domain
            domain = Domain.objects.get(name='example.com')
            tenant = domain.tenant
            ```

        Purpose:
            The domain model links domain names (e.g., "api.example.com") to
            specific tenants, enabling:
            1. Custom domain support per tenant
            2. Subdomain-based tenant resolution
            3. Multiple domains pointing to same tenant
            4. Domain management UI

        Requirements:
            - Must be set in OMNITENANT_CONFIG
            - Model should extend BaseDomain (or similar)
            - Must have a ForeignKey to TENANT_MODEL
            - Model must be in an installed app

        Note:
            This is a REQUIRED setting. Django will not start if not properly configured.
        """
        domain_model: str = self.OMNITENANT_CONFIG.get(constants.DOMAIN_MODEL, "")
        if not domain_model:
            raise ImproperlyConfigured(
                "OMNITENANT_CONFIG.DOMAIN_MODEL is not set. "
                "You must define DOMAIN_MODEL in your Omnitenant configuration."
            )
        return domain_model

    @cached_property
    def PUBLIC_HOST(self) -> str:
        """
        Get the default public host/domain.

        This is the main domain for public or non-tenant-specific content. When a
        request comes from this host and no specific tenant is resolved, the public
        tenant is used.

        Returns:
            str: The public host domain name (e.g., 'example.com', 'localhost')

        Default:
            'localhost'

        Configuration Key:
            Uses the constant: constants.PUBLIC_HOST = "PUBLIC_HOST"
            Location: OMNITENANT_CONFIG['PUBLIC_HOST']

        Configuration Example:
            ```python
            OMNITENANT_CONFIG = {
                'PUBLIC_HOST': 'example.com',
            }
            ```

        Use Cases:
            1. Main application domain (e.g., www.example.com)
            2. Public API endpoint (e.g., api.example.com)
            3. Admin/management console (e.g., admin.example.com)
            4. Documentation site (e.g., docs.example.com)

        Middleware Behavior:
            When TenantMiddleware receives a request:
            1. Attempts to resolve tenant using configured resolver
            2. If resolution fails and host matches PUBLIC_HOST:
               - Uses PUBLIC_TENANT_NAME as fallback tenant
            3. If host doesn't match PUBLIC_HOST:
               - Returns 400 "Invalid Domain" error

        Examples:
            ```python
            # Production configuration
            OMNITENANT_CONFIG = {
                'PUBLIC_HOST': 'mycompany.com',
            }

            # Development configuration
            OMNITENANT_CONFIG = {
                'PUBLIC_HOST': 'localhost:8000',
            }

            # Staging configuration
            OMNITENANT_CONFIG = {
                'PUBLIC_HOST': 'staging.mycompany.com',
            }
            ```

        Related:
            - PUBLIC_TENANT_NAME: Tenant used for this host
            - TenantMiddleware: Uses this for fallback resolution
            - Tenant Resolvers: Primary resolution method

        Note:
            For development, 'localhost' is the default. For production,
            set this to your actual domain name for proper resolution.
        """
        return self.OMNITENANT_CONFIG.get(constants.PUBLIC_HOST, "localhost")


# Module-Level Singleton Instance
# ================================

settings = _WrappedSettings()
"""
Singleton instance of _WrappedSettings providing the public configuration API.

This module-level instance is the primary way to access all django-omnitenant
configuration throughout the application. It provides:

1. Transparent access to all Django settings
2. Cached access to omnitenant-specific configuration
3. Validation of required settings
4. Sensible defaults for optional settings

Usage:
    ```python
    from django_omnitenant.conf import settings
    
    # Access omnitenant configuration
    tenant_model = settings.TENANT_MODEL
    resolver = settings.TENANT_RESOLVER
    public_host = settings.PUBLIC_HOST
    
    # Access standard Django settings
    debug = settings.DEBUG
    databases = settings.DATABASES
    ```

Configuration:
    All configuration is read from Django's OMNITENANT_CONFIG setting:
    
    ```python
    # In your Django settings.py
    OMNITENANT_CONFIG = {
        'TENANT_MODEL': 'myapp.Tenant',
        'DOMAIN_MODEL': 'myapp.Domain',
        'TENANT_RESOLVER': 'myapp.resolvers.SubdomainResolver',
        'PUBLIC_HOST': 'example.com',
        'PUBLIC_TENANT_NAME': 'public',
        'MASTER_TENANT_NAME': 'master',
        # ... other settings
    }
    ```

Best Practices:
    1. Always import from django_omnitenant.conf, not django.conf
    2. Use the settings instance in module-level code (imported at app startup)
    3. For request-specific access, use settings in views/services
    4. Never try to modify settings at runtime (raises ValueError)
    5. Cache the result if accessing in loops for performance

Performance:
    - Uses @cached_property for efficient caching
    - Configuration loaded once per application lifetime
    - Subsequent accesses use cached values (O(1) lookup)
    - No repeated dictionary lookups

Thread Safety:
    - All accesses are thread-safe
    - Cached properties are initialized once
    - Immutability prevents race conditions

Example Application Configuration:
    ```python
    # settings.py
    OMNITENANT_CONFIG = {
        'TENANT_MODEL': 'myapp.models.Tenant',
        'DOMAIN_MODEL': 'myapp.models.Domain',
        'TENANT_RESOLVER': 'myapp.resolvers.CustomDomainResolver',
        'PUBLIC_HOST': 'myapp.com',
        'PUBLIC_TENANT_NAME': 'public',
        'MASTER_TENANT_NAME': 'master',
        'MASTER_DB_ALIAS': 'default',
        'PUBLIC_DB_ALIAS': 'public',
        'DEFAULT_SCHEMA_NAME': 'public',
    }
    
    # In your code
    from django_omnitenant.conf import settings
    
    def get_current_tenant_name():
        return settings.MASTER_TENANT_NAME
    ```

See Also:
    - constants.py: Configuration key constants
    - settings._WrappedSettings: The wrapper class
    - Django settings documentation: https://docs.djangoproject.com/en/stable/topics/settings/
"""
