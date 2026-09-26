// @ts-check

// Client-side counterpart of the home route's server-side filtering: reads the
// shared sidebar filter form and builds a predicate over already-loaded media
// items (the locations map filters its markers with it, no round trip).
//
// Which fields exist, their names, types, allowed values and defaults are NOT
// declared here: the page passes the server's table (client_filter_config() in
// yaffo/domain/media_filter_params.py), and criteria use its keys. The matching
// rules below are the browser's copy of media_filter_repository.apply_media_filters
// and must stay in step with it.

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.filters = window.PHOTO_ORGANIZER.filters || {};

(() => {
    /**
     * Read the sidebar form into criteria keyed like the server's selections
     * (person_ids, media_type, …): each parameter in `config` read by its form
     * name and kind. Empty or disallowed values become the parameter's default
     * (null, [], false, or 'any' for a match type), as the server parses them.
     * @param {HTMLFormElement} form
     * @param {ClientFilterConfig} config
     * @returns {ClientFilterCriteria}
     */
    const readCriteria = (form, config) => {
        const data = new FormData(form);
        /** @type {Record<string, unknown>} */
        const criteria = {};
        for (const param of config.params) {
            const raw = data.getAll(param.param).map((value) => String(value).trim()).filter((value) => value !== '');
            /** @type {unknown} */
            let value;
            if (param.kind === 'int_list') {
                value = raw.map(Number).filter(Number.isInteger);
            } else if (param.kind === 'str_list') {
                value = raw;
            } else if (param.kind === 'flag') {
                value = raw.length > 0 && Number(raw[0]) !== 0;
            } else if (raw.length === 0) {
                value = null;
            } else if (param.kind === 'int') {
                const parsed = Number(raw[0]);
                value = Number.isInteger(parsed) ? parsed : null;
            } else if (param.kind === 'float') {
                const parsed = Number(raw[0]);
                value = Number.isNaN(parsed) ? null : parsed;
            } else {
                value = raw[0];
            }
            if (value !== null && !Array.isArray(value) && param.choices && !param.choices.includes(value)) {
                value = null;
            }
            criteria[param.key] = value ?? param.default ?? null;
        }
        return /** @type {ClientFilterCriteria} */ (criteria);
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
     * @param {ClientFilterConfig} config
     * @param {{ distanceUnit?: string }} [options]
     * @returns {(item: ClientFilterItem) => boolean}
     */
    const buildPredicate = (criteria, config, options = {}) => {
        const pathNeedle = criteria.path ? criteria.path.toLowerCase() : null;
        const { proximity_lat: lat, proximity_lon: lon, proximity_distance: distance } = criteria;
        const kilometersPerUnit = config.kilometers_per_unit[options.distanceUnit ?? 'km'] ?? 1;
        const box = lat !== null && lon !== null && distance
            ? boundingBox(lat, lon, distance * kilometersPerUnit)
            : null;

        return (item) => {
            if (pathNeedle && !String(item.photo_path ?? '').toLowerCase().includes(pathNeedle)) return false;
            if (criteria.year !== null && item.year !== criteria.year) return false;
            if (criteria.month !== null && item.month !== criteria.month) return false;
            if (criteria.device && item.device !== criteria.device) return false;
            if (criteria.favorite && !item.favorite) return false;
            if (criteria.media_type && item.media_type !== criteria.media_type) return false;
            // The server precomputes `shape` from the stored dimensions; an item
            // without them has none, and matches no shape — same as the SQL, where
            // the NULL comparison is false.
            if (criteria.shape && item.shape !== criteria.shape) return false;
            if (criteria.person_ids.length > 0
                && !matchesIds(item.person_ids, criteria.person_ids, criteria.person_match_type)) return false;
            if (criteria.gender !== null && !(item.genders || []).includes(criteria.gender)) return false;
            if (criteria.label_ids.length > 0
                && !matchesIds(item.label_ids, criteria.label_ids, criteria.labels_match_type)) return false;
            if (criteria.tag_name) {
                const tags = item.tags || [];
                const matched = criteria.tag_value
                    ? tags.some((tag) => tag.name === criteria.tag_name && tag.value === criteria.tag_value)
                    : tags.some((tag) => tag.name === criteria.tag_name);
                if (!matched) return false;
            }
            // Like the server, 'all' is meaningless for locations (one per item)
            // and is treated as 'any'.
            if (criteria.location_names.length > 0
                && !criteria.location_names.includes(String(item.name ?? ''))) return false;
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
     * @param {{ form: HTMLFormElement | null, config: ClientFilterConfig, distanceUnit?: string, onApply: (predicate: (item: ClientFilterItem) => boolean) => void }} opts
     * @returns {ClientFilterApi | undefined}
     */
    window.PHOTO_ORGANIZER.filters.initClientFilter = ({ form, config, distanceUnit, onApply }) => {
        if (!form) return undefined;
        const apply = () => onApply(buildPredicate(readCriteria(form, config), config, { distanceUnit }));
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
        const api = { apply, clear, readCriteria: () => readCriteria(form, config) };
        window.PHOTO_ORGANIZER.filters.clientFilter = api;
        return api;
    };

    // Pure pieces exposed for reuse and unit tests.
    window.PHOTO_ORGANIZER.filters.clientFilterCore = { readCriteria, buildPredicate };
})();
