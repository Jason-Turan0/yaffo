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

    const vectorSource = new ol.source.Vector();

    locations.forEach(location => {
        const feature = new ol.Feature({
            geometry: new ol.geom.Point(
                ol.proj.fromLonLat([location.lon, location.lat])
            ),
            name: location.name,
            id: location.id,
            photo_path: location.photo_path,
            filename: location.filename
        });
        vectorSource.addFeature(feature);
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
        const photoUrl = window.APP_CONFIG.buildUrl('photo', { filename: photoData.photo_path });
        const photoViewUrl = window.APP_CONFIG.buildUrl('photo_view', { filename: photoData.photo_path });

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
                const photoUrl = window.APP_CONFIG.buildUrl('photo', { filename: firstPhoto.photo_path });
                const photoViewUrl = window.APP_CONFIG.buildUrl('photo_view', { filename: firstPhoto.photo_path });

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

                    const newPhotoUrl = window.APP_CONFIG.buildUrl('photo', { filename: selectedPhoto.photo_path });
                    const newPhotoViewUrl = window.APP_CONFIG.buildUrl('photo_view', { filename: selectedPhoto.photo_path });

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
        map.getTarget().style.cursor = hit ? 'pointer' : '';
    });

    const updateSelectionPanel = () => {
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

            return {
                index: idx,
                photoCount: features.length,
                photoIds: photoIds,
                locationBreakdown: locationBreakdown
            };
        });

        panelContent.innerHTML = `
            <h3>Selected Clusters (${selectedClusters.length})</h3>
            <div class="clusters-list">
                ${selectedClusters.map(cluster => `
                    <div class="cluster-item" data-cluster-index="${cluster.index}">
                        <div class="cluster-info">
                            <strong>${cluster.photoCount} photo${cluster.photoCount > 1 ? 's' : ''}</strong>
                            <span class="current-location">${cluster.locationBreakdown}</span>
                        </div>
                        <div class="cluster-assign">
                            <input type="text"
                                   class="location-input"
                                   placeholder="New location name"
                                   data-cluster-index="${cluster.index}">
                            <button class="btn-assign"
                                    data-cluster-index="${cluster.index}"
                                    data-photo-ids="${cluster.photoIds.join(',')}">
                                Assign
                            </button>
                        </div>
                    </div>
                `).join('')}
            </div>
            <button class="btn-clear-selection">Clear Selection</button>
        `;

        document.querySelectorAll('.btn-assign').forEach(btn => {
            btn.addEventListener('click', async function() {
                const photoIds = this.dataset.photoIds.split(',').map(Number);
                const clusterIndex = parseInt(this.dataset.clusterIndex);
                const input = document.querySelector(`input[data-cluster-index="${clusterIndex}"]`);
                const newLocationName = input.value.trim();

                if (!newLocationName) {
                    window.notification.error('Please enter a location name');
                    return;
                }

                try {
                    const response = await fetch('/locations/bulk-update', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            photo_ids: photoIds,
                            location_name: newLocationName
                        })
                    });

                    if (response.ok) {
                        window.notification.success(`Updated ${photoIds.length} photo(s)`);
                        input.value = '';
                        setTimeout(() => window.location.reload(), 1000);
                    } else {
                        window.notification.error('Failed to update locations');
                    }
                } catch (error) {
                    window.notification.error('Error updating locations');
                    console.error(error);
                }
            });
        });

        document.querySelector('.btn-clear-selection').addEventListener('click', () => {
            selectedFeatures.clear();
            clusterLayer.changed();
            updateSelectionPanel();
        });

        panel.classList.add('active');
    };

    return { map, vectorSource, selectedFeatures, updateSelectionPanel };
};