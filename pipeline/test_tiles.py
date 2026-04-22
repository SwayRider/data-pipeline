"""Unit tests for tiles.py"""

import json
import os
import tempfile
from unittest.mock import mock_open, patch
import pytest

from tiles import add_road_zorder


class TestAddRoadZorder:
    """Test suite for add_road_zorder function"""

    def test_valid_geojson_with_various_highway_types(self):
        """Test that z_order is correctly assigned to various highway types"""
        # Arrange
        geojson_data = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"highway": "motorway", "name": "A1"},
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
                },
                {
                    "type": "Feature",
                    "properties": {"highway": "trunk", "name": "B1"},
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
                },
                {
                    "type": "Feature",
                    "properties": {"highway": "primary", "name": "C1"},
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
                },
                {
                    "type": "Feature",
                    "properties": {"highway": "motorway_link", "name": "A1 Link"},
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
                },
                {
                    "type": "Feature",
                    "properties": {"highway": "service", "name": "Service Road"},
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
                },
            ]
        }

        expected_z_orders = {
            0: 100,  # motorway
            1: 90,   # trunk
            2: 80,   # primary
            3: 30,   # motorway_link
            4: 5,    # service
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False) as f:
            json.dump(geojson_data, f)
            temp_file = f.name

        try:
            # Act
            result = add_road_zorder(temp_file)

            # Assert
            assert result is True

            with open(temp_file, 'r') as f:
                modified_geojson = json.load(f)

            for idx, expected_z_order in expected_z_orders.items():
                actual_z_order = modified_geojson['features'][idx]['properties']['z_order']
                assert actual_z_order == expected_z_order, \
                    f"Feature {idx}: expected z_order={expected_z_order}, got {actual_z_order}"

        finally:
            os.unlink(temp_file)

    def test_unknown_highway_types_use_default(self):
        """Test that unknown highway types receive default z_order=10"""
        # Arrange
        geojson_data = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"highway": "unknown_type", "name": "Unknown Road"},
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
                },
                {
                    "type": "Feature",
                    "properties": {"highway": "footway", "name": "Footway"},
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
                },
                {
                    "type": "Feature",
                    "properties": {"highway": "cycleway", "name": "Cycleway"},
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
                },
            ]
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False) as f:
            json.dump(geojson_data, f)
            temp_file = f.name

        try:
            # Act
            result = add_road_zorder(temp_file)

            # Assert
            assert result is True

            with open(temp_file, 'r') as f:
                modified_geojson = json.load(f)

            for feature in modified_geojson['features']:
                assert feature['properties']['z_order'] == 10, \
                    f"Unknown highway type should have z_order=10, got {feature['properties']['z_order']}"

        finally:
            os.unlink(temp_file)

    def test_missing_highway_property(self):
        """Test that features without highway property receive default z_order=10"""
        # Arrange
        geojson_data = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": "Road without highway tag"},
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
                },
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
                },
            ]
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False) as f:
            json.dump(geojson_data, f)
            temp_file = f.name

        try:
            # Act
            result = add_road_zorder(temp_file)

            # Assert
            assert result is True

            with open(temp_file, 'r') as f:
                modified_geojson = json.load(f)

            for feature in modified_geojson['features']:
                assert feature['properties']['z_order'] == 10, \
                    f"Missing highway property should have z_order=10, got {feature['properties']['z_order']}"

        finally:
            os.unlink(temp_file)

    def test_empty_geojson_no_features(self):
        """Test that empty GeoJSON (no features) is handled correctly"""
        # Arrange
        geojson_data = {
            "type": "FeatureCollection",
            "features": []
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False) as f:
            json.dump(geojson_data, f)
            temp_file = f.name

        try:
            # Act
            result = add_road_zorder(temp_file)

            # Assert
            assert result is True

            with open(temp_file, 'r') as f:
                modified_geojson = json.load(f)

            assert modified_geojson['features'] == []

        finally:
            os.unlink(temp_file)

    def test_geojson_missing_features_key(self):
        """Test that GeoJSON without 'features' key is handled correctly"""
        # Arrange
        geojson_data = {
            "type": "FeatureCollection"
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False) as f:
            json.dump(geojson_data, f)
            temp_file = f.name

        try:
            # Act
            result = add_road_zorder(temp_file)

            # Assert
            assert result is True

            with open(temp_file, 'r') as f:
                modified_geojson = json.load(f)

            # features key should still not exist
            assert 'features' not in modified_geojson

        finally:
            os.unlink(temp_file)

    def test_file_not_found_error(self):
        """Test that FileNotFoundError is handled and returns False"""
        # Arrange
        non_existent_file = "/tmp/does_not_exist_12345.geojson"

        # Act
        with patch('builtins.print') as mock_print:
            result = add_road_zorder(non_existent_file)

        # Assert
        assert result is False
        mock_print.assert_called_once()
        assert "Error adding z_order" in mock_print.call_args[0][0]

    def test_invalid_json_error(self):
        """Test that invalid JSON is handled and returns False"""
        # Arrange
        with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False) as f:
            f.write("invalid json content {{{")
            temp_file = f.name

        try:
            # Act
            with patch('builtins.print') as mock_print:
                result = add_road_zorder(temp_file)

            # Assert
            assert result is False
            mock_print.assert_called_once()
            assert "Error adding z_order" in mock_print.call_args[0][0]

        finally:
            os.unlink(temp_file)

    def test_permission_error_on_write(self):
        """Test that write permission errors are handled and return False"""
        # Arrange
        geojson_data = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"highway": "motorway"},
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
                }
            ]
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False) as f:
            json.dump(geojson_data, f)
            temp_file = f.name

        try:
            # Make file read-only
            os.chmod(temp_file, 0o444)

            # Act
            with patch('builtins.print') as mock_print:
                result = add_road_zorder(temp_file)

            # Assert
            assert result is False
            mock_print.assert_called_once()
            assert "Error adding z_order" in mock_print.call_args[0][0]

        finally:
            # Restore write permissions before cleanup
            os.chmod(temp_file, 0o644)
            os.unlink(temp_file)

    def test_all_highway_types_in_map(self):
        """Test that all highway types defined in z_order_map are correctly assigned"""
        # Arrange - Test all highway types from the z_order_map
        highway_types = {
            'motorway': 100,
            'trunk': 90,
            'primary': 80,
            'secondary': 70,
            'tertiary': 60,
            'unclassified': 50,
            'residential': 40,
            'motorway_link': 30,
            'trunk_link': 25,
            'primary_link': 20,
            'secondary_link': 15,
            'tertiary_link': 10,
            'service': 5,
            'track': 3,
            'path': 1,
        }

        features = []
        for highway_type in highway_types.keys():
            features.append({
                "type": "Feature",
                "properties": {"highway": highway_type, "name": f"{highway_type} road"},
                "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
            })

        geojson_data = {
            "type": "FeatureCollection",
            "features": features
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False) as f:
            json.dump(geojson_data, f)
            temp_file = f.name

        try:
            # Act
            result = add_road_zorder(temp_file)

            # Assert
            assert result is True

            with open(temp_file, 'r') as f:
                modified_geojson = json.load(f)

            for idx, (highway_type, expected_z_order) in enumerate(highway_types.items()):
                actual_z_order = modified_geojson['features'][idx]['properties']['z_order']
                assert actual_z_order == expected_z_order, \
                    f"{highway_type}: expected z_order={expected_z_order}, got {actual_z_order}"

        finally:
            os.unlink(temp_file)

    def test_feature_without_properties(self):
        """Test that features without properties dictionary are handled correctly"""
        # Arrange
        geojson_data = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
                }
            ]
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False) as f:
            json.dump(geojson_data, f)
            temp_file = f.name

        try:
            # Act
            result = add_road_zorder(temp_file)

            # Assert
            assert result is True

            with open(temp_file, 'r') as f:
                modified_geojson = json.load(f)

            # The function should create properties dict and add z_order
            assert 'properties' in modified_geojson['features'][0]
            assert modified_geojson['features'][0]['properties']['z_order'] == 10

        finally:
            os.unlink(temp_file)

    def test_existing_properties_are_preserved(self):
        """Test that existing properties are preserved when z_order is added"""
        # Arrange
        geojson_data = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "highway": "primary",
                        "name": "Main Street",
                        "ref": "A123",
                        "lanes": 2,
                        "custom_prop": "test_value"
                    },
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
                }
            ]
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False) as f:
            json.dump(geojson_data, f)
            temp_file = f.name

        try:
            # Act
            result = add_road_zorder(temp_file)

            # Assert
            assert result is True

            with open(temp_file, 'r') as f:
                modified_geojson = json.load(f)

            properties = modified_geojson['features'][0]['properties']
            assert properties['highway'] == 'primary'
            assert properties['name'] == 'Main Street'
            assert properties['ref'] == 'A123'
            assert properties['lanes'] == 2
            assert properties['custom_prop'] == 'test_value'
            assert properties['z_order'] == 80

        finally:
            os.unlink(temp_file)

    def test_empty_highway_value(self):
        """Test that empty highway value uses default z_order"""
        # Arrange
        geojson_data = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"highway": "", "name": "Road with empty highway"},
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
                }
            ]
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False) as f:
            json.dump(geojson_data, f)
            temp_file = f.name

        try:
            # Act
            result = add_road_zorder(temp_file)

            # Assert
            assert result is True

            with open(temp_file, 'r') as f:
                modified_geojson = json.load(f)

            assert modified_geojson['features'][0]['properties']['z_order'] == 10

        finally:
            os.unlink(temp_file)

    def test_mixed_valid_and_invalid_highways(self):
        """Test that files with both valid and invalid highway types are processed correctly"""
        # Arrange
        geojson_data = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"highway": "motorway"},
                    "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
                },
                {
                    "type": "Feature",
                    "properties": {"highway": "unknown_type"},
                    "geometry": {"type": "LineString", "coordinates": [[1, 1], [2, 2]]}
                },
                {
                    "type": "Feature",
                    "properties": {"highway": "residential"},
                    "geometry": {"type": "LineString", "coordinates": [[2, 2], [3, 3]]}
                },
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {"type": "LineString", "coordinates": [[3, 3], [4, 4]]}
                },
            ]
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False) as f:
            json.dump(geojson_data, f)
            temp_file = f.name

        try:
            # Act
            result = add_road_zorder(temp_file)

            # Assert
            assert result is True

            with open(temp_file, 'r') as f:
                modified_geojson = json.load(f)

            assert modified_geojson['features'][0]['properties']['z_order'] == 100  # motorway
            assert modified_geojson['features'][1]['properties']['z_order'] == 10   # unknown
            assert modified_geojson['features'][2]['properties']['z_order'] == 40   # residential
            assert modified_geojson['features'][3]['properties']['z_order'] == 10   # missing

        finally:
            os.unlink(temp_file)
