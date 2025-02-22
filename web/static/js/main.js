// Add styles for the start marker
const style = document.createElement('style');
style.textContent = `
    .start-marker {
        width: 16px !important;
        height: 16px !important;
        margin-left: -8px !important;
        margin-top: -8px !important;
    }
    .start-marker div {
        width: 16px;
        height: 16px;
        background-color: #22c55e;
        border: 2px solid white;
        border-radius: 50%;
        box-shadow: 0 2px 4px rgba(0,0,0,0.2);
    }
`;
document.head.appendChild(style);

// Global map variables
let map = null;
let routeLayer = null;
let segmentsLayer = null;
let activitiesLayer = null;
let baseRouteLayer = null;
let directionsLayer = null;  // Layer for direction arrows
let straightLinesLayer = null;  // New layer for straight line segments

// Configuration object for map settings
const MAP_CONFIG = {
    defaultZoom: 13,
    maxZoom: 15,
    mapPadding: 50,
    searchLimit: 10,
    bufferTolerance: 0.0003,  // For straight line detection
    arrowBaseLength: 0.0002,  // For direction arrows
    arrowBackAngle: 40,  // Degrees for arrow back points
    layerWeights: {
        baseRoute: 3,
        straightLine: 4,
        direction: 3.5,
        activity: 2,
        completed: 5
    },
    layerColors: {
        baseRoute: '#0000ff',
        straightLine: '#8b5cf6',
        direction: '#3388ff',
        activity: '#ff69b4',
        completed: '#22c55e',
        incomplete: '#ef4444'
    },
    layerOpacity: {
        baseRoute: 0.8,
        straightLine: 1.0,
        direction: 1.0,
        activity: 0.6,
        completed: 1.0
    }
};

// Global function for initializing map
function initMap(center) {
    // Show map container before initializing map
    const mapContainer = document.getElementById('mapContainer');
    mapContainer.classList.remove('hidden');

    // Clear existing map if it exists
    if (map) {
        map.remove();
        map = null;
        routeLayer = null;
        segmentsLayer = null;
        activitiesLayer = null;
        baseRouteLayer = null;
        directionsLayer = null;
        straightLinesLayer = null;
    }

    // Create new map instance
    map = L.map('map').setView(center, MAP_CONFIG.defaultZoom);
    
    // Create custom panes with explicit z-index values
    map.createPane('activitiesPane');
    map.createPane('baseRoutePane');
    map.createPane('routePane');
    map.createPane('segmentsPane');
    map.createPane('directionsPane');  // New pane for directions
    map.createPane('straightLinesPane');  // New pane for straight lines
    
    // Set z-index and pointer events for panes
    map.getPane('activitiesPane').style.zIndex = 300;
    map.getPane('baseRoutePane').style.zIndex = 350;
    map.getPane('routePane').style.zIndex = 400;
    map.getPane('segmentsPane').style.zIndex = 500;
    map.getPane('directionsPane').style.zIndex = 600;  // Highest z-index for arrows
    map.getPane('straightLinesPane').style.zIndex = 450;  // Between route and segments
    
    // Ensure pointer events are enabled
    map.getPane('activitiesPane').style.pointerEvents = 'auto';
    map.getPane('baseRoutePane').style.pointerEvents = 'auto';
    map.getPane('routePane').style.pointerEvents = 'auto';
    map.getPane('segmentsPane').style.pointerEvents = 'auto';
    map.getPane('directionsPane').style.pointerEvents = 'none';  // No pointer events needed for arrows
    map.getPane('straightLinesPane').style.pointerEvents = 'auto';

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    // Initialize layers with their respective panes
    activitiesLayer = L.featureGroup([], { pane: 'activitiesPane' });
    baseRouteLayer = L.featureGroup([], { pane: 'baseRoutePane' });
    routeLayer = L.featureGroup([], { pane: 'routePane' });
    segmentsLayer = L.featureGroup([], { pane: 'segmentsPane' });
    directionsLayer = L.featureGroup([], { pane: 'directionsPane' });  // Initialize directions layer
    straightLinesLayer = L.featureGroup([], { pane: 'straightLinesPane' });  // Initialize straight lines layer

    // Add layers to map in correct order
    activitiesLayer.addTo(map);
    baseRouteLayer.addTo(map);
    routeLayer.addTo(map);
    segmentsLayer.addTo(map);
    directionsLayer.addTo(map);
    // Don't add straightLinesLayer by default - it will be controlled by the layer control

    // Force a resize event after the map is visible
    setTimeout(() => {
        map.invalidateSize();
    }, 100);
}

// Global function for displaying route
function displayRoute(filename, startCoordinates = null) {
    console.log('Attempting to display route for:', filename, 'with start coordinates:', startCoordinates);
    const mapContainer = document.getElementById('mapContainer');
    const result = document.getElementById('result');
    const resultContent = document.getElementById('resultContent');
    const routeAnalysis = document.getElementById('routeAnalysis');
    const aiSummary = document.getElementById('aiSummary');
    const completionInfo = document.getElementById('completionInfo');

    // Show the route analysis section
    routeAnalysis.classList.remove('hidden');

    // Show map container before initializing map
    mapContainer.classList.remove('hidden');

    // First fetch the route summary
    fetch(`/route/${filename}/summary`)
        .then(response => response.json())
        .then(summaryData => {
            if (summaryData.error) {
                console.error('Error getting route summary:', summaryData.error);
            } else {
                // Create the AI summary HTML
                const summary = summaryData.summary;
                const aiSummaryHtml = `
                    <div class="space-y-6">
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div class="space-y-2">
                                <h4 class="font-medium text-gray-700">Distance</h4>
                                <div class="text-sm space-y-1">
                                    <div class="flex justify-between">
                                        <span class="text-gray-600">Total</span>
                                        <span class="font-medium">${summary.distance.kilometers.toFixed(1)} km (${summary.distance.miles.toFixed(1)} mi)</span>
                                    </div>
                                </div>
                            </div>

                            <div class="space-y-2">
                                <h4 class="font-medium text-gray-700">Elevation</h4>
                                <div class="text-sm space-y-1">
                                    <div class="flex justify-between">
                                        <span class="text-gray-600">Gain</span>
                                        <span class="font-medium">↑ ${summary.elevation.gain_meters}m</span>
                                    </div>
                                    <div class="flex justify-between">
                                        <span class="text-gray-600">Loss</span>
                                        <span class="font-medium">↓ ${summary.elevation.loss_meters}m</span>
                                    </div>
                                    <div class="flex justify-between">
                                        <span class="text-gray-600">Net</span>
                                        <span class="font-medium">${summary.elevation.net_meters}m</span>
                                    </div>
                                </div>
                            </div>

                            <div class="space-y-2">
                                <h4 class="font-medium text-gray-700">Terrain</h4>
                                <div class="text-sm space-y-1">
                                    <div class="flex justify-between">
                                        <span class="text-gray-600">Hilliness</span>
                                        <span class="font-medium">${summary.hilliness.description}</span>
                                    </div>
                                    <div class="w-full bg-gray-200 rounded-full h-1.5 mt-1">
                                        <div class="bg-blue-600 h-1.5 rounded-full" style="width: ${summary.hilliness.score}%"></div>
                                    </div>
                                </div>
                            </div>

                            <div class="space-y-2">
                                <h4 class="font-medium text-gray-700">Safety</h4>
                                <div class="text-sm space-y-1">
                                    <div class="flex justify-between">
                                        <span class="text-gray-600">Rating</span>
                                        <span class="font-medium">${summary.safety.description}</span>
                                    </div>
                                    <div class="w-full bg-gray-200 rounded-full h-1.5 mt-1">
                                        <div class="bg-blue-600 h-1.5 rounded-full" style="width: ${summary.safety.score}%"></div>
                                    </div>
                                    ${summary.safety.factors.map(factor => `
                                        <div class="flex justify-between text-xs mt-1">
                                            <span class="text-gray-500">${factor.factor}</span>
                                            <span class="text-gray-600">${factor.description}</span>
                                        </div>
                                    `).join('')}
                                </div>
                            </div>
                        </div>

                        <div class="border-t border-gray-200 pt-4">
                            <h4 class="font-medium text-gray-700 mb-2">Estimated Time to Complete</h4>
                            <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
                                <div class="bg-gray-50 p-3 rounded-md">
                                    <div class="text-sm font-medium text-gray-900">Walking</div>
                                    <div class="mt-1 text-sm text-gray-600">${summary.estimated_time.walking.hours} hours</div>
                                    <div class="text-xs text-gray-500">${summary.estimated_time.walking.pace_kmh} km/h</div>
                                </div>
                                <div class="bg-gray-50 p-3 rounded-md">
                                    <div class="text-sm font-medium text-gray-900">Running</div>
                                    <div class="mt-1 text-sm text-gray-600">${summary.estimated_time.running.hours} hours</div>
                                    <div class="text-xs text-gray-500">${summary.estimated_time.running.pace_kmh} km/h</div>
                                </div>
                                <div class="bg-gray-50 p-3 rounded-md">
                                    <div class="text-sm font-medium text-gray-900">Cycling</div>
                                    <div class="mt-1 text-sm text-gray-600">${summary.estimated_time.cycling.hours} hours</div>
                                    <div class="text-xs text-gray-500">${summary.estimated_time.cycling.pace_kmh} km/h</div>
                                </div>
                            </div>
                        </div>

                        ${summary.alerts.length > 0 ? `
                            <div class="border-t border-gray-200 pt-4">
                                <h4 class="font-medium text-gray-700 mb-2">Alerts</h4>
                                <div class="space-y-2">
                                    ${summary.alerts.map(alert => `
                                        <div class="flex items-start space-x-2 text-sm">
                                            <div class="flex-shrink-0">
                                                ${alert.severity === 'warning' ? `
                                                    <svg class="h-5 w-5 text-yellow-400" viewBox="0 0 20 20" fill="currentColor">
                                                        <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/>
                                                    </svg>
                                                ` : `
                                                    <svg class="h-5 w-5 text-blue-400" viewBox="0 0 20 20" fill="currentColor">
                                                        <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd"/>
                                                    </svg>
                                                `}
                                            </div>
                                            <div class="flex-1">
                                                <p class="text-gray-600">${alert.message}</p>
                                            </div>
                                        </div>
                                    `).join('')}
                                </div>
                            </div>
                        ` : ''}
                    </div>
                `;
                aiSummary.innerHTML = aiSummaryHtml;
            }
        })
        .catch(error => {
            console.error('Error fetching route summary:', error);
            aiSummary.innerHTML = `
                <div class="bg-red-50 border border-red-200 rounded-md p-4">
                    <div class="flex">
                        <div class="flex-shrink-0">
                            <svg class="h-5 w-5 text-red-400" viewBox="0 0 20 20" fill="currentColor">
                                <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"/>
                            </svg>
                        </div>
                        <div class="ml-3">
                            <p class="text-sm font-medium text-red-800">
                                Error loading route analysis
                            </p>
                        </div>
                    </div>
                </div>
            `;
        });

    fetch(`/route/${filename}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                throw new Error(data.error);
            }

            console.log('Received route data:', data);

            const bounds = data.bounds;
            if (!bounds) {
                throw new Error('No bounds data available for the route');
            }

            // Use provided start coordinates if available, otherwise use the ones from the route data
            data.start_coordinates = startCoordinates || data.start_coordinates;
            console.log('Using start coordinates:', data.start_coordinates);

            console.log('Route bounds:', bounds);
            // If we have start coordinates, initialize map centered on start point
            if (data.start_coordinates) {
                console.log('Initializing map centered on start point:', data.start_coordinates);
                initMap(data.start_coordinates);
                
                // Add a marker for the start location immediately
                const startMarker = L.marker(data.start_coordinates, {
                    title: 'Start Location',
                    icon: L.divIcon({
                        className: 'start-marker',
                        html: '<div></div>'
                    })
                }).addTo(map);
            } else {
                // Fall back to route center if no start point
                const center = [
                    (bounds.minLat + bounds.maxLat) / 2,
                    (bounds.minLng + bounds.maxLng) / 2
                ];
                console.log('Falling back to route center:', center);
                initMap(center);
            }

            // Add route lines first
            console.debug('All features:', data.geojson.features);
            console.debug('Features with straight_line type:', data.geojson.features.filter(f => f.properties && f.properties.type === 'straight_line'));
            
            // First separate direction features
            const directionFeatures = data.geojson.features.filter(f => 
                f.properties && f.properties.type === 'direction'
            );

            // Then separate straight line features
            const straightLineFeatures = data.geojson.features.filter(f => 
                f.properties && f.properties.type === 'straight_line'
            );

            // Finally get regular route features (excluding straight lines and directions)
            const regularRouteFeatures = data.geojson.features.filter(f => 
                (!f.properties || !f.properties.type || f.properties.type === 'route') &&
                (!f.properties || f.properties.type !== 'straight_line') &&
                (!f.properties || f.properties.type !== 'direction')
            );
            
            // Log feature counts
            console.debug('Feature counts:');
            console.debug(`  Regular routes: ${regularRouteFeatures.length}`);
            console.debug(`  Straight lines: ${straightLineFeatures.length}`);
            console.debug(`  Directions: ${directionFeatures.length}`);

            // Log straight line segments specifically
            console.debug('Straight line features:', straightLineFeatures);
            console.debug(`Found ${straightLineFeatures.length} straight line segments:`);
            straightLineFeatures.forEach((feature, index) => {
                const coords = feature.geometry.coordinates;
                console.debug(`Straight line segment ${index + 1}:`);
                console.debug(`  Start: (${coords[0][1]}, ${coords[0][0]})`);
                console.debug(`  End: (${coords[coords.length-1][1]}, ${coords[coords.length-1][0]})`);
                console.debug('  Properties:', feature.properties);
            });

            // Add original route to base route layer
            baseRouteLayer.clearLayers();
            straightLinesLayer.clearLayers();
            
            // First add the regular route lines
            const baseRoute = L.geoJSON({
                type: "FeatureCollection",
                features: regularRouteFeatures
            }, {
                style: {
                    color: MAP_CONFIG.layerColors.baseRoute,
                    weight: MAP_CONFIG.layerWeights.baseRoute,
                    opacity: MAP_CONFIG.layerOpacity.baseRoute,
                    pane: 'baseRoutePane'
                }
            }).addTo(baseRouteLayer);

            // Add straight line segments to their own layer
            const straightLines = L.geoJSON({
                type: "FeatureCollection",
                features: straightLineFeatures
            }, {
                style: {
                    color: MAP_CONFIG.layerColors.straightLine,
                    weight: MAP_CONFIG.layerWeights.straightLine,
                    opacity: MAP_CONFIG.layerOpacity.straightLine,
                    dashArray: '10, 10',
                    pane: 'straightLinesPane'
                }
            }).addTo(straightLinesLayer);
            
            // Log the number of straight line segments
            const straightLineCount = straightLines.getLayers().length;
            console.log(`Added ${straightLineCount} straight line segments to the map`);

            // Add straight lines layer to the map if there are straight line segments
            if (straightLineCount > 0) {
                map.addLayer(straightLinesLayer);
                map.removeLayer(straightLinesLayer);  // Add but hide by default
            }
            
            // Log the number of layers added
            console.log(`Added ${baseRoute.getLayers().length} layers to the map`);
            
            // Add direction ticks to the directions layer
            directionFeatures.forEach((feature, index) => {
                const bearing = feature.properties.bearing;
                const coords = feature.geometry.coordinates;
                
                // Convert from [lng, lat] to [lat, lng] for Leaflet
                const center = [coords[1], coords[0]];
                
                // Calculate base length and angles (2x larger)
                const baseLength = 0.0002; // Doubled from 0.0001
                const backAngle = 40; // Keep the same angle
                
                // Convert bearing to radians and adjust for map coordinates (-90 degree rotation)
                const bearingRad = (bearing * Math.PI) / 180;
                
                // Calculate the front point (tip of the arrow)
                const frontPoint = [
                    center[0] + (baseLength * Math.cos(bearingRad)),
                    center[1] + (baseLength * Math.sin(bearingRad))
                ];
                
                // Calculate back points with the same rotation
                const leftRad = bearingRad + (backAngle * Math.PI / 180);
                const rightRad = bearingRad - (backAngle * Math.PI / 180);
                
                const leftPoint = [
                    center[0] + (baseLength * 0.7 * Math.cos(leftRad)),
                    center[1] + (baseLength * 0.7 * Math.sin(leftRad))
                ];
                
                const rightPoint = [
                    center[0] + (baseLength * 0.7 * Math.cos(rightRad)),
                    center[1] + (baseLength * 0.7 * Math.sin(rightRad))
                ];
                
                // Create the arrow shape from front to back points (increased weight to match larger size)
                const tick = L.polyline([frontPoint, center, leftPoint, center, rightPoint], {
                    color: MAP_CONFIG.layerColors.direction,
                    weight: MAP_CONFIG.layerWeights.direction,
                    opacity: MAP_CONFIG.layerOpacity.direction,
                    pane: 'directionsPane'
                }).addTo(directionsLayer);
            });

            // Store the original route for later use
            const originalRoute = baseRouteLayer.getLayers()[0];
            
            // Log layer information for debugging
            console.log('Route layer has features:', routeLayer.getLayers().length);
            console.log('Route layer is on map:', map.hasLayer(routeLayer));

            // Fit map to route bounds with padding
            console.log('Fitting map to bounds');
            // If we have start coordinates, ensure they're included in bounds
            if (data.start_coordinates) {
                console.log('Adjusting bounds to include start coordinates:', data.start_coordinates);
                const padding = 0.002; // Add some padding to ensure start point isn't right at the edge
                bounds.minLat = Math.min(bounds.minLat, data.start_coordinates[0] - padding);
                bounds.maxLat = Math.max(bounds.maxLat, data.start_coordinates[0] + padding);
                bounds.minLng = Math.min(bounds.minLng, data.start_coordinates[1] - padding);
                bounds.maxLng = Math.max(bounds.maxLng, data.start_coordinates[1] + padding);
                console.log('Updated bounds:', bounds);
            }
            
            map.fitBounds([
                [bounds.minLat, bounds.minLng],
                [bounds.maxLat, bounds.maxLng]
            ], { 
                padding: [MAP_CONFIG.mapPadding, MAP_CONFIG.mapPadding],
                maxZoom: MAP_CONFIG.maxZoom
            });

            // Now try to fetch completion data
            fetch(`/route/${filename}/completion`)
                .then(response => {
                    if (response.status === 404) {
                        throw new Error('Route not found');
                    }
                    if (response.redirected || !response.ok) {
                        throw new Error('Please connect with Strava to view completion data');
                    }
                    return response.json();
                })
                .then(completionData => {
                    console.log('Received completion data:', completionData);
                    
                    // Clear only the activities layer
                    console.log('Clearing activities layer');
                    activitiesLayer.clearLayers();

                    // Add activities if available
                    if (completionData.activities && completionData.activities.length > 0) {
                        console.log(`Processing ${completionData.activities.length} activities`);
                        completionData.activities.forEach((activity, index) => {
                            console.log(`Adding activity ${index + 1}`);
                            const line = L.geoJSON(activity, {
                                style: {
                                    color: MAP_CONFIG.layerColors.activity,
                                    weight: MAP_CONFIG.layerWeights.activity,
                                    opacity: MAP_CONFIG.layerOpacity.activity
                                }
                            });

                            const popup = L.popup().setContent(`
                                <div class="text-sm">
                                    <p class="font-medium">${activity.properties.name}</p>
                                    <p class="text-gray-600">Distance: ${(activity.properties.distance / 1000).toFixed(1)}km</p>
                                    <p class="text-gray-600">Date: ${new Date(activity.properties.date).toLocaleDateString()}</p>
                                    <p class="text-gray-600">Type: ${activity.properties.type}</p>
                                    <a href="https://www.strava.com/activities/${activity.properties.id}" 
                                       target="_blank" 
                                       class="text-blue-600 hover:text-blue-800">
                                        View on Strava
                                    </a>
                                </div>
                            `);

                            line.bindPopup(popup);
                            line.addTo(activitiesLayer);
                        });
                    }

                    // Handle route segments
                    if (completionData.incomplete_segments.length > 0 || completionData.completed_segments.length > 0) {
                        console.info('Updating route with completion data');
                        
                        // Store direction ticks before clearing
                        const directionTicks = directionsLayer.getLayers();
                        
                        // First, collect all straight line segments coordinates for comparison
                        const straightLineSegments = [];
                        [...completionData.incomplete_segments, ...completionData.completed_segments].forEach(segment => {
                            if (segment.is_straight_line && segment.coordinates && segment.coordinates.length >= 2) {
                                straightLineSegments.push({
                                    start: segment.coordinates[0],
                                    end: segment.coordinates[segment.coordinates.length - 1],
                                    coordinates: segment.coordinates
                                });
                            }
                        });

                        // Helper function to calculate distance between two points
                        function getDistance(p1, p2) {
                            return Math.sqrt(
                                Math.pow(p1[0] - p2[0], 2) + 
                                Math.pow(p1[1] - p2[1], 2)
                            );
                        }

                        // Helper function to check if a segment is part of a straight line connection
                        function isPartOfStraightLine(coords) {
                            if (coords.length < 2) return false;
                            
                            const segmentStart = coords[0];
                            const segmentEnd = coords[coords.length - 1];
                            
                            return straightLineSegments.some(straightLine => {
                                const forwardMatch = 
                                    getDistance(segmentStart, straightLine.start) < MAP_CONFIG.bufferTolerance &&
                                    getDistance(segmentEnd, straightLine.end) < MAP_CONFIG.bufferTolerance;
                                const reverseMatch = 
                                    getDistance(segmentStart, straightLine.end) < MAP_CONFIG.bufferTolerance &&
                                    getDistance(segmentEnd, straightLine.start) < MAP_CONFIG.bufferTolerance;
                                return forwardMatch || reverseMatch;
                            });
                        }

                        // Clear existing layers
                        segmentsLayer.clearLayers();
                        straightLinesLayer.clearLayers();
                        baseRouteLayer.clearLayers();
                        routeLayer.clearLayers();  // Clear route layer as well

                        // First process straight line segments
                        straightLineSegments.forEach(segment => {
                            // Fix coordinate order: Convert from [lng, lat] to [lat, lng]
                            const correctedCoords = segment.coordinates.map(coord => [coord[1], coord[0]]);
                            
                            const line = L.polyline(correctedCoords, {
                                color: MAP_CONFIG.layerColors.straightLine,
                                weight: MAP_CONFIG.layerWeights.straightLine,
                                opacity: MAP_CONFIG.layerOpacity.straightLine,
                                dashArray: '10, 10',  // dashed line
                                pane: 'straightLinesPane',
                                interactive: true
                            });
                            line.addTo(straightLinesLayer);
                        });

                        // Process all non-straight-line segments for both Completed and Route layers
                        [...completionData.incomplete_segments, ...completionData.completed_segments].forEach(segment => {
                            if (!segment.coordinates || segment.coordinates.length < 2 || segment.is_straight_line) return;
                            
                            // Skip if it's part of a straight line
                            if (isPartOfStraightLine(segment.coordinates)) return;
                            
                            // Fix coordinate order: Convert from [lng, lat] to [lat, lng]
                            const correctedCoords = segment.coordinates.map(coord => [coord[1], coord[0]]);
                            
                            // Add to Route layer in blue
                            const routeLine = L.polyline(correctedCoords, {
                                color: MAP_CONFIG.layerColors.baseRoute,
                                weight: MAP_CONFIG.layerWeights.baseRoute,
                                opacity: MAP_CONFIG.layerOpacity.baseRoute,
                                pane: 'routePane',
                                interactive: true
                            });
                            routeLine.addTo(routeLayer);
                            
                            // Add to Completed layer with appropriate color
                            const isComplete = completionData.completed_segments.includes(segment);
                            const completedLine = L.polyline(correctedCoords, {
                                color: isComplete ? MAP_CONFIG.layerColors.completed : MAP_CONFIG.layerColors.incomplete,
                                weight: MAP_CONFIG.layerWeights.completed,
                                opacity: MAP_CONFIG.layerOpacity.completed,
                                pane: 'segmentsPane',
                                interactive: true
                            });
                            completedLine.addTo(segmentsLayer);
                        });

                        // Re-add direction ticks to the directions layer
                        directionTicks.forEach(tick => {
                            tick.addTo(directionsLayer);
                        });

                        // Ensure proper layer order
                        activitiesLayer.remove();
                        baseRouteLayer.remove();
                        routeLayer.remove();
                        directionsLayer.remove();
                        segmentsLayer.remove();
                        straightLinesLayer.remove();
                        
                        // Add layers in correct order (bottom to top)
                        activitiesLayer.addTo(map);
                        baseRouteLayer.addTo(map);
                        routeLayer.addTo(map);
                        segmentsLayer.addTo(map);
                        directionsLayer.addTo(map);

                        // Force map update and redraw
                        map.invalidateSize();
                        map._onResize();

                        console.info('Layers updated:', {
                            activities: activitiesLayer.getLayers().length,
                            baseRoute: baseRouteLayer.getLayers().length,
                            route: routeLayer.getLayers().length,
                            segments: segmentsLayer.getLayers().length
                        });
                    } else {
                        console.info('No segment data available, showing original route in red');
                        routeLayer.clearLayers();
                        L.geoJSON(data.geojson, {
                            style: {
                                color: MAP_CONFIG.layerColors.incomplete,
                                weight: MAP_CONFIG.layerWeights.baseRoute,
                                opacity: MAP_CONFIG.layerOpacity.baseRoute,
                                pane: 'routePane'  // Ensure it uses the correct pane
                            }
                        }).addTo(routeLayer);
                        
                        // Make sure route layer is on the map
                        routeLayer.addTo(map);
                    }

                    // Log layer information
                    console.log('Activities layer has features:', activitiesLayer.getLayers().length > 0);
                    console.log('Route layer has features:', routeLayer.getLayers().length > 0);

                    // Add layer control with the new directions layer
                    const overlayMaps = {
                        "Route": routeLayer,  // Change from baseRouteLayer to routeLayer
                        ...(straightLines.getLayers().length > 0 ? {"Straight Lines": straightLinesLayer} : {}),
                        "Directions": directionsLayer,
                        "Activities": activitiesLayer,
                        "Completed": segmentsLayer
                    };

                    if (map._layers_control) {
                        map._layers_control.remove();
                    }
                    map._layers_control = L.control.layers(null, overlayMaps, {
                        collapsed: false,
                        position: 'topright'
                    }).addTo(map);

                    // Ensure all layers except straight lines are visible by default
                    map.addLayer(routeLayer);
                    map.addLayer(directionsLayer);
                    map.addLayer(activitiesLayer);
                    map.addLayer(segmentsLayer);

                    // Force a map update
                    map.invalidateSize();

                    // Add completion status below the map
                    const completionPercentage = (completionData.total_completion * 100).toFixed(1);
                    const totalDistance = (completionData.total_distance * 111).toFixed(1);  // Convert to km (rough approximation)
                    const completedDistance = (completionData.completed_distance * 111).toFixed(1);

                    const completionHtml = `
                        <div class="bg-gray-50 border border-gray-200 rounded-md p-4">
                            <h3 class="text-lg font-medium text-gray-900 mb-2">Route Completion</h3>
                            <div class="space-y-2">
                                <div class="flex justify-between">
                                    <span class="text-gray-600">Completion</span>
                                    <span class="font-medium">${completionPercentage}%</span>
                                </div>
                                <div class="w-full bg-gray-200 rounded-full h-2.5">
                                    <div class="bg-green-600 h-2.5 rounded-full" style="width: ${completionPercentage}%"></div>
                                </div>
                                <div class="flex justify-between text-sm">
                                    <span class="text-gray-600">Total Distance</span>
                                    <span class="font-medium">${totalDistance} km</span>
                                </div>
                                <div class="flex justify-between text-sm">
                                    <span class="text-gray-600">Completed</span>
                                    <span class="font-medium">${completedDistance} km</span>
                                </div>
                            </div>
                        </div>
                    `;
                    completionInfo.innerHTML = completionHtml;
                })
                .catch(error => {
                    console.error('Error loading completion data:', error);
                    // Add error message below the map for Strava authentication
                    const authMessage = `
                        <div class="bg-yellow-50 border border-yellow-200 rounded-md p-4">
                            <div class="flex">
                                <div class="flex-shrink-0">
                                    <svg class="h-5 w-5 text-yellow-400" viewBox="0 0 20 20" fill="currentColor">
                                        <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/>
                                    </svg>
                                </div>
                                <div class="ml-3">
                                    <p class="text-sm font-medium text-yellow-800">
                                        ${error.message}
                                    </p>
                                </div>
                            </div>
                        </div>
                    `;
                    completionInfo.innerHTML = authMessage;
                    
                    // Make sure the original route is still visible
                    originalRoute.addTo(routeLayer);
                });
        })
        .catch(error => {
            console.error('Error loading route:', error);
            resultContent.innerHTML = `
                <div class="bg-red-50 border border-red-200 rounded-md p-4">
                    <div class="flex">
                        <div class="flex-shrink-0">
                            <svg class="h-5 w-5 text-red-400" viewBox="0 0 20 20" fill="currentColor">
                                <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"/>
                            </svg>
                        </div>
                        <div class="ml-3">
                            <p class="text-sm font-medium text-red-800">
                                ${error.message}
                            </p>
                        </div>
                    </div>
                </div>
            `;
            result.classList.remove('hidden');
        });
}

// Function to create success message HTML
function createSuccessMessage(gpxFile) {
    return `
        <div class="mt-6 space-y-4">
            <div class="bg-green-50 border border-green-200 rounded-md p-4">
                <div class="flex">
                    <div class="flex-shrink-0">
                        <svg class="h-5 w-5 text-green-400" viewBox="0 0 20 20" fill="currentColor">
                            <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/>
                        </svg>
                    </div>
                    <div class="ml-3">
                        <p class="text-sm font-medium text-green-800">
                            Route generated successfully!
                        </p>
                    </div>
                </div>
            </div>
            <div class="flex space-x-4">
                <a href="/download/${gpxFile}" 
                   class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500">
                    <svg class="mr-2 -ml-1 h-5 w-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>
                    </svg>
                    Download GPX File
                </a>
                <label class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md text-white bg-green-600 hover:bg-green-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-green-500 cursor-pointer">
                    <svg class="mr-2 -ml-1 h-5 w-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19V5m0 0l-7 7m7-7l7 7"/>
                    </svg>
                    Upload GPX File
                    <input type="file" class="hidden" accept=".gpx" id="gpxFileInput">
                </label>
            </div>
        </div>
    `;
}

// Global function for handling file uploads
async function handleFileUpload(input) {
    console.log('File upload triggered');
    if (!input.files || !input.files[0]) {
        console.error('No file selected');
        return;
    }

    const file = input.files[0];
    console.log('Selected file:', file.name);
    
    if (!file.name.endsWith('.gpx')) {
        alert('Please select a GPX file');
        return;
    }

    const formData = new FormData();
    formData.append('file', file);

    try {
        console.log('Sending file to server...');
        const response = await fetch('/upload', {
            method: 'POST',
            body: formData
        });

        console.log('Server response:', response);
        const data = await response.json();
        console.log('Response data:', data);

        if (!response.ok) {
            throw new Error(data.error || 'Failed to upload file');
        }

        // Show map container before initializing map
        const mapContainer = document.getElementById('mapContainer');
        mapContainer.classList.remove('hidden');

        // Clear existing map instance and layers
        if (map) {
            map.remove();
            map = null;
            routeLayer = null;
            segmentsLayer = null;
            activitiesLayer = null;
            baseRouteLayer = null;
            directionsLayer = null;
            straightLinesLayer = null;
        }

        // Display the uploaded route
        console.log('Displaying uploaded route:', data.gpx_file);
        displayRoute(data.gpx_file);

    } catch (error) {
        console.error('Error uploading file:', error);
        alert('Error uploading file: ' + error.message);
    }
}

document.addEventListener('DOMContentLoaded', function() {
    // Handle form collapsing
    const formHeader = document.getElementById('formHeader');
    const formContent = document.getElementById('formContent');
    const toggleIcon = document.getElementById('toggleIcon');
    let isFormCollapsed = false;

    function toggleForm(collapse = null) {
        if (collapse !== null) {
            isFormCollapsed = collapse;
        } else {
            isFormCollapsed = !isFormCollapsed;
        }
        
        if (isFormCollapsed) {
            formContent.style.display = 'none';
            toggleIcon.style.transform = 'rotate(-90deg)';
        } else {
            formContent.style.display = 'block';
            toggleIcon.style.transform = 'rotate(0)';
        }
    }

    formHeader.addEventListener('click', () => toggleForm());

    // Handle buffer size slider updates
    const bufferSlider = document.getElementById('bufferSize');
    const bufferValue = document.getElementById('bufferSizeValue');
    if (bufferSlider && bufferValue) {
        bufferSlider.addEventListener('input', function() {
            bufferValue.textContent = `${this.value}m`;
        });
    }

    // Location search functionality
    const startPointInput = document.getElementById('startPoint');
    const locationInput = document.getElementById('location');

    // Simple function to search locations
    async function searchLocation(query, cityContext = null) {
        try {
            const baseUrl = 'https://nominatim.openstreetmap.org/search';
            let searchQuery = query.trim();
            
            // Format the city context if provided
            const formattedCity = cityContext ? cityContext.trim() : '';
            
            // If searching for a street address
            if (searchQuery.match(/^\d+/)) {  // If query starts with numbers (likely an address)
                // First try with "Street" since that seems to work best
                const withStreet = `${searchQuery}${searchQuery.toLowerCase().includes('street') ? '' : ' Street'}`;
                if (formattedCity && !withStreet.toLowerCase().includes(formattedCity.toLowerCase())) {
                    searchQuery = `${withStreet}, ${formattedCity}`;
                } else {
                    searchQuery = withStreet;
                }
                
                console.log('Trying search with:', searchQuery);
                let results = await performSearch(baseUrl, searchQuery);
                if (results.length) {
                    return filterResultsByCity(results, formattedCity);
                }

                // If that fails, try original query
                const originalQuery = `${query.trim()}${formattedCity ? `, ${formattedCity}` : ''}`;
                if (originalQuery !== searchQuery) {
                    console.log('Trying original query:', originalQuery);
                    results = await performSearch(baseUrl, originalQuery);
                    if (results.length) {
                        return filterResultsByCity(results, formattedCity);
                    }
                }

                // Last resort: try with minimal formatting
                const minimalMatch = searchQuery.match(/^(\d+)\s+(?:[NSEW]\.\s*)?([^,\s]+)/i);
                if (minimalMatch) {
                    const minimalQuery = `${minimalMatch[1]} ${minimalMatch[2]}, ${formattedCity}`;
                    if (minimalQuery !== searchQuery && minimalQuery !== originalQuery) {
                        console.log('Trying minimal query:', minimalQuery);
                        results = await performSearch(baseUrl, minimalQuery);
                        if (results.length) {
                            return filterResultsByCity(results, formattedCity);
                        }
                    }
                }

                return null;
            } else {
                // For non-address searches
                if (formattedCity) {
                    searchQuery = `${searchQuery}, ${formattedCity}`;
                }
                const results = await performSearch(baseUrl, searchQuery);
                return results.length ? filterResultsByCity(results, formattedCity) : null;
            }
        } catch (error) {
            console.error('Error in searchLocation:', error);
            return null;
        }
    }

    // Helper function to filter results by city
    function filterResultsByCity(results, cityContext) {
        if (!cityContext || !results.length) return results[0];

        const [cityName, stateCode] = cityContext.split(',').map(part => part.trim().toLowerCase());
        const cityMatch = results.find(result => {
            const address = result.address || {};
            const resultCity = (address.city || '').toLowerCase();
            const resultState = (address.state || '').toLowerCase();
            return resultCity === cityName && 
                   (resultState === stateCode || resultState.includes(stateCode));
        });
        return cityMatch || results[0];
    }

    // Helper function to perform the actual search
    async function performSearch(baseUrl, query) {
        const params = new URLSearchParams({
            format: 'json',
            q: query,
            limit: MAP_CONFIG.searchLimit,
            addressdetails: 1,
            countrycodes: 'us',
            'accept-language': 'en'
        });

        const searchUrl = `${baseUrl}?${params}`;
        console.log('Search URL:', searchUrl);
        console.log('Search query:', query);

        const response = await fetch(searchUrl, {
            headers: {
                'User-Agent': 'OptiburB Route Generator v1.0'
            }
        });

        if (!response.ok) {
            console.error('Search failed with status:', response.status);
            throw new Error(`Search failed: ${response.status}`);
        }

        const results = await response.json();
        console.log('Search results:', results);
        return results;
    }

    // Handle form submission
    const form = document.getElementById('routeForm');
    const loading = document.getElementById('loading');
    const result = document.getElementById('result');
    const resultContent = document.getElementById('resultContent');
    const mapContainer = document.getElementById('mapContainer');

    // Generate a unique session ID
    function generateSessionId() {
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            const r = Math.random() * 16 | 0;
            const v = c == 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
    }

    function updateProgress(progress) {
        const loadingContent = document.getElementById('loadingContent');
        if (!loadingContent) return;

        loadingContent.innerHTML = `
            <div class="space-y-4">
                <div class="flex items-center justify-between">
                    <span class="text-sm font-medium text-gray-700">${progress.step || 'Processing...'}</span>
                    <span class="text-sm font-medium text-gray-700">${progress.progress || 0}%</span>
                </div>
                <div class="w-full bg-gray-200 rounded-full h-2.5">
                    <div class="bg-blue-600 h-2.5 rounded-full transition-all duration-300" style="width: ${progress.progress || 0}%"></div>
                </div>
                <p class="text-sm text-gray-600">${progress.message || ''}</p>
            </div>
        `;
    }

    form.addEventListener('submit', async function(e) {
        e.preventDefault();
        
        // Clear any existing completion information immediately
        const existingCompletion = document.querySelector('.completion-info');
        if (existingCompletion) {
            existingCompletion.remove();
        }
        
        // Show loading spinner and progress
        loading.classList.remove('hidden');
        result.classList.add('hidden');
        mapContainer.classList.add('hidden');
        resultContent.innerHTML = '';
        
        // Clear existing map if it exists
        if (map) {
            map.remove();
            map = null;
            routeLayer = null;
            segmentsLayer = null;
            activitiesLayer = null;
            baseRouteLayer = null;
            directionsLayer = null;
            straightLinesLayer = null;
        }

        const sessionId = generateSessionId();
        console.log('Starting route generation with session:', sessionId);

        try {
            // Initialize formData first
            const formData = {
                location: null,
                center_coordinates: null,
                start_point: null,
                simplify: document.querySelector('input[name="simplify"]').checked,
                prune: document.querySelector('input[name="prune"]').checked,
                simplify_gpx: document.querySelector('input[name="simplifyGpx"]').checked,
                exclude_completed: document.querySelector('input[name="excludeCompleted"]')?.checked || false,
                buffer: parseInt(document.getElementById('bufferSize').value),
                session_id: sessionId
            };

            // Get the city input
            const cityInput = document.getElementById('city').value.trim();
            if (!cityInput) {
                throw new Error('Please enter a city');
            }

            // Search for the city first
            const cityLocation = await searchLocation(cityInput);
            if (!cityLocation) {
                throw new Error('Could not find the specified city');
            }

            // Get the start point location if provided
            const startPoint = startPointInput.value.trim();
            if (startPoint) {
                // Search for the start point location using city as context
                const startLocation = await searchLocation(startPoint, cityInput);
                if (!startLocation) {
                    throw new Error('Could not find the specified start point');
                }

                // Use the start point coordinates for both center and start location
                const coordinates = [
                    parseFloat(startLocation.lat),
                    parseFloat(startLocation.lon)
                ];
                formData.center_coordinates = coordinates;
                formData.start_point = startLocation.display_name;
                formData.start_coordinates = coordinates;
                formData.location = cityLocation.display_name;
            } else {
                // If no start point, use city coordinates as center
                const cityCoordinates = [
                    parseFloat(cityLocation.lat),
                    parseFloat(cityLocation.lon)
                ];
                formData.center_coordinates = cityCoordinates;
                formData.location = cityLocation.display_name;
            }
            
            console.log('Form data:', formData);
            
            // Send request to generate route
            console.log('Sending route generation request');
            const response = await fetch('/generate', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(formData)
            });

            const data = await response.json();
            console.log('Received response:', data);

            if (!response.ok) {
                throw new Error(data.error || 'Failed to generate route');
            }

            // Show success message and download link
            resultContent.innerHTML = createSuccessMessage(data.gpx_file);
            result.classList.remove('hidden');

            // Automatically collapse the options section
            toggleForm(true);

            // Add event listener to file input
            const fileInput = document.getElementById('gpxFileInput');
            if (fileInput) {
                fileInput.addEventListener('change', function() {
                    handleFileUpload(this);
                });
            }

            // Display route on map with start coordinates
            displayRoute(data.gpx_file, formData.start_coordinates);
        } catch (error) {
            console.error('Error in route generation:', error);
            resultContent.innerHTML = `
                <div class="bg-red-50 border border-red-200 rounded-md p-4">
                    <div class="flex">
                        <div class="flex-shrink-0">
                            <svg class="h-5 w-5 text-red-400" viewBox="0 0 20 20" fill="currentColor">
                                <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"/>
                            </svg>
                        </div>
                        <div class="ml-3">
                            <p class="text-sm font-medium text-red-800">
                                ${error.message}
                            </p>
                        </div>
                    </div>
                </div>
            `;
            result.classList.remove('hidden');
        } finally {
            loading.classList.add('hidden');
        }
    });
});