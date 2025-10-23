window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

window.PHOTO_ORGANIZER.initLocationsMap = (locations) => {
    const map = new ol.Map({
        target: 'map',
        layers: [
            new ol.layer.Tile({
                source: new ol.source.OSM()
            })
        ],
        view: new ol.View({
            center: ol.proj.fromLonLat([0, 0]),
            zoom: 2
        })
    });

    const allFeatures = locations.map(location => {
        return new ol.Feature({
            geometry: new ol.geom.Point(
                ol.proj.fromLonLat([location.lon, location.lat])
            ),
            name: location.name,
            id: location.id,
            photo_path: location.photo_path,
            filename: location.filename
        });
    });

    const vectorSource = new ol.source.Vector({
        features: allFeatures
    });

    const clusterSource = new ol.source.Cluster({
        distance: 40,
        source: vectorSource
    });

    const selectedFeatures = new Set();

    const styleCache = {};
    const clusterLayer = new ol.layer.Vector({
        source: clusterSource,
        style: function(feature) {
            const size = feature.get('features').length;
            const isSelected = selectedFeatures.has(feature);
            const cacheKey = `${size}-${isSelected}`;

            let style = styleCache[cacheKey];
            if (!style) {
                const fillColor = isSelected ? '#28a745' : '#007BFF';
                if (size > 1) {
                    style = new ol.style.Style({
                        image: new ol.style.Circle({
                            radius: 15,
                            fill: new ol.style.Fill({ color: fillColor }),
                            stroke: new ol.style.Stroke({
                                color: '#fff',
                                width: 2
                            })
                        }),
                        text: new ol.style.Text({
                            text: size.toString(),
                            fill: new ol.style.Fill({ color: '#fff' }),
                            font: 'bold 12px sans-serif'
                        })
                    });
                } else {
                    style = new ol.style.Style({
                        image: new ol.style.Circle({
                            radius: 7,
                            fill: new ol.style.Fill({ color: fillColor }),
                            stroke: new ol.style.Stroke({
                                color: '#fff',
                                width: 2
                            })
                        })
                    });
                }
                styleCache[cacheKey] = style;
            }
            return style;
        }
    });

    map.addLayer(clusterLayer);

    const dragBox = new ol.interaction.DragBox({
        condition: ol.events.condition.shiftKeyOnly
    });

    map.addInteraction(dragBox);

    dragBox.on('boxend', function() {
        const extent = dragBox.getGeometry().getExtent();
        const boxFeatures = [];

        clusterSource.getFeatures().forEach(function(feature) {
            if (ol.extent.intersects(extent, feature.getGeometry().getExtent())) {
                boxFeatures.push(feature);
            }
        });

        selectedFeatures.clear();
        boxFeatures.forEach(f => selectedFeatures.add(f));
        clusterLayer.changed();
        updateSelectionPanel();
    });

    dragBox.on('boxstart', function() {
        selectedFeatures.clear();
        clusterLayer.changed();
    });

    if (locations.length > 0) {
        const extent = vectorSource.getExtent();
        map.getView().fit(extent, {
            padding: [50, 50, 50, 50],
            maxZoom: 16
        });
    }

    const popup = document.getElementById('popup');
    const popupContent = document.getElementById('popup-content');
    const popupCloser = document.getElementById('popup-closer');

    const overlay = new ol.Overlay({
        element: popup,
        autoPan: {
            animation: {
                duration: 250
            }
        }
    });

    map.addOverlay(overlay);

    popupCloser.onclick = function() {
        overlay.setPosition(undefined);
        popupCloser.blur();
        return false;
    };

    const showPhotoInPopup = (photoData, coordinate) => {
        debugger
        const photoUrl = window.APP_CONFIG.buildUrl('photo', { photo_id: photoData.id });
        const photoViewUrl = window.APP_CONFIG.buildUrl('photo_view', { photo_id: photoData.id });

        popupContent.innerHTML = `
            <div class="popup-photo-container">
                <a href="${photoViewUrl}" target="_blank">
                    <img src="${photoUrl}" alt="${photoData.name}" class="popup-photo">
                </a>
            </div>
            <h3>${photoData.name}</h3>
            <p class="photo-location">${photoData.location || 'Unknown Location'}</p>
        `;
        overlay.setPosition(coordinate);
    };

    map.on('click', function(evt) {
        const feature = map.forEachFeatureAtPixel(evt.pixel, function(feature) {
            return feature;
        });

        if (feature) {
            const features = feature.get('features');
            const coordinate = feature.getGeometry().getCoordinates();

            if (features && features.length > 1) {
                const photosData = features.map(f => ({
                    name: f.get('filename'),
                    location: f.get('name'),
                    id: f.get('id'),
                    photo_path: f.get('photo_path')
                }));

                const selectId = 'photo-select-' + Date.now();
                const selectOptions = photosData.map((photo, idx) =>
                    `<option value="${idx}">${photo.name}</option>`
                ).join('');

                const firstPhoto = photosData[0];
                const photoUrl = window.APP_CONFIG.buildUrl('photo', { photo_id: firstPhoto.id });
                const photoViewUrl = window.APP_CONFIG.buildUrl('photo_view', { photo_id: firstPhoto.id });

                popupContent.innerHTML = `
                    <div class="popup-select-container">
                        <label for="${selectId}">Select Photo (${photosData.length} total):</label>
                        <select id="${selectId}" class="photo-select">
                            ${selectOptions}
                        </select>
                    </div>
                    <div class="popup-photo-container">
                        <a id="photo-link" href="${photoViewUrl}" target="_blank">
                            <img id="photo-img" src="${photoUrl}" alt="${firstPhoto.name}" class="popup-photo">
                        </a>
                    </div>
                    <h3 id="photo-name">${firstPhoto.name}</h3>
                    <p class="photo-location">${firstPhoto.location || 'Unknown Location'}</p>
                `;

                const selectElement = document.getElementById(selectId);
                selectElement.addEventListener('change', function(e) {
                    const selectedIndex = parseInt(e.target.value);
                    const selectedPhoto = photosData[selectedIndex];

                    const newPhotoUrl = window.APP_CONFIG.buildUrl('photo', { photo_id: selectedPhoto.id });
                    const newPhotoViewUrl = window.APP_CONFIG.buildUrl('photo_view', { photo_id: selectedPhoto.id });

                    document.getElementById('photo-img').src = newPhotoUrl;
                    document.getElementById('photo-img').alt = selectedPhoto.name;
                    document.getElementById('photo-link').href = newPhotoViewUrl;
                    document.getElementById('photo-name').textContent = selectedPhoto.name;
                    document.querySelector('.photo-location').textContent = selectedPhoto.location || 'Unknown Location';
                });

                overlay.setPosition(coordinate);
            } else if (features && features.length === 1) {
                const singleFeature = features[0];
                const photoData = {
                    name: singleFeature.get('filename'),
                    location: singleFeature.get('name'),
                    id: singleFeature.get('id'),
                    photo_path: singleFeature.get('photo_path')
                };
                showPhotoInPopup(photoData, coordinate);
            }
        } else {
            overlay.setPosition(undefined);
        }
    });

    map.on('pointermove', function(evt) {
        const pixel = map.getEventPixel(evt.originalEvent);
        const hit = map.hasFeatureAtPixel(pixel);
        const targetElement = map.getTargetElement();
        if (targetElement) {
            targetElement.style.cursor = hit ? 'pointer' : '';
        }
    });

    const calculateCentroid = (features) => {
        let totalLat = 0;
        let totalLon = 0;

        features.forEach(f => {
            const coords = ol.proj.toLonLat(f.getGeometry().getCoordinates());
            totalLon += coords[0];
            totalLat += coords[1];
        });

        return {
            lat: totalLat / features.length,
            lon: totalLon / features.length
        };
    };

    const getRecommendedLocation = async (lat, lon) => {
        try {
            const response = await fetch('/locations/reverse-geocode', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ lat, lon })
            });

            if (response.ok) {
                const data = await response.json();
                return data.location_name;
            }
        } catch (error) {
            console.error('Error fetching recommended location:', error);
        }
        return null;
    };

    const updateSelectionPanel = async () => {
        const panel = document.getElementById('selection-panel');
        const panelContent = document.getElementById('selection-panel-content');

        if (selectedFeatures.size === 0) {
            panel.classList.remove('active');
            return;
        }

        const selectedClusters = Array.from(selectedFeatures).map((clusterFeature, idx) => {
            const features = clusterFeature.get('features');
            const photoIds = features.map(f => f.get('id'));

            const locationCounts = {};
            features.forEach(f => {
                const locationName = f.get('name') || 'Unknown Location';
                locationCounts[locationName] = (locationCounts[locationName] || 0) + 1;
            });

            const locationBreakdown = Object.entries(locationCounts)
                .sort((a, b) => b[1] - a[1])
                .map(([name, count]) => `${count} ${name}`)
                .join(', ');

            const centroid = calculateCentroid(features);

            return {
                index: idx,
                photoCount: features.length,
                photoIds: photoIds,
                locationBreakdown: locationBreakdown,
                locationCounts: locationCounts,
                centroid: centroid,
                recommendedLocation: null
            };
        });

        const allPhotoIds = selectedClusters.flatMap(c => c.photoIds);
        const totalPhotos = allPhotoIds.length;

        const allLocationCounts = {};
        selectedClusters.forEach(cluster => {
            Object.entries(cluster.locationCounts).forEach(([name, count]) => {
                if (name !== 'Unknown Location') {
                    allLocationCounts[name] = (allLocationCounts[name] || 0) + count;
                }
            });
        });

        const sortedLocations = Object.entries(allLocationCounts)
            .sort((a, b) => b[1] - a[1]);

        panelContent.innerHTML = `
            <h3>Mass Assignment</h3>
            <div class="mass-assignment-info">
                <strong>${totalPhotos} photo${totalPhotos > 1 ? 's' : ''}</strong> in
                <strong>${selectedClusters.length} cluster${selectedClusters.length > 1 ? 's' : ''}</strong>
            </div>

            ${sortedLocations.length > 0 ? `
                <div class="quick-actions">
                    <div class="quick-action-label">Quick assign:</div>
                    <div class="quick-actions-buttons">
                        ${sortedLocations.map(([name, count]) => `
                            <button class="btn-quick-assign"
                                    data-photo-ids="${allPhotoIds.join(',')}"
                                    data-location-name="${name}">
                                ${name} (${count})
                            </button>
                        `).join('')}
                    </div>
                </div>
            ` : ''}

            <div class="cluster-assign">
                <input type="text"
                       class="location-input"
                       placeholder="Or enter custom location"
                       id="mass-location-input">
                <button class="btn-assign"
                        data-photo-ids="${allPhotoIds.join(',')}"
                        id="mass-assign-btn">
                    Assign to All
                </button>
            </div>

            <div class="clusters-summary">
                <div class="summary-label">Selected clusters:</div>
                <div class="clusters-list">
                    ${selectedClusters.map(cluster => `
                        <div class="cluster-summary-item">
                            <strong>${cluster.photoCount} photo${cluster.photoCount > 1 ? 's' : ''}</strong>
                            <span class="current-location">${cluster.locationBreakdown}</span>
                        </div>
                    `).join('')}
                </div>
            </div>

            <button class="btn-clear-selection">Clear Selection</button>
        `;

        const assignLocation = async (photoIds, locationName) => {
            try {
                const response = await fetch('/locations/bulk-update', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        photo_ids: photoIds,
                        location_name: locationName
                    })
                });

                if (response.ok) {
                    window.notification.success(`Updated ${photoIds.length} photo(s) to "${locationName}"`);

                    allFeatures.forEach(feature => {
                        const featureId = feature.get('id');
                        if (photoIds.includes(featureId)) {
                            feature.set('name', locationName);
                        }
                    });

                    const filterCheckbox = document.getElementById('filter-unnamed');
                    if (filterCheckbox && filterCheckbox.checked) {
                        applyFilter(true);
                    } else {
                        clusterLayer.changed();
                    }

                    selectedFeatures.clear();
                    clusterLayer.changed();
                    updateSelectionPanel();

                    return true;
                } else {
                    window.notification.error('Failed to update locations');
                    return false;
                }
            } catch (error) {
                window.notification.error('Error updating locations');
                console.error(error);
                return false;
            }
        };

        document.querySelectorAll('.btn-quick-assign').forEach(btn => {
            btn.addEventListener('click', async function() {
                const photoIds = this.dataset.photoIds.split(',').map(Number);
                const locationName = this.dataset.locationName;
                await assignLocation(photoIds, locationName);
            });
        });

        const massAssignBtn = document.getElementById('mass-assign-btn');
        if (massAssignBtn) {
            massAssignBtn.addEventListener('click', async function() {
                const photoIds = this.dataset.photoIds.split(',').map(Number);
                const input = document.getElementById('mass-location-input');
                const newLocationName = input.value.trim();

                if (!newLocationName) {
                    window.notification.error('Please enter a location name');
                    return;
                }

                const success = await assignLocation(photoIds, newLocationName);
                if (success) {
                    input.value = '';
                }
            });
        }

        document.querySelector('.btn-clear-selection').addEventListener('click', () => {
            selectedFeatures.clear();
            clusterLayer.changed();
            updateSelectionPanel();
        });

        panel.classList.add('active');

        (async () => {
            const firstCluster = selectedClusters[0];
            if (firstCluster && firstCluster.centroid) {
                const recommendedLocation = await getRecommendedLocation(firstCluster.centroid.lat, firstCluster.centroid.lon);

                if (recommendedLocation) {
                    const recommendedSection = document.createElement('div');
                    recommendedSection.className = 'quick-actions';
                    recommendedSection.innerHTML = `
                        <div class="quick-action-label">Recommended:</div>
                        <button class="btn-quick-assign btn-recommended"
                                data-photo-ids="${allPhotoIds.join(',')}"
                                data-location-name="${recommendedLocation}">
                            ${recommendedLocation}
                        </button>
                    `;

                    const quickActionsSection = panelContent.querySelector('.quick-actions');
                    if (quickActionsSection) {
                        panelContent.insertBefore(recommendedSection, quickActionsSection);
                    } else {
                        const clusterAssign = panelContent.querySelector('.cluster-assign');
                        panelContent.insertBefore(recommendedSection, clusterAssign);
                    }

                    const newRecommendedBtn = recommendedSection.querySelector('.btn-quick-assign');
                    newRecommendedBtn.addEventListener('click', async function() {
                        const photoIds = this.dataset.photoIds.split(',').map(Number);
                        const locationName = this.dataset.locationName;
                        await assignLocation(photoIds, locationName);
                    });
                }
            }
        })();
    };

    const applyFilter = (showOnlyUnnamed) => {
        vectorSource.clear();

        if (showOnlyUnnamed) {
            const unnamedFeatures = allFeatures.filter(feature => !feature.get('name'));
            vectorSource.addFeatures(unnamedFeatures);
        } else {
            vectorSource.addFeatures(allFeatures);
        }

        selectedFeatures.clear();
        clusterLayer.changed();
        updateSelectionPanel();

        if (vectorSource.getFeatures().length > 0) {
            const extent = vectorSource.getExtent();
            map.getView().fit(extent, {
                padding: [50, 50, 50, 50],
                maxZoom: 16
            });
        }
    };

    const filterCheckbox = document.getElementById('filter-unnamed');
    if (filterCheckbox) {
        filterCheckbox.addEventListener('change', (e) => {
            applyFilter(e.target.checked);
        });
    }

    return { map, vectorSource, selectedFeatures, updateSelectionPanel, applyFilter };
};