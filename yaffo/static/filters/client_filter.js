// @ts-check

// Client-side counterpart of the home route's server-side filtering: reads the
// shared sidebar filter form and builds a predicate over already-loaded media
// items (the locations map filters its markers with it, no round trip). The
// semantics must stay in step with yaffo/routes/home.py — same querystring
// names, same matching rules, same proximity bounding box.

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.filters = window.PHOTO_ORGANIZER.filters || {};

(() => {
    // Mirrors yaffo/distance_units.py: proximity distances are entered in the
    // saved unit but the bounding box is computed in kilometers.
    const KILOMETERS_PER_UNIT = { mi: 1.609344, km: 1 };

    /**
     * Read the sidebar form into a criteria object; empty controls become null
     * (no filter), matching the server's querystring parsing.
     * @param {HTMLFormElement} form
     * @returns {ClientFilterCriteria}
     */
    const readCriteria = (form) => {
        const data = new FormData(form);
        const str = (/** @type {string} */ name) => String(data.get(name) ?? '').trim();
        const num = (/** @type {string} */ name) => {
            const value = str(name);
            if (value === '') return null;
            const parsed = Number(value);
            return Number.isNaN(parsed) ? null : parsed;
        };
        const mediaType = str('media-type');
        const proximityLat = num('proximity-lat');
        const proximityLon = num('proximity-lon');
        const proximityDistance = num('proximity-distance');
        return {
            path: str('path') || null,
            year: num('year'),
            month: num('month'),
            device: str('device') || null,
            favorite: Boolean(num('favorite')),
            mediaType: mediaType === 'photo' || mediaType === 'video' ? mediaType : null,
            personIds: data.getAll('person').map(Number),
            personMatchType: str('person-match-type') || 'any',
            gender: num('gender'),
            labelIds: data.getAll('labels').map(Number),
            labelsMatchType: str('labels-match-type') || 'any',
            tagName: str('tag-name') || null,
            tagValue: str('tag-value') || null,
            locationNames: data.getAll('location').map(String),
            unnamed: Boolean(num('unnamed')),
            proximity: proximityLat !== null && proximityLon !== null && proximityDistance
                ? { lat: proximityLat, lon: proximityLon, distance: proximityDistance }
                : null,
        };
    };

    /**
     * Same box the server uses (media_filter_repository.calculate_bounding_box):
     * a flat-earth degree offset, not a true great-circle distance.
     * @param {number} lat
     * @param {number} lon
     * @param {number} distanceKilometers
     */
    const boundingBox = (lat, lon, distanceKilometers) => {
        const latDegreeKilometers = 111.0;
        const lonDegreeKilometers = Math.abs(Math.cos(lat * Math.PI / 180) * 111.0);
        const latOffset = distanceKilometers / latDegreeKilometers;
        const lonOffset = distanceKilometers / lonDegreeKilometers;
        return {
            minLat: lat - latOffset,
            maxLat: lat + latOffset,
            minLon: lon - lonOffset,
            maxLon: lon + lonOffset,
        };
    };

    /**
     * @param {number[] | undefined} itemIds
     * @param {number[]} selectedIds
     * @param {string} matchType
     */
    const matchesIds = (itemIds, selectedIds, matchType) => {
        const ids = itemIds || [];
        return matchType === 'all'
            ? selectedIds.every((id) => ids.includes(id))
            : selectedIds.some((id) => ids.includes(id));
    };

    /**
     * @param {ClientFilterCriteria} criteria
     * @param {{ distanceUnit?: string }} [options]
     * @returns {(item: ClientFilterItem) => boolean}
     */
    const buildPredicate = (criteria, options = {}) => {
        const pathNeedle = criteria.path ? criteria.path.toLowerCase() : null;
        const box = criteria.proximity
            ? boundingBox(
                criteria.proximity.lat,
                criteria.proximity.lon,
                criteria.proximity.distance * (KILOMETERS_PER_UNIT[options.distanceUnit ?? 'km'] ?? 1),
            )
            : null;

        return (item) => {
            if (pathNeedle && !String(item.photo_path ?? '').toLowerCase().includes(pathNeedle)) return false;
            if (criteria.year !== null && item.year !== criteria.year) return false;
            if (criteria.month !== null && item.month !== criteria.month) return false;
            if (criteria.device && item.device !== criteria.device) return false;
            if (criteria.favorite && !item.favorite) return false;
            if (criteria.mediaType && item.media_type !== criteria.mediaType) return false;
            if (criteria.personIds.length > 0
                && !matchesIds(item.person_ids, criteria.personIds, criteria.personMatchType)) return false;
            if (criteria.gender !== null && !(item.genders || []).includes(criteria.gender)) return false;
            if (criteria.labelIds.length > 0
                && !matchesIds(item.label_ids, criteria.labelIds, criteria.labelsMatchType)) return false;
            if (criteria.tagName) {
                const tags = item.tags || [];
                const matched = criteria.tagValue
                    ? tags.some((tag) => tag.name === criteria.tagName && tag.value === criteria.tagValue)
                    : tags.some((tag) => tag.name === criteria.tagName);
                if (!matched) return false;
            }
            // Like the server, 'all' is meaningless for locations (one per item)
            // and is treated as 'any'.
            if (criteria.locationNames.length > 0
                && !criteria.locationNames.includes(String(item.name ?? ''))) return false;
            // A falsy name (null or "") counts as unnamed, same as the server's
            // coalesce(location_name, '') = ''.
            if (criteria.unnamed && item.name) return false;
            if (box) {
                if (item.lat == null || item.lon == null) return false;
                if (item.lat < box.minLat || item.lat > box.maxLat) return false;
                if (item.lon < box.minLon || item.lon > box.maxLon) return false;
            }
            return true;
        };
    };

    /**
     * Clear the real form controls and notify the custom select widgets that wrap
     * them. This intentionally does not use form.reset(), because the page may
     * have loaded with querystring-selected filters; Clear should mean "no
     * filters", not "back to initial URL state".
     * @param {HTMLFormElement} form
     */
    const clearControls = (form) => {
        form.querySelectorAll('input').forEach((input) => {
            if (!(input instanceof HTMLInputElement)) return;
            if (input.type === 'radio') {
                input.checked = input.value === 'any';
                input.dispatchEvent(new Event('change', { bubbles: true }));
                return;
            }
            if (input.type === 'checkbox') {
                input.checked = false;
                input.dispatchEvent(new Event('change', { bubbles: true }));
                return;
            }
            input.value = '';
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
        });

        form.querySelectorAll('select').forEach((select) => {
            if (!(select instanceof HTMLSelectElement)) return;
            if (select.multiple) {
                Array.from(select.options).forEach((option) => {
                    option.selected = false;
                });
            } else if (Array.from(select.options).some((option) => option.value === '')) {
                select.value = '';
            } else {
                select.selectedIndex = 0;
            }
            select.dispatchEvent(new Event('change', { bubbles: true }));
        });

        form.querySelectorAll('.multi-select-wrapper').forEach((wrapper) => {
            const search = wrapper.querySelector('.multi-select-search');
            if (search instanceof HTMLInputElement) {
                search.value = '';
                search.dispatchEvent(new Event('input', { bubbles: true }));
            }
            const firstCheckbox = wrapper.querySelector('input[type="checkbox"]');
            if (firstCheckbox instanceof HTMLInputElement) {
                window.updateMultiSelectText?.(firstCheckbox);
            } else {
                const selectedText = wrapper.querySelector('.selected-text');
                if (selectedText) {
                    selectedText.textContent = wrapper instanceof HTMLElement
                        ? wrapper.dataset.placeholder || ''
                        : '';
                }
            }
            wrapper.classList.remove('open');
        });
    };

    /**
     * Turn the sidebar into a client-side filter: intercept the form's GET
     * submit and hand a fresh predicate to `onApply` instead. Clear is also
     * handled here so pages using client-side filters do not reload.
     * @param {{ form: HTMLFormElement | null, distanceUnit?: string, onApply: (predicate: (item: ClientFilterItem) => boolean) => void }} opts
     * @returns {ClientFilterApi | undefined}
     */
    window.PHOTO_ORGANIZER.filters.initClientFilter = ({ form, distanceUnit, onApply }) => {
        if (!form) return undefined;
        const apply = () => onApply(buildPredicate(readCriteria(form), { distanceUnit }));
        form.addEventListener('submit', (event) => {
            event.preventDefault();
            apply();
        });
        const clear = () => {
            clearControls(form);
            apply();
        };
        form.querySelector('.clear-filters')?.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopImmediatePropagation();
            clear();
        }, { capture: true });
        const api = { apply, clear, readCriteria: () => readCriteria(form) };
        window.PHOTO_ORGANIZER.filters.clientFilter = api;
        return api;
    };

    // Pure pieces exposed for reuse and unit tests.
    window.PHOTO_ORGANIZER.filters.clientFilterCore = { readCriteria, buildPredicate };
})();
