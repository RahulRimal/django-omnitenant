from importlib import import_module

from django.core.exceptions import ImproperlyConfigured
from django.db.models import Model

from .conf import settings
from .constants import constants
from .utils import get_tenant_model


class _BootStrapper:
    """
    Bootstrap orchestrator for django-omnitenant initialization.

    This class handles all aspects of application startup validation and configuration:
    1. Parsing configuration settings
    2. Validating required configuration
    3. Loading patches (optional extension modules)

    The bootstrapper is called during Django app initialization to catch
    configuration errors early and provide helpful error messages.

    Attributes:
        _patches (list[str]): List of patch module paths to load

    Lifecycle:
        1. __init__: Initialize with default patches
        2. _parse: Extract configuration from settings
        3. _run_validation: Validate all required settings and models
        4. _run_patches: Dynamically import and apply patches
        5. run: Orchestrate all steps

    Error Handling:
        All validation errors raise ImproperlyConfigured with helpful messages
        that include configuration examples and suggestions.
    """

    def __init__(self):
        """
        Initialize the bootstrapper with default patches.

        Sets up the default list of patches to be applied. These are the
        built-in patches that extend django-omnitenant functionality.

        Default Patches:
            - django_omnitenant.patches.cache: Patches for cache backends
            - django_omnitenant.patches.celery: Celery task integration

        Additional Patches:
            Custom patches can be added via OMNITENANT_CONFIG['PATCHES']
            in Django settings.

        Example:
            ```python
            OMNITENANT_CONFIG = {
                'PATCHES': [
                    'myapp.patches.custom_cache',
                    'myapp.patches.custom_signals',
                ]
            }
            ```
        """
        # Initialize with built-in patches
        # These are always applied unless explicitly removed
        self._patches: list[str] = [
            "django_omnitenant.patches.cache",
            "django_omnitenant.patches.celery",
            "django_omnitenant.patches.settings",
        ]

    def _parse(self):
        """
        Parse configuration to extract patches and validate structure.

        This method:
        1. Reads PATCHES from OMNITENANT_CONFIG (or uses defaults)
        2. Validates that PATCHES is a list or tuple
        3. Extends the patches list with user-provided patches
        4. Converts tuple to list if needed (for consistency)

        Configuration Key:
            Uses constant: constants.PATCHES = "PATCHES"
            Location: OMNITENANT_CONFIG['PATCHES']

        Validation:
            - PATCHES must be a list or tuple (not dict, set, string, etc.)
            - If not provided, uses built-in patches only
            - Tuples are automatically converted to lists

        Raises:
            ImproperlyConfigured: If PATCHES is not a list or tuple

        Example:
            ```python
            OMNITENANT_CONFIG = {
                'PATCHES': [
                    'myapp.patches.custom',
                ]
            }
            # Results in patches = [
            #     'django_omnitenant.patches.cache',
            #     'django_omnitenant.patches.celery',
            #     'myapp.patches.custom',
            # ]
            ```

        Error Example:
            ```python
            OMNITENANT_CONFIG = {
                'PATCHES': 'myapp.patches.custom'  # Wrong: string instead of list
            }
            # Raises: ImproperlyConfigured with clear error message
            ```
        """
        # Get patches from configuration, or use built-in patches if not specified
        patches = settings.OMNITENANT_CONFIG.get(constants.PATCHES, self._patches)

        # Validate that patches is a list or tuple
        if not isinstance(patches, list) and not isinstance(patches, tuple):
            raise ImproperlyConfigured(
                f"OMNITENANT_CONFIG['{constants.PATCHES}'] must be a list of patch module paths."
            )

        # Convert tuple to list for consistent handling
        if isinstance(patches, tuple):
            patches = list(patches)

        # Extend the patches list with user-provided patches
        # This keeps built-in patches and adds custom ones
        self._patches.extend(patches)

    def _run_validation(self) -> None:
        """
        Validate all required configuration and models.

        This method performs comprehensive validation:
        1. Checks TENANT_MODEL is defined
        2. Verifies TENANT_MODEL references a valid Django model
        3. Verifies the model is a proper Model subclass
        4. Checks PUBLIC_HOST is defined (required for middleware)

        Validations:
            TENANT_MODEL:
            - Must be defined in OMNITENANT_CONFIG
            - Must be in "app_label.ModelName" format
            - Model must exist in installed apps
            - Must be a subclass of django.db.models.Model

            PUBLIC_HOST:
            - Must be defined in OMNITENANT_CONFIG
            - Used by middleware for fallback tenant resolution
            - Should be the main application domain

        Raises:
            ImproperlyConfigured: For any validation failure with helpful message

        Error Messages:
            Each error includes:
            - What is missing or invalid
            - Configuration key to fix
            - Example configuration
            - Suggestions for resolution

        Examples:
            ```python
            # Valid configuration
            OMNITENANT_CONFIG = {
                'TENANT_MODEL': 'myapp.Tenant',
                'PUBLIC_HOST': 'example.com',
            }
            # Validation passes

            # Invalid: Missing TENANT_MODEL
            OMNITENANT_CONFIG = {
                'PUBLIC_HOST': 'example.com',
            }
            # Raises: ImproperlyConfigured with example showing TENANT_MODEL

            # Invalid: TENANT_MODEL references non-existent model
            OMNITENANT_CONFIG = {
                'TENANT_MODEL': 'nonexistent.Tenant',
                'PUBLIC_HOST': 'example.com',
            }
            # Raises: ImproperlyConfigured with model lookup error

            # Invalid: PUBLIC_HOST missing
            OMNITENANT_CONFIG = {
                'TENANT_MODEL': 'myapp.Tenant',
            }
            # Raises: ImproperlyConfigured with example showing PUBLIC_HOST
            ```
        """
        # Step 1: Validate TENANT_MODEL is configured
        tenant_model_path: str = settings.OMNITENANT_CONFIG.get(constants.TENANT_MODEL, "")
        if not tenant_model_path:
            raise ImproperlyConfigured(
                f"OMNITENANT_CONFIG must define '{constants.TENANT_MODEL}'. Example:\n"
                f"OMNITENANT_CONFIG = {{ '{constants.TENANT_MODEL}': 'myapp.Tenant' }}"
            )

        # Step 2: Try to load the TENANT_MODEL to verify it exists
        try:
            model = get_tenant_model()
        except LookupError:
            # Model could not be found in installed apps
            raise ImproperlyConfigured(
                f"Could not find tenant model '{tenant_model_path}'. Check your OMNITENANT_CONFIG in settings.py."
            )

        # Step 3: Verify the tenant model is actually a Django Model
        if not issubclass(model, Model):
            raise ImproperlyConfigured(f"{tenant_model_path} is not a valid Django model.")

        # Step 4: Validate PUBLIC_HOST is configured
        public_host: str = settings.OMNITENANT_CONFIG.get(constants.PUBLIC_HOST, "")
        if not public_host:
            raise ImproperlyConfigured(
                f"OMNITENANT_CONFIG must define '{constants.PUBLIC_HOST}'. Example:\n"
                f"OMNITENANT_CONFIG = {{ '{constants.PUBLIC_HOST}': 'localhost' }}"
            )

    def _run_patches(self):
        """
        Dynamically import and apply all configured patches.

        This method iterates through all patches (built-in and custom) and
        imports them. Importing patches allows them to run module-level code
        to register signals, patch Django components, etc.

        Patches:
            Patches are regular Python modules that modify django-omnitenant
            or Django behavior when imported. They typically:
            - Register signal handlers
            - Monkey-patch Django classes or functions
            - Register custom cache backends
            - Configure Celery tasks
            - Initialize third-party integrations

        Patch Modules:
            Built-in patches:
            - django_omnitenant.patches.cache: Cache backend handling
            - django_omnitenant.patches.celery: Celery integration

            Custom patches can be added via configuration:
            ```python
            OMNITENANT_CONFIG = {
                'PATCHES': [
                    'myapp.patches.signals',
                    'myapp.patches.celery',
                ]
            }
            ```

        Error Handling:
            If a patch fails to import:
            - Provides the patch module path in error message
            - Includes the underlying exception for debugging
            - Prevents application startup (fail fast)

        Raises:
            Exception: If any patch fails to import with detailed error message

        Examples:
            ```python
            # Successful patch import
            # In django_omnitenant/patches/cache.py
            from django.core.signals import request_finished
            from django_omnitenant.signals import tenant_deactivated

            def cleanup_tenant_cache(sender, tenant, **kwargs):
                # Custom cache cleanup logic
                pass

            tenant_deactivated.connect(cleanup_tenant_cache)

            # When _run_patches() imports this module, the signal handler is registered

            # Failed patch import
            OMNITENANT_CONFIG = {
                'PATCHES': ['myapp.patches.broken'],  # Module doesn't exist
            }
            # Raises: Exception with message about which patch failed
            ```
        """
        # Iterate through all patches and import them
        for patch in self._patches:
            try:
                # Dynamically import the patch module
                # This causes any module-level code to execute
                import_module(patch)
            except Exception as e:
                # If import fails, provide helpful error message
                raise Exception(
                    "Unable to import patch module {patch} due to: {exc_info}".format(patch=patch, exc_info=e)
                )

    def run(self):
        """
        Execute the complete bootstrap process.

        This method orchestrates all bootstrap steps in the correct order:
        1. Parse configuration and extract patches
        2. Validate all required settings and models
        3. Import and apply all patches

        Execution Order:
            The specific order is important:
            1. _parse() first to get configuration
            2. _run_validation() second to check for errors early
            3. _run_patches() last after validation passes

            This ensures patches run in a validated environment.

        Error Handling:
            If any step raises an exception:
            - The entire bootstrap process fails
            - Django initialization is aborted
            - Developer gets clear error message
            - Application does not start (fail fast)

        Called By:
            This method is called by Django's app ready() method during
            application initialization.

        Example Flow:
            ```python
            # During Django startup
            django.setup()
            # -> app_bootstrapper.run()  (called from apps.py ready())
            # -> _parse()                (extract patches from config)
            # -> _run_validation()       (verify config is correct)
            # -> _run_patches()          (apply patches)
            # -> Application ready
            ```

        Configuration Requirements:
            Before calling run(), ensure:
            - OMNITENANT_CONFIG is set in Django settings
            - TENANT_MODEL is defined and points to valid model
            - PUBLIC_HOST is defined for middleware
            - All patch modules are importable (if custom patches used)
        """
        # Step 1: Parse OMNITENANT_CONFIG to extract patches
        self._parse()

        # Step 2: Validate all required settings and models
        # If validation fails, raises ImproperlyConfigured and stops here
        self._run_validation()

        # Step 3: Import all patches (now that configuration is validated)
        # Patches run module-level code to extend functionality
        self._run_patches()


app_bootstrapper = _BootStrapper()
"""
Singleton instance of _BootStrapper.

This module-level instance is used by Django's app configuration
(in apps.py) to perform initialization during Django startup.

Lifecycle:
    1. Django loads apps during setup()
    2. app_bootstrapper is created (this instance)
    3. Django calls AppConfig.ready()
    4. AppConfig.ready() calls app_bootstrapper.run()
    5. Bootstrap validation and patch loading happens
    6. If successful, application continues
    7. If failed, Django aborts with ImproperlyConfigured

Usage:
    Automatically called by Django's app system. No manual invocation needed.
    
    For testing, you might create separate instances:
    ```python
    from django_omnitenant.bootstrap import _BootStrapper
    
    bootstrapper = _BootStrapper()
    bootstrapper.run()  # For testing configuration
    ```

Related:
    - apps.py: Django app configuration that calls run()
    - conf.py: Configuration reading
"""
