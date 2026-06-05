from django.conf import settings


def base_template(request):
    """Inject the correct base template depending on standalone vs embedded mode."""
    standalone = getattr(settings, "TIMESPACE_STANDALONE", False)
    return {
        "base_template": "base.html" if standalone else "guide/base.html",
    }
