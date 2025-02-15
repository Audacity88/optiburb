#!/usr/bin/env python3.9

# this is a undirected graph version.. one-way streets and multi-edges
# are reduced, which means:

# WARNING - the resulting paths are not guaranteed to be rideable or
# safe.  You must confirm the path yourself.

import math
import time
import os
import sys
import re
import shapely
import logging
import geopandas
import osmnx
import networkx as nx
import numpy as np
import itertools
import argparse
import gpxpy
import gpxpy.gpx
import datetime

logging.basicConfig(format='%(asctime)-15s %(filename)s:%(funcName)s:%(lineno)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S', level=logging.INFO)
log = logging.getLogger(__name__)

class Burbing:

    WARNING = '''WARNING - this program does not consider the direction of one-way roads or other roads that may be not suitable for your mode of transport. You must confirm the path safe for yourself'''

    def __init__(self):

        self.g = None

        self.polygons = {}
        self.region = shapely.geometry.Polygon()
        self.name = ''
        self.start = None

        #
        # filters to roughly match those used by rendrer.earth (see
        # https://wandrer.earth/scoring )
        #
        self.custom_filter = (

            '["highway"]'

            '["area"!~"yes"]'

            #'["highway"!~"motorway|motorway_link|trunk|trunk_link|bridleway|footway|service|pedestrian|'
            '["highway"!~"motorway|motorway_link|bridleway|footway|service|pedestrian|'
            'steps|stairs|escalator|elevator|construction|proposed|demolished|escape|bus_guideway|'
            'sidewalk|crossing|bus_stop|traffic_signals|stop|give_way|milestone|platform|speed_camera|'
            'raceway|rest_area|traffic_island|services|yes|no|drain|street_lamp|razed|corridor|abandoned"]'

            '["access"!~"private|no|customers"]'

            '["bicycle"!~"dismount|use_sidepath|private|no"]'

            '["service"!~"private|parking_aisle"]'

            '["motorroad"!="yes"]'

            '["golf_cart"!~"yes|designated|private"]'

            '[!"waterway"]'

            '[!"razed"]'
        )


        log.debug('custom_filter=%s', self.custom_filter)

        # not all of these fields are used at the moment, but they
        # look like fun for the future.

        useful_tags_way = [
            'bridge', 'tunnel', 'oneway', 'lanes', 'ref', 'name', 'highway', 'maxspeed', 'service',
            'access', 'area', 'landuse', 'width', 'est_width', 'junction', 'surface',
        ]

        osmnx.settings.useful_tags_way = useful_tags_way
        osmnx.settings.use_cache = True
        osmnx.settings.log_console = True

        log.warning(self.WARNING)

        return

    ##
    ##
    def add_polygon(self, polygon, name):

        self.polygons[name] = polygon

        self.region = self.region.union(polygon)

        if self.name:
            self.name += '_'
            pass

        processed_name = name.lower()
        processed_name = re.sub(r'[\s,_]+', '_', processed_name)

        self.name += processed_name

        return

    ##
    ##
    def get_osm_polygon(self, name, select=1, buffer_dist=20):

        log.info('searching for query=%s, which_result=%s', name, select)

        # First get the coordinates
        location = osmnx.geocoder.geocode(name)
        if not location:
            raise ValueError(f"Could not find location: {name}")
            
        # Create a point and buffer it - using a larger buffer distance (500 meters)
        point = shapely.geometry.Point(location[1], location[0])  # lon, lat
        polygon = point.buffer(500 / 111000)  # Convert meters to degrees (roughly)

        return polygon

    ##
    ##
    def get_shapefile_polygon(self, shapefile, key, name):

        log.info('shapefile=%s, key=%s, name=%s', shapefile, key, name)

        df = shapefile

        suburb = df[df[key] == name]
        suburb = suburb.to_crs(epsg=4326)
        log.info('suburb=%s', suburb)

        polygon = suburb['geometry'].values[0]

        return polygon

    ##
    ##
    def set_start_location(self, addr):

        point =  osmnx.geocoder.geocode(addr)
        self.start = point
        log.info('setting start point to %s', self.start)
        return

    ##
    ##
    def find_odd_nodes(self):

        # for undirected graphs

        odd_nodes = { i for i, n in self.g.degree if n % 2 == 1 }

        return odd_nodes

    ##
    ##
    def get_pair_combinations(self, nodes):
        pairs = list(itertools.combinations(nodes, 2))
        return pairs

    ##
    ##
    def get_shortest_path_pairs(self, g, pairs):

        # XXX - consider Floyd–Warshall here instead of repeated
        # Dijkstra.  Also consider how to parallelise this as a
        # short-term speed-up, by palming off chunks to another
        # thread, except this wont work in python.

        shortest_paths = {}

        _prev_pct = 0
        _size = len(pairs)
        _prev_n = 0
        _prev_time = time.time()

        for n, pair in enumerate(pairs):
            i, j = pair
            shortest_paths[pair] = nx.dijkstra_path_length(g, i, j, weight='length')

            ## output progress

            _cur_pct = int(100 * n / _size)
            if _prev_pct != _cur_pct:
                _cur_time = time.time()
                log.info('dijkstra progress %s%%, [%d/%d] %d/second', _cur_pct, n, _size, (_prev_n - n) / (_prev_time - _cur_time))

                _prev_time = _cur_time
                _prev_pct = _cur_pct
                _prev_n = n
                pass
            pass

        return shortest_paths

    ##
    ##
    def augment_graph(self, pairs):

        # create a new graph and stuff in the new fake/virtual edges
        # between odd pairs.  Generate the edge metadata to make them
        # look similar to the native edges.

        log.info('pre augmentation eulerian=%s', nx.is_eulerian(self.g_augmented))

        for i, pair in enumerate(pairs):
            a, b = pair

            length, path = nx.single_source_dijkstra(self.g, a, b, weight='length')

            log.debug('PAIR[%s] nodes = (%s,%s), length=%s, path=%s', i, a, b, length, path)

            linestring = self.path_to_linestring(self.g_augmented, path)

            # create a linestring of paths...

            data = {
                'length': length,
                'augmented': True,
                'path': path,
                'geometry': linestring,
                'from': a,
                'to': b,
            }
            log.debug('  creating new edge (%s,%s) - data=%s', a, b, data)

            self.g_augmented.add_edge(a, b, **data)
            pass

        log.info('post augmentation eulerian=%s', nx.is_eulerian(self.g_augmented))

        return


    ##
    ##
    def print_edges(self, g):

        for edge in g.edges:
            data = g.get_edge_data(*edge, 0)

            _osmid = ','.join(data.get('osmid')) if type(data.get('osmid')) == list else str(data.get('osmid'))
            _name = ','.join(data.get('name')) if type(data.get('name')) == list else str(data.get('name'))
            _highway = data.get('highway', '-')
            _surface = data.get('surface', '-')
            _oneway = data.get('oneway', '-')
            _access = data.get('access', '-')
            log.debug(f'{_osmid:10} {_name:30} {_highway:20} {_surface:10} {_oneway:10} {_access:10}')
            pass

    ##
    ##
    def determine_nodes(self):

        log.info('converting directed graph to undirected')

        # convert to undirected graph.  this is a bit of a hack, but
        # it makes the problem simpler.  it means we can't guarantee
        # the path is rideable, but we can at least get a path that
        # covers all the roads.

        self.g_directed = self.g
        self.g = self.g_directed.to_undirected()
        
        self.print_edges(self.g)
        
        self.g_augmented = self.g.copy()
        self.odd_nodes = self.find_odd_nodes()

        return


    ##
    ##
    def optimise_dead_ends(self):

        # preempt virtual path augmentation for the case of a dead-end
        # road.  Here the optimum combination pair is its only
        # neighbour node, so why bother iterating through all the
        # pairs to find that.

        # XXX - not quite clean yet.. we are augmenting the original
        # grpah.. need a cleaner way to pass changes through the
        # processing pipeline.

        deadends = { i for i, n in self.g.degree if n == 1 }

        n1 = len(self.find_odd_nodes())

        for deadend in deadends:

            neighbours = self.g[deadend]

            #node_data = self.g.nodes[deadend]
            #log.info('deadend_ndoe=%s, data=%s', deadend, node_data)
            log.debug('deadend_node=%s', deadend)

            if len(neighbours) != 1:
                log.error('wrong number of neighbours for a dead-end street')
                continue

            for neighbour in neighbours.keys():
                log.debug('neighbour=%s', neighbour)

                edge_data = dict(self.g.get_edge_data(deadend, neighbour, 0))
                edge_data['augmented'] = True
                
                log.debug('  creating new edge (%s,%s) - data=%s', deadend, neighbour, edge_data)

                self.g.add_edge(deadend, neighbour, **edge_data)

                pass

            pass

        # fix up the stuff we just busted.  XXX - this should not be
        # hidden in here.

        self.odd_nodes = self.find_odd_nodes()
        self.g_augmented = self.g.copy()

        n2 = len(self.odd_nodes)

        log.info('odd_nodes_before=%d, odd_nodes_after=%d', n1, n2)
        log.info('optimised %d nodes out', n1 - n2)

        return

    ##
    ##
    def determine_combinations(self):

        log.info('eulerian=%s, odd_nodes=%s', nx.is_eulerian(self.g), len(self.odd_nodes))

        odd_node_pairs = self.get_pair_combinations(self.odd_nodes)

        log.info('combinations=%s', len(odd_node_pairs))

        odd_pair_paths = self.get_shortest_path_pairs(self.g, odd_node_pairs)

        # XXX - this part doesn't work well because it doesn't
        # consider the direction of the paths.

        # create a temporary graph of odd pairs.. really we should be
        # doing the combination max calculations here.

        self.g_odd_nodes = nx.Graph()

        for k, length in odd_pair_paths.items():
            i,j = k
            attrs = {
                'length': length,
                'weight': -length,
            }

            self.g_odd_nodes.add_edge(i, j, **attrs)
            pass

        log.info('new_nodes=%s, edges=%s, eulerian=%s', self.g_odd_nodes.order(), self.g_odd_nodes.size(), nx.is_eulerian(self.g_odd_nodes))

        log.info('calculating max weight matching - this can also take a while')

        return

    ##
    ##
    def determine_circuit(self):

        odd_matching = nx.algorithms.max_weight_matching(self.g_odd_nodes, True)

        log.info('augment original graph with %s pairs', len(odd_matching))

        self.augment_graph(odd_matching)

        start_node = self.get_start_node(self.g, self.start)

        self.euler_circuit = list(nx.eulerian_circuit(self.g_augmented, source=start_node))

        return

    ##
    ##
    def reverse_linestring(self, line):

        return shapely.geometry.LineString(line.coords[::-1])

    ##
    ##
    def distance(self, point1, point2):
        """Calculate the Euclidean distance between two points."""
        return math.sqrt((point1[0] - point2[0])**2 + (point1[1] - point2[1])**2)

    ##
    ##
    def directional_linestring(self, edge, linestring):
        """
        Create a directional linestring for an edge, ensuring the direction matches the edge nodes.
        Returns None if the linestring cannot be created.
        """
        u, v = edge
        try:
            # Get node coordinates
            u_coords = (self.g.nodes[u]['x'], self.g.nodes[u]['y'])
            v_coords = (self.g.nodes[u]['x'], self.g.nodes[u]['y'])
        except (KeyError, AttributeError) as e:
            log.error(f"Missing node coordinates for edge {edge}: {str(e)}")
            return None

        try:
            # Get linestring coordinates
            coords = list(linestring.coords)
            if len(coords) < 2:
                log.error(f"Linestring for edge {edge} has fewer than 2 coordinates")
                return None

            # Get start and end points of the linestring
            start_point = coords[0]
            end_point = coords[-1]

            # Calculate distances to determine if we need to reverse
            start_dist_to_u = self.distance(start_point, u_coords)
            start_dist_to_v = self.distance(start_point, v_coords)
            end_dist_to_u = self.distance(end_point, u_coords)
            end_dist_to_v = self.distance(end_point, v_coords)

            # Use a small tolerance for coordinate matching
            tolerance = 1e-5

            # Check if linestring needs to be reversed
            needs_reverse = False
            if abs(start_dist_to_v) < tolerance and abs(end_dist_to_u) < tolerance:
                needs_reverse = True
            elif abs(start_dist_to_u) >= tolerance and abs(end_dist_to_v) >= tolerance:
                # If neither end matches exactly, use the closest points
                if start_dist_to_v + end_dist_to_u < start_dist_to_u + end_dist_to_v:
                    needs_reverse = True

            if needs_reverse:
                log.debug(f"Reversing linestring for edge {edge}")
                coords = coords[::-1]

            return coords

        except (AttributeError, IndexError) as e:
            log.error(f"Error processing linestring for edge {edge}: {str(e)}")
            return None

    ##
    ##
    def get_start_node(self, g, start_addr):

        if start_addr:
            (start_node, distance) = osmnx.distance.get_nearest_node(g, start_addr, return_dist=True)
            log.info('start_node=%s, distance=%s', start_node, distance)
        else:
            start_node = None
            pass

        return start_node

    ##
    ##
    def path_to_linestring(self, g, path):
        # this creates a new linestring that follows the path of the
        # augmented route between two odd nodes.  this is needed to
        # force a path with the final GPX route, rather than drawing a
        # straight line between the two odd nodes and hoping some
        # other program route the same way we wanted to.

        if not path or len(path) < 2:
            log.error('Invalid path provided: must contain at least 2 nodes')
            return None

        coords = []
        prev = None
        u = path[0]

        # First, try to get coordinates for all nodes in the path
        node_coords = {}
        for node in path:
            try:
                node_coords[node] = (g.nodes[node]['x'], g.nodes[node]['y'])
            except (KeyError, AttributeError) as e:
                log.error(f"Missing coordinates for node {node}: {str(e)}")
                return None

        for v in path[1:]:
            edge = (u, v)
            log.debug('Processing edge=%s', edge)

            # Get edge data
            edge_data = g.get_edge_data(u, v, 0)
            if edge_data is None:
                log.debug(f"No edge data for edge={edge}, using straight line")
                directional_linestring = [node_coords[u], node_coords[v]]
            else:
                # Try to get geometry from edge data
                linestring = edge_data.get('geometry')
                if linestring is not None:
                    directional_linestring = self.directional_linestring(edge, linestring)
                    if directional_linestring is None:
                        log.debug(f"Failed to get directional linestring for edge={edge}, using straight line")
                        directional_linestring = [node_coords[u], node_coords[v]]
                else:
                    log.debug(f"No geometry data for edge={edge}, using straight line")
                    directional_linestring = [node_coords[u], node_coords[v]]

            # Add coordinates to the path
            if directional_linestring:
                for c in directional_linestring:
                    if c == prev:
                        continue
                    coords.append(c)
                    prev = c
            else:
                log.error(f"No valid directional_linestring for edge={edge}")
                return None

            u = v

        if not coords:
            log.error('No valid coordinates found for path')
            return None

        try:
            return shapely.geometry.LineString(coords)
        except (ValueError, TypeError) as e:
            log.error(f"Failed to create LineString from coordinates: {str(e)}")
            return None

    ##
    ##
    def prune(self):

        # eliminate edges with unnamed tracks.  At least where I live,
        # these tend to be 4wd tracks that require a mountain bike to
        # navigate.  probably need to do a better fitler that looks at
        # surface type and other aspects.

        remove_types = ('track', 'path')

        removeset = set()
        for edge in self.g.edges:
            data = self.g.get_edge_data(*edge)

            if data.get('highway') in remove_types and data.get('name') is None:
                log.debug('removing edge %s, %s', edge, data)
                removeset.add(edge)
                pass

            if data.get('highway') in ('cycleway',):
                log.debug('removing edge %s, %s', edge, data)
                removeset.add(edge)
                pass
            pass

        for edge in removeset:
            self.g.remove_edge(*edge)
            pass

        # this removes the isolated nodes orphaned from the removed
        # edges above.  It does not solve the problem of a
        # non-connected graph (ie, nodes and edges in a blob that
        # aren't reachable to other parts of the graph)

        self.g = osmnx.utils_graph.remove_isolated_nodes(self.g)
        return

    ##
    ##
    def save_fig(self):

        filename = f'burb_nodes_{self.name}.svg'

        log.info('saving SVG node file as %s', filename)

        nc = ['red' if node in self.odd_nodes else 'blue' for node in self.g.nodes() ]

        fig, ax = osmnx.plot_graph(self.g, show=False, save=True, node_color=nc, filepath=filename)

        return

    ##
    ##
    def load(self, options):

        log.info('fetching OSM data bounded by polygon')
        self.g = osmnx.graph_from_polygon(self.region, network_type='bike', simplify=False, custom_filter=self.custom_filter)

        log.debug('original g=%s, g=%s', self.g, type(self.g))
        log.info('original nodes=%s, edges=%s', self.g.order(), self.g.size())

        if options.simplify:
            log.info('simplifying graph')
            self.g = osmnx.simplification.simplify_graph(self.g, strict=False, remove_rings=False)
            pass

        return

    ##
    ##
    def load_shapefile(self, filename):

        df = geopandas.read_file(filename)
        log.info('df=%s', df)
        log.info('df.crs=%s', df.crs)

        return df

    ##
    ##
    def add_shapefile_region(self, name):

        df = self.shapefile_df
        key = self.shapefile_key

        suburb = df[df[key] == value]
        log.info('suburb=%s', suburb)
        suburb = suburb.to_crs(epsg=4326)
        log.info('suburb=%s', suburb)

        polygon = suburb['geometry'].values[0]

        return polygon

    ##
    ##
    def create_gpx_polygon(self, polygon):

        gpx = gpxpy.gpx.GPX()
        gpx.name = f'boundary {self.name}'
        gpx.author_name = 'optiburb'
        gpx.creator = 'experimental burbing'
        gpx.description = f'experimental burbing boundary for {self.name}'

        track = gpxpy.gpx.GPXTrack()
        track.name = f'burb bound {self.name}'

        filename = f'burb_polygon_{self.name}.gpx'

        log.info('saving suburb boundary - %s', filename)

        # XXX - add colour?

        #xmlns:gpxx="http://www.garmin.com/xmlschemas/GpxExtensions/v3"
        #track.extensions =
        #<extensions>
        #  <gpxx:TrackExtension>
        #    <gpxx:DisplayColor>Red</gpxx:DisplayColor>
        #  </gpxx:TrackExtension>
        #</extensions>

        gpx.tracks.append(track)

        segment = gpxpy.gpx.GPXTrackSegment()
        track.segments.append(segment)

        for x, y in polygon.exterior.coords:
            segment.points.append(gpxpy.gpx.GPXRoutePoint(latitude=y, longitude=x))
            pass

        data = gpx.to_xml()
        with open(filename, 'w') as f:
            f.write(data)
            pass

        return

    ##
    ##
    def create_gpx_track(self, g, edges, simplify=False):
        # create GPX XML.

        stats_distance = 0.0
        stats_backtrack = 0.0
        stats_deadends = 0

        gpx = gpxpy.gpx.GPX()
        gpx.name = f'burb {self.name}'
        gpx.author_name = 'optiburb'
        gpx.creator = 'experimental burbing'
        gpx.description = f'experimental burbing route for {self.name}'

        track = gpxpy.gpx.GPXTrack()
        track.name = f'burb trk {self.name}'
        gpx.tracks.append(track)

        segment = gpxpy.gpx.GPXTrackSegment()
        track.segments.append(segment)

        i = 1

        for n, edge in enumerate(edges):
            u, v = edge
            edge_data = g.get_edge_data(*edge, 0)

            log.debug('EDGE [%d] - edge=%s, data=%s', n, edge, edge_data)

            if edge_data is None:
                log.warning('null data for edge %s', edge)
                # Create straight line between nodes
                try:
                    u_coords = (g.nodes[u]['x'], g.nodes[u]['y'])
                    v_coords = (g.nodes[v]['x'], g.nodes[v]['y'])
                    segment.points.append(gpxpy.gpx.GPXRoutePoint(latitude=u_coords[1], longitude=u_coords[0]))
                    segment.points.append(gpxpy.gpx.GPXRoutePoint(latitude=v_coords[1], longitude=v_coords[0]))
                    i += 2
                except (KeyError, AttributeError) as e:
                    log.error(f"Cannot create straight line for edge {edge}: {str(e)}")
                continue

            linestring = edge_data.get('geometry')
            augmented = edge_data.get('augmented')
            stats_distance += edge_data.get('length', 0)

            log.debug(' leg [%d] -> %s (%s,%s,%s,%s,%s)', n, edge_data.get('name', ''), edge_data.get('highway', ''), edge_data.get('surface', ''), edge_data.get('oneway', ''), edge_data.get('access', ''), edge_data.get('length', 0))

            coords_to_use = None
            if linestring:
                directional_linestring = self.directional_linestring(edge, linestring)
                if directional_linestring:
                    coords_to_use = directional_linestring

            if coords_to_use is None:
                # If no valid linestring, create straight line between nodes
                try:
                    u_coords = (g.nodes[u]['x'], g.nodes[u]['y'])
                    v_coords = (g.nodes[v]['x'], g.nodes[v]['y'])
                    coords_to_use = [u_coords, v_coords]
                    log.debug(f"Using straight line for edge {edge}")
                except (KeyError, AttributeError) as e:
                    log.error(f"Cannot create straight line for edge {edge}: {str(e)}")
                    continue

            for lon, lat in coords_to_use:
                segment.points.append(gpxpy.gpx.GPXRoutePoint(latitude=lat, longitude=lon))
                log.debug('     INDEX[%d] = (%s, %s)', i, lat, lon)
                i += 1

            if edge_data.get('augmented', False):
                stats_backtrack += edge_data.get('length', 0)

        log.info('total distance = %.2fkm', stats_distance/1000.0)
        log.info('backtrack distance = %.2fkm', stats_backtrack/1000.0)
        
        if simplify:
            log.info('simplifying GPX')
            gpx.simplify()

        data = gpx.to_xml()
        filename = f'burb_track_{self.name}.gpx'

        with open(filename, 'w') as f:
            f.write(data)

        return

    pass

##
##
if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Optimum Suburb Route Generator')
    parser.add_argument('names', type=str, nargs=argparse.REMAINDER, help='suburb names with state, country, etc')
    parser.add_argument('--debug', type=str, default='info', help='debug level debug, info, warn, etc')
    parser.add_argument('--start', type=str, help='optional starting address')
    parser.add_argument('--prune', default=False, action='store_true', help='prune unnamed gravel tracks')
    parser.add_argument('--simplify', default=False, action='store_true', help='simplify OSM nodes on load')
    parser.add_argument('--simplify-gpx', dest='simplify_gpx', default=True, action='store_true', help='reduce GPX points')
    parser.add_argument('--complex-gpx', dest='simplify_gpx', action='store_false', help='leave all the OSM points in the GPX output')
    parser.add_argument('--select', type=int, default=1, help='select the nth item from the search results. a truely awful hack because i cant work out how to search for administrative boundaries.')
    parser.add_argument('--shapefile', type=str, default=None, help='filename of shapefile to load localities, comma separated by the column to match on')
    parser.add_argument('--buffer', type=int, dest='buffer', default=20, help='buffer distsance around polygon')
    parser.add_argument('--save-fig', default=False, action='store_true', help='save an SVG image of the nodes and edges')
    parser.add_argument('--save-boundary', default=False, action='store_true', help='save a GPX file of the suburb boundary')
    parser.add_argument('--feature-deadend', default=False, action='store_true', help='experimental feature to optimised deadends in solution')

    args = parser.parse_args()

    log.setLevel(logging.getLevelName(args.debug.upper()))

    log.debug('called with args - %s', args)

    start_time = datetime.datetime.now()

    burbing = Burbing()

    if not args.names:
        parser.print_help()
        sys.exit(1)
        pass

    if args.shapefile:

        filename, key = args.shapefile.split(',')

        log.info('shapefile=%s, key=%s', filename, key)

        shapefile = burbing.load_shapefile(filename)

        for name in args.names:
            polygon = burbing.get_shapefile_polygon(shapefile, key, name)
            burbing.add_polygon(polygon, name)
            pass
        pass

    else:

        for name in args.names:

            polygon = burbing.get_osm_polygon(name, args.select, args.buffer)
            burbing.add_polygon(polygon, name)
            pass
        pass

    if args.save_boundary:
        burbing.create_gpx_polygon(burbing.region)
        pass

    if args.start:
        burbing.set_start_location(args.start)
        pass

    burbing.load(args)

    if args.prune:
        burbing.prune()
        pass

    burbing.determine_nodes()

    if args.feature_deadend:
        burbing.optimise_dead_ends()
        pass

    if args.save_fig:
        burbing.save_fig()
        pass

    burbing.determine_combinations()

    burbing.determine_circuit()

    burbing.create_gpx_track(burbing.g_augmented, burbing.euler_circuit, args.simplify_gpx)

    end_time = datetime.datetime.now()

    log.info('elapsed time = %s', end_time - start_time)

    pass

