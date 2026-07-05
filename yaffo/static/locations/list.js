// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.locations = window.PHOTO_ORGANIZER.locations || {};

/**
 * @param {LocationMediaItem[]} locations
 * @param {I18nService} i18n
 * @param {AppConfig} config
 * @param {{ nearbyRadiusKm?: number }} options
 * @returns {LocationMapApi}
 */
window.PHOTO_ORGANIZER.locations.initMap = (locations, i18n, config, options = {}) => {
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
    const nearbyRadiusKm = Number.isFinite(options.nearbyRadiusKm) ? Number(options.nearbyRadiusKm) : 10;

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
            media_type: location.media_type,
            // The full payload, kept for the sidebar's client-side filtering.
            item: location
        });
    });

    const vectorSource = new ol.source.Vector({
        features: allFeatures
    });

    const clusterSource = new ol.source.Cluster({
        distance: 40,
        source: vectorSource
    });

    /** @type {Set<number>} */
    const selectedPhotoIds = new Set();

    const getClusterPhotoIds = (/** @type {any} */ clusterFeature) => {
        const features = clusterFeature.get('features') || [];
        return features.map((/** @type {any} */ f) => f.get('id')).filter((/** @type {unknown} */ id) => typeof id === 'number');
    };

    const getClusterSelection = (/** @type {any} */ clusterFeature) => {
        const photoIds = getClusterPhotoIds(clusterFeature);
        const selectedCount = photoIds.filter((/** @type {number} */ id) => selectedPhotoIds.has(id)).length;
        return {
            photoIds,
            selectedCount,
            totalCount: photoIds.length,
            ratio: photoIds.length > 0 ? selectedCount / photoIds.length : 0,
            selected: photoIds.length > 0 && selectedCount === photoIds.length,
            partial: selectedCount > 0 && selectedCount < photoIds.length,
        };
    };

    const sectorPath = (/** @type {number} */ ratio) => {
        if (ratio <= 0 || ratio >= 1) return '';
        const angle = (Math.PI * 2 * ratio) - (Math.PI / 2);
        const x = 16 + (16 * Math.cos(angle));
        const y = 16 + (16 * Math.sin(angle));
        const largeArc = ratio > 0.5 ? 1 : 0;
        return 'M16 16 L16 0 A16 16 0 '
            + largeArc
            + ' 1 '
            + x.toFixed(3)
            + ' '
            + y.toFixed(3)
            + ' Z';
    };

    const markerSvg = (
        /** @type {number} */ size,
        /** @type {number} */ selectedCount,
        /** @type {number} */ totalCount,
    ) => {
        const svgNamespace = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(svgNamespace, 'svg');
        svg.setAttribute('xmlns', svgNamespace);
        svg.setAttribute('width', '32');
        svg.setAttribute('height', '32');
        svg.setAttribute('viewBox', '0 0 32 32');

        const addCircle = (/** @type {string} */ fill) => {
            const circle = document.createElementNS(svgNamespace, 'circle');
            circle.setAttribute('cx', '16');
            circle.setAttribute('cy', '16');
            circle.setAttribute('r', '15');
            circle.setAttribute('fill', fill);
            circle.setAttribute('stroke', markerColors.contrast);
            circle.setAttribute('stroke-width', '2');
            svg.appendChild(circle);
        };

        const ratio = totalCount > 0 ? selectedCount / totalCount : 0;
        const full = ratio >= 1;
        const partialPath = sectorPath(ratio);
        addCircle(full ? markerColors.selected : markerColors.base);

        if (partialPath) {
            const path = document.createElementNS(svgNamespace, 'path');
            path.setAttribute('d', partialPath);
            path.setAttribute('fill', markerColors.selected);
            svg.appendChild(path);
        }

        addCircle('none');

        if (size > 1) {
            const label = document.createElementNS(svgNamespace, 'text');
            label.setAttribute('x', '16');
            label.setAttribute('y', '20');
            label.setAttribute('text-anchor', 'middle');
            label.setAttribute('font-family', 'sans-serif');
            label.setAttribute('font-size', '12');
            label.setAttribute('font-weight', '700');
            label.setAttribute('fill', markerColors.contrast);
            label.textContent = String(size);
            svg.appendChild(label);
        }

        return 'data:image/svg+xml;charset=UTF-8,' + encodeURIComponent(new XMLSerializer().serializeToString(svg));
    };

    /** @type {Record<string, any>} */
    const styleCache = {};
    const clusterLayer = new ol.layer.Vector({
        source: clusterSource,
        style: function(/** @type {any} */ feature) {
            const size = feature.get('features').length;
            const selection = getClusterSelection(feature);
            const cacheKey = String(size) + '-' + selection.selectedCount + '-' + selection.totalCount;

            let style = styleCache[cacheKey];
            if (!style) {
                style = new ol.style.Style({
                    image: new ol.style.Icon({
                        src: markerSvg(size, selection.selectedCount, selection.totalCount),
                        scale: size > 1 ? 1 : 0.58,
                    })
                });
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

    // The DragBox fires for a stationary shift-click too. Below this pixel
    // distance it is treated as a click — the map's click handler then toggles
    // the cluster in the selection — while a real drag replaces the selection
    // with the box contents.
    const CLICK_DRAG_THRESHOLD_PX = 6;
    /** @type {number[] | null} */
    let boxStartPixel = null;

    dragBox.on('boxend', function(/** @type {any} */ evt) {
        const startPixel = boxStartPixel;
        boxStartPixel = null;
        const endPixel = evt && evt.mapBrowserEvent ? evt.mapBrowserEvent.pixel : null;
        if (startPixel && endPixel) {
            const distance = Math.hypot(endPixel[0] - startPixel[0], endPixel[1] - startPixel[1]);
            if (distance < CLICK_DRAG_THRESHOLD_PX) return; // shift-click, not a box
        }

        const extent = dragBox.getGeometry().getExtent();
        /** @type {any[]} */
        const boxFeatures = [];

        clusterSource.getFeatures().forEach(function(/** @type {any} */ feature) {
            if (ol.extent.intersects(extent, feature.getGeometry().getExtent())) {
                boxFeatures.push(feature);
            }
        });

        // Like shift-click, a box extends the current selection rather than
        // replacing it; the panel's × (or an empty plain click) starts over.
        boxFeatures.forEach(f => getClusterPhotoIds(f).forEach((/** @type {number} */ id) => selectedPhotoIds.add(id)));
        clusterLayer.changed();
        updateSelectionPanel();
    });

    dragBox.on('boxstart', function(/** @type {any} */ evt) {
        // Selection is replaced at boxend, not here, so a shift-click can
        // toggle a cluster without first wiping the current selection.
        boxStartPixel = evt && evt.mapBrowserEvent ? evt.mapBrowserEvent.pixel : null;
    });

    if (locations.length > 0) {
        const extent = vectorSource.getExtent();
        map.getView().fit(extent, {
            padding: [50, 50, 50, 50],
            maxZoom: 16
        });
    }

    // A video's media route returns the raw clip, so the preview thumbnail
    // points at its poster frame (falling back to the placeholder when there's no
    // poster, via data-fallback + initImageFallbacks). Photos use /media directly.
    const thumbUrl = (/** @type {LocationMediaItem} */ item) => item.media_type === 'video'
        ? config.buildUrl('media_poster', { media_item_id: item.id })
        : config.buildUrl('media', { media_item_id: item.id });

    const fallbackUrl = config.urls.placeholder;

    // Collapsed/expanded state of the panel's preview section; kept outside the
    // renderer so it survives re-renders as the selection changes.
    let previewCollapsed = false;
    let recommendationRenderId = 0;
    let reverseGeocodeRateLimited = false;
    /** @type {Map<string, Promise<string | null>>} */
    const reverseGeocodeCache = new Map();

    // Show at most this many thumbnails; the select box still lists every photo.
    const PREVIEW_THUMBS_LIMIT = 24;

    /**
     * The collapsible preview section of the selection panel: the current
     * photo (image + caption), a select box, and a thumbnail strip.
     * @param {LocationMediaItem[]} photos
     */
    const buildPreviewSection = (photos) => {
        const firstPhoto = photos[0];
        if (!firstPhoto) return '';

        // The select box and thumbnails only make sense for several photos.
        const selectOptions = photos.map((photo, idx) =>
            `<option value="${idx}">${escapeHtml(photo.name)}</option>`
        ).join('');
        const selectorHtml = photos.length > 1 ? `
                <div class="preview-select-container">
                    <label for="preview-photo-select">${escapeHtml(i18n.t('locations:popup.selectPhoto', {
                        count: photos.length,
                        total: i18n.number(photos.length),
                    }))}</label>
                    <select id="preview-photo-select" class="photo-select searchable-select">
                        ${selectOptions}
                    </select>
                </div>` : '';

        const thumbButtons = photos.slice(0, PREVIEW_THUMBS_LIMIT).map((photo, idx) => {
            const safeName = escapeHtml(photo.name);
            return `
                <button type="button" class="preview-thumb${idx === 0 ? ' active' : ''}"
                        data-index="${idx}" title="${safeName}">
                    <img src="${thumbUrl(photo)}" data-fallback="${fallbackUrl}" alt="${safeName}">
                </button>`;
        }).join('');
        const moreHtml = photos.length > PREVIEW_THUMBS_LIMIT
            ? `<span class="preview-thumbs-more">${escapeHtml(i18n.t('locations:selection.moreThumbnails', {
                more: i18n.number(photos.length - PREVIEW_THUMBS_LIMIT),
            }))}</span>`
            : '';
        const thumbsHtml = photos.length > 1
            ? `<div class="preview-thumbs">${thumbButtons}${moreHtml}</div>`
            : '';

        const photoViewUrl = config.buildUrl('media_view', { media_item_id: firstPhoto.id });
        const safeFirstName = escapeHtml(firstPhoto.name);

        return `
            <div class="preview-section${previewCollapsed ? ' collapsed' : ''}">
                <button type="button" class="preview-toggle" aria-expanded="${previewCollapsed ? 'false' : 'true'}">
                    <span>${escapeHtml(i18n.t('locations:selection.preview'))}</span>
                    <span class="preview-toggle-arrow" aria-hidden="true">${previewCollapsed ? '▸' : '▾'}</span>
                </button>
                <div class="preview-body">
                    ${selectorHtml}
                    <div class="preview-photo-container">
                        <a id="photo-link" href="${photoViewUrl}" target="_blank">
                            <img id="photo-img" src="${thumbUrl(firstPhoto)}" data-fallback="${fallbackUrl}" alt="${safeFirstName}" class="preview-photo">
                        </a>
                    </div>
                    <h3 id="photo-name">${safeFirstName}</h3>
                    <p class="photo-location">${escapeHtml(firstPhoto.location || unknownLocation)}</p>
                    ${thumbsHtml}
                </div>
            </div>`;
    };

    /**
     * @param {HTMLElement} panelContent
     * @param {LocationMediaItem[]} photos
     */
    const wirePreviewSection = (panelContent, photos) => {
        const section = panelContent.querySelector('.preview-section');
        if (!(section instanceof HTMLElement)) return;

        const toggle = section.querySelector('.preview-toggle');
        toggle?.addEventListener('click', () => {
            previewCollapsed = !previewCollapsed;
            section.classList.toggle('collapsed', previewCollapsed);
            toggle.setAttribute('aria-expanded', previewCollapsed ? 'false' : 'true');
            const arrow = toggle.querySelector('.preview-toggle-arrow');
            if (arrow) arrow.textContent = previewCollapsed ? '▸' : '▾';
        });

        const showPhoto = (/** @type {number} */ index) => {
            const photo = photos[index];
            if (!photo) return;

            const img = document.getElementById('photo-img');
            const link = document.getElementById('photo-link');
            const name = document.getElementById('photo-name');
            const location = section.querySelector('.photo-location');
            const photoName = photo.name || '';
            if (img instanceof HTMLImageElement) {
                img.src = thumbUrl(photo);
                img.alt = photoName;
            }
            if (link instanceof HTMLAnchorElement) {
                link.href = config.buildUrl('media_view', { media_item_id: photo.id });
            }
            if (name) name.textContent = photoName;
            if (location) location.textContent = photo.location || unknownLocation;
            section.querySelectorAll('.preview-thumb').forEach((thumb) => {
                if (!(thumb instanceof HTMLElement)) return;
                thumb.classList.toggle('active', Number(thumb.dataset.index) === index);
            });
            // Re-arm the fallback for the swapped src (the handler is once-only).
            window.PHOTO_ORGANIZER.utils?.initImageFallbacks?.();
        };

        const select = document.getElementById('preview-photo-select');
        if (select instanceof HTMLSelectElement) {
            window.SearchableSelect?.init(select);
            select.addEventListener('change', () => showPhoto(parseInt(select.value)));
        }

        section.querySelectorAll('.preview-thumb').forEach((thumb) => {
            thumb.addEventListener('click', () => {
                if (!(thumb instanceof HTMLElement)) return;
                const index = Number(thumb.dataset.index);
                if (select instanceof HTMLSelectElement) {
                    // Route through the select so its searchable widget stays
                    // in sync; the change listener above updates the preview.
                    select.value = String(index);
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                } else {
                    showPhoto(index);
                }
            });
        });
    };

    map.on('click', function(/** @type {any} */ evt) {
        const feature = map.forEachFeatureAtPixel(evt.pixel, function(/** @type {any} */ feature) {
            return feature;
        });
        const isShiftClick = Boolean(evt.originalEvent && evt.originalEvent.shiftKey);

        if (feature) {
            const selection = getClusterSelection(feature);
            if (selection.totalCount === 0) return;

            if (isShiftClick) {
                // Shift-click cycles just this cluster: partial/full, then empty.
                if (selection.selected) {
                    selection.photoIds.forEach((/** @type {number} */ id) => selectedPhotoIds.delete(id));
                } else {
                    selection.photoIds.forEach((/** @type {number} */ id) => selectedPhotoIds.add(id));
                }
            } else {
                // A plain click starts from this cluster unless it is already in
                // the selection cycle, where partial -> full -> unselected.
                selectedPhotoIds.clear();
                if (!selection.selected) {
                    selection.photoIds.forEach((/** @type {number} */ id) => selectedPhotoIds.add(id));
                }
            }
            clusterLayer.changed();
            updateSelectionPanel();
        } else if (!isShiftClick && selectedPhotoIds.size > 0) {
            // Clicking empty map clears the selection and closes the panel;
            // a missed shift-click keeps the selection being built.
            selectedPhotoIds.clear();
            clusterLayer.changed();
            updateSelectionPanel();
        }
    });

    map.on('moveend', function() {
        if (selectedPhotoIds.size === 0) return;
        clusterLayer.changed();
        updateSelectionPanel();
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

    const featureCoordinates = (/** @type {any} */ feature) => {
        const coords = ol.proj.toLonLat(feature.getGeometry().getCoordinates());
        return { lat: coords[1], lon: coords[0] };
    };

    const calculateMedoid = (/** @type {any[]} */ features) => {
        if (features.length === 0) return null;
        const centroid = calculateCentroid(features);
        return features.reduce((/** @type {any} */ closest, /** @type {any} */ feature) => {
            const closestCoords = featureCoordinates(closest);
            const featureCoords = featureCoordinates(feature);
            const closestDistance = Math.hypot(closestCoords.lon - centroid.lon, closestCoords.lat - centroid.lat);
            const featureDistance = Math.hypot(featureCoords.lon - centroid.lon, featureCoords.lat - centroid.lat);
            return featureDistance < closestDistance ? feature : closest;
        }, features[0]);
    };

    const distanceKm = (
        /** @type {{ lat: number, lon: number }} */ a,
        /** @type {{ lat: number, lon: number }} */ b,
    ) => {
        const earthRadiusKm = 6371.0088;
        const toRadians = (/** @type {number} */ degrees) => degrees * Math.PI / 180;
        const lat1 = toRadians(a.lat);
        const lat2 = toRadians(b.lat);
        const deltaLat = toRadians(b.lat - a.lat);
        const deltaLon = toRadians(b.lon - a.lon);
        const sinLat = Math.sin(deltaLat / 2);
        const sinLon = Math.sin(deltaLon / 2);
        const h = (sinLat * sinLat) + (Math.cos(lat1) * Math.cos(lat2) * sinLon * sinLon);
        return earthRadiusKm * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
    };

    const uniqueNearbyLocationName = (/** @type {any} */ medoid) => {
        const medoidCoords = featureCoordinates(medoid);
        const nearbyNames = new Set();
        allFeatures.forEach(feature => {
            const locationName = String(feature.get('name') || '').trim();
            if (!locationName) return;
            if (distanceKm(medoidCoords, featureCoordinates(feature)) <= nearbyRadiusKm) {
                nearbyNames.add(locationName);
            }
        });
        return nearbyNames.size === 1 ? Array.from(nearbyNames)[0] : null;
    };

    const getRecommendedLocation = async (/** @type {number} */ lat, /** @type {number} */ lon) => {
        if (reverseGeocodeRateLimited) return null;
        const cacheKey = lat.toFixed(6) + ',' + lon.toFixed(6);
        const cached = reverseGeocodeCache.get(cacheKey);
        if (cached) return cached;

        const request = (async () => {
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
                if (response.status === 429) {
                    reverseGeocodeRateLimited = true;
                }
            } catch (error) {
                console.error('Error fetching recommended location:', error);
            }
            return null;
        })();
        reverseGeocodeCache.set(cacheKey, request);
        return request;
    };

    const updateSelectionPanel = async () => {
        const panel = document.getElementById('selection-panel');
        const panelContent = document.getElementById('selection-panel-content');
        if (!panel || !panelContent) return;

        if (selectedPhotoIds.size === 0) {
            recommendationRenderId += 1;
            panel.classList.remove('active');
            return;
        }

        const selectedClusters = /** @type {any[]} */ (clusterSource.getFeatures()).map((
            /** @type {any} */ clusterFeature,
            /** @type {number} */ idx,
        ) => {
            const features = clusterFeature.get('features');
            const selection = getClusterSelection(clusterFeature);
            const selectedFeatures = features.filter((/** @type {any} */ f) => selectedPhotoIds.has(f.get('id')));
            const photoIds = selectedFeatures.map((/** @type {any} */ f) => f.get('id'));
            const photos = /** @type {LocationMediaItem[]} */ (selectedFeatures.map((/** @type {any} */ f) => ({
                id: f.get('id'),
                name: f.get('filename'),
                location: f.get('name'),
                media_type: f.get('media_type')
            })));

            /** @type {Record<string, number>} */
            const locationCounts = {};
            selectedFeatures.forEach((/** @type {any} */ f) => {
                const locationName = f.get('name') || unknownLocation;
                locationCounts[locationName] = (locationCounts[locationName] || 0) + 1;
            });

            const locationBreakdown = Object.entries(locationCounts)
                .sort((a, b) => b[1] - a[1])
                .map(([name, count]) => i18n.number(count) + ' ' + name)
                .join(', ');

            return {
                index: idx,
                photoCount: selectedFeatures.length,
                clusterPhotoCount: features.length,
                selectedRatio: selection.ratio,
                partial: selection.partial,
                summaryLabel: selection.partial
                    ? i18n.number(selectedFeatures.length)
                        + ' / '
                        + i18n.number(features.length)
                        + ' '
                        + i18n.t('locations:selection.photos')
                    : i18n.t('locations:selection.photoCount', {
                        count: selectedFeatures.length,
                        formattedCount: i18n.number(selectedFeatures.length),
                    }),
                photoIds: photoIds,
                photos: photos,
                features: selectedFeatures,
                locationBreakdown: locationBreakdown,
                locationCounts: locationCounts,
                recommendedLocation: null
            };
        }).filter((/** @type {any} */ cluster) => cluster.photoCount > 0);

        if (selectedClusters.length === 0) {
            recommendationRenderId += 1;
            panel.classList.remove('active');
            return;
        }

        const allPhotoIds = selectedClusters.flatMap((/** @type {any} */ c) => c.photoIds);
        const allPhotos = selectedClusters.flatMap((/** @type {any} */ c) => c.photos);
        const allSelectedFeatures = selectedClusters.flatMap((/** @type {any} */ c) => c.features);
        const totalPhotos = allPhotoIds.length;

        /** @type {Record<string, number>} */
        const allLocationCounts = {};
        selectedClusters.forEach((/** @type {any} */ cluster) => {
            Object.entries(cluster.locationCounts).forEach(([name, count]) => {
                if (name !== unknownLocation) {
                    allLocationCounts[name] = (allLocationCounts[name] || 0) + count;
                }
            });
        });

        const sortedLocations = Object.entries(allLocationCounts)
            .sort((a, b) => b[1] - a[1]);

        const summaryKey = 'locations:selection.summary'
            + (totalPhotos === 1 ? 'One' : 'Other')
            + (selectedClusters.length === 1 ? 'One' : 'Other');
        const previewSectionHtml = buildPreviewSection(allPhotos);
        panelContent.innerHTML = `
            <div class="selection-panel-header">
                <div class="selection-panel-heading">
                    <h3>${escapeHtml(i18n.t('locations:selection.massAssignment'))}</h3>
                    <div class="mass-assignment-info">
                        ${escapeHtml(i18n.t(summaryKey, {
                            photos: i18n.number(totalPhotos),
                            clusters: i18n.number(selectedClusters.length),
                        }))}
                    </div>
                </div>
                <button type="button" class="selection-panel-close"
                        aria-label="${escapeHtml(i18n.t('common:close'))}"
                        title="${escapeHtml(i18n.t('common:close'))}">×</button>
            </div>

            ${previewSectionHtml}

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
                            <strong>${escapeHtml(cluster.summaryLabel)}</strong>
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

        wirePreviewSection(panelContent, allPhotos);
        window.PHOTO_ORGANIZER.utils?.initImageFallbacks?.();

        // The × closes the panel and drops the whole selection.
        panelContent.querySelector('.selection-panel-close')?.addEventListener('click', () => {
            selectedPhotoIds.clear();
            clusterLayer.changed();
            updateSelectionPanel();
        });

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
                            // Keep the filterable payload in step so an active
                            // location-name filter sees the new value.
                            const item = feature.get('item');
                            if (item) item.name = clearing ? null : locationName;
                        }
                    });

                    // An active filter may match on the (just-changed) names,
                    // so re-derive the visible set; otherwise a restyle is enough.
                    if (clientPredicate) {
                        refreshFeatures();
                    } else {
                        clusterLayer.changed();
                    }

                    selectedPhotoIds.clear();
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
            selectedPhotoIds.clear();
            clusterLayer.changed();
            updateSelectionPanel();
        });

        panel.classList.add('active');
        const renderId = recommendationRenderId + 1;
        recommendationRenderId = renderId;

        (async () => {
            const medoid = calculateMedoid(allSelectedFeatures);
            let recommendedLocation = medoid ? uniqueNearbyLocationName(medoid) : null;

            if (!recommendedLocation && medoid) {
                const coords = featureCoordinates(medoid);
                recommendedLocation = await getRecommendedLocation(coords.lat, coords.lon);
            }

            if (renderId !== recommendationRenderId) return;
            if (!recommendedLocation) return;

            const recommendedSection = document.createElement('div');
            recommendedSection.className = 'quick-actions recommendation-section';
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
            assignmentSection?.querySelector('.recommendation-section')?.remove();
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
        })();
    };

    // The sidebar's client-side predicate (set via the shared filter panel,
    // "only unnamed" included) narrows the markers from the full feature list.
    /** @type {((item: ClientFilterItem) => boolean) | null} */
    let clientPredicate = null;

    const refreshFeatures = () => {
        const predicate = clientPredicate;
        const visibleFeatures = predicate
            ? allFeatures.filter(feature => predicate(feature.get('item')))
            : allFeatures;

        vectorSource.clear();
        vectorSource.addFeatures(visibleFeatures);

        // Filters change which photos are on the map, so start the map selection over.
        selectedPhotoIds.clear();
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

    const setClientFilter = (/** @type {(item: ClientFilterItem) => boolean} */ predicate) => {
        clientPredicate = predicate;
        refreshFeatures();
    };

    return { map, vectorSource, selectedPhotoIds, updateSelectionPanel, setClientFilter };
};
