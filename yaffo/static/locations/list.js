// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.locations = window.PHOTO_ORGANIZER.locations || {};

/**
 * @param {LocationMediaItem[]} locations
 * @param {I18nService} i18n
 * @param {AppConfig} config
 * @returns {LocationMapApi}
 */
window.PHOTO_ORGANIZER.locations.initMap = (locations, i18n, config) => {
    const escapeHtml = (/** @type {unknown} */ value) => String(value ?? '').replace(
        /[&<>"']/g,
        (character) => /** @type {Record<string, string>} */ ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
        })[character],
    );
    const unknownLocation = i18n.t('locations:unknownLocation');
    const tokens = getComputedStyle(document.documentElement);
    const markerColors = {
        base: tokens.getPropertyValue('--color-accent').trim(),
        selected: tokens.getPropertyValue('--color-success').trim(),
        contrast: tokens.getPropertyValue('--color-on-accent').trim()
    };

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
            filename: location.filename,
            media_type: location.media_type
        });
    });

    const vectorSource = new ol.source.Vector({
        features: allFeatures
    });

    const clusterSource = new ol.source.Cluster({
        distance: 40,
        source: vectorSource
    });

    /** @type {Set<any>} */
    const selectedFeatures = new Set();

    /** @type {Record<string, any>} */
    const styleCache = {};
    const clusterLayer = new ol.layer.Vector({
        source: clusterSource,
        style: function(/** @type {any} */ feature) {
            const size = feature.get('features').length;
            const isSelected = selectedFeatures.has(feature);
            const cacheKey = `${size}-${isSelected}`;

            let style = styleCache[cacheKey];
            if (!style) {
                const fillColor = isSelected ? markerColors.selected : markerColors.base;
                if (size > 1) {
                    style = new ol.style.Style({
                        image: new ol.style.Circle({
                            radius: 15,
                            fill: new ol.style.Fill({ color: fillColor }),
                            stroke: new ol.style.Stroke({
                                color: markerColors.contrast,
                                width: 2
                            })
                        }),
                        text: new ol.style.Text({
                            text: i18n.number(size),
                            fill: new ol.style.Fill({ color: markerColors.contrast }),
                            font: 'bold 12px sans-serif'
                        })
                    });
                } else {
                    style = new ol.style.Style({
                        image: new ol.style.Circle({
                            radius: 7,
                            fill: new ol.style.Fill({ color: fillColor }),
                            stroke: new ol.style.Stroke({
                                color: markerColors.contrast,
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
        /** @type {any[]} */
        const boxFeatures = [];

        clusterSource.getFeatures().forEach(function(/** @type {any} */ feature) {
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
    if (!popup || !popupContent || !popupCloser) {
        throw new Error('Location map popup markup is incomplete');
    }

    const overlay = new ol.Overlay({
        element: popup,
        autoPan: {
            animation: {
                duration: 250
            }
        }
    });

    map.addOverlay(overlay);

    popupCloser.addEventListener('click', (event) => {
        event.preventDefault();
        overlay.setPosition(undefined);
        popupCloser.blur();
    });

    // A video's media route returns the raw clip, so the popup thumbnail
    // points at its poster frame (falling back to the placeholder when there's no
    // poster, via data-fallback + initImageFallbacks). Photos use /media directly.
    const thumbUrl = (/** @type {LocationMediaItem} */ item) => item.media_type === 'video'
        ? config.buildUrl('media_poster', { media_item_id: item.id })
        : config.buildUrl('media', { media_item_id: item.id });

    const fallbackUrl = config.urls.placeholder;

    /**
     * @param {LocationMediaItem} photoData
     * @param {any} coordinate
     */
    const showPhotoInPopup = (photoData, coordinate) => {
        const photoViewUrl = config.buildUrl('media_view', { media_item_id: photoData.id });
        const safeName = escapeHtml(photoData.name);

        popupContent.innerHTML = `
            <div class="popup-photo-container">
                <a href="${photoViewUrl}" target="_blank">
                    <img src="${thumbUrl(photoData)}" data-fallback="${fallbackUrl}" alt="${safeName}" class="popup-photo">
                </a>
            </div>
            <h3>${safeName}</h3>
            <p class="photo-location">${escapeHtml(photoData.location || unknownLocation)}</p>
        `;
        window.PHOTO_ORGANIZER.utils?.initImageFallbacks?.();
        overlay.setPosition(coordinate);
    };

    map.on('click', function(/** @type {any} */ evt) {
        const feature = map.forEachFeatureAtPixel(evt.pixel, function(/** @type {any} */ feature) {
            return feature;
        });

        if (feature) {
            const features = feature.get('features');
            const coordinate = feature.getGeometry().getCoordinates();

            if (features && features.length > 1) {
                const photosData = /** @type {LocationMediaItem[]} */ (features.map((/** @type {any} */ f) => ({
                    name: f.get('filename'),
                    location: f.get('name'),
                    id: f.get('id'),
                    photo_path: f.get('photo_path'),
                    media_type: f.get('media_type')
                })));

                const selectId = 'photo-select-' + Date.now();
                const selectOptions = photosData.map((photo, idx) =>
                    `<option value="${idx}">${escapeHtml(photo.name)}</option>`
                ).join('');

                const firstPhoto = photosData[0];
                const photoViewUrl = config.buildUrl('media_view', { media_item_id: firstPhoto.id });
                const safeFirstName = escapeHtml(firstPhoto.name);

                popupContent.innerHTML = `
                    <div class="popup-select-container">
                        <label for="${selectId}">${escapeHtml(i18n.t('locations:popup.selectPhoto', {
                            count: photosData.length,
                            total: i18n.number(photosData.length),
                        }))}</label>
                        <select id="${selectId}" class="photo-select searchable-select">
                            ${selectOptions}
                        </select>
                    </div>
                    <div class="popup-photo-container">
                        <a id="photo-link" href="${photoViewUrl}" target="_blank">
                            <img id="photo-img" src="${thumbUrl(firstPhoto)}" data-fallback="${fallbackUrl}" alt="${safeFirstName}" class="popup-photo">
                        </a>
                    </div>
                    <h3 id="photo-name">${safeFirstName}</h3>
                    <p class="photo-location">${escapeHtml(firstPhoto.location || unknownLocation)}</p>
                `;
                window.PHOTO_ORGANIZER.utils?.initImageFallbacks?.();

                const selectElement = document.getElementById(selectId);
                if (!(selectElement instanceof HTMLSelectElement)) return;
                window.SearchableSelect?.init(selectElement);
                selectElement.addEventListener('change', function() {
                    const selectedIndex = parseInt(selectElement.value);
                    const selectedPhoto = photosData[selectedIndex];
                    if (!selectedPhoto) return;

                    const newPhotoViewUrl = config.buildUrl('media_view', { media_item_id: selectedPhoto.id });

                    const img = document.getElementById('photo-img');
                    const link = document.getElementById('photo-link');
                    const name = document.getElementById('photo-name');
                    const location = document.querySelector('.photo-location');
                    const selectedName = selectedPhoto.filename || selectedPhoto.name || '';
                    if (img instanceof HTMLImageElement) {
                        img.src = thumbUrl(selectedPhoto);
                        img.alt = selectedName;
                    }
                    if (link instanceof HTMLAnchorElement) link.href = newPhotoViewUrl;
                    if (name) name.textContent = selectedName;
                    if (location) location.textContent = selectedPhoto.location || unknownLocation;
                    // Re-arm the fallback for the swapped src (the handler is once-only).
                    window.PHOTO_ORGANIZER.utils?.initImageFallbacks?.();
                });

                overlay.setPosition(coordinate);
            } else if (features && features.length === 1) {
                const singleFeature = features[0];
                const photoData = {
                    name: singleFeature.get('filename'),
                    location: singleFeature.get('name'),
                    id: singleFeature.get('id'),
                    photo_path: singleFeature.get('photo_path'),
                    media_type: singleFeature.get('media_type')
                };
                showPhotoInPopup(photoData, coordinate);
            }
        } else {
            overlay.setPosition(undefined);
        }
    });

    map.on('pointermove', function(/** @type {any} */ evt) {
        const pixel = map.getEventPixel(evt.originalEvent);
        const hit = map.hasFeatureAtPixel(pixel);
        const targetElement = map.getTargetElement();
        if (targetElement) {
            targetElement.style.cursor = hit ? 'pointer' : '';
        }
    });

    const calculateCentroid = (/** @type {any[]} */ features) => {
        let totalLat = 0;
        let totalLon = 0;

        features.forEach((/** @type {any} */ f) => {
            const coords = ol.proj.toLonLat(f.getGeometry().getCoordinates());
            totalLon += coords[0];
            totalLat += coords[1];
        });

        return {
            lat: totalLat / features.length,
            lon: totalLon / features.length
        };
    };

    const getRecommendedLocation = async (/** @type {number} */ lat, /** @type {number} */ lon) => {
        try {
            const response = await fetch(config.urls.reverse_geocode_route, {
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
        if (!panel || !panelContent) return;

        if (selectedFeatures.size === 0) {
            panel.classList.remove('active');
            return;
        }

        const selectedClusters = Array.from(selectedFeatures).map((clusterFeature, idx) => {
            const features = clusterFeature.get('features');
            const photoIds = features.map((/** @type {any} */ f) => f.get('id'));

            /** @type {Record<string, number>} */
            const locationCounts = {};
            features.forEach((/** @type {any} */ f) => {
                const locationName = f.get('name') || unknownLocation;
                locationCounts[locationName] = (locationCounts[locationName] || 0) + 1;
            });

            const locationBreakdown = Object.entries(locationCounts)
                .sort((a, b) => b[1] - a[1])
                .map(([name, count]) => `${i18n.number(count)} ${name}`)
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

        /** @type {Record<string, number>} */
        const allLocationCounts = {};
        selectedClusters.forEach(cluster => {
            Object.entries(cluster.locationCounts).forEach(([name, count]) => {
                if (name !== unknownLocation) {
                    allLocationCounts[name] = (allLocationCounts[name] || 0) + count;
                }
            });
        });

        const sortedLocations = Object.entries(allLocationCounts)
            .sort((a, b) => b[1] - a[1]);

        const summaryKey = `locations:selection.summary${
            totalPhotos === 1 ? 'One' : 'Other'
        }${selectedClusters.length === 1 ? 'One' : 'Other'}`;
        panelContent.innerHTML = `
            <div class="selection-panel-header">
                <h3>${escapeHtml(i18n.t('locations:selection.massAssignment'))}</h3>
                <div class="mass-assignment-info">
                    ${escapeHtml(i18n.t(summaryKey, {
                        photos: i18n.number(totalPhotos),
                        clusters: i18n.number(selectedClusters.length),
                    }))}
                </div>
            </div>

            <div class="selection-assignment">
                ${sortedLocations.length > 0 ? `
                    <div class="quick-actions">
                        <div class="quick-action-label">${escapeHtml(i18n.t('locations:selection.quickAssign'))}</div>
                        <div class="quick-actions-buttons">
                            ${sortedLocations.map(([name, count]) => `
                                <button class="btn-quick-assign"
                                        data-photo-ids="${allPhotoIds.join(',')}"
                                        data-location-name="${escapeHtml(name)}"
                                        title="${escapeHtml(name)} (${i18n.number(count)})">
                                    ${escapeHtml(name)} (${i18n.number(count)})
                                </button>
                            `).join('')}
                        </div>
                    </div>
                ` : ''}

                <div class="cluster-assign">
                    <input type="text"
                           class="location-input"
                           placeholder="${escapeHtml(i18n.t('locations:selection.customLocation'))}"
                           id="mass-location-input">
                    <button class="btn btn-primary btn-assign"
                            data-photo-ids="${allPhotoIds.join(',')}"
                            id="mass-assign-btn">
                        ${escapeHtml(i18n.t('locations:selection.assignAll'))}
                    </button>
                </div>
            </div>

            <div class="clusters-summary">
                <div class="summary-label">${escapeHtml(i18n.t('locations:selection.selectedClusters'))}</div>
                <div class="clusters-list">
                    ${selectedClusters.map(cluster => `
                        <div class="cluster-summary-item">
                            <strong>${escapeHtml(i18n.t('locations:selection.photoCount', {
                                count: cluster.photoCount,
                                formattedCount: i18n.number(cluster.photoCount),
                            }))}</strong>
                            <span class="current-location">${escapeHtml(cluster.locationBreakdown)}</span>
                        </div>
                    `).join('')}
                </div>
            </div>

            <div class="selection-panel-actions">
                <button class="btn btn-secondary btn-clear-selection">${escapeHtml(i18n.t('locations:selection.clearSelection'))}</button>
                <button class="btn btn-secondary btn-clear-names">${escapeHtml(i18n.t('locations:selection.clearNames'))}</button>
            </div>
        `;

        /**
         * Assign `locationName` to the photos, or remove their names when it is
         * null (the server requires an explicit clear flag so a blank input can
         * never wipe names by accident).
         * @param {number[]} photoIds
         * @param {string | null} locationName
         */
        const assignLocation = async (photoIds, locationName) => {
            const clearing = locationName === null;
            try {
                const response = await fetch(config.urls.locations_bulk_update, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(clearing ? {
                        media_item_ids: photoIds,
                        clear: true
                    } : {
                        media_item_ids: photoIds,
                        location_name: locationName
                    })
                });

                if (response.ok) {
                    window.notification.success(clearing
                        ? i18n.t('locations:update.cleared', { count: photoIds.length })
                        : i18n.t('locations:update.succeeded', {
                            count: photoIds.length,
                            location: locationName,
                        }));

                    allFeatures.forEach(feature => {
                        const featureId = feature.get('id');
                        if (photoIds.includes(featureId)) {
                            feature.set('name', clearing ? null : locationName);
                        }
                    });

                    const filterCheckbox = document.getElementById('filter-unnamed');
                    if (filterCheckbox instanceof HTMLInputElement && filterCheckbox.checked) {
                        applyFilter(true);
                    } else {
                        clusterLayer.changed();
                    }

                    selectedFeatures.clear();
                    clusterLayer.changed();
                    updateSelectionPanel();

                    return true;
                } else {
                    const data = await response.json().catch(() => ({}));
                    window.notification.error(
                        data.error || i18n.t('locations:update.failed'),
                    );
                    return false;
                }
            } catch (error) {
                window.notification.error(i18n.t('locations:update.failed'));
                console.error(error);
                return false;
            }
        };

        document.querySelectorAll('.btn-quick-assign').forEach(btn => {
            btn.addEventListener('click', async (event) => {
                if (!(event.currentTarget instanceof HTMLElement)) return;
                const photoIds = (event.currentTarget.dataset.photoIds || '').split(',').filter(Boolean).map(Number);
                const locationName = event.currentTarget.dataset.locationName || '';
                await assignLocation(photoIds, locationName);
            });
        });

        const massAssignBtn = document.getElementById('mass-assign-btn');
        if (massAssignBtn) {
            massAssignBtn.addEventListener('click', async (event) => {
                if (!(event.currentTarget instanceof HTMLElement)) return;
                const photoIds = (event.currentTarget.dataset.photoIds || '').split(',').filter(Boolean).map(Number);
                const input = document.getElementById('mass-location-input');
                if (!(input instanceof HTMLInputElement)) return;
                const newLocationName = input.value.trim();

                if (!newLocationName) {
                    window.notification.error(i18n.t('locations:update.nameRequired'));
                    return;
                }

                const success = await assignLocation(photoIds, newLocationName);
                if (success) {
                    input.value = '';
                }
            });
        }

        document.querySelector('.btn-clear-names')?.addEventListener('click', async () => {
            await assignLocation(allPhotoIds, null);
        });

        document.querySelector('.btn-clear-selection')?.addEventListener('click', () => {
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
                        <div class="quick-action-label">${escapeHtml(i18n.t('locations:selection.recommended'))}</div>
                        <button class="btn-quick-assign btn-recommended"
                                data-photo-ids="${allPhotoIds.join(',')}"
                                data-location-name="${escapeHtml(recommendedLocation)}"
                                title="${escapeHtml(recommendedLocation)}">
                            ${escapeHtml(recommendedLocation)}
                        </button>
                    `;

                    const assignmentSection = panelContent.querySelector('.selection-assignment');
                    const quickActionsSection = assignmentSection?.querySelector('.quick-actions');
                    if (quickActionsSection) {
                        assignmentSection?.insertBefore(recommendedSection, quickActionsSection);
                    } else {
                        const clusterAssign = assignmentSection?.querySelector('.cluster-assign');
                        if (clusterAssign) assignmentSection?.insertBefore(recommendedSection, clusterAssign);
                    }

                    const newRecommendedBtn = recommendedSection.querySelector('.btn-quick-assign');
                    newRecommendedBtn?.addEventListener('click', async (event) => {
                        if (!(event.currentTarget instanceof HTMLElement)) return;
                        const photoIds = (event.currentTarget.dataset.photoIds || '').split(',').filter(Boolean).map(Number);
                        const locationName = event.currentTarget.dataset.locationName || '';
                        await assignLocation(photoIds, locationName);
                    });
                }
            }
        })();
    };

    const applyFilter = (/** @type {boolean} */ showOnlyUnnamed) => {
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
            if (e.target instanceof HTMLInputElement) applyFilter(e.target.checked);
        });
    }

    return { map, vectorSource, selectedFeatures, updateSelectionPanel, applyFilter };
};
