document.addEventListener('DOMContentLoaded', function() {
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
    
    let map = null;
    let routeLayer = null;
    let segmentsLayer = null;
    let activitiesLayer = null;  // Add activities layer to track globally
    let baseRouteLayer = null;  // New layer for the original route

    function initMap(center) {
        if (map) {
            map.remove();
        }

        map = L.map('map').setView(center, 13);
        
        // Create custom panes with explicit z-index values
        map.createPane('activitiesPane');
        map.createPane('baseRoutePane');  // New pane for the original route
        map.createPane('routePane');
        map.createPane('segmentsPane');
        map.createPane('markersPane');  // New pane for start/end markers
        
        // Set z-index and pointer events for panes
        map.getPane('activitiesPane').style.zIndex = 300;
        map.getPane('baseRoutePane').style.zIndex = 350;
        map.getPane('routePane').style.zIndex = 400;
        map.getPane('segmentsPane').style.zIndex = 500;
        map.getPane('markersPane').style.zIndex = 600;  // Markers on top
        
        // Ensure pointer events are enabled
        map.getPane('activitiesPane').style.pointerEvents = 'auto';
        map.getPane('baseRoutePane').style.pointerEvents = 'auto';
        map.getPane('routePane').style.pointerEvents = 'auto';
        map.getPane('segmentsPane').style.pointerEvents = 'auto';
        map.getPane('markersPane').style.pointerEvents = 'auto';

        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        }).addTo(map);

        // Initialize layers with their respective panes but don't add to map yet
        activitiesLayer = L.featureGroup([], { pane: 'activitiesPane' });
        baseRouteLayer = L.featureGroup([], { pane: 'baseRoutePane' });
        routeLayer = L.featureGroup([], { pane: 'routePane' });
        segmentsLayer = L.featureGroup([], { pane: 'segmentsPane' });
    }

    function displayRoute(filename) {
        console.log('Attempting to display route for:', filename);
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

                // Show map container before initializing map
                mapContainer.classList.remove('hidden');

                // Initialize new map - this will clear all existing layers
                console.log('Initializing new map');
                initMap(center);

                // Add the route to the map
                console.log('Adding route to map');
                
                // Create a custom arrow icon using the SVG file
                const arrowIcon = L.divIcon({
                    html: `<div class="direction-arrow" style="width: 24px; height: 24px;">
                        <svg width="24" height="24" viewBox="0 0 24 24" version="1.1" xmlns="http://www.w3.org/2000/svg">
                            <path d="M12 2 L22 22 L12 18 L2 22 Z" 
                                  fill="#3388ff" 
                                  stroke="#ffffff" 
                                  stroke-width="2"
                                  stroke-linejoin="round"/>
                        </svg>
                    </div>`,
                    className: 'direction-marker',
                    iconSize: [24, 24],
                    iconAnchor: [12, 12]
                });
                
                console.log('Created arrow icon:', arrowIcon);
                
                // Add route lines first
                const routeFeatures = data.geojson.features.filter(f => f.properties.type === 'route');
                const directionFeatures = data.geojson.features.filter(f => f.properties.type === 'direction');
                
                console.log(`Found ${routeFeatures.length} route features and ${directionFeatures.length} direction features`);
                
                // Add original route to base route layer
                baseRouteLayer.clearLayers();
                
                // First add the route lines
                const baseRoute = L.geoJSON(routeFeatures, {
                    style: {
                        color: '#2563eb',  // dark blue
                        weight: 5,
                        opacity: 1.0,
                        pane: 'baseRoutePane'
                    }
                }).addTo(baseRouteLayer);
                
                // Add start and end markers if we have route coordinates
                if (routeFeatures.length > 0 && routeFeatures[0].geometry.coordinates.length > 0) {
                    const coordinates = routeFeatures[0].geometry.coordinates;
                    const startCoord = coordinates[0];
                    const endCoord = coordinates[coordinates.length - 1];
                    
                    console.log('Start coordinates:', startCoord);
                    console.log('End coordinates:', endCoord);
                    
                    // Check if start and end are the same or very close
                    const isSamePoint = startCoord[0] === endCoord[0] && startCoord[1] === endCoord[1];
                    console.log('Start and end are same point:', isSamePoint);
                    
                    // Create custom start marker icon
                    const startIcon = L.divIcon({
                        className: 'custom-marker-icon',
                        html: `<div style="background-color: #22c55e; width: 32px; height: 32px; border-radius: 50%; border: 3px solid white; box-shadow: 0 2px 4px rgba(0,0,0,0.3); display: flex; align-items: center; justify-content: center; z-index: 1001;">
                                <div style="color: white; font-weight: bold; font-size: 16px;">S</div>
                               </div>`,
                        iconSize: [32, 32],
                        iconAnchor: [16, 16]
                    });
                    
                    // Create custom end marker icon
                    const endIcon = L.divIcon({
                        className: 'custom-marker-icon',
                        html: `<div style="background-color: #ef4444; width: 32px; height: 32px; border-radius: 50%; border: 3px solid white; box-shadow: 0 2px 4px rgba(0,0,0,0.3); display: flex; align-items: center; justify-content: center; z-index: 1000;">
                                <div style="color: white; font-weight: bold; font-size: 16px;">E</div>
                               </div>`,
                        iconSize: [32, 32],
                        iconAnchor: [16, 16]
                    });
                    
                    // Create markers layer
                    const markersLayer = L.featureGroup([], { pane: 'markersPane' });
                    
                    // Add start marker (with slight offset if same point)
                    L.marker([startCoord[1] + (isSamePoint ? 0.0001 : 0), startCoord[0]], {
                        icon: startIcon,
                        pane: 'markersPane',
                        zIndexOffset: 1001  // Ensure start is above end
                    }).bindPopup('<div class="font-medium">Start Point</div>').addTo(markersLayer);
                    
                    // Add end marker
                    L.marker([endCoord[1], endCoord[0]], {
                        icon: endIcon,
                        pane: 'markersPane',
                        zIndexOffset: 1000
                    }).bindPopup('<div class="font-medium">End Point</div>').addTo(markersLayer);
                    
                    // Add markers layer to map
                    markersLayer.addTo(map);
                }
                
                // Add base route layer to map
                baseRouteLayer.addTo(map);
                
                // Then re-add direction markers
                routeLayer.clearLayers(); // Clear existing markers
                
                directionFeatures.forEach((feature, index) => {
                    const bearing = feature.properties.bearing;
                    const coords = feature.geometry.coordinates;
                    
                    // console.log(`Adding direction tick ${index + 1}:`, {coords, bearing});
                    
                    // Convert from [lng, lat] to [lat, lng] for Leaflet
                    const center = [coords[1], coords[0]];
                    
                    // Calculate base length and angles
                    const baseLength = 0.0001; // Keep the same size
                    const backAngle = 40; // Slightly tighter angle
                    
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
                    
                    // Create the arrow shape from front to back points
                    const tick = L.polyline([frontPoint, center, leftPoint, center, rightPoint], {
                        color: '#3388ff',
                        weight: 2.5,
                        opacity: 1.0,
                        pane: 'routePane'
                    }).addTo(routeLayer);
                });
                
                // Ensure routeLayer is added to map
                routeLayer.addTo(map);
                
                // Store the original route for later use
                const originalRoute = routeLayer.getLayers()[0];
                
                // Log layer information for debugging
                console.log('Route layer has features:', routeLayer.getLayers().length);
                console.log('Route layer is on map:', map.hasLayer(routeLayer));

                // Force a resize event after the map is visible
                setTimeout(() => {
                    console.log('Resizing map');
                    map.invalidateSize();
                    
                    // Fit map to route bounds with padding
                    console.log('Fitting map to bounds');
                    map.fitBounds([
                        [bounds.minLat, bounds.minLng],
                        [bounds.maxLat, bounds.maxLng]
                    ], { 
                        padding: [50, 50],
                        maxZoom: 15
                    });
                }, 100);

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
                        console.log('Total completion:', completionData.total_completion);
                        console.log('Total distance:', completionData.total_distance);
                        console.log('Completed distance:', completionData.completed_distance);
                        
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
                            const directionTicks = routeLayer.getLayers();
                            
                            // Clear only the segments layer
                            segmentsLayer.clearLayers();
                            routeLayer.clearLayers();

                            // Process incomplete segments
                            console.info(`Processing ${completionData.incomplete_segments.length} incomplete segments and ${completionData.completed_segments.length} completed segments`);
                            
                            completionData.incomplete_segments.forEach(segment => {
                                if (!segment.coordinates || segment.coordinates.length < 2) return;
                                
                                // Fix coordinate order: Convert from [lng, lat] to [lat, lng]
                                const correctedCoords = segment.coordinates.map(coord => [coord[1], coord[0]]);
                                
                                const line = L.polyline(correctedCoords, {
                                    color: '#ef4444',  // red
                                    weight: 5,
                                    opacity: 0.8,
                                    pane: 'segmentsPane',
                                    interactive: true
                                });
                                line.addTo(segmentsLayer);
                            });
                            
                            // Process complete segments
                            completionData.completed_segments.forEach(segment => {
                                if (!segment.coordinates || segment.coordinates.length < 2) return;
                                
                                // Fix coordinate order: Convert from [lng, lat] to [lat, lng]
                                const correctedCoords = segment.coordinates.map(coord => [coord[1], coord[0]]);
                                
                                const line = L.polyline(correctedCoords, {
                                    color: '#22c55e',  // green
                                    weight: 5,
                                    opacity: 0.8,
                                    pane: 'segmentsPane',
                                    interactive: true
                                });
                                line.addTo(segmentsLayer);
                            });

                            // Re-add direction ticks
                            directionTicks.forEach(tick => {
                                tick.addTo(routeLayer);
                            });

                            // Ensure proper layer order
                            activitiesLayer.remove();
                            baseRouteLayer.remove();
                            routeLayer.remove();
                            segmentsLayer.remove();
                            
                            // Add layers in correct order (bottom to top)
                            activitiesLayer.addTo(map);
                            baseRouteLayer.addTo(map);
                            segmentsLayer.addTo(map);
                            routeLayer.addTo(map);  // Add route layer last to keep ticks on top
                            
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

                        // Add layer control
                        const overlayMaps = {
                            "Route": routeLayer,
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
                        map.addLayer(routeLayer);
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
            feature_deadend: document.querySelector('input[name="featureDeadend"]').checked,
            exclude_completed: document.querySelector('input[name="excludeCompleted"]')?.checked || false,
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
            resultContent.innerHTML = `
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
                <div class="mt-4">
                    <a href="/download/${data.gpx_file}" 
                       class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500">
                        <svg class="mr-2 -ml-1 h-5 w-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>
                        </svg>
                        Download GPX File
                    </a>
                </div>
            `;
            result.classList.remove('hidden');

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