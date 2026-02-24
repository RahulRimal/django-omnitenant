"""
Create Tenant Superuser Management Command

Django management command for creating Django superusers within tenant context.

Purpose:
    Extends Django's built-in createsuperuser command to work with multi-tenant
    architecture, allowing creation of admin users that are isolated to specific
    tenants while maintaining Django admin functionality.
    
Key Features:
    - Inherits Django's createsuperuser command
    - Accepts all Django superuser arguments (username, email, etc.)
    - Adds required --tenant-id argument
    - Validates tenant exists before creating user
    - Sets proper tenant context for user creation
    - User created in tenant's database/schema
    - User can only access tenant's data

Tenant Scope:
    Superuser created within tenant context means:
    - User stored in tenant database (database isolation)
    - User stored in tenant schema (schema isolation)
    - User cannot access other tenants' data
    - User's queries automatically scoped to tenant
    - Django admin shows only tenant's data
    
Usage:
    ```bash
    # Interactive mode
    python manage.py createtenantsuperuser --tenant-id=acme
    
    # Non-interactive mode
    python manage.py createtenantsuperuser \\
        --tenant-id=acme \\
        --username=admin \\
        --email=admin@acme.com \\
        --noinput
    ```
    
    Supported Django Arguments:
    - --username: Superuser username
    - --email: Superuser email address
    - --no-input: Non-interactive mode (requires username/email)
    - --preserve: Keep existing password
    - Any other createsuperuser argument
    
Command Flow:
    1. Parse command line arguments
    2. Extract required --tenant-id
    3. Validate tenant exists
    4. Set tenant context
    5. Call parent createsuperuser
    6. User created in tenant's database
    
Error Handling:
    - CommandError if tenant doesn't exist
    - CommandError if superuser creation fails
    - Invalid arguments delegated to parent
    - Connection errors from database operations

Interactive vs Non-Interactive:
    
    Interactive (default):
    ```bash
    python manage.py createtenantsuperuser --tenant-id=acme
    Using tenant: ACME Corporation
    Username: admin
    Email address: admin@acme.com
    Password: ****
    Password (again): ****
    Superuser created successfully.
    ```
    
    Non-Interactive:
    ```bash
    python manage.py createtenantsuperuser \\
        --tenant-id=acme \\
        --username=admin \\
        --email=admin@acme.com \\
        --noinput
    ```

Related:
    - createtenant: Create new tenant
    - changepassword: Change superuser password
    - Django's createsuperuser: Parent command
    - TenantContext: Manages tenant isolation
"""

from django.contrib.auth.management.commands.createsuperuser import Command as CreateSuperuserCommand
from django.core.management.base import CommandError

from django_omnitenant.tenant_context import TenantContext
from django_omnitenant.utils import get_tenant_model
from django_omnitenant.models import BaseTenant

class Command(CreateSuperuserCommand):
    """
    Tenant-aware management command for creating superusers.
    
    This command extends Django's built-in createsuperuser command to work within
    the django-omnitenant multi-tenant architecture. It ensures that the created
    superuser is properly scoped to a specific tenant and can only access that
    tenant's data through the Django admin interface.
    
    Inheritance:
        Inherits from django.contrib.auth.management.commands.createsuperuser.Command,
        which means it retains all the standard Django superuser creation functionality
        while adding tenant-specific behavior.
    
    Key Differences from Django's createsuperuser:
        1. Requires --tenant-id argument (mandatory, not optional)
        2. Validates tenant exists before user creation
        3. Wraps superuser creation in TenantContext
        4. User created in tenant's isolated database/schema
        5. User cannot access other tenants through Django admin
    
    Attributes:
        help (str): Short help text displayed in management command list
    
    Usage:
        # Interactive mode - prompts for username, email, password
        python manage.py createtenantsuperuser --tenant-id=acme
        
        # Non-interactive - all required fields provided
        python manage.py createtenantsuperuser \\
            --tenant-id=acme \\
            --username=admin \\
            --email=admin@acme.com \\
            --noinput
    
    Examples:
        Creating superuser in ACME tenant (interactive):
        ```python
        # Command line
        $ python manage.py createtenantsuperuser --tenant-id=acme
        Using tenant: ACME Corporation
        Username: john.admin
        Email address: john.admin@acme.com
        Password: 
        Password (again):
        Superuser created successfully.
        
        # Result: John's user exists in ACME's database only
        # Can log into Django admin, sees only ACME's data
        ```
    
    Error Cases:
        - Tenant does not exist: CommandError raised immediately
        - Invalid arguments: Delegated to parent createsuperuser (same validation)
        - Database error: Propagated from parent command
        - Connection error: Propagated when accessing tenant's database
    
    Notes:
        - The parent command (Django's createsuperuser) handles all validation
        - This command only adds tenant isolation layer
        - Superuser status means admin=True and staff=True in tenant's database
        - User's queryset automatically filtered by TenantContext
        - Django admin will show only this tenant's objects
    """
    help = "Tenant-aware createsuperuser"

    def add_arguments(self, parser):
        """
        Add command-line arguments to the command parser.
        
        This method extends the parent createsuperuser command's arguments by adding
        a required --tenant-id argument while preserving all Django's built-in arguments
        for username, email, password, and other superuser options.
        
        Arguments Added:
            parser (argparse.ArgumentParser): Django's argument parser for this command.
        
        Django Arguments Inherited (from parent createsuperuser):
            --username (str): The username for the new superuser (optional in interactive, required in non-interactive)
            --email (str): The email address for the new superuser (optional in interactive, required in non-interactive)
            --no-input: Run in non-interactive mode (requires username and email)
            --preserve: Keep existing password (if superuser already exists)
            --dry-run: Show what would be created without actually creating (varies by Django version)
        
        Custom Arguments:
            --tenant-id (str): REQUIRED. The tenant_id of the tenant where the superuser
                should be created. Must be an existing tenant or CommandError is raised.
                This is the primary extension to Django's standard createsuperuser.
        
        Examples:
            ```python
            # Interactive with tenant
            $ manage.py createtenantsuperuser --tenant-id=acme
            
            # Non-interactive with all arguments
            $ manage.py createtenantsuperuser \\
                --tenant-id=acme \\
                --username=admin \\
                --email=admin@acme.com \\
                --noinput
            
            # Using with other arguments
            $ manage.py createtenantsuperuser \\
                --tenant-id=acme \\
                --username=admin \\
                --email=admin@acme.com \\
                --preserve
            ```
        
        Notes:
            - Tenant ID must exist or handle() will raise CommandError
            - All Django superuser arguments are preserved through super().add_arguments()
            - Parser is modified in-place; no return value
            - Required arguments cannot be made optional without breaking tenant isolation
        """
        super().add_arguments(parser)  # keep Django's arguments (username, email, etc.)
        parser.add_argument(
            "--tenant-id",
            required=True,
            help="The tenant_id of the tenant where the superuser should be created. "
                 "Must be an existing tenant. The created user will only exist in this tenant's database/schema."
        )

    def handle(self, *args, **options):
        """
        Execute the command to create a tenant-scoped superuser.
        
        This is the main execution method. It performs the following steps:
        1. Extracts and validates the required --tenant-id argument
        2. Retrieves the Tenant model class from tenant_model setting
        3. Queries database to find the tenant with given tenant_id
        4. Raises CommandError if tenant doesn't exist
        5. Sets up tenant context using TenantContext.use_tenant()
        6. Calls parent's handle() to execute Django's superuser creation
        
        All superuser creation logic (prompting for username/email/password, validation)
        is delegated to Django's createsuperuser parent command within the tenant context.
        The user is created in the tenant's isolated database/schema automatically.
        
        Arguments:
            *args: Positional arguments (passed to parent command, typically empty)
            **options (dict): Command options from argparse, including:
                - tenant_id (str): The tenant identifier (extracted in this method)
                - username (str, optional): Superuser username for non-interactive mode
                - email (str, optional): Superuser email for non-interactive mode
                - interactive (bool): Whether to prompt for missing arguments (default True)
                - no_input (bool): Force non-interactive mode (no prompting)
                - preserve (bool): Keep existing password if user exists
                - [other options]: Any additional arguments from Django's createsuperuser
        
        Returns:
            None: Django management commands don't return values. Output is via stdout/stderr.
        
        Process Flow:
            ```
            1. Receive options dict with tenant_id and other args
                options = {
                    'tenant_id': 'acme',
                    'username': 'admin',
                    'email': 'admin@acme.com',
                    'interactive': True,
                    ...other django args...
                }
            
            2. Extract and remove tenant_id from options
                tenant_id = options.pop('tenant_id')  # 'acme'
                # options now only has Django's arguments
            
            3. Get Tenant model class
                Tenant = get_tenant_model()  # e.g., CustomTenantModel
            
            4. Query for tenant (with error handling)
                tenant = Tenant.objects.get(tenant_id='acme')
                # Raises Tenant.DoesNotExist if not found
            
            5. Output confirmation to user
                self.stdout.write(
                    self.style.SUCCESS(f"Using tenant: ACME Corporation")
                )
            
            6. Enter tenant context (all subsequent DB access scoped to this tenant)
                with TenantContext.use_tenant(tenant):
                    # Now in tenant-scoped context
                    # Database queries/writes are isolated
                    # User will be created in tenant's database only
            
            7. Call parent's handle() to create superuser
                super().handle(*args, **options)
                # This runs Django's createsuperuser within tenant context
                # User created in tenant's isolated database/schema
                # Superuser attributes set (admin=True, staff=True)
            
            8. Exit context when 'with' block ends
                # Automatically switches back to default context
                # Connection closed or reset as needed
            ```
        
        Usage Examples:
            Interactive creation:
            ```bash
            $ python manage.py createtenantsuperuser --tenant-id=acme
            Using tenant: ACME Corporation
            Username: john.admin
            Email address: john@acme.com
            Password:
            Password (again):
            Superuser created successfully.
            ```
            
            Non-interactive creation:
            ```bash
            $ python manage.py createtenantsuperuser \\
                --tenant-id=beta \\
                --username=admin \\
                --email=admin@beta.com \\
                --noinput
            Using tenant: Beta Corp
            Superuser created successfully.
            ```
            
            With additional arguments:
            ```bash
            $ python manage.py createtenantsuperuser \\
                --tenant-id=gamma \\
                --username=superuser \\
                --email=admin@gamma.com \\
                --preserve \\
                --noinput
            Using tenant: Gamma Industries
            Superuser created successfully.
            ```
        
        Error Handling:
            
            Case 1: Missing tenant_id (should not occur, argument is required)
            - Argparse prevents missing required argument
            - Command exits before handle() is called
            
            Case 2: Tenant doesn't exist
            ```python
            # Command: --tenant-id=nonexistent
            except Tenant.DoesNotExist:
                raise CommandError(
                    "Tenant with id 'nonexistent' does not exist"
                )
            # Output: CommandError: Tenant with id 'nonexistent' does not exist
            # Exit code: 1
            ```
            
            Case 3: Database connection error
            ```python
            # e.g., tenant's database unreachable
            # Error propagated from tenant's database driver
            # User sees connection error traceback
            ```
            
            Case 4: Invalid username format in non-interactive mode
            ```bash
            $ manage.py createtenantsuperuser \\
                --tenant-id=acme \\
                --username="" \\
                --noinput
            # Django raises ValidationError (from parent command)
            # Error message about invalid username
            ```
            
            Case 5: Superuser already exists (depends on Django behavior)
            ```bash
            $ manage.py createtenantsuperuser \\
                --tenant-id=acme \\
                --username=existing_admin \\
                --noinput
            # Behavior depends on Django version and --preserve flag
            # May error or update existing user
            ```
        
        Context Management:
            The TenantContext.use_tenant() context manager ensures:
            - All database operations within the 'with' block are tenant-scoped
            - User query defaults include tenant filter automatically
            - User creation happens in tenant's database/schema
            - After context exits, operations revert to default (non-tenant) scope
            - Database connection is properly managed through context lifecycle
        
        Notes:
            - The parent Command (Django's createsuperuser) handles all user validation
            - This method only adds tenant isolation - no custom validation
            - Username/email must be unique within tenant (not globally unique)
            - Multiple tenants can have superusers with same username
            - User created by this command is completely isolated per tenant
            - Django admin will only show this tenant's users when accessed by tenant-scoped user
        
        Django Integration:
            - Uses self.stdout and self.style for formatted output (Django conventions)
            - Inherits from CreateSuperuserCommand, so parent handle() has extensive validation
            - Raises CommandError for immediate exit and error reporting
            - Exit code 0 on success, 1 on CommandError
        """
        # Extract tenant_id from options and remove it (parent command doesn't know about it)
        tenant_id = options.pop("tenant_id")

        # Get the tenant model (can be customized via settings)
        Tenant = get_tenant_model()
        
        # Validate that tenant exists before attempting to create user
        try:
            tenant: BaseTenant = Tenant.objects.get(tenant_id=tenant_id)
        except Tenant.DoesNotExist:
            # Tenant not found - raise error immediately, don't proceed
            raise CommandError(
                f"Tenant with id '{tenant_id}' does not exist. "
                f"Create tenant first using: python manage.py createtenant"
            )

        # Confirm to user which tenant we're using (good UX for error prevention)
        self.stdout.write(self.style.SUCCESS(f"Using tenant: {tenant.name}"))

        # Enter tenant context: all subsequent database operations are scoped to this tenant
        # User will be created in this tenant's database/schema only
        # When context exits, scope is restored to default
        with TenantContext.use_tenant(tenant):
            # Call parent's handle to perform Django's superuser creation
            # Parent handles: prompting for username/email/password, validation, user creation
            # All of this happens within the tenant context above
            if tenant.isolation_type == BaseTenant.IsolationType.DATABASE:
                db_config = tenant.config.get("db_config", {})
                options["database"] = db_config.get("DB_ALIAS", db_config.get("NAME"))
            super().handle(*args, **options)
