"""
Serializers for the fuel routing API request and response data.
"""
from rest_framework import serializers

class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(
        required=True,
        help_text="Start location within the USA (e.g. 'New York, NY' or '40.7128,-74.0060')"
    )
    finish = serializers.CharField(
        required=True,
        help_text="Finish location within the USA (e.g. 'Los Angeles, CA' or '34.0522,-118.2437')"
    )
    mpg = serializers.FloatField(
        required=False,
        default=10.0,
        min_value=1.0,
        help_text="Vehicle fuel efficiency in miles per gallon (default: 10 MPG)"
    )
    max_range = serializers.FloatField(
        required=False,
        default=500.0,
        min_value=50.0,
        help_text="Maximum vehicle range in miles on a full tank (default: 500 miles)"
    )
    buffer_miles = serializers.FloatField(
        required=False,
        default=10.0,
        min_value=1.0,
        max_value=50.0,
        help_text="Corridor search buffer radius in miles off the route (default: 10.0 miles)"
    )
    initial_fuel_miles = serializers.FloatField(
        required=False,
        default=500.0,
        min_value=0.0,
        help_text="Starting fuel range available in the vehicle (default: 500.0 miles)"
    )
    fuel_policy = serializers.ChoiceField(
        choices=["standard", "replace_consumed"],
        default="standard",
        required=False,
        help_text="'standard' buys fuel needed to reach destination; 'replace_consumed' ends with full tank"
    )

