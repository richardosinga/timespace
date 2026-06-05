from django.urls import include, path

urlpatterns = [
    path("", include("timespace.urls")),
]
