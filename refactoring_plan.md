# OptiburB Refactoring Plan

## Overview
The current codebase has a monolithic structure in `optiburb.py` with some web service organization in `web/services/route.py` and `web/routes/route_generation.py`. This refactoring plan aims to break down the monolithic code into focused modules while maintaining compatibility with existing web services.

## Current Structure
```
optiburb/
├── optiburb.py              # Monolithic main code
├── web/
│   ├── routes/
│   │   └── route_generation.py  # HTTP endpoints
│   └── services/
│       └── route.py            # Business logic layer
```

## Proposed Structure
```
optiburb/
├── web/
│   ├── core/
│   │   ├── __init__.py
│   │   ├── graph.py           # Graph management
│   │   ├── balancing.py       # Graph balancing operations
│   │   ├── geometry.py        # Geometry operations
│   │   ├── route_generator.py # Route generation and GPX creation
│   │   └── data_loader.py     # Data loading operations
│   ├── routes/
│   │   └── route_generation.py # (existing)
│   └── services/
│       └── route.py           # (existing)
└── optiburb.py               # Main class using core modules
```

## Module Specifications

### 1. Graph Manager (`web/core/graph.py`)
```python
class GraphManager:
    def __init__(self):
        self.g = None
        self.g_working = None
        self.g_augmented = None
        self.g_original = None
        self.node_coords = {}

    def load_graph(self, polygon, options):
        # From Burbing.load()
        pass

    def prune_graph(self):
        # From Burbing.prune()
        pass

    def save_graph_visualization(self):
        # From Burbing.save_fig()
        pass
```

### 2. Graph Balancer (`web/core/balancing.py`)
```python
class GraphBalancer:
    def balance_graph(self, graph):
        # From Burbing.determine_nodes()
        pass

    def optimize_dead_ends(self, graph):
        # From Burbing.optimise_dead_ends()
        pass

    def determine_combinations(self, graph):
        # From Burbing.determine_combinations()
        pass
```

### 3. Geometry Manager (`web/core/geometry.py`)
```python
class GeometryManager:
    def create_linestring(self, path):
        # From Burbing.path_to_linestring()
        pass

    def get_directional_linestring(self, edge, linestring):
        # From Burbing.directional_linestring()
        pass

    def calculate_distance(self, point1, point2):
        # From Burbing.distance()
        pass
```

### 4. Route Generator (`web/core/route_generator.py`)
```python
class RouteGenerator:
    def determine_circuit(self, graph):
        # From Burbing.determine_circuit()
        pass

    def create_gpx_track(self, graph, circuit, simplify=False):
        # From Burbing.create_gpx_track()
        pass
```

### 5. Data Loader (`web/core/data_loader.py`)
```python
class DataLoader:
    def load_osm_data(self, location, select=1, buffer_dist=20):
        # From Burbing.get_osm_polygon()
        pass

    def load_shapefile(self, filename):
        # From Burbing.load_shapefile()
        pass
```

## Modified Burbing Class
```python
from web.core.graph import GraphManager
from web.core.balancing import GraphBalancer
from web.core.geometry import GeometryManager
from web.core.route_generator import RouteGenerator
from web.core.data_loader import DataLoader

class Burbing:
    def __init__(self):
        self.graph_manager = GraphManager()
        self.balancer = GraphBalancer()
        self.geometry = GeometryManager()
        self.route_generator = RouteGenerator()
        self.data_loader = DataLoader()
        # ... rest of initialization

    def load(self, options):
        self.graph_manager.load_graph(self.region, options)
        if options.prune:
            self.graph_manager.prune_graph()

    def determine_nodes(self):
        self.balancer.balance_graph(self.graph_manager.g_working)

    # ... other methods now delegate to appropriate modules
```

## Implementation Plan

1. **Phase 1: Setup**
   - Create new directory structure
   - Create empty module files with class definitions
   - Add necessary imports and dependencies

2. **Phase 2: Core Module Implementation**
   - Implement each core module one at a time
   - Write unit tests for each module
   - Ensure each module is fully functional before moving to the next

3. **Phase 3: Burbing Class Modification**
   - Modify Burbing class to use new modules
   - Maintain all existing public methods for compatibility
   - Update initialization to use new module instances

4. **Phase 4: Integration**
   - Update web services to use new structure
   - Ensure all existing functionality works
   - Add integration tests

5. **Phase 5: Cleanup**
   - Remove redundant code
   - Update documentation
   - Optimize imports and dependencies

## Benefits

1. **Maintainability**: Each module has a single responsibility
2. **Testability**: Easier to write unit tests for focused modules
3. **Extensibility**: New features can be added by extending specific modules
4. **Readability**: Clear separation of concerns
5. **Compatibility**: Maintains existing web service functionality

## Migration Strategy

1. Implement changes incrementally
2. Keep existing code working while refactoring
3. Use feature flags if needed for gradual rollout
4. Maintain backward compatibility throughout

## Documentation

1. Update README.md with new structure
2. Add docstrings to all new modules and methods
3. Create API documentation for each module
4. Update any existing documentation to reflect changes
