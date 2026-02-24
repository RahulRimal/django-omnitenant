"""
List All Tenants with Detailed Information

Django management command for discovering and inspecting all tenants in multi-tenant system.

Purpose:
    Provides administrators and developers with visibility into all tenants configured
    in the multi-tenant application. Shows tenant metadata, isolation types, database
    configuration, and other relevant information for monitoring and troubleshooting.

Key Features:
    - Lists all tenants with configurable output format
    - Supports multiple output formats (table, JSON, CSV)
    - Filter tenants by isolation type
    - Shows database configuration for database-per-tenant
    - Displays creation timestamps and metadata
    - Pretty-printed table view with proper alignment
    - Machine-readable JSON and CSV formats

Output Formats:
    - table (default): Human-readable table with ASCII borders
    - json: Structured JSON for integration with other tools
    - csv: CSV format for spreadsheets or data pipelines

Supported Filters:
    --isolation-type: Filter by isolation strategy (database, schema, table)

Usage:
    ```bash
    # List all tenants (table format)
    python manage.py showtenants

    # List with specific isolation type
    python manage.py showtenants --isolation-type=database

    # JSON format for scripts
    python manage.py showtenants --format=json

    # CSV format for spreadsheets
    python manage.py showtenants --format=csv --isolation-type=schema
    ```

Command Flow:
    1. Get Tenant model
    2. Retrieve all tenants from database
    3. Apply isolation_type filter if provided
    4. Check if tenants exist
    5. Format and output according to selected format
    6. Use appropriate helper method (_output_table, _output_json, _output_csv)

Output Examples:
    Table format shows: ID, Name, Isolation Type, Domain, Created Date, DB Config
    JSON format: Complete tenant data as structured objects
    CSV format: Flat format with headers for spreadsheet import

Related:
    - createtenant: Create new tenant
    - createtenantsuperuser: Create user in tenant
    - Database configuration inspection
    - Tenant metadata review
"""

from django.core.management.base import BaseCommand
from django_omnitenant.utils import get_tenant_model
from django_omnitenant.models import BaseTenant


class Command(BaseCommand):
    """
    Management command for listing and inspecting all tenants.

    This command provides comprehensive visibility into the multi-tenant system by
    displaying all configured tenants with their metadata, isolation types, and
    database configuration. Output can be formatted as human-readable tables,
    JSON for integration with tools, or CSV for spreadsheet analysis.

    Inheritance:
        Inherits from django.core.management.base.BaseCommand, the base Django
        management command class.

    Key Functionality:
        - Queries all tenants from configured Tenant model
        - Supports filtering by isolation type (database, schema, table)
        - Multiple output formats (table, JSON, CSV)
        - Displays database configuration details
        - Shows creation timestamps
        - Pretty formatting with alignment and colors

    Attributes:
        help (str): Help text shown in management command listing

    Usage Examples:
        List all tenants (default table format):
        ```bash
        $ python manage.py showtenants
        Found 3 tenant(s):

        Tenant ID        Name                           Isolation       Domain                         Created
        ────────────────────────────────────────────────────────────────────────────────────────────────────────
        acme             ACME Corporation               DATABASE        acme.example.com               2024-01-15 10:30:45
                 └─ Database: acme_db @ localhost:5432
        beta             Beta Corp                      SCHEMA          beta.example.com               2024-01-20 14:20:10
                 └─ Database: shared_db @ localhost:5432
        gamma            Gamma Industries               TABLE           gamma.example.com              2024-02-01 09:15:33
                 └─ Database: shared_db @ localhost:5432
        ```

        Filter by isolation type:
        ```bash
        $ python manage.py showtenants --isolation-type=database
        Found 1 tenant(s):

        Tenant ID        Name                           Isolation       Domain                         Created
        ────────────────────────────────────────────────────────────────────────────────────────────────────────
        acme             ACME Corporation               DATABASE        acme.example.com               2024-01-15 10:30:45
                 └─ Database: acme_db @ localhost:5432
        ```

        JSON output for scripts:
        ```bash
        $ python manage.py showtenants --format=json
        [
          {
            "id": 1,
            "tenant_id": "acme",
            "name": "ACME Corporation",
            "isolation_type": "DATABASE",
            "config": {...},
            "created_at": "2024-01-15T10:30:45"
          },
          ...
        ]
        ```

        CSV output:
        ```bash
        $ python manage.py showtenants --format=csv
        ID,Tenant ID,Name,Isolation Type,Created At,DB Name,DB Host
        1,acme,ACME Corporation,DATABASE,2024-01-15T10:30:45,acme_db,localhost
        2,beta,Beta Corp,SCHEMA,2024-01-20T14:20:10,shared_db,localhost
        3,gamma,Gamma Industries,TABLE,2024-02-01T09:15:33,shared_db,localhost
        ```

    Notes:
        - No arguments required (operates on all tenants by default)
        - Filtering is optional (--isolation-type)
        - Output format can be selected (table is default)
        - Table format includes database configuration details
        - JSON/CSV formats suitable for automation and integration
    """

    help = "List all tenants with their details"

    def add_arguments(self, parser):
        """
        Add command-line arguments to the command parser.

        This method adds optional arguments for filtering tenants by isolation type
        and selecting output format. Both are optional; defaults provide reasonable
        behavior (all tenants, table format).

        Arguments:
            parser (argparse.ArgumentParser): Django's argument parser for this command.

        Optional Arguments:
            --isolation-type (str): Filter tenants by isolation strategy.
                Valid values: database, schema, table (case-insensitive)
                If provided, only tenants with this isolation type are shown.
                If invalid value provided, error shown and command exits.
                Examples:
                    - database: Database-per-tenant isolation
                    - schema: PostgreSQL schema-per-tenant isolation
                    - table: Row-level isolation (shared database/schema)

            --format (str): Output format for tenant listing.
                Default: 'table'
                Choices: table, json, csv
                - table: Human-readable table with ASCII separators
                - json: Structured JSON output (machine-readable)
                - csv: CSV format for spreadsheet import

        Examples:
            ```python
            # List all tenants, table format (default)
            $ manage.py showtenants

            # List database-isolated tenants only
            $ manage.py showtenants --isolation-type=database

            # List schema-isolated tenants in JSON
            $ manage.py showtenants --isolation-type=schema --format=json

            # All tenants in CSV format
            $ manage.py showtenants --format=csv
            ```

        Notes:
            - Isolation type filter is case-insensitive internally
            - Format choices are validated by argparse (fixed list)
            - Parser is modified in-place; no return value
        """
        parser.add_argument(
            "--isolation-type",
            type=str,
            help="Filter by isolation type (database/schema/table). "
            "Shows only tenants with the specified isolation strategy.",
        )
        parser.add_argument(
            "--format",
            type=str,
            choices=["table", "json", "csv"],
            default="table",
            help="Output format (default: table). "
            "table: Human-readable formatted output. "
            "json: Structured JSON (machine-readable). "
            "csv: CSV format for spreadsheets.",
        )

    def handle(self, *args, **options):
        """
        Execute the command to list all tenants.

        This method retrieves all tenants, applies optional filters, and outputs
        the results in the requested format. It handles error cases gracefully
        (invalid isolation type, no tenants found) before delegating to format-specific
        output methods.

        Arguments:
            *args: Positional arguments (typically empty)
            **options (dict): Command options including:
                - isolation_type (str, optional): Filter by isolation type
                - format (str): Output format (table, json, csv)

        Returns:
            None: Django management commands don't return values. Output via stdout.

        Process Flow:
            ```
            1. Get Tenant model
                TenantModel = get_tenant_model()

            2. Query all tenants
                tenants = TenantModel.objects.all()

            3. Apply isolation_type filter if provided
                if isolation_type:
                    # Convert to uppercase (matches database choices)
                    # Validate against BaseTenant.IsolationType.choices
                    # Filter query if valid, error if invalid

            4. Check if any tenants match
                if not tenants.exists():
                    output: 'No tenants found.'
                    return

            5. Delegate to format-specific method
                if format == 'json':
                    self._output_json(tenants)
                elif format == 'csv':
                    self._output_csv(tenants)
                else:
                    self._output_table(tenants)
            ```

        Usage Examples:
            List all tenants:
            ```bash
            $ python manage.py showtenants
            Found 3 tenant(s):

            Tenant ID        Name                           Isolation       Domain...
            acme             ACME Corporation               DATABASE        acme.example.com...
            ```

            Filter by database isolation:
            ```bash
            $ python manage.py showtenants --isolation-type=database
            Found 1 tenant(s):

            Tenant ID        Name                           Isolation       Domain...
            acme             ACME Corporation               DATABASE        acme.example.com...
            ```

            JSON output:
            ```bash
            $ python manage.py showtenants --format=json
            [
              {
                "id": 1,
                "tenant_id": "acme",
                ...
              }
            ]
            ```

        Error Handling:

            Case 1: Invalid isolation type
            ```bash
            $ python manage.py showtenants --isolation-type=invalid
            Invalid isolation type. Valid options: DATABASE, SCHEMA, TABLE
            ```

            Case 2: No tenants found (filtered)
            ```bash
            $ python manage.py showtenants --isolation-type=database
            # (when no database-isolated tenants exist)
            No tenants found.
            ```

            Case 3: No tenants in system
            ```bash
            $ python manage.py showtenants
            No tenants found.
            ```

        Notes:
            - Isolation type validation prevents invalid filters
            - Empty result handled gracefully (warning message shown)
            - Format-specific methods handle remaining output logic
            - Case handling for isolation type (accepts lowercase, converts to uppercase)
            - Each output format has dedicated helper method

        Integration Points:
            - Calls get_tenant_model(): Gets configured Tenant model
            - Accesses BaseTenant.IsolationType.choices: Valid isolation types
            - Delegates to _output_table/json/csv: Format-specific logic
            - Uses self.stdout: Django's command output stream
            - Uses self.style: Django's output formatting (SUCCESS, WARNING, ERROR)
        """
        # Get the Tenant model class (can be customized via settings)
        TenantModel = get_tenant_model()

        # Query all tenants from database
        tenants = TenantModel.objects.all()

        # Apply isolation_type filter if provided by user
        isolation_type = options.get("isolation_type")
        if isolation_type:
            # Convert to uppercase to match database choice values
            isolation_type_upper = isolation_type.upper()

            # Get valid isolation type values from BaseTenant model
            valid_types = {choice[0] for choice in BaseTenant.IsolationType.choices}

            # Validate isolation type is valid
            if isolation_type_upper in valid_types:
                # Filter tenants to only those matching this isolation type
                tenants = tenants.filter(isolation_type=isolation_type_upper)
            else:
                # Show error and exit (don't proceed with invalid filter)
                self.stdout.write(self.style.ERROR(f"Invalid isolation type. Valid options: {', '.join(valid_types)}"))
                return

        # Check if any tenants match the query (filtered or all)
        if not tenants.exists():
            self.stdout.write(self.style.WARNING("No tenants found."))
            return

        # Get requested output format (default is 'table')
        output_format = options.get("format")

        # Delegate to format-specific output method
        if output_format == "json":
            self._output_json(tenants)
        elif output_format == "csv":
            self._output_csv(tenants)
        else:
            # Default: table format
            self._output_table(tenants)

    def _output_table(self, tenants):
        """
        Display tenants in a human-readable formatted table.

        This method outputs tenants in a formatted table with columns for:
        Tenant ID, Name, Isolation Type, Domain, Created Date, and Database Config.

        The table uses ASCII separators, aligned columns, and Django's style formatting
        for colored output. Database configuration (if available) is shown on a
        separate indented line under each tenant.

        Arguments:
            tenants (QuerySet): Queryset of Tenant objects to display.

        Returns:
            None: Outputs directly to self.stdout.

        Examples:
            Output with 2 tenants:
            ```
            Found 2 tenant(s):

            Tenant ID        Name                           Isolation       Domain                         Created
            ────────────────────────────────────────────────────────────────────────────────────────────────────────
            acme             ACME Corporation               DATABASE        acme.example.com               2024-01-15 10:30:45
                     └─ Database: acme_db @ localhost:5432
            beta             Beta Corp                      SCHEMA          beta.example.com               2024-01-20 14:20:10
                     └─ Database: shared_db @ localhost:5432
            ```
        """
        # Display count and blank line
        self.stdout.write(self.style.SUCCESS(f"\\nFound {tenants.count()} tenant(s):\\n"))

        # Create and display header line
        header = f"{'Tenant ID':<20} {'Name':<30} {'Isolation':<15} {'Domain':<30} {'Created':<20}"
        self.stdout.write(self.style.SUCCESS(header))

        # Display separator line (dashes matching header length)
        self.stdout.write(self.style.SUCCESS("-" * len(header)))

        # Iterate through each tenant and display row
        for tenant in tenants:
            # Format created date (handle missing attribute gracefully)
            created = tenant.created_at.strftime("%Y-%m-%d %H:%M:%S") if hasattr(tenant, "created_at") else "N/A"

            # Get isolation type display name (e.g., 'DATABASE' -> 'Database')
            isolation_display = (
                tenant.get_isolation_type_display()
                if hasattr(tenant, "get_isolation_type_display")
                else tenant.isolation_type
            )

            domain_display = "Not Set"
            if hasattr(tenant, "domain") and tenant.domain:
                domain_display = getattr(tenant.domain, "domain", str(tenant.domain))

            row = (
                f"{tenant.tenant_id:<20} {tenant.name:<30} {isolation_display:<15} {domain_display:<30} {created:<20}"
            )
            self.stdout.write(row)

            # If database config exists, show it on indented line below tenant
            if tenant.config and tenant.config.get("db_config"):
                db_config = tenant.config["db_config"]
                # Show database name if available
                if db_config.get("NAME"):
                    self.stdout.write(
                        self.style.WARNING(
                            f"           └─ Database: {db_config.get('NAME')} @ "
                            f"{db_config.get('HOST')}:{db_config.get('PORT')}"
                        )
                    )
            # Add blank line after each tenant for readability
            self.stdout.write("")

    def _output_json(self, tenants):
        """
        Display tenants in JSON format.

        This method outputs all tenants as a JSON array, with each tenant as an object
        containing id, tenant_id, name, isolation_type, config, and timestamps.
        Suitable for integration with other tools, scripts, or APIs.

        Arguments:
            tenants (QuerySet): Queryset of Tenant objects to display.

        Returns:
            None: Outputs JSON directly to self.stdout.

        Examples:
            Output:
            ```json
            [
              {
                "id": 1,
                "tenant_id": "acme",
                "name": "ACME Corporation",
                "isolation_type": "DATABASE",
                "config": {"db_config": {...}},
                "created_at": "2024-01-15T10:30:45"
              },
              {
                "id": 2,
                "tenant_id": "beta",
                "name": "Beta Corp",
                "isolation_type": "SCHEMA",
                "config": {...},
                "created_at": "2024-01-20T14:20:10"
              }
            ]
            ```
        """
        import json

        # Build list of tenant dictionaries
        tenant_list = []
        for tenant in tenants:
            # Create base tenant data dictionary
            tenant_data = {
                "id": tenant.id,
                "tenant_id": tenant.tenant_id,
                "name": tenant.name,
                "isolation_type": tenant.isolation_type,
                "config": tenant.config,
            }

            # Add timestamps if available (handle model variations)
            if hasattr(tenant, "created_at"):
                tenant_data["created_at"] = tenant.created_at.isoformat()
            if hasattr(tenant, "updated_at"):
                tenant_data["updated_at"] = tenant.updated_at.isoformat()

            tenant_list.append(tenant_data)

        # Output as formatted JSON with 2-space indentation
        self.stdout.write(json.dumps(tenant_list, indent=2))

    def _output_csv(self, tenants):
        """
        Display tenants in CSV format.

        This method outputs tenants as comma-separated values suitable for import
        into spreadsheets or data analysis tools. Includes headers and database
        configuration details for database-isolated tenants.

        Arguments:
            tenants (QuerySet): Queryset of Tenant objects to display.

        Returns:
            None: Outputs CSV directly to self.stdout.

        Examples:
            Output:
            ```
            ID,Tenant ID,Name,Isolation Type,Created At,DB Name,DB Host
            1,acme,ACME Corporation,DATABASE,2024-01-15T10:30:45,acme_db,localhost
            2,beta,Beta Corp,SCHEMA,2024-01-20T14:20:10,shared_db,localhost
            3,gamma,Gamma Industries,TABLE,2024-02-01T09:15:33,shared_db,localhost
            ```
        """
        import csv
        import sys

        # Create CSV writer for stdout
        writer = csv.writer(sys.stdout)

        # Write header row
        writer.writerow(["ID", "Tenant ID", "Name", "Isolation Type", "Created At", "DB Name", "DB Host"])

        # Write data rows for each tenant
        for tenant in tenants:
            # Format created timestamp (ISO format if available)
            created = tenant.created_at.isoformat() if hasattr(tenant, "created_at") else ""

            # Extract database configuration if available
            db_name = ""
            db_host = ""

            if tenant.config and tenant.config.get("db_config"):
                db_config = tenant.config["db_config"]
                db_name = db_config.get("NAME", "")
                db_host = db_config.get("HOST", "")

            # Write row to CSV
            writer.writerow(
                [tenant.id, tenant.tenant_id, tenant.name, tenant.isolation_type, created, db_name, db_host]
            )
