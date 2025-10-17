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
    const vectorLayer = new ol.layer.Vector({
        source: vectorSource,
        style: new ol.style.Style({
            image: new ol.style.Circle({
                radius: 7,
                fill: new ol.style.Fill({ color: '#007BFF' }),
                stroke: new ol.style.Stroke({
                    color: '#fff',
                    width: 2
                })
            })
        })
    });

    map.addLayer(vectorLayer);

    locations.forEach(location => {
        const feature = new ol.Feature({
            geometry: new ol.geom.Point(
                ol.proj.fromLonLat([location.lon, location.lat])
            ),
            name: location.name,
            id: location.id,
            photo_path: location.photo_path
        });
        vectorSource.addFeature(feature);
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

    map.on('click', function(evt) {
        const feature = map.forEachFeatureAtPixel(evt.pixel, function(feature) {
            return feature;
        });

        if (feature) {
            const coordinate = feature.getGeometry().getCoordinates();
            const name = feature.get('name') || 'Unknown Location';
            const id = feature.get('id');
            const photoPath = feature.get('photo_path');

            const photoUrl = window.APP_CONFIG.buildUrl('photo', { filename: photoPath });
            const photoViewUrl = window.APP_CONFIG.buildUrl('photo_view', { filename: photoPath });

            popupContent.innerHTML = `
                <div class="popup-photo-container">
                    <a href="${photoViewUrl}" target="_blank">
                        <img src="${photoUrl}" alt="${name}" class="popup-photo">
                    </a>
                </div>
                <h3>${name || 'Unknown Location'}</h3>
            `;
            overlay.setPosition(coordinate);
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