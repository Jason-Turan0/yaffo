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

    const styleCache = {};
    const clusterLayer = new ol.layer.Vector({
        source: clusterSource,
        style: function(feature) {
            const size = feature.get('features').length;
            let style = styleCache[size];
            if (!style) {
                if (size > 1) {
                    style = new ol.style.Style({
                        image: new ol.style.Circle({
                            radius: 15,
                            fill: new ol.style.Fill({ color: '#007BFF' }),
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
                            fill: new ol.style.Fill({ color: '#007BFF' }),
                            stroke: new ol.style.Stroke({
                                color: '#fff',
                                width: 2
                            })
                        })
                    });
                }
                styleCache[size] = style;
            }
            return style;
        }
    });

    map.addLayer(clusterLayer);

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

    return { map, vectorSource };
};