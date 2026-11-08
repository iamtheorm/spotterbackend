"""
URL routes for the fuel_routing application.
"""
from django.urls import path
from fuel_routing.views import RouteFuelAPIView, HealthView, LocationSearchAPIView, map_view

urlpatterns = [
    path("api/route/", RouteFuelAPIView.as_view(), name="api-route"),
    path("api/health/", HealthView.as_view(), name="api-health"),
    path("api/locations/search/", LocationSearchAPIView.as_view(), name="api-location-search"),
    path("map/", map_view, name="map-view"),
    path("", map_view, name="index-view"),
]

