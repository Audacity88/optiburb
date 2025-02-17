document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('routeForm');
    const loading = document.getElementById('loading');
    const result = document.getElementById('result');
    const resultContent = document.getElementById('resultContent');
    const mapContainer = document.getElementById('mapContainer');
    const showSegmentsCheckbox = document.getElementById('showSegments');
    
    let map = null;
    let routeLayer = null;
    let segmentsLayer = null;
    let activitiesLayer = null;  // Add activities layer to track globally

    function initMap(center) {
        if (map) {
            map.remove();
        }

        map = L.map('map').setView(center, 13);
        
        // Create custom panes with explicit z-index values
        map.createPane('activitiesPane');
        map.createPane('routePane');
        map.createPane('segmentsPane');
        
        // Set z-index and pointer events for panes
        map.getPane('activitiesPane').style.zIndex = 300;
        map.getPane('routePane').style.zIndex = 400;
        map.getPane('segmentsPane').style.zIndex = 500;
        
        // Ensure pointer events are enabled
        map.getPane('activitiesPane').style.pointerEvents = 'auto';
        map.getPane('routePane').style.pointerEvents = 'auto';
        map.getPane('segmentsPane').style.pointerEvents = 'auto';

        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        }).addTo(map);

        // Initialize layers with their respective panes
        activitiesLayer = L.featureGroup([], { pane: 'activitiesPane' });
        routeLayer = L.featureGroup([], { pane: 'routePane' });
        segmentsLayer = L.featureGroup([], { pane: 'segmentsPane' });
        
        // Add layers to map
        activitiesLayer.addTo(map);
        routeLayer.addTo(map);
        segmentsLayer.addTo(map);
    }

    function displaySegments(bounds) {
        if (!showSegmentsCheckbox) return;

        fetch(`/strava/segments?bounds=${JSON.stringify(bounds)}`)
            .then(response => response.json())
            .then(data => {
                segmentsLayer.clearLayers();

                if (!data.segments) return;

                data.segments.forEach(segment => {
                    const line = L.polyline(segment.points, {
                        color: segment.completed ? '#22c55e' : '#f97316',
                        weight: 4,
                        opacity: 0.7
                    });

                    const popup = L.popup().setContent(`
                        <div class="text-sm">
                            <p class="font-medium">${segment.name}</p>
                            <p class="text-gray-600">Distance: ${(segment.distance / 1000).toFixed(1)}km</p>
                            <p class="text-gray-600">Elevation: ${segment.total_elevation_gain}m</p>
                            ${segment.completed ? '<p class="text-green-600">✓ Completed</p>' : ''}
                        </div>
                    `);

                    line.bindPopup(popup);
                    line.addTo(segmentsLayer);
                });
            })
            .catch(error => {
                console.error('Error loading segments:', error);
            });
    }

    if (showSegmentsCheckbox) {
        showSegmentsCheckbox.addEventListener('change', function() {
            segmentsLayer.clearLayers();
            if (this.checked && map) {
                const bounds = map.getBounds();
                const boundingBox = {
                    minLat: bounds.getSouth(),
                    maxLat: bounds.getNorth(),
                    minLng: bounds.getWest(),
                    maxLng: bounds.getEast()
                };
                displaySegments(boundingBox);
            }
        });
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
                const routeGeoJSON = L.geoJSON(data.geojson, {
                    style: {
                        color: '#2563eb',
                        weight: 5,
                        opacity: 1.0
                    }
                }).addTo(routeLayer);
                console.log('Added initial route to map');

                // Store the original route for later use
                const originalRoute = routeGeoJSON;

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

                    // Display segments if checkbox is checked
                    if (showSegmentsCheckbox && showSegmentsCheckbox.checked) {
                        displaySegments(bounds);
                    }
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
                            console.log('Updating route with completion data');
                            
                            // Clear existing layers
                            routeLayer.clearLayers();
                            segmentsLayer.clearLayers();

                            // Log the current state of layers
                            console.log('Layer status after clearing:');
                            console.log('- Route layer empty:', routeLayer.getLayers().length === 0);
                            console.log('- Segments layer empty:', segmentsLayer.getLayers().length === 0);
                            
                            // Process incomplete segments
                            console.log(`Processing ${completionData.incomplete_segments.length} incomplete segments`);
                            completionData.incomplete_segments.forEach((segment, index) => {
                                console.log(`Adding incomplete segment ${index + 1}:`, JSON.stringify(segment.coordinates));
                                if (!segment.coordinates || segment.coordinates.length < 2) {
                                    console.error(`Invalid coordinates for incomplete segment ${index + 1}`);
                                    return;
                                }
                                
                                // Fix coordinate order: Convert from [lng, lat] to [lat, lng]
                                const correctedCoords = segment.coordinates.map(coord => [coord[1], coord[0]]);
                                
                                const line = L.polyline(correctedCoords, {
                                    color: '#ef4444',  // red
                                    weight: 5,
                                    opacity: 1.0,
                                    pane: 'segmentsPane',
                                    interactive: true
                                });
                                line.addTo(segmentsLayer);
                                
                                console.log(`Added incomplete segment ${index + 1} to segments layer with corrected coordinates:`, correctedCoords);
                            });
                            
                            // Process complete segments
                            console.log(`Processing ${completionData.completed_segments.length} completed segments`);
                            completionData.completed_segments.forEach((segment, index) => {
                                console.log(`Adding completed segment ${index + 1}:`, JSON.stringify(segment.coordinates));
                                if (!segment.coordinates || segment.coordinates.length < 2) {
                                    console.error(`Invalid coordinates for completed segment ${index + 1}`);
                                    return;
                                }
                                
                                // Fix coordinate order: Convert from [lng, lat] to [lat, lng]
                                const correctedCoords = segment.coordinates.map(coord => [coord[1], coord[0]]);
                                
                                const line = L.polyline(correctedCoords, {
                                    color: '#22c55e',  // green
                                    weight: 5,
                                    opacity: 1.0,
                                    pane: 'segmentsPane',
                                    interactive: true
                                });
                                line.addTo(segmentsLayer);
                                
                                console.log(`Added completed segment ${index + 1} to segments layer with corrected coordinates:`, correctedCoords);
                            });

                            // Ensure proper layer order
                            activitiesLayer.remove();
                            routeLayer.remove();
                            segmentsLayer.remove();
                            
                            activitiesLayer.addTo(map);
                            routeLayer.addTo(map);
                            segmentsLayer.addTo(map);
                            
                            // Force map update and redraw
                            map.invalidateSize();
                            map._onResize();

                            // Log final status
                            console.log('Final layer visibility check:');
                            console.log('- Activities layer visible:', activitiesLayer.getLayers().length > 0);
                            console.log('- Route layer visible:', routeLayer.getLayers().length > 0);
                            console.log('- Segments layer visible:', segmentsLayer.getLayers().length > 0);
                            console.log('- Segments coordinates sample:', 
                                segmentsLayer.getLayers().length > 0 ? 
                                segmentsLayer.getLayers()[0].getLatLngs() : 'No segments');
                        } else {
                            console.log('No segment data available, showing original route in red');
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
                            "Activities": activitiesLayer
                        };

                        if (map._layers_control) {
                            map._layers_control.remove();
                        }
                        map._layers_control = L.control.layers(null, overlayMaps).addTo(map);

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
        
        // Set up SSE for progress updates
        const eventSource = new EventSource(`/progress/${sessionId}`);
        
        eventSource.onmessage = function(event) {
            const data = JSON.parse(event.data);
            
            if (data.type === 'progress') {
                updateProgress(data);
            } else if (data.type === 'error') {
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
                eventSource.close();
            }
        };
        
        // Get form data
        const formData = {
            location: document.getElementById('location').value,
            start_point: document.getElementById('startPoint').value || null,
            simplify: document.querySelector('input[name="simplify"]').checked,
            prune: document.querySelector('input[name="prune"]').checked,
            simplify_gpx: document.querySelector('input[name="simplifyGpx"]').checked,
            feature_deadend: document.querySelector('input[name="featureDeadend"]').checked,
            session_id: sessionId
        };

        try {
            // Send request to generate route
            const response = await fetch('/generate', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(formData)
            });

            const data = await response.json();

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