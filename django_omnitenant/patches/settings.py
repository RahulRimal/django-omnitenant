from django.conf import settings


def patch_django_settings():
    # Force settings to load if they haven't yet
    if not settings.configured:
        # This triggers the loading of the settings module
        _ = settings.DEBUG

    required_routers = ["django_omnitenant.routers.TenantRouter"]

    # Get current routers, ensure it's a list
    current_routers = list(getattr(settings, "DATABASE_ROUTERS", []))

    modified = False
    for router in required_routers:
        if router not in current_routers:
            current_routers.append(router)
            modified = True

    if modified:
        if hasattr(settings, "DATABASE_ROUTERS"):
            delattr(settings, "DATABASE_ROUTERS")

        setattr(settings._wrapped, "DATABASE_ROUTERS", current_routers)


patch_django_settings()
