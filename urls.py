from django.urls import path
from . import views

app_name = "timespace"

urlpatterns = [
    path("", views.map_view, name="map"),
    path("events/", views.event_list, name="event_list"),
    path("events/<slug:slug>/", views.event_detail, name="event_detail"),
    path("api/nearby/", views.api_nearby, name="api_nearby"),
]
