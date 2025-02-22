import unittest
import os
from web.services.route_analysis import RouteAnalysisService
from web.config import settings
import gpxpy

class TestRouteAnalysis(unittest.TestCase):
    def setUp(self):
        # Create a simple GPX file for testing
        self.test_gpx_content = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="Test">
  <trk>
    <name>Test Route</name>
    <trkseg>
      <trkpt lat="37.7749" lon="-122.4194">
        <ele>10.0</ele>
      </trkpt>
      <trkpt lat="37.7750" lon="-122.4195">
        <ele>15.0</ele>
      </trkpt>
      <trkpt lat="37.7751" lon="-122.4196">
        <ele>12.0</ele>
      </trkpt>
    </trkseg>
  </trk>
</gpx>"""
        self.test_gpx_file = "test_route.gpx"
        with open(os.path.join(settings.UPLOAD_FOLDER, self.test_gpx_file), 'w') as f:
            f.write(self.test_gpx_content)

    def tearDown(self):
        # Clean up test file
        test_file_path = os.path.join(settings.UPLOAD_FOLDER, self.test_gpx_file)
        if os.path.exists(test_file_path):
            os.remove(test_file_path)

    def test_analyze_route(self):
        # Test basic route analysis
        summary = RouteAnalysisService.analyze_route(self.test_gpx_file)
        
        # Verify summary is not None
        self.assertIsNotNone(summary, "Route analysis summary should not be None")
        
        # Verify elevation calculations
        self.assertIn('elevation', summary, "Summary should contain elevation data")
        elevation = summary['elevation']
        self.assertIsNotNone(elevation['gain_meters'], "Elevation gain should not be None")
        self.assertIsNotNone(elevation['loss_meters'], "Elevation loss should not be None")
        self.assertIsNotNone(elevation['net_meters'], "Net elevation should not be None")
        
        # Print detailed summary for debugging
        print("\nRoute Analysis Summary:")
        print(f"Distance: {summary['distance']}")
        print(f"Elevation: {summary['elevation']}")
        print(f"Hilliness: {summary['hilliness']}")
        print(f"Estimated Time: {summary['estimated_time']}")
        print(f"Safety: {summary['safety']}")
        print(f"Alerts: {summary['alerts']}")

if __name__ == '__main__':
    unittest.main() 