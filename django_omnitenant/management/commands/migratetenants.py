"""
Run Database Migrations for All Tenants

Django management command for bulk executing schema/database migrations across all tenants.

Purpose:
    Simplifies deployment process by running migrations for every tenant in a single
    command invocation. Useful for updating all tenant databases/schemas when deploying
    new application versions with model changes.

Key Features:
    - No arguments required (operates on all tenants)
    - Optionally specify app_label and migration_name for targeted migrations
    - Automatically discovers all tenants from database
    - Iterates through each tenant and runs migrations
    - Proper error handling (one tenant's failure doesn't stop others)
    - Detailed per-tenant output for troubleshooting
    - Uses tenant-specific backend for each tenant
    - Supports migrate zero for all tenants

Use Cases:
    - Production deployments: Run all migrations after code update
    - Multi-tenant applications: Keep all tenants in sync
    - Schema updates: Propagate schema changes to all tenants
    - Initial setup: Migrate all tenants after creating multiple tenants
    - Rollback: Unapply migrations for specific app across all tenants

Tenant Isolation Context:
    Each tenant migrated within isolated context:
    - Database-per-tenant: Each tenant's database receives migrations
    - Schema-per-tenant: Each tenant's schema receives migrations
    - Row-level isolation: Migrations run on shared database (once)
    - All tenants independently updated or failed tracked

Usage:
    ```bash
    # Migrate all tenants to latest
    python manage.py migratetenants

    # Migrate specific app for all tenants
    python manage.py migratetenants hrms

    # Migrate to specific migration for all tenants
    python manage.py migratetenants hrms 0002

    # Rollback all migrations for an app across all tenants
    python manage.py migratetenants hrms zero

    # Verbose output
    python manage.py migratetenants --verbosity=2
    ```

Command Flow:
    1. Parse app_label and migration_name arguments (if provided)
    2. Get Tenant model from settings
    3. Query database for all tenant instances
    4. For each tenant:
        a. Retrieve tenant-specific backend
        b. Execute backend.migrate(app_label, migration_name, **options)
        c. Output success or error
    5. Continue to next tenant even if one fails
    6. Provide summary of migrations

Error Handling:
    - Individual tenant failures caught and reported
    - Command continues to migrate other tenants
    - Failed tenants listed in output
    - No global rollback (per-tenant isolation)
    - Exception details shown for each failure

Related:
    - migratetenant: Migrate specific tenant
    - createtenant: Create new tenant
    - TenantBackend: Handles migration execution

Notes:
    - Does not take tenant_id argument (all tenants)
    - Supports standard Django options (verbosity, etc.)
    - Better for production than running migratetenant multiple times
    - Ensures all tenants remain schema-synchronized
"""

from django.core.management.base import BaseCommand

from django_omnitenant.models import BaseTenant
from django_omnitenant.utils import get_tenant_backend, get_tenant_model


class Command(BaseCommand):
    """
    Management command for running migrations on all tenants.

    This command provides a convenient way to run migrations across an entire
    multi-tenant deployment without needing to specify each tenant individually.
    It iterates through all tenants, runs their migrations, and handles errors
    gracefully to ensure failures don't stop the migration of other tenants.

    Inheritance:
        Inherits from django.core.management.base.BaseCommand, the base Django
        management command class.

    Key Functionality:
        - Discovers all tenants from database
        - Runs migrations for each tenant sequentially
        - Optionally targets specific app or migration
        - Catches exceptions per-tenant to continue with others
        - Provides detailed output per tenant
        - Uses tenant-specific backend for each tenant

    Attributes:
        help (str): Help text shown in management command listing

    Usage Examples:
        Migrate all tenants to latest:
        ```bash
        $ python manage.py migratetenants
        Migrating tenant: acme
        Tenant 'acme' migrated successfully.
        Migrating tenant: beta
        Tenant 'beta' migrated successfully.
        Migrating tenant: gamma
        Tenant 'gamma' migrated successfully.
        ```

        Migrate specific app for all tenants:
        ```bash
        $ python manage.py migratetenants hrms
        Migrating tenant: acme (app: hrms)
        Tenant 'acme' migrated successfully.
        Migrating tenant: beta (app: hrms)
        Tenant 'beta' migrated successfully.
        ```

        Migrate to specific migration for all tenants:
        ```bash
        $ python manage.py migratetenants hrms 0002
        Migrating tenant: acme (app: hrms, migration: 0002)
        Tenant 'acme' migrated successfully.
        Migrating tenant: beta (app: hrms, migration: 0002)
        Tenant 'beta' migrated successfully.
        ```

        Rollback migrations for all tenants:
        ```bash
        $ python manage.py migratetenants hrms zero
        Migrating tenant: acme (app: hrms, migration: zero)
        Tenant 'acme' migrated successfully.
        Migrating tenant: beta (app: hrms, migration: zero)
        Tenant 'beta' migrated successfully.
        ```

        With verbose output:
        ```bash
        $ python manage.py migratetenants --verbosity=2
        Migrating tenant: acme
        Operations to perform:
          Apply all migrations: ...
        Running migrations:
          Rendering model states... DONE
          Applying app1.0001_initial... OK
          ...
        Tenant 'acme' migrated successfully.
        ...
        ```

    Notes:
        - No tenant_id argument (unlike migratetenant command)
        - Useful for deployments when schema changes affect all tenants
        - Each tenant migrated independently (one failure doesn't block others)
        - Takes longer than single migratetenant on large deployments
        - Consider testing on subset first with migratetenant before running on all
    """

    help = "Run migrations for all tenants."

    def add_arguments(self, parser):
        """
        Add command-line arguments to the command parser.

        This method adds optional positional arguments for app_label and migration_name
        to allow targeted migrations across all tenants.

        Arguments:
            parser (argparse.ArgumentParser): Django's argument parser for this command.

        Positional Arguments:
            app_label: App to migrate (optional, migrates all if not specified)
            migration_name: Specific migration to target (optional)

        Django Arguments Inherited:
            --plan: Show migration plan without executing
            --verbosity: Output verbosity (0=silent, 1=normal, 2=verbose, 3=debug)
            --no-color: Disable colored output
            --skip-checks: Skip system checks
            --no-input: Non-interactive mode
            Any other Django migrate options

        Examples:
            ```python
            # All tenants, all apps
            $ manage.py migratetenants

            # All tenants, specific app
            $ manage.py migratetenants users

            # All tenants, specific app and migration
            $ manage.py migratetenants hrms 0002

            # All tenants, rollback app
            $ manage.py migratetenants hrms zero

            # With verbosity
            $ manage.py migratetenants --verbosity=2

            # With plan
            $ manage.py migratetenants hrms --plan
            ```

        Notes:
            - Both arguments are optional
            - migration_name requires app_label
            - Parser is modified in-place
        """
        parser.add_argument(
            "app_label",
            nargs="?",
            help="App label of the application to migrate.",
        )

        parser.add_argument(
            "migration_name",
            nargs="?",
            help='Target migration name (e.g., "0002", "0002_auto", or "zero").',
        )

    def handle(self, *args, **options):
        """
        Execute database migrations for all tenants.

        This method performs the following steps:
        1. Extracts app_label and migration_name from options
        2. Gets the Tenant model class
        3. Queries database for all tenant instances
        4. Iterates through each tenant sequentially
        5. For each tenant: Gets backend and runs migrations
        6. Catches and reports errors per-tenant
        7. Continues to next tenant even if current one fails

        The command provides detailed output for each tenant so administrators
        can identify which tenants succeeded and which failed.

        Arguments:
            *args: Positional arguments (typically empty, not used)
            **options (dict): Command options including:
                - app_label (str): Optional app to migrate
                - migration_name (str): Optional specific migration
                - verbosity (int): Output verbosity level (0-3, default 1)
                - no_color (bool): Whether to disable colored output
                - plan (bool): If True, show plan but don't execute
                - [other options]: Standard Django command options

        Returns:
            None: Django management commands don't return values. Output via stdout/stderr.

        Process Flow:
            ```
            1. Extract app_label and migration_name
                app_label = options.pop('app_label', None)
                migration_name = options.pop('migration_name', None)

            2. Get Tenant model
                Tenant = get_tenant_model()

            3. Query all tenants
                for tenant in Tenant.objects.all():
                    # Process each tenant

            4. For each tenant:
                a. Output migration start message
                   self.stdout.write(
                       self.style.MIGRATE_HEADING(f'Migrating tenant: {tenant_id}')
                   )

                b. Get tenant-specific backend
                   backend = get_tenant_backend(tenant)

                c. Execute migrations with app_label/migration_name
                   if app_label:
                       if migration_name:
                           backend.migrate(app_label, migration_name, **options)
                       else:
                           backend.migrate(app_label, **options)
                   else:
                       backend.migrate(**options)

                d. Output success
                   self.stdout.write(
                       self.style.SUCCESS(f'Tenant {tenant_id} migrated successfully.')
                   )

            5. On exception:
                except Exception as e:
                    # Log error but continue to next tenant
                    self.stdout.write(
                        self.style.ERROR(f'Migrations failed for {tenant_id}: {e}')
                    )
            ```

        Usage Examples:
            Basic migration of all tenants:
            ```bash
            $ python manage.py migratetenants
            Migrating tenant: acme
            Tenant 'acme' migrated successfully.
            Migrating tenant: beta
            Tenant 'beta' migrated successfully.
            Migrating tenant: gamma
            Tenant 'gamma' migrated successfully.
            ```

            Migrate specific app for all tenants:
            ```bash
            $ python manage.py migratetenants hrms
            Migrating tenant: acme (app: hrms)
            Tenant 'acme' migrated successfully.
            Migrating tenant: beta (app: hrms)
            Tenant 'beta' migrated successfully.
            Migrating tenant: gamma (app: hrms)
            Tenant 'gamma' migrated successfully.
            ```

            Migrate to specific migration for all tenants:
            ```bash
            $ python manage.py migratetenants hrms 0002
            Migrating tenant: acme (app: hrms, migration: 0002)
            Tenant 'acme' migrated successfully.
            Migrating tenant: beta (app: hrms, migration: 0002)
            Tenant 'beta' migrated successfully.
            ```

            Rollback app for all tenants:
            ```bash
            $ python manage.py migratetenants hrms zero
            Migrating tenant: acme (app: hrms, migration: zero)
            Tenant 'acme' migrated successfully.
            Migrating tenant: beta (app: hrms, migration: zero)
            Tenant 'beta' migrated successfully.
            ```

            With verbosity for debugging:
            ```bash
            $ python manage.py migratetenants hrms --verbosity=2
            Migrating tenant: acme (app: hrms)
            Operations to perform:
              Apply all migrations: hrms
            Running migrations:
              Rendering model states... DONE (5.234s)
              Applying hrms.0001_initial... (0.234s)
              Applying hrms.0002_add_field... (0.105s)
              ...
            Tenant 'acme' migrated successfully.

            Migrating tenant: beta (app: hrms)
            [...migrations for beta...]
            Tenant 'beta' migrated successfully.

            [...more tenants...]
            ```

            With partial failure:
            ```bash
            $ python manage.py migratetenants hrms 0002
            Migrating tenant: acme (app: hrms, migration: 0002)
            Tenant 'acme' migrated successfully.
            Migrating tenant: beta (app: hrms, migration: 0002)
            Migrations failed for tenant 'beta': IntegrityError: duplicate key value
            Migrating tenant: gamma (app: hrms, migration: 0002)
            Tenant 'gamma' migrated successfully.
            # Note: beta failed, but acme and gamma succeeded
            ```

        Error Handling:

            Case 1: Migration syntax error in migration file
            ```bash
            $ python manage.py migratetenants
            Migrating tenant: acme
            Migrations failed for tenant 'acme': ImportError: cannot import name 'SomeModel'
            # Continues to next tenant despite error
            ```

            Case 2: Database connection error for specific tenant
            ```bash
            $ python manage.py migratetenants
            Migrating tenant: acme
            Migrations failed for tenant 'acme': could not connect to database
            # Continues to other tenants
            ```

            Case 3: Schema error (e.g., column already exists)
            ```bash
            $ python manage.py migratetenants hrms
            Migrating tenant: beta (app: hrms)
            Migrations failed for tenant 'beta': column already exists
            # Continues despite error
            ```

        Important Characteristics:
            - Sequential execution: Tenants migrated one at a time
            - No dependency: One tenant's failure doesn't affect others
            - No rollback: Failed tenants don't rollback successful ones
            - All tenants attempted: Even if some fail, all are tried
            - Detailed output: Each tenant's status clearly shown

        Notes:
            - No --tenant-id argument (operates on all)
            - Inherits Django options (--verbosity, --no-color, etc.)
            - Output styled with MIGRATE_HEADING and SUCCESS/ERROR colors
            - Exception details displayed for debugging failures
            - On production, consider dry-run with single migratetenant first
            - Large deployments: may take considerable time if many tenants
            - Use "zero" as migration_name to rollback all migrations for an app

        Integration Points:
            - Calls get_tenant_model(): Gets configured Tenant model
            - Calls Tenant.objects.all(): Gets all tenant instances
            - Calls get_tenant_backend(): Gets tenant-specific backend
            - Backend.migrate(): Executes migrations in tenant context
            - Uses self.style: Django's output formatting (MIGRATE_HEADING, SUCCESS, ERROR)
            - Uses self.stdout: Django's command output stream
        """
        # Extract app_label and migration_name from options
        app_label = options.pop("app_label", None)
        migration_name = options.pop("migration_name", None)

        reset_all = app_label == "zero" and migration_name is None
        if reset_all:
            app_label = None

        # Get the Tenant model class (can be customized via settings)
        Tenant = get_tenant_model()

        # Iterate through all tenants in database
        for tenant in Tenant.objects.all():  # type: ignore
            tenant: BaseTenant = tenant

            # Build migration target description for output
            migration_target = ""
            if app_label:
                migration_target = f" (app: {app_label}"
                if migration_name:
                    migration_target += f", migration: {migration_name}"
                migration_target += ")"
            elif reset_all:
                migration_target = " (all apps: zero)"

            # Display which tenant we're about to migrate (good for monitoring output)
            self.stdout.write(self.style.MIGRATE_HEADING(f"Migrating tenant: {tenant.tenant_id}{migration_target}"))

            # Try to migrate this tenant, but continue if failure
            try:
                # Get the backend that knows how to access this tenant's database/schema
                backend = get_tenant_backend(tenant)

                # Execute migrations for this tenant in its isolated context
                # Pass app_label and migration_name as positional args if provided
                if reset_all:
                    from django.apps import apps as django_apps

                    for app_config in django_apps.get_app_configs():
                        try:
                            backend.migrate(app_config.label, "zero", **options)
                        except Exception as app_error:
                            if "does not have migrations" in str(app_error):
                                continue
                            raise
                elif app_label:
                    if migration_name:
                        backend.migrate(app_label, migration_name, **options)
                    else:
                        backend.migrate(app_label, **options)
                else:
                    backend.migrate(**options)

                # On success, confirm to user
                self.stdout.write(self.style.SUCCESS(f"Tenant '{tenant.tenant_id}' migrated successfully."))
            except Exception as e:
                # On failure, log error but continue to next tenant
                # This ensures one tenant's error doesn't prevent others from migrating
                self.stdout.write(self.style.ERROR(f"Migrations failed for tenant '{tenant.tenant_id}': {e}"))
