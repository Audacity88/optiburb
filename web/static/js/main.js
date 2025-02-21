// Global map variables
let map = null;
let routeLayer = null;
let segmentsLayer = null;
let activitiesLayer = null;
let baseRouteLayer = null;
let directionsLayer = null;  // Layer for direction arrows

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
    }

    // Create new map instance
    map = L.map('map').setView(center, 13);
    
    // Create custom panes with explicit z-index values
    map.createPane('activitiesPane');
    map.createPane('baseRoutePane');
    map.createPane('routePane');
    map.createPane('segmentsPane');
    map.createPane('directionsPane');  // New pane for directions
    
    // Set z-index and pointer events for panes
    map.getPane('activitiesPane').style.zIndex = 300;
    map.getPane('baseRoutePane').style.zIndex = 350;
    map.getPane('routePane').style.zIndex = 400;
    map.getPane('segmentsPane').style.zIndex = 500;
    map.getPane('directionsPane').style.zIndex = 600;  // Highest z-index for arrows
    
    // Ensure pointer events are enabled
    map.getPane('activitiesPane').style.pointerEvents = 'auto';
    map.getPane('baseRoutePane').style.pointerEvents = 'auto';
    map.getPane('routePane').style.pointerEvents = 'auto';
    map.getPane('segmentsPane').style.pointerEvents = 'auto';
    map.getPane('directionsPane').style.pointerEvents = 'none';  // No pointer events needed for arrows

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    // Initialize layers with their respective panes
    activitiesLayer = L.featureGroup([], { pane: 'activitiesPane' });
    baseRouteLayer = L.featureGroup([], { pane: 'baseRoutePane' });
    routeLayer = L.featureGroup([], { pane: 'routePane' });
    segmentsLayer = L.featureGroup([], { pane: 'segmentsPane' });
    directionsLayer = L.featureGroup([], { pane: 'directionsPane' });  // Initialize directions layer

    // Add layers to map in correct order
    activitiesLayer.addTo(map);
    baseRouteLayer.addTo(map);
    routeLayer.addTo(map);
    segmentsLayer.addTo(map);
    directionsLayer.addTo(map);

    // Force a resize event after the map is visible
    setTimeout(() => {
        map.invalidateSize();
    }, 100);
}

// Global function for displaying route
function displayRoute(filename) {
    console.log('Attempting to display route for:', filename);
    const mapContainer = document.getElementById('mapContainer');
    const result = document.getElementById('result');
    const resultContent = document.getElementById('resultContent');

    // Clear any existing completion information
    const existingCompletion = resultContent.querySelector('.bg-gray-50.border-gray-200');
    if (existingCompletion) {
        existingCompletion.remove();
    }

    // Show map container before initializing map
    mapContainer.classList.remove('hidden');

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

            console.log('Route bounds:', bounds);
            const center = [
                (bounds.minLat + bounds.maxLat) / 2,
                (bounds.minLng + bounds.maxLng) / 2
            ];
            console.log('Map center:', center);

            // Initialize new map - this will clear all existing layers
            console.log('Initializing new map');
            initMap(center);

            // Add route lines first
            const routeFeatures = data.geojson.features.filter(f => 
                !f.properties || 
                !f.properties.type || 
                f.properties.type === 'route' || 
                f.properties.type === 'straight_line'
            );
            const directionFeatures = data.geojson.features.filter(f => 
                f.properties && f.properties.type === 'direction'
            );
            
            // Log feature counts
            console.log(`Found ${routeFeatures.length} route features and ${directionFeatures.length} direction features`);

            // Log straight line segments specifically
            const straightLineFeatures = routeFeatures.filter(f => f.properties && f.properties.type === 'straight_line');
            console.log(`Found ${straightLineFeatures.length} straight line segments:`);
            straightLineFeatures.forEach((feature, index) => {
                const coords = feature.geometry.coordinates;
                console.log(`Straight line segment ${index + 1}:`);
                console.log(`  Start: (${coords[0][1]}, ${coords[0][0]})`);
                console.log(`  End: (${coords[coords.length-1][1]}, ${coords[coords.length-1][0]})`);
            });

            // Add original route to base route layer
            baseRouteLayer.clearLayers();
            
            // First add the route lines (without decorators)
            const baseRoute = L.geoJSON({
                type: "FeatureCollection",
                features: routeFeatures
            }, {
                style: function(feature) {
                    // Check if the feature is marked as a straight line
                    if (feature.properties && feature.properties.type === 'straight_line') {
                        console.log('Styling straight line segment');
                        return {
                            color: '#8b5cf6',  // purple color
                            weight: 4,
                            opacity: 1.0,
                            dashArray: '10, 10',  // dashed line
                            pane: 'segmentsPane'  // Use segments pane for higher z-index
                        };
                    }
                    
                    // Default style for normal roads
                    return {
                        color: '#0000ff',  // blue color
                        weight: 3,
                        opacity: 0.8,
                        pane: 'baseRoutePane'
                    };
                }
            }).addTo(baseRouteLayer);
            
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
                    color: '#3388ff',
                    weight: 3.5,  // Increased from 2.5 to match larger size
                    opacity: 1.0,
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
            map.fitBounds([
                [bounds.minLat, bounds.minLng],
                [bounds.maxLat, bounds.maxLng]
            ], { 
                padding: [50, 50],
                maxZoom: 15
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
                                    color: '#ff69b4',  // hot pink
                                    weight: 2,
                                    opacity: 0.6
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
                        
                        // Clear only the segments layer
                        segmentsLayer.clearLayers();
                        routeLayer.clearLayers();

                        // Process incomplete segments
                        console.info(`Processing ${completionData.incomplete_segments.length} incomplete segments and ${completionData.completed_segments.length} completed segments`);
                        
                        // First, collect all straight line segments coordinates for comparison
                        const straightLineSegments = [];
                        [...completionData.incomplete_segments, ...completionData.completed_segments].forEach(segment => {
                            if (segment.is_straight_line && segment.coordinates && segment.coordinates.length >= 2) {
                                straightLineSegments.push({
                                    start: segment.coordinates[0],
                                    end: segment.coordinates[segment.coordinates.length - 1]
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
                            const tolerance = 0.0003; // Approximately 30 meters at typical lat/lng scale
                            
                            return straightLineSegments.some(straightLine => {
                                // Check both forward and reverse directions
                                const forwardMatch = 
                                    getDistance(segmentStart, straightLine.start) < tolerance &&
                                    getDistance(segmentEnd, straightLine.end) < tolerance;
                                const reverseMatch = 
                                    getDistance(segmentStart, straightLine.end) < tolerance &&
                                    getDistance(segmentEnd, straightLine.start) < tolerance;
                                return forwardMatch || reverseMatch;
                            });
                        }

                        // Process incomplete segments
                        completionData.incomplete_segments.forEach(segment => {
                            if (!segment.coordinates || segment.coordinates.length < 2) return;
                            
                            // Fix coordinate order: Convert from [lng, lat] to [lat, lng]
                            const correctedCoords = segment.coordinates.map(coord => [coord[1], coord[0]]);
                            
                            // Check if this segment is part of a straight line connection
                            const isStraightLine = segment.is_straight_line || isPartOfStraightLine(segment.coordinates);
                            
                            const line = L.polyline(correctedCoords, {
                                color: isStraightLine ? '#8b5cf6' : '#ef4444',  // purple for straight lines, red for others
                                weight: isStraightLine ? 4 : 5,
                                opacity: 1.0,
                                dashArray: isStraightLine ? '10, 10' : null,
                                pane: 'segmentsPane',
                                interactive: true
                            });
                            line.addTo(segmentsLayer);
                        });
                        
                        // Process complete segments
                        completionData.completed_segments.forEach(segment => {
                            if (!segment.coordinates || segment.coordinates.length < 2) return;
                            
                            // Skip if this segment is part of a straight line connection in either direction
                            if (segment.is_straight_line || isPartOfStraightLine(segment.coordinates)) return;
                            
                            // Fix coordinate order: Convert from [lng, lat] to [lat, lng]
                            const correctedCoords = segment.coordinates.map(coord => [coord[1], coord[0]]);
                            
                            const line = L.polyline(correctedCoords, {
                                color: '#22c55e',  // green for completed segments
                                weight: 5,
                                opacity: 1.0,
                                pane: 'segmentsPane',
                                interactive: true
                            });
                            line.addTo(segmentsLayer);
                        });

                        // Re-add direction ticks to the directions layer
                        directionTicks.forEach(tick => {
                            tick.addTo(directionsLayer);
                        });

                        // Ensure proper layer order
                        activitiesLayer.remove();
                        baseRouteLayer.remove();
                        directionsLayer.remove();
                        segmentsLayer.remove();
                        
                        // Add layers in correct order (bottom to top)
                        activitiesLayer.addTo(map);
                        baseRouteLayer.addTo(map);
                        segmentsLayer.addTo(map);
                        directionsLayer.addTo(map);  // Add directions layer last to keep arrows on top
                        
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
                                color: '#ef4444',  // red
                                weight: 5,
                                opacity: 1.0
                            }
                        }).addTo(routeLayer);
                    }

                    // Log layer information
                    console.log('Activities layer has features:', activitiesLayer.getLayers().length > 0);
                    console.log('Route layer has features:', routeLayer.getLayers().length > 0);

                    // Add layer control with the new directions layer
                    const overlayMaps = {
                        "Route": baseRouteLayer,
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

                    // Ensure all layers are visible by default
                    map.addLayer(baseRouteLayer);
                    map.addLayer(directionsLayer);
                    map.addLayer(activitiesLayer);
                    map.addLayer(segmentsLayer);

                    // Force a map update
                    map.invalidateSize();

                    // Add completion status to the result content
                    const completionPercentage = (completionData.total_completion * 100).toFixed(1);
                    const totalDistance = (completionData.total_distance * 111).toFixed(1);  // Convert to km (rough approximation)
                    const completedDistance = (completionData.completed_distance * 111).toFixed(1);

                    const completionHtml = `
                        <div class="mt-4 bg-gray-50 border border-gray-200 rounded-md p-4">
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
                    resultContent.insertAdjacentHTML('beforeend', completionHtml);
                })
                .catch(error => {
                    console.error('Error loading completion data:', error);
                    // Add a message to the result content about needing Strava authentication
                    const authMessage = `
                        <div class="mt-4 bg-yellow-50 border border-yellow-200 rounded-md p-4">
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
                    resultContent.insertAdjacentHTML('beforeend', authMessage);
                    
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
        <div class="mt-4 flex space-x-4">
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
    // Handle buffer size slider updates
    const bufferSlider = document.getElementById('bufferSize');
    const bufferValue = document.getElementById('bufferSizeValue');
    if (bufferSlider && bufferValue) {
        bufferSlider.addEventListener('input', function() {
            bufferValue.textContent = `${this.value}m`;
        });
    }

    // Check for Strava activity fetch progress
    const urlParams = new URLSearchParams(window.location.search);
    const fetchId = document.cookie.split('; ').find(row => row.startsWith('strava_fetch_id'))?.split('=')[1];
    
    if (fetchId) {
        const progressModal = document.getElementById('stravaProgress');
        const progressBar = document.getElementById('stravaProgressBar');
        const progressStep = document.getElementById('stravaProgressStep');
        const progressPercent = document.getElementById('stravaProgressPercent');
        const progressMessage = document.getElementById('stravaProgressMessage');
        
        progressModal.classList.remove('hidden');
        
        // Set up SSE for progress updates
        const eventSource = new EventSource(`/strava/fetch-progress/${fetchId}`);
        
        eventSource.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);
                console.log('Strava progress update:', data);
                
                if (data.type === 'progress') {
                    if (data.step) progressStep.textContent = data.step;
                    if (data.progress) {
                        progressBar.style.width = `${data.progress}%`;
                        progressPercent.textContent = `${data.progress}%`;
                    }
                    if (data.message) progressMessage.textContent = data.message;
                } else if (data.type === 'done') {
                    console.log('Activity fetch complete');
                    eventSource.close();
                    // Hide progress modal after a short delay
                    setTimeout(() => {
                        progressModal.classList.add('hidden');
                    }, 1000);
                }
            } catch (error) {
                console.error('Error processing server message:', error, event.data);
                eventSource.close();
            }
        };
        
        eventSource.onerror = function(error) {
            console.error('EventSource error:', error);
            eventSource.close();
            progressModal.classList.add('hidden');
        };
    }

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
        }
        
        const sessionId = generateSessionId();
        console.log('Starting route generation with session:', sessionId);
        
        // Set up SSE for progress updates
        const eventSource = new EventSource(`/progress/${sessionId}`);
        
        eventSource.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);
                console.log('Progress update:', data);
                
                if (data.type === 'progress') {
                    updateProgress(data);
                } else if (data.type === 'error') {
                    console.error('Server error:', data.message);
                    eventSource.close();
                    loading.classList.add('hidden');
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
                                        ${data.message}
                                    </p>
                                </div>
                            </div>
                        </div>
                    `;
                    result.classList.remove('hidden');
                } else if (data.type === 'done') {
                    console.log('Processing complete');
                    eventSource.close();
                }
            } catch (error) {
                console.error('Error processing server message:', error, event.data);
                eventSource.close();
            }
        };

        eventSource.onerror = function(error) {
            console.error('EventSource error:', error);
            eventSource.close();
            loading.classList.add('hidden');
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
                                Connection error. Please try again.
                            </p>
                        </div>
                    </div>
                </div>
            `;
            result.classList.remove('hidden');
        };
        
        // Get form data
        const formData = {
            location: document.getElementById('location').value,
            start_point: document.getElementById('startPoint').value || null,
            simplify: document.querySelector('input[name="simplify"]').checked,
            prune: document.querySelector('input[name="prune"]').checked,
            simplify_gpx: document.querySelector('input[name="simplifyGpx"]').checked,
            exclude_completed: document.querySelector('input[name="excludeCompleted"]')?.checked || false,
            buffer: parseInt(document.getElementById('bufferSize').value), // Send buffer size in meters
            session_id: sessionId
        };
        
        console.log('Form data:', formData);
        
        try {
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

            // Add event listener to file input
            const fileInput = document.getElementById('gpxFileInput');
            if (fileInput) {
                fileInput.addEventListener('change', function() {
                    handleFileUpload(this);
                });
            }

            // Display route on map
            displayRoute(data.gpx_file);
        } catch (error) {
            console.error('Error in route generation:', error);
            // Show error message
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
            // Hide loading spinner
            loading.classList.add('hidden');
            // Close SSE connection if still open
            if (eventSource) {
                eventSource.close();
            }
        }
    });
});