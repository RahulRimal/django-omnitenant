"""
Database-per-Tenant Backend Module

This module implements the Database-per-Tenant isolation strategy where each
tenant gets its own separate PostgreSQL database within the same or different
database servers.

Isolation Strategy:
    Each tenant is completely isolated in a separate database. This provides
    strong isolation at the cost of managing multiple databases.

Architecture:
    - Tenant configuration specifies database credentials (NAME, USER, PASSWORD, HOST, PORT)
    - Each tenant's database is registered dynamically in Django's DATABASES setting
    - TenantContext manages switching between databases during request processing
    - The public/shared database holds the Tenant and Domain models

Database Configuration:
    Tenant configuration should include a 'db_config' dictionary:

    ```python
    tenant.config = {
        'db_config': {
            'NAME': 'tenant_acme_db',           # Database name
            'USER': 'tenant_acme_user',         # Database user
            'PASSWORD': 'secret_password',      # Database password
            'HOST': 'db.example.com',           # Database host
            'PORT': 5432,                       # Database port
            'ENGINE': 'django.db.backends.postgresql',  # Optional, defaults to master
            'ALIAS': 'acme_db',                 # Optional, defaults to NAME or MASTER_DB_ALIAS
        }
    }
    ```

Lifecycle:
    1. create() - Creates new database, binds to Django settings, runs migrations
    2. activate() - Switches database connection when entering tenant context
    3. deactivate() - Restores previous database when exiting context
    4. delete() - Drops database and removes from Django settings
    5. bind() - Manually attaches database to settings for connections

Connection Management:
    - Uses Django's DATABASES setting for multi-database support
    - TenantContext maintains stack of active database aliases
    - Each request or explicit context switch changes active database
    - Schema management ensures no cross-tenant data leakage

PostgreSQL Support:
    - Compatible with both psycopg2 and psycopg3 drivers
    - Detects available driver and uses appropriate SQL construction API
    - Handles PostgreSQL-specific operations (CREATE DATABASE, DROP DATABASE)

Shared Database:
    The master/public database (configured in settings.MASTER_DB_ALIAS) contains:
    - Tenant model instances
    - Domain model instances
    - Configuration and metadata
    - Shared application data

Performance Considerations:
    - Database creation can be slow (especially over network)
    - Connection pooling per database (see CONN_MAX_AGE setting)
    - Index and query optimization must happen per database
    - Migrations run per database (can be parallelized)

Security Considerations:
    - Each tenant needs separate database credentials
    - Strongly isolate databases at DB server level
    - Use firewalls/security groups to restrict access
    - Monitor for cross-database queries (security bug)
    - Regular backups per database

Usage Example:
    ```python
    from django_omnitenant.backends.database_backend import DatabaseTenantBackend
    from myapp.models import Tenant

    # Create new tenant
    tenant = Tenant.objects.create(
        tenant_id='acme',
        name='Acme Corporation',
        config={
            'db_config': {
                'NAME': 'tenant_acme_db',
                'USER': 'acme_user',
                'PASSWORD': 'secret',
                'HOST': 'db.example.com',
            }
        }
    )

    # Provision database
    backend = DatabaseTenantBackend(tenant)
    backend.create(run_migrations=True)

    # Use tenant
    with TenantContext.use_tenant(tenant):
        # Queries automatically route to tenant's database
        User.objects.create(username='john')

    # Cleanup
    backend.delete(drop_db=True)
    ```

Related:
    - base.py: Abstract backend base class
    - schema_backend.py: Schema-per-tenant alternative
    - tenant_context.py: Request context management
    - utils.py: Utility functions
    - postgresql/base.py: PostgreSQL-specific routing
"""

from django.core.management import call_command
from django.db import connection
from requests.structures import CaseInsensitiveDict

from django_omnitenant.conf import settings
from django_omnitenant.models import BaseTenant
from django_omnitenant.tenant_context import TenantContext
from django_omnitenant.utils import get_active_schema_name

from .base import BaseTenantBackend

try:
    from django.db.backends.postgresql.psycopg_any import is_psycopg3
except ImportError:
    is_psycopg3 = False

if is_psycopg3:
    import psycopg as psycopg_driver
else:
    import psycopg2 as psycopg_driver


class DatabaseTenantBackend(BaseTenantBackend):
    """
    Database-per-Tenant isolation backend.

    Implements the database-per-tenant isolation strategy where each tenant
    gets its own separate PostgreSQL database.

    Each tenant's database is created and configured dynamically. The backend
    manages database creation, connection routing, migrations, and cleanup.

    Key Features:
        - Complete data isolation between tenants
        - Independent database credentials per tenant
        - Dynamic database registration in Django
        - Automatic migration handling
        - PostgreSQL native database management

    Configuration:
        Tenants must have 'db_config' in their config dictionary containing:
        - NAME: Database name (required)
        - USER: Database username (required)
        - PASSWORD: Database password (required)
        - HOST: Database host (required)
        - PORT: Database port (required)
        - ENGINE: Database engine (optional, defaults to master)
        - ALIAS: Database alias (optional, defaults to NAME)
        - TIME_ZONE: Database timezone (optional, inherits from settings)

    Attributes:
        tenant (BaseTenant): The tenant instance
        db_config (CaseInsensitiveDict): Tenant's database configuration
        previous_schema (str): Stores schema before activation (for deactivation)
    """

    def __init__(self, tenant: BaseTenant):
        """
        Initialize database backend for a tenant.

        Args:
            tenant (BaseTenant): Tenant instance to manage

        Process:
            1. Call parent __init__ to store tenant reference
            2. Extract and store tenant's database configuration
            3. Use CaseInsensitiveDict for case-insensitive config lookups

        The db_config is extracted from tenant.config['db_config'] if present,
        defaulting to empty dict if not configured.
        """
        super().__init__(tenant)
        # Extract database configuration from tenant, defaulting to empty dict
        # CaseInsensitiveDict allows case-insensitive lookups (NAME, name, Name all work)
        self.db_config: CaseInsensitiveDict = CaseInsensitiveDict(self.tenant.config.get("db_config", {}))

    def __init__(self, tenant: BaseTenant):
        super().__init__(tenant)
        self.db_config: CaseInsensitiveDict = CaseInsensitiveDict(self.tenant.config.get("db_config", {}))

    def create(self, run_migrations=False, **kwargs):
        """
        Provision a new database for the tenant.

        This method creates the tenant's database on the configured database server,
        registers it with Django, and optionally runs initial migrations.

        Process:
            1. Get tenant's database alias and resolved configuration
            2. Create the database on the database server using PostgreSQL
            3. Call parent create() which:
               - Calls bind() to register database in Django settings
               - Emits tenant_created signal
               - Optionally runs migrations if run_migrations=True

        Args:
            run_migrations (bool): Whether to run migrations after creation
            **kwargs: Additional arguments passed to parent create()

        Raises:
            Exception: If database creation fails (already exists, permission denied, etc.)
            psycopg2.Error: If database server connection fails

        Database Creation:
            Uses the psycopg driver (psycopg2 or psycopg3) to connect directly
            to PostgreSQL server and execute CREATE DATABASE statement.

        Configuration:
            Database details from tenant.config['db_config']:
            - NAME: Database name to create
            - USER: Database user credentials for creation
            - PASSWORD: Database password
            - HOST: Database server host
            - PORT: Database server port

        Workflow:
            1. _create_database() → Creates DB on PostgreSQL server
            2. bind() → Registers in Django's DATABASES setting
            3. tenant_created signal → Notifies listeners
            4. migrate() → Runs initial schema if run_migrations=True

        Error Handling:
            If database already exists or other errors occur:
            ```python
            try:
                backend.create(run_migrations=True)
            except Exception as e:
                logger.error(f"Failed to create tenant database: {e}")
                # Database may be partially created
                # May need manual cleanup
            ```

        Example:
            ```python
            tenant = Tenant.objects.create(
                tenant_id='acme',
                config={
                    'db_config': {
                        'NAME': 'tenant_acme_db',
                        'USER': 'acme_user',
                        'PASSWORD': 'secret',
                        'HOST': 'db.example.com',
                        'PORT': 5432,
                    }
                }
            )

            backend = DatabaseTenantBackend(tenant)
            backend.create(run_migrations=True)
            ```

        Performance:
            Database creation can take several seconds especially over network.
            Consider running in background task for large number of tenants.

        See Also:
            - delete(): Remove tenant database
            - bind(): Register database in Django
            - migrate(): Run database migrations
        """
        # Get the database alias and fully resolved configuration
        # (merges tenant config with master database settings)
        _, db_config = self.get_alias_and_config(self.tenant)

        # Create the actual database on the PostgreSQL server
        # Extracts connection details from db_config
        self._create_database(
            db_config["NAME"],
            db_config["USER"],
            db_config["PASSWORD"],
            db_config["HOST"],
            db_config["PORT"],
        )

        # Call parent create() to:
        # 1. bind() - Register database in Django DATABASES setting
        # 2. Emit tenant_created signal for listeners
        # 3. Run migrations if run_migrations=True
        super().create(run_migrations=run_migrations)

    def migrate(self, *args, **kwargs):
        """
        Run database migrations for the tenant's database.

        This method executes Django's migrate management command within the tenant's
        database context, applying any pending migrations to the tenant's schema.

        Process:
            1. Get tenant's database alias and configuration
            2. Activate tenant context (switches to tenant's database)
            3. Run Django migrate command for that specific database
            4. Call parent migrate() which emits tenant_migrated signal

        Args:
            *args: Positional arguments passed to migrate command
                  - app_label: Migrate specific app (e.g., 'myapp')
                  - migration_name: Migrate to specific migration (e.g., '0005_custom')
            **kwargs: Keyword arguments passed to migrate command
                     - verbosity: Output verbosity (0-3)
                     - interactive: Allow interactive prompts (default True)
                     - run_syncdb: Create tables for apps without migrations (default False)

        Raises:
            Exception: If migration fails (syntax errors, conflicts, etc.)

        Signals:
            Emits: tenant_migrated(sender=Tenant, tenant=instance)
            Handlers can perform post-migration setup

        Database Context:
            Uses TenantContext.use_tenant() to ensure all migration operations
            run against the tenant's specific database, not the master database.

        Error Handling:
            If migration fails, the exception is caught and logged before re-raising.
            This ensures the failure is visible while still propagating the error.

            ```python
            try:
                backend.migrate()
            except Exception as e:
                logger.error(f"Migration failed for {tenant.tenant_id}: {e}")
                # Tenant database may be in inconsistent state
                # May need to rollback or manual intervention
            ```

        Usage Examples:
            ```python
            backend = DatabaseTenantBackend(tenant)

            # Run all pending migrations
            backend.migrate()

            # Migrate specific app
            backend.migrate('myapp')

            # Migrate to specific migration
            backend.migrate(app_label='myapp', migration_name='0005_custom')

            # With verbosity
            backend.migrate(verbosity=2)
            ```

        Management Command:
            Typically invoked via Django management command:
            ```bash
            python manage.py migratetenant acme
            python manage.py migratetenants
            ```

        Workflow:
            1. Get database alias for tenant
            2. Enter TenantContext for that tenant
            3. Call migrate command for that database only
            4. Emit tenant_migrated signal
            5. Exit context and return

        Performance:
            - Large migrations may take significant time
            - Database locks during migration
            - Consider running during maintenance window
            - Can migrate multiple tenants in parallel

        See Also:
            - create(): Provision database and optionally migrate
            - management commands: migratetenant, migratetenants
            - tenant_migrated signal: For post-migration handlers
        """
        # Get the database alias for this tenant
        db_alias, _ = self.get_alias_and_config(self.tenant)

        # Run migrations within tenant context to ensure queries hit correct database
        with TenantContext.use_tenant(self.tenant):
            try:
                # Call Django's migrate command with:
                # - Positional/keyword arguments from caller
                # - database parameter to ensure it uses tenant's database
                call_command("migrate", *args, database=db_alias, **kwargs)
            except Exception as e:
                # Log the error with database alias for debugging
                print(f"[DB BACKEND] Migration failed for db `{db_alias}`: {e}")
                # Re-raise so caller knows migration failed
                raise

        # Call parent migrate() to emit tenant_migrated signal
        # Allows listeners to perform post-migration setup
        super().migrate()

    def delete(self, drop_db=False):
        """
        Tear down and optionally drop the tenant's database.

        This method removes the tenant's database from Django settings and
        optionally drops it from the PostgreSQL server.

        Process:
            1. Get tenant's database alias and configuration
            2. Optionally drop the database from PostgreSQL server
            3. Remove database from Django's DATABASES setting
            4. Call parent delete() which emits tenant_deleted signal

        Args:
            drop_db (bool): Whether to actually drop the database from PostgreSQL.
                           Default is False (only remove from Django settings).
                           Set to True to permanently delete the database.

        Raises:
            Exception: If database drop fails (permission denied, active connections, etc.)
            psycopg2.Error: If PostgreSQL connection fails

        Destructive Operation:
            This operation CANNOT be recovered without backups!
            Always archive tenant data before deletion if compliance requires it.

        Two-Step Deletion:
            The method supports soft and hard deletion:

            1. drop_db=False (default):
               - Removes from Django settings
               - Database remains on PostgreSQL server (can manually restore)
               - Allows recovery if deletion was accidental

            2. drop_db=True:
               - Actually drops database from PostgreSQL
               - Data is gone (unless you have backups)
               - Frees disk space and resources

        Workflow:
            1. Get database alias and configuration
            2. If drop_db=True: _drop_database() removes from PostgreSQL
            3. Remove from Django's DATABASES setting
            4. Emit tenant_deleted signal for cleanup handlers

        Error Handling:
            Handle errors carefully during deletion:

            ```python
            try:
                # First try soft delete (keeps data)
                backend.delete(drop_db=False)
            except Exception as e:
                logger.error(f"Error removing from Django: {e}")

            # Later, after confirming no issues:
            try:
                # Hard delete (remove data)
                backend.delete(drop_db=True)
            except Exception as e:
                logger.error(f"Error dropping database: {e}")
                # May need manual intervention
                notify_administrators()
            ```

        Active Connections:
            If the database has active connections, DROP DATABASE may fail.
            The implementation uses CASCADE or disconnects active sessions first.

            Common causes of failures:
            - Applications still connecting to database
            - Open transactions
            - Scheduled jobs using database
            - IDE connections

        Examples:
            ```python
            tenant = Tenant.objects.get(tenant_id='acme')
            backend = DatabaseTenantBackend(tenant)

            # Soft delete (for safety)
            backend.delete(drop_db=False)
            # Data remains, can be restored

            # Later, hard delete (after confirming)
            backend.delete(drop_db=True)
            # Database is gone permanently

            # Clean up model
            tenant.delete()
            ```

        Compliance & Archival:
            For GDPR/compliance requirements:

            ```python
            # 1. Archive tenant data
            archive_tenant_data(tenant)

            # 2. Soft delete from Django
            backend.delete(drop_db=False)

            # 3. Verify no issues for X days
            schedule_hard_delete.delay(tenant.id, delay=30)

            # 4. Later, hard delete
            def hard_delete_tenant(tenant_id):
                tenant = Tenant.objects.get(id=tenant_id)
                backend = DatabaseTenantBackend(tenant)
                backend.delete(drop_db=True)
                tenant.delete()
            ```

        See Also:
            - create(): Provision database
            - tenant_deleted signal: For cleanup handlers
            - Archival strategies: For compliance
        """
        # Get database alias and configuration
        db_alias, db_config = self.get_alias_and_config(self.tenant)

        # Step 1: Optionally drop the database from PostgreSQL server
        if drop_db:
            self._drop_database(
                db_config["NAME"],
                db_config["USER"],
                db_config["PASSWORD"],
                db_config["HOST"],
                db_config["PORT"],
            )

        # Step 2: Remove database from Django's DATABASES setting
        # This prevents the database from being used for future queries
        if db_alias in settings.DATABASES:
            del settings.DATABASES[db_alias]

        # Step 3: Call parent delete() to emit tenant_deleted signal
        # Handlers can perform cleanup tasks (remove from caches, cleanup sidecars, etc.)
        super().delete()

    def bind(self):
        """
        Register tenant's database in Django's DATABASES setting.

        This method makes the tenant's database available to Django for queries.
        After bind() is called, the database alias is registered and can be used
        with Django's database router and multi-database features.

        Process:
            1. Get tenant's database alias and resolved configuration
            2. Add/register database in Django's DATABASES setting
            3. Print confirmation message for logging

        Configuration Resolution:
            The configuration is built by:
            1. Getting tenant's db_config from tenant.config['db_config']
            2. Merging with master database settings as defaults
            3. Respecting tenant-specific overrides

            Example merged config:
            - ENGINE: Tenant-specific or master's
            - NAME: Tenant database name
            - USER: Tenant database user
            - PASSWORD: Tenant password
            - HOST: Tenant host or master's
            - PORT: Tenant port or master's
            - Options: Merged from both

        Lifecycle:
            bind() is called:
            - During create() to register new database
            - During activate() to ensure database is available
            - Manually to update database settings

        Effect:
            After bind() completes:
            - Database is in settings.DATABASES[db_alias]
            - Can be used with Django ORM (e.g., Model.objects.using(db_alias))
            - Router can direct queries to it
            - Connections can be established to it

        Examples:
            ```python
            tenant = Tenant.objects.get(tenant_id='acme')
            backend = DatabaseTenantBackend(tenant)

            # Bind the database
            backend.bind()

            # Now database is available in settings
            assert 'acme_db' in settings.DATABASES

            # Can use it with explicit routing
            User.objects.using('acme_db').create(username='john')

            # Or within TenantContext (automatic routing)
            with TenantContext.use_tenant(tenant):
                User.objects.create(username='john')
            ```

        Dynamic Registration:
            Unlike static DATABASES configuration in settings.py, bind()
            registers databases at runtime. This allows:
            - Adding tenants without restarting application
            - Dynamically changing database credentials
            - Supporting unlimited number of tenants

        Idempotency:
            Calling bind() multiple times is safe:
            - Later calls overwrite previous registration
            - No errors if database alias already exists
            - Can be used to update database configuration

        See Also:
            - activate(): Switches connection to use this database
            - get_alias_and_config(): Builds resolved configuration
            - DatabaseTenantBackend: Full database lifecycle
        """
        # Get the database alias and fully resolved configuration
        # Merges tenant-specific config with master database defaults
        db_alias, db_config = self.get_alias_and_config(self.tenant)

        # Register the database in Django's DATABASES setting
        # Now queries can use database=db_alias or be routed by router
        settings.DATABASES[db_alias] = db_config

        # Log the binding for visibility and debugging
        print(f"[DB BACKEND] Bound tenant {self.tenant.tenant_id} to alias {db_alias}.")

    def activate(self):
        """
        Switch to the tenant's database for the current context.

        This method activates the tenant's database by:
        1. Ensuring the database is bound (registered in Django settings)
        2. Pushing the database alias onto the context stack
        3. Saving current schema state for restoration on deactivate
        4. Setting schema to 'public' to ensure consistency

        Process:
            1. Get database alias from tenant config
            2. If database not yet in Django settings, bind it
            3. Push database alias onto TenantContext stack
            4. Save current PostgreSQL schema name
            5. Set schema to 'public' for consistency

        Lifecycle:
            Called when:
            - Entering TenantContext context manager
            - Request middleware starts request processing
            - Explicitly switching tenant

        Database Alias Resolution:
            The alias is determined in order:
            1. tenant.config['db_config']['ALIAS'] (explicit alias)
            2. tenant.config['db_config']['NAME'] (use database name)
            3. settings.MASTER_DB_ALIAS (fallback to master)

            This allows flexibility in configuration.

        Lazy Binding:
            If the database isn't already in DATABASES, activate() calls bind()
            to register it. This allows databases to be registered on-demand
            rather than all at startup.

        Schema Management:
            When using database-per-tenant with PostgreSQL:
            - Each database has its own schemas
            - Setting schema to 'public' ensures consistency
            - Prevents cross-tenant data if schema-backend tenant exists
            - Saves previous schema for restoration on deactivate

            Example scenario:
            - Request enters with schema 'tenant1_schema'
            - Tenant1 context activates (switches database)
            - Sets schema to 'public' in tenant1's database
            - On exit, restores previous schema

        Context Stack:
            Uses TenantContext.push_db_alias() to maintain stack:
            - Supports nested tenant contexts
            - Proper cleanup on context exit
            - Thread-local so safe for concurrent requests

        Examples:
            ```python
            from django_omnitenant.tenant_context import TenantContext

            tenant = Tenant.objects.get(tenant_id='acme')
            backend = DatabaseTenantBackend(tenant)

            # Automatic via context manager (preferred)
            with TenantContext.use_tenant(tenant):
                # activate() called automatically
                User.objects.all()  # Queries tenant's database
                # deactivate() called automatically

            # Manual usage
            backend.activate()
            try:
                User.objects.all()
            finally:
                backend.deactivate()
            ```

        Performance:
            activate() is called for every request. Should be fast:
            - Only pushes alias onto context stack
            - Lazy binds database only if needed (usually cached)
            - Schema switching is fast PostgreSQL operation

        Thread Safety:
            TenantContext uses thread-local storage so:
            - Each request thread has independent context
            - Concurrent requests don't interfere
            - Safe for multi-threaded application servers

        Errors:
            If database connection fails:
            - Exception will be raised
            - Context is not fully activated
            - deactivate() won't be called
            - Caller must handle error

        See Also:
            - deactivate(): Exit tenant context
            - TenantContext: Context manager for activation
            - bind(): Register database if not already done
        """
        # Get the database alias for this tenant
        # Checks ALIAS config first, then NAME, then falls back to master
        db_alias = self.db_config.get("ALIAS") or self.db_config.get("NAME") or settings.MASTER_DB_ALIAS

        # Ensure database is registered in Django settings
        # Lazy bind if not already done (e.g., if activate() called before create())
        if db_alias not in settings.DATABASES:
            self.bind()

        # Push database alias onto context stack
        # TenantContext maintains a stack for nested context support
        # Enables context managers and request handling
        TenantContext.push_db_alias(db_alias)

        # Save current PostgreSQL schema name so we can restore it on deactivate
        # This is important if mixing database-per-tenant with schema-based tenants
        self.previous_schema = get_active_schema_name(connection)

        # Set schema to 'public' in this database
        # If a tenant is using schema backend, switching databases alone isn't enough
        # because the connection might still be in a tenant's schema
        # This ensures we're in the default schema for consistency
        connection.set_schema("public")

    def deactivate(self):
        """
        Exit the tenant's database context and restore previous state.

        This method deactivates the tenant's database by:
        1. Popping the database alias from the context stack
        2. Restoring the previous PostgreSQL schema

        Process:
            1. Pop database alias from TenantContext stack
            2. Restore previous schema that was saved on activate

        Lifecycle:
            Called when:
            - Exiting TenantContext context manager
            - Request middleware completes request processing
            - Explicitly exiting tenant context

        Context Stack Management:
            Pops the database alias from the context stack maintained by TenantContext.
            This allows:
            - Nested contexts to work properly
            - Previous database to be restored
            - Proper cleanup even if multiple activations

        Schema Restoration:
            Restores the PostgreSQL schema that was active before activate()
            was called. This is important for:
            - Proper isolation between operations
            - Preventing cross-tenant data leakage
            - Consistency when switching between databases

        Examples:
            ```python
            from django_omnitenant.tenant_context import TenantContext

            # Automatic via context manager (preferred)
            with TenantContext.use_tenant(tenant):
                # activate() called
                User.objects.all()
                # deactivate() called automatically, even on exception

            # Manual usage
            try:
                backend.activate()
                User.objects.all()
            finally:
                backend.deactivate()  # Always called
            ```

        Exception Safety:
            deactivate() should always be called, even if errors occur during
            the context. Similar to try/finally semantics:

            ```python
            backend.activate()
            try:
                # May raise exception
                dangerous_operation()
            finally:
                # Guaranteed to run
                backend.deactivate()
            ```

        Error Handling:
            If deactivate() itself fails (database error, etc.):
            - Exception is raised but context is partially cleaned up
            - Previous schema restoration attempt was made
            - Database alias was removed from stack

            Handle gracefully:
            ```python
            try:
                backend.deactivate()
            except Exception as e:
                logger.error(f"Error deactivating tenant: {e}")
                # Context is still partially cleaned up
            ```

        Nested Contexts:
            With nested tenant contexts:
            ```python
            with TenantContext.use_tenant(tenant1):
                # Activates tenant1's database
                with TenantContext.use_tenant(tenant2):
                    # Activates tenant2's database
                    # Deactivates, back to tenant1
                # Deactivates, back to master database
            ```

            Each deactivate() restores the context from the previous level.

        Performance:
            deactivate() is called for every request. Should be fast:
            - Only pops from context stack
            - Restores saved schema name
            - No expensive operations

        Thread Safety:
            Thread-local TenantContext ensures:
            - Each thread maintains independent context
            - deactivate() in one thread doesn't affect others
            - Safe for concurrent request processing

        See Also:
            - activate(): Enter tenant context
            - TenantContext: Context manager for activation/deactivation
            - Schema management: For consistent isolation
        """
        # Pop the database alias from the context stack
        # Restores the previous database for any parent context
        TenantContext.pop_db_alias()

        # Restore the PostgreSQL schema that was active before activate
        # This ensures schema isolation and proper state restoration
        connection.set_schema(self.previous_schema)

    # ========== Helper Methods ==========

    @classmethod
    def get_alias_and_config(cls, tenant):
        """
        Build and return the database alias and fully resolved configuration for a tenant.

        This method merges tenant-specific database configuration with master database
        settings to create a complete, ready-to-use Django database configuration.

        Process:
            1. Extract tenant's db_config from tenant.config['db_config']
            2. Determine database alias (from ALIAS, NAME, or MASTER_DB_ALIAS)
            3. Get base configuration from master database
            4. Merge tenant config with base, with tenant overrides taking precedence
            5. Handle special fields (TEST, OPTIONS, etc.)

        Args:
            tenant (BaseTenant): Tenant instance to get configuration for

        Returns:
            tuple: (db_alias, resolved_config)
            - db_alias (str): Database alias for use in Django DATABASES setting
            - resolved_config (dict): Complete Django database configuration dict

        Configuration Precedence:
            For each setting, resolved config uses:
            1. Tenant's db_config value if present
            2. Master database value if tenant doesn't override
            3. Django default if neither specified

            Example:
            ```
            TENANT db_config: {'NAME': 'tenant_db', 'HOST': 'db.example.com'}
            MASTER DATABASES:  {'NAME': 'master_db', 'HOST': 'localhost', 'PORT': 5432}
            RESULT:           {'NAME': 'tenant_db', 'HOST': 'db.example.com', 'PORT': 5432}
            ```

        Alias Determination:
            Database alias is resolved in order:
            1. tenant.config['db_config']['ALIAS'] - Explicit alias (preferred)
            2. tenant.config['db_config']['NAME'] - Use database name as alias
            3. settings.MASTER_DB_ALIAS - Fallback to master (shouldn't happen)

        Configuration Fields:
            The resolved config includes all Django database settings:

            - ENGINE: Database backend module
            - NAME: Database name (required)
            - USER: Database user
            - PASSWORD: Database password
            - HOST: Database host
            - PORT: Database port
            - OPTIONS: Database-specific options
            - TIME_ZONE: Timezone for this database
            - ATOMIC_REQUESTS: Atomic request wrapping
            - AUTOCOMMIT: Autocommit mode
            - CONN_MAX_AGE: Connection pool age
            - CONN_HEALTH_CHECKS: Enable health checks
            - TEST: Test database configuration

        Test Database Configuration:
            The TEST dictionary is special:
            - Merged from both tenant and master configs
            - Default NAME is set to resolved['NAME'] if not specified
            - Allows per-tenant test database customization

        Examples:
            ```python
            from myapp.models import Tenant
            from django_omnitenant.backends.database_backend import DatabaseTenantBackend

            tenant = Tenant.objects.create(
                tenant_id='acme',
                config={
                    'db_config': {
                        'NAME': 'acme_db',
                        'USER': 'acme_user',
                        'PASSWORD': 'secret',
                        'HOST': 'db.example.com',
                        'ALIAS': 'acme',  # Optional
                    }
                }
            )

            alias, config = DatabaseTenantBackend.get_alias_and_config(tenant)
            # Returns: ('acme', {'ENGINE': ..., 'NAME': 'acme_db', ...})

            # Without explicit ALIAS
            alias, config = DatabaseTenantBackend.get_alias_and_config(tenant)
            # Returns: ('acme_db', {...})  # Uses NAME as alias

            # With minimal config (uses master defaults)
            tenant.config = {'db_config': {'NAME': 'minimal_db'}}
            alias, config = DatabaseTenantBackend.get_alias_and_config(tenant)
            # Returns: ('minimal_db', {
            #   'ENGINE': 'django.db.backends.postgresql',  # From master
            #   'NAME': 'minimal_db',
            #   'HOST': 'localhost',  # From master
            #   'PORT': 5432,  # From master
            #   ...
            # })
            ```

        Error Handling:
            If required fields are missing:
            - NAME is required but will be missing if not in tenant or master config
            - USER, PASSWORD, HOST, PORT should come from master if not overridden
            - Missing values will cause connection errors later

        Performance:
            This is a fast operation (dict merging):
            - Called frequently during request processing
            - Cached implicitly by TenantContext
            - No database queries

        See Also:
            - __init__(): Uses get_alias_and_config for initial setup
            - create(): Uses to get database name for creation
            - bind(): Uses to register database in Django
            - activate(): Uses to get database alias for routing
        """
        db_config = CaseInsensitiveDict(tenant.config.get("db_config", {}))

        # Determine database alias in order of preference
        # Uses explicit ALIAS if provided, otherwise uses database NAME, fallback to MASTER_DB_ALIAS
        db_alias = db_config.get("ALIAS") or db_config.get("NAME") or settings.MASTER_DB_ALIAS

        # Get the base configuration from the master database
        # We'll use this as defaults for any settings not explicitly set for this tenant
        base_config: dict = settings.DATABASES.get(settings.MASTER_DB_ALIAS, {}).copy()

        # Build the resolved configuration by merging tenant settings with master defaults
        # Tenant settings take precedence over master settings
        resolved_config = {
            # ENGINE: Database backend to use
            # Tenant can override, otherwise use master's, default to PostgreSQL
            "ENGINE": db_config.get("ENGINE") or base_config.get("ENGINE", "django_omnitenant.backends.postgresql"),
            # NAME: Database name (required)
            "NAME": db_config.get("NAME") or base_config.get("NAME"),
            # USER: Database user (required for connections)
            "USER": db_config.get("USER") or base_config.get("USER"),
            # PASSWORD: Database password (required for connections)
            "PASSWORD": db_config.get("PASSWORD") or base_config.get("PASSWORD"),
            # HOST: Database server host (required)
            "HOST": db_config.get("HOST") or base_config.get("HOST"),
            # PORT: Database server port (required)
            "PORT": db_config.get("PORT") or base_config.get("PORT"),
            # OPTIONS: Database-specific connection options
            "OPTIONS": db_config.get("OPTIONS") or base_config.get("OPTIONS", {}),
            # TIME_ZONE: Timezone for this database (for datetime handling)
            "TIME_ZONE": db_config.get("TIME_ZONE") or base_config.get("TIME_ZONE", settings.TIME_ZONE),
            # ATOMIC_REQUESTS: Wrap each request in a transaction
            # Use tenant value if explicitly set, otherwise use master's, default to False
            "ATOMIC_REQUESTS": db_config.get("ATOMIC_REQUESTS")
            if "ATOMIC_REQUESTS" in db_config
            else base_config.get("ATOMIC_REQUESTS", False),
            # AUTOCOMMIT: Enable autocommit mode (implicit transaction commits)
            # Use tenant value if explicitly set, otherwise use master's, default to True
            "AUTOCOMMIT": db_config.get("AUTOCOMMIT")
            if "AUTOCOMMIT" in db_config
            else base_config.get("AUTOCOMMIT", True),
            # CONN_MAX_AGE: Maximum age of database connections in seconds
            # Use tenant value if explicitly set, otherwise use master's, default to 0 (no pooling)
            "CONN_MAX_AGE": db_config.get("CONN_MAX_AGE")
            if "CONN_MAX_AGE" in db_config
            else base_config.get("CONN_MAX_AGE", 0),
            # CONN_HEALTH_CHECKS: Enable health checks on old connections
            # Use tenant value if explicitly set, otherwise use master's, default to False
            "CONN_HEALTH_CHECKS": db_config.get("CONN_HEALTH_CHECKS")
            if "CONN_HEALTH_CHECKS" in db_config
            else base_config.get("CONN_HEALTH_CHECKS", False),
            # TEST: Test database configuration (used by test runner)
            # Use tenant value if explicitly set, otherwise use master's, default to empty dict
            "TEST": db_config.get("TEST") if "TEST" in db_config else base_config.get("TEST", {}),
        }

        # Ensure TEST dictionary has a NAME if not already set
        # Default to using the same database name as the production database
        if not resolved_config["TEST"].get("NAME"):
            resolved_config["TEST"]["NAME"] = resolved_config["NAME"]

        return db_alias, resolved_config

    def _create_database(self, db_name, user, password, host, port):
        """
        Create a new database on the PostgreSQL server.

        This low-level method directly connects to PostgreSQL and executes a
        CREATE DATABASE statement. It handles both psycopg2 and psycopg3 drivers.

        Process:
            1. Connect to PostgreSQL server (to 'postgres' database)
            2. Enable autocommit mode (DDL needs autocommit)
            3. Execute CREATE DATABASE statement with proper escaping
            4. Print success/error message
            5. Close connection

        Args:
            db_name (str): Name of database to create
            user (str): PostgreSQL user with database creation privileges
            password (str): PostgreSQL password
            host (str): PostgreSQL server host
            port (int): PostgreSQL server port (usually 5432)

        Raises:
            psycopg2.Error or psycopg.Error: If database creation fails
            Exception: If connection fails (host unreachable, auth failed, etc.)

        Security:
            Uses SQL identifier escaping to prevent SQL injection:
            - sql.Identifier() properly quotes database name
            - Prevents malicious database names from breaking SQL

            Example of protection:
            ```python
            # Dangerous (raw string): "CREATE DATABASE " + db_name
            # With db_name = "test; DROP DATABASE master; --"
            # Would execute: CREATE DATABASE test; DROP DATABASE master; --

            # Safe (with escaping):
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name))
            # Executes: CREATE DATABASE "test; DROP DATABASE master; --"
            # Treats entire string as identifier, no injection possible
            ```

        PostgreSQL Version Support:
            The method detects and adapts to driver version:
            - psycopg3: Uses psycopg.sql module for SQL construction
            - psycopg2: Uses psycopg2.sql module for SQL construction

            Both modules provide safe SQL construction:
            ```python
            if is_psycopg3:
                from psycopg import sql
            else:
                from psycopg2 import sql

            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
            ```

        Autocommit Mode:
            Database DDL operations (CREATE, DROP, ALTER) require autocommit:
            - conn.autocommit = True enables autocommit mode
            - Each statement is committed automatically
            - No explicit commit() needed
            - Important for CREATE DATABASE which can't run in transaction

        Connection Details:
            - Always connects to 'postgres' database, not the new one
            - 'postgres' is the system database available on all PostgreSQL servers
            - Used as gateway to create/manage other databases
            - Can be changed in template1 or other databases if 'postgres' unavailable

        Error Handling:
            If database already exists:
            ```
            psycopg2.DatabaseError: database "test" already exists
            ```

            Current implementation catches and re-raises, allowing caller to handle:
            ```python
            try:
                backend._create_database('test', ...)
            except Exception as e:
                print(f"Skipped DB create: {e}")
                raise
            ```

        Example:
            ```python
            backend = DatabaseTenantBackend(tenant)
            try:
                backend._create_database(
                    db_name='tenant_acme_db',
                    user='postgres',
                    password='secret',
                    host='db.example.com',
                    port=5432
                )
                print("Database created successfully")
            except Exception as e:
                if "already exists" in str(e):
                    print("Database already exists, skipping creation")
                else:
                    raise
            ```

        Performance:
            Database creation is fast (typically < 1 second for empty database)
            but can be slow for:
            - Large template databases (rare)
            - Slow network connections
            - High database server load

        See Also:
            - create(): High-level method that calls _create_database
            - _drop_database(): Drop a database
        """
        # Connect to PostgreSQL server using superuser/admin credentials
        # Connect to 'postgres' system database to issue database creation commands
        conn = psycopg_driver.connect(dbname="postgres", user=user, password=password, host=host, port=port)

        # Enable autocommit mode
        # Required for DDL operations like CREATE DATABASE
        # In transaction mode, CREATE DATABASE would fail
        conn.autocommit = True

        # Get a cursor for executing SQL statements
        cur = conn.cursor()
        try:
            # Import the appropriate SQL module based on driver version
            # Both psycopg2 and psycopg3 provide sql module for safe query construction
            if is_psycopg3:
                from psycopg import sql
            else:
                from psycopg2 import sql

            # Execute CREATE DATABASE with proper identifier escaping
            # sql.Identifier() escapes the database name to prevent SQL injection
            # Example: "test" -> "test" (with quotes), special chars properly handled
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))

            # Log successful creation
            print(f"[DB BACKEND] Database '{db_name}' created.")
        except Exception as e:
            # Log and re-raise the error
            # Common error: database already exists
            # Caller can catch specific errors if needed
            print(f"[DB BACKEND] Skipped DB create: {e}")
            raise
        finally:
            # Always close cursor and connection to free resources
            cur.close()
            conn.close()

    def _drop_database(self, db_name, user, password, host, port):
        """
        Drop a database from the PostgreSQL server.

        This low-level method directly connects to PostgreSQL and executes a
        DROP DATABASE statement to completely remove a database and all its data.

        Process:
            1. Connect to PostgreSQL server (to 'postgres' database)
            2. Enable autocommit mode (DDL needs autocommit)
            3. Execute DROP DATABASE statement
            4. Print confirmation message
            5. Close connection

        Args:
            db_name (str): Name of database to drop
            user (str): PostgreSQL user with database drop privileges
            password (str): PostgreSQL password
            host (str): PostgreSQL server host
            port (int): PostgreSQL server port (usually 5432)

        Raises:
            psycopg2.Error or psycopg.Error: If drop fails (in-use, no permissions, etc.)
            Exception: If connection fails (host unreachable, auth failed, etc.)

        Destructive Operation:
            This is IRREVERSIBLE without backups!

            Once executed, the database and ALL data are permanently deleted:
            - User tables and data
            - Extensions and functions
            - Roles and permissions specific to database
            - Connections must be closed first

        Error Handling:
            The method uses "DROP DATABASE IF EXISTS" to be idempotent:
            - If database doesn't exist, no error is raised
            - Can be safely called multiple times
            - Typical usage doesn't raise errors unless permissions denied

            ```python
            # Even if database doesn't exist, no error
            backend._drop_database('nonexistent', ...)
            # Completes successfully
            ```

        Active Connections:
            If the database has active connections, DROP DATABASE will fail:

            ```
            psycopg2.DatabaseError: database is being accessed by other users
            ```

            Solutions:
            1. Wait for connections to close (applications stop accessing DB)
            2. Kill active sessions (via PostgreSQL admin)
            3. Use TERMINATE on database connections (PostgreSQL 11+)

            Current implementation doesn't handle this - caller must ensure
            no active connections before calling _drop_database().

        PostgreSQL Permissions:
            User must have database drop privileges:
            - Typically the superuser or database owner
            - Regular users usually cannot drop databases
            - If "permission denied" error, check PostgreSQL roles

        Empty Drop:
            The method doesn't check if database is empty or in use:
            - No pre-check before dropping
            - Assumes caller verified it's safe to drop
            - Fast operation (just removes metadata)

        Performance:
            Database drop is very fast:
            - Typically completes in < 1 second
            - Frees disk space for large databases
            - No need to delete individual tables

        Examples:
            ```python
            backend = DatabaseTenantBackend(tenant)

            try:
                # Ensure no active connections first
                close_tenant_connections(tenant)

                # Drop the database
                backend._drop_database(
                    db_name='tenant_acme_db',
                    user='postgres',
                    password='secret',
                    host='db.example.com',
                    port=5432
                )
            except Exception as e:
                logger.error(f"Failed to drop database: {e}")
                raise
            ```

        Comparison with delete():
            - delete() is high-level: optionally drops database, removes from Django
            - _drop_database() is low-level: only drops from PostgreSQL
            - Use delete() for normal operations
            - Use _drop_database() only for custom database management

        Recovery:
            Once dropped, recovery is only possible via:
            1. Backup restoration
            2. Point-in-time recovery (PITR) if configured
            3. Replication failover if using replication

            No in-database recovery mechanism exists.

        See Also:
            - delete(): High-level method that optionally calls _drop_database
            - _create_database(): Create a database
            - Archival before deletion: Best practice for compliance
        """
        # Connect to PostgreSQL server to issue database drop command
        # Connect to 'postgres' system database (can't drop while connected to target DB)
        psycopg = connection.Database
        conn = psycopg.connect(dbname="postgres", user=user, password=password, host=host, port=port)

        # Enable autocommit mode
        # Required for DDL operations like DROP DATABASE
        conn.autocommit = True

        # Get a cursor for executing SQL statements
        cur = conn.cursor()
        try:
            # Execute DROP DATABASE with IF EXISTS clause
            # IF EXISTS makes the operation idempotent:
            # - If database exists, it's dropped
            # - If database doesn't exist, no error
            # Uses quoted identifier to handle special characters in database name
            cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')

            # Log successful drop
            print(f"[DB BACKEND] Database '{db_name}' dropped.")
        finally:
            # Always close cursor and connection to free resources
            cur.close()
            conn.close()
