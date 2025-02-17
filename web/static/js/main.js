document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('routeForm');
    const loading = document.getElementById('loading');
    const result = document.getElementById('result');
    const resultContent = document.getElementById('resultContent');
    const mapContainer = document.getElementById('mapContainer');
    
    let map = null;
    let routeLayer = null;

    function initMap(center) {
        if (map) {
            map.remove();
        }

        map = L.map('map').setView(center, 13);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        }).addTo(map);

        routeLayer = L.layerGroup().addTo(map);
    }

    function displayRoute(filename) {
        fetch(`/route/${filename}`)
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    throw new Error(data.error);
                }

                const bounds = data.bounds;
                if (!bounds) {
                    throw new Error('No bounds data available for the route');
                }

                console.log('Route bounds:', bounds);
                const center = [
                    (bounds.minLat + bounds.maxLat) / 2,
                    (bounds.minLng + bounds.maxLng) / 2
                ];

                // Show map container before initializing map
                mapContainer.classList.remove('hidden');

                // Initialize map if not already done
                if (!map) {
                    initMap(center);
                }

                // Clear existing route
                routeLayer.clearLayers();

                // Add new route
                const route = L.geoJSON(data.geojson, {
                    style: {
                        color: '#2563eb',
                        weight: 3,
                        opacity: 0.8
                    }
                }).addTo(routeLayer);

                // Force a resize event after the map is visible
                setTimeout(() => {
                    map.invalidateSize();
                    
                    // Fit map to route bounds with padding
                    map.fitBounds([
                        [bounds.minLat, bounds.minLng],
                        [bounds.maxLat, bounds.maxLng]
                    ], { 
                        padding: [50, 50],
                        maxZoom: 15
                    });
                }, 100);
            })
            .catch(error => {
                console.error('Error loading route:', error);
                resultContent.innerHTML += `
                    <div class="mt-4 bg-red-50 border border-red-200 rounded-md p-4">
                        <div class="flex">
                            <div class="ml-3">
                                <p class="text-sm font-medium text-red-800">
                                    Error displaying route: ${error.message}
                                </p>
                            </div>
                        </div>
                    </div>
                `;
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