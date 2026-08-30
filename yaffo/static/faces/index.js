// @ts-check

/**
 * A face's detection box, in source-image pixels.
 * @typedef {Object} FaceRegion
 * @property {number} top
 * @property {number} right
 * @property {number} bottom
 * @property {number} left
 *
 * One face awaiting assignment within a cluster.
 * @typedef {Object} FaceRecord
 * @property {number} id
 * @property {string} [photo_date]
 * @property {number | null} [similarity]
 * @property {number} [media_item_id]
 * @property {string} [media_type]
 * @property {FaceRegion | null} [region]
 *
 * A person offered as a keyboard/shortcut assignment target.
 * @typedef {Object} PersonShortcut
 * @property {string | number} id
 * @property {string} name
 *
 * The public surface returned by the page initializer.
 * @typedef {Object} FacesAssignmentApi
 * @property {(personId: string | number | null, faceStatus: string) => Promise<void>} submitFaces
 * @property {(on: boolean) => void} selectWholeCluster
 * @property {(arr: FaceRecord[], n: number) => FaceRecord[]} randomSample
 * @property {() => Set<number>} getSelectedIds
 * @property {() => PersonShortcut[]} getShortcutPeople
 */

const facesWindow = window;

facesWindow.PHOTO_ORGANIZER = facesWindow.PHOTO_ORGANIZER || {};
const facesNamespace = facesWindow.PHOTO_ORGANIZER.faces =
    /** @type {FacesNamespace} */ (facesWindow.PHOTO_ORGANIZER.faces || {});

// Edge length of the source-photo preview in the hover tooltip, in CSS pixels.
const PREVIEW_SIZE = 320;
// Breathing room around the detection box, as a fraction of its own size — a box
// drawn tight to the crop reads as if it's cutting the face off.
const REGION_PADDING = 0.1;

// One cluster is shown at a time. For each cluster we only paint a random
// sample of up to `sampleSize` thumbnails (a 50k batch would otherwise melt the
// DOM), but selection/assignment span the WHOLE cluster: `selectedIds` starts as
// every face id and clicking a visible face just removes it. Assigning advances
// to the next cluster; when none remain we reload to pull the next batch.
/**
 * @param {number} sampleSize
 * @param {PersonShortcut[]} initialShortcutPeople
 * @param {PersonShortcut[]} allPeople
 * @param {boolean} initialShortcutPeopleCustomized
 * @param {I18nService} i18n
 * @param {AppConfig} config
 * @returns {FacesAssignmentApi}
 */
facesNamespace.initAssignment = (
    sampleSize,
    initialShortcutPeople,
    allPeople,
    initialShortcutPeopleCustomized,
    i18n,
    config
) => {
    const tooltip = document.createElement('div');
    tooltip.className = 'tooltip face-tooltip';
    document.body.appendChild(tooltip);

    const groups = /** @type {HTMLElement[]} */ (Array.from(document.querySelectorAll('.suggestion-group')));
    /** @type {HTMLElement | null} */
    let activeGroup = null;
    /** @type {FaceRecord[]} */
    let faces = [];            // current cluster: [{id, photo_date, similarity}]
    /** @type {FaceRecord[]} */
    let order = [];            // `faces` in display order (shuffle reorders this)
    let page = 0;              // 0-based page within `order`
    let pageCount = 1;
    /** @type {Set<number>} */
    let selectedIds = new Set();
    /** @type {number | null} */
    let lastClickedId = null;
    /** @type {PersonShortcut[]} */
    let shortcutPeople = initialShortcutPeople.slice(0, 9);
    let shortcutPeopleCustomized = initialShortcutPeopleCustomized;
    /** @type {PersonShortcut[]} */
    let automaticShortcutPeople = initialShortcutPeople.slice(0, 9);

    /** @param {number} id */
    const thumbUrl = (id) => config.buildUrl('face_thumbnail', { face_id: id });
    const placeholderUrl = config.urls.placeholder;

    // The hover preview shows the photo the face was cropped from. A video's /media
    // route returns the raw clip and its poster frame is rarely the frame the face
    // came from, so video-sourced faces get a "no preview" note instead of an image.
    /** @param {FaceRecord} face */
    const sourceUrl = (face) => (face.media_type === 'video' || face.media_item_id == null
        ? ''
        : config.buildUrl('media', { media_item_id: face.media_item_id }));

    /**
     * @param {string | undefined} raw
     * @returns {FaceRegion | null}
     */
    const parseRegion = (raw) => {
        if (!raw) return null;
        try {
            return /** @type {FaceRegion} */ (JSON.parse(raw));
        } catch {
            return null;
        }
    };

    // The source photo, box-fitted into a PREVIEW_SIZE square with the detection
    // outlined on it. The outline is placed once the image reports its natural
    // size: `contain` letterboxes the photo inside the square, so the region — in
    // source-image pixels — has to be scaled and offset onto the *drawn* area
    // rather than the frame.
    /**
     * @param {string} source
     * @param {FaceRegion | null} region
     * @returns {HTMLElement}
     */
    const buildPreview = (source, region) => {
        const preview = document.createElement('div');
        preview.className = 'face-source-preview';

        const image = document.createElement('img');
        image.className = 'face-source-image';
        image.alt = '';
        preview.appendChild(image);

        const box = document.createElement('div');
        box.className = 'face-source-region';
        box.hidden = true;  // until placed, so it can't flash in the corner
        if (region) preview.appendChild(box);

        image.addEventListener('load', () => {
            const { naturalWidth, naturalHeight } = image;
            if (!region || !naturalWidth || !naturalHeight) return;
            // Pad the box by REGION_PADDING of its own size, clamped to the photo so
            // a face at the edge of the frame doesn't push the outline off the image.
            const padX = (region.right - region.left) * REGION_PADDING;
            const padY = (region.bottom - region.top) * REGION_PADDING;
            const left = Math.max(0, region.left - padX);
            const top = Math.max(0, region.top - padY);
            const right = Math.min(naturalWidth, region.right + padX);
            const bottom = Math.min(naturalHeight, region.bottom + padY);

            const previewSize = preview.clientWidth || PREVIEW_SIZE;
            const scale = Math.min(previewSize / naturalWidth, previewSize / naturalHeight);
            const offsetX = (previewSize - naturalWidth * scale) / 2;
            const offsetY = (previewSize - naturalHeight * scale) / 2;
            box.style.left = offsetX + left * scale + 'px';
            box.style.top = offsetY + top * scale + 'px';
            box.style.width = (right - left) * scale + 'px';
            box.style.height = (bottom - top) * scale + 'px';
            box.hidden = false;
        });
        image.addEventListener('error', () => {
            if (image.src !== placeholderUrl) image.src = placeholderUrl;
            box.hidden = true;  // the placeholder isn't the photo; nothing to outline
        }, { once: true });
        image.src = source;  // set last: the listeners have to be attached first

        return preview;
    };

    // Re-load /faces with the current filters, resetting to page 1: assignments
    // shrink the unassigned set, so page 1 always holds the next batch and the
    // server renders the "All Faces Assigned!" panel once it's empty.
    const loadNextBatch = () => {
        const url = new URL(window.location.href);
        url.searchParams.delete('page');
        window.location.assign(url.toString());
    };

    /**
     * @param {FaceRecord[]} arr
     * @param {number} n
     * @returns {FaceRecord[]}
     */
    const randomSample = (arr, n) => {
        const copy = arr.slice();
        for (let i = copy.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [copy[i], copy[j]] = [copy[j], copy[i]];
        }
        return copy.slice(0, n);
    };

    const updatePager = () => {
        if (!activeGroup) return;
        /** @type {HTMLElement} */ (activeGroup.querySelector('.cluster-page-label')).textContent =
            i18n.t('common:pageOf', {
                page: i18n.number(page + 1),
                total: i18n.number(pageCount),
            });
        const atFirst = page === 0;
        const atLast = page >= pageCount - 1;
        // `.disabled` matches the home pager's look; the `disabled` attribute
        // blocks the click so no extra guard is needed in the handlers.
        /** @type {[string, boolean][]} */
        const states = [
            ['.cluster-first', atFirst], ['.cluster-prev', atFirst],
            ['.cluster-next', atLast], ['.cluster-last', atLast],
        ];
        states.forEach(([selector, off]) => {
            const btn = /** @type {HTMLButtonElement} */ (activeGroup?.querySelector(selector));
            btn.disabled = off;
            btn.classList.toggle('disabled', off);
        });
    };

    const renderPage = () => {
        if (!activeGroup) return;
        const grid = /** @type {HTMLElement} */ (activeGroup.querySelector('.grid'));
        const start = page * sampleSize;
        const shown = order.slice(start, start + sampleSize);
        grid.innerHTML = shown.map(face => {
            const similarity = face.similarity != null ? face.similarity : '';
            const selected = selectedIds.has(face.id) ? ' selected' : '';
            // data-region is single-quoted: it holds JSON, whose own quotes are double.
            return `<div class="face${selected}" data-face-id="${face.id}"`
                + ` data-similarity="${similarity}" data-date="${face.photo_date || ''}"`
                + ` data-media-type="${face.media_type || ''}" data-source="${sourceUrl(face)}"`
                + ` data-region='${face.region ? JSON.stringify(face.region) : ''}'>`
                + `<img src="${thumbUrl(face.id)}" data-fallback="${placeholderUrl}" width="100" height="100">`
                + `</div>`;
        }).join('');
        grid.querySelectorAll('.face').forEach((faceEl) => {
            const previewButton = document.createElement('button');
            previewButton.type = 'button';
            previewButton.className = 'face-preview-button';
            previewButton.setAttribute('aria-label', i18n.t('faces:assignment.preview'));
            previewButton.textContent = 'ⓘ';
            faceEl.appendChild(previewButton);
        });
        facesWindow.PHOTO_ORGANIZER.utils?.initImageFallbacks?.();
        /** @type {HTMLElement} */ (activeGroup.querySelector('.sample-range')).textContent =
            shown.length
                ? i18n.number(start + 1) + '–' + i18n.number(start + shown.length)
                : i18n.number(0);
        updatePager();
        lastClickedId = null;
    };

    /** @param {HTMLElement} group */
    const activateGroup = (group) => {
        groups.forEach(g => { g.hidden = g !== group; });
        activeGroup = group;
        faces = JSON.parse(group.dataset.faces || '[]');
        order = faces.slice();
        page = 0;
        pageCount = Math.max(1, Math.ceil(faces.length / sampleSize));
        selectedIds = new Set(faces.map(f => f.id));
        updateSelectAllChip();
        // Shuffling only matters when the cluster spills past a single page.
        const shuffleBtn = /** @type {HTMLButtonElement | null} */ (group.querySelector('.shuffle-sample-btn'));
        if (shuffleBtn) shuffleBtn.disabled = faces.length <= sampleSize;
        // People-mode clusters carry their matched people on the assign buttons;
        // similarity-mode clusters have none, so fall back to the frequent people.
        const clusterPeople = Array.from(group.querySelectorAll('.assign-group-btn'))
            .map(b => ({
                id: /** @type {HTMLElement} */ (b).dataset.personId || '',
                name: /** @type {HTMLElement} */ (b).dataset.personName || '',
            }));
        automaticShortcutPeople = (clusterPeople.length ? clusterPeople : initialShortcutPeople).slice(0, 9);
        rebuildShortcuts(shortcutPeopleCustomized ? shortcutPeople : automaticShortcutPeople);
        if (!shortcutPeopleCustomized) syncShortcutConfigSelection(automaticShortcutPeople);
        renderPage();
    };

    /**
     * @param {HTMLElement} faceEl
     * @param {boolean} on
     */
    const setVisibleSelected = (faceEl, on) => {
        const id = Number(faceEl.dataset.faceId);
        if (on) selectedIds.add(id); else selectedIds.delete(id);
        faceEl.classList.toggle('selected', on);
        updateSelectAllChip();
    };

    /** Everything in the cluster selected? Drives the chip's label. */
    const wholeClusterSelected = () => faces.length > 0 && selectedIds.size === faces.length;

    /** The chip always names what pressing it will DO. */
    const updateSelectAllChip = () => {
        if (!activeGroup) return;
        const chip = /** @type {HTMLElement | null} */ (activeGroup.querySelector('.cluster-select-all'));
        if (!chip) return;
        chip.textContent = wholeClusterSelected()
            ? (chip.dataset.selectNoneLabel || '')
            : (chip.dataset.selectAllLabel || '');
    };

    /** @param {boolean} on */
    const selectWholeCluster = (on) => {
        selectedIds = on ? new Set(faces.map(f => f.id)) : new Set();
        if (!activeGroup) return;
        activeGroup.querySelectorAll('.face').forEach(el => el.classList.toggle('selected', on));
        updateSelectAllChip();
    };

    const advanceCluster = () => {
        if (!activeGroup) return;
        const index = groups.indexOf(activeGroup);
        const next = groups[index + 1];
        if (next) {
            activateGroup(next);
        } else {
            loadNextBatch();
        }
    };

    /**
     * @param {string | number | null} personId
     * @param {string} faceStatus
     */
    const submitFaces = async (personId, faceStatus) => {
        const faceIds = Array.from(selectedIds);
        // Nothing selected: treat the cluster as skipped. The faces stay
        // unassigned (they'll resurface in a later batch) and we move on.
        if (faceIds.length === 0) {
            facesWindow.notification.info(i18n.t('faces:assignment.noneSelected'));
            advanceCluster();
            return;
        }
        try {
            const response = await fetch(config.urls.faces_assign, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ faces: faceIds, person: personId, faceStatus: faceStatus })
            });
            const result = await response.json();
            if (result.success) {
                facesWindow.showNotification?.(result.message, 'success');
                advanceCluster();
            } else {
                facesWindow.showNotification?.(result.message, 'error');
            }
        } catch (error) {
            facesWindow.notification.error(i18n.t('faces:assignment.requestFailed', {
                reason: error instanceof Error ? error.message : String(error),
            }));
            console.error('Error submitting faces:', error);
        }
    };

    // --- Click handling on the active cluster (delegated; the grid is re-rendered on every shuffle) ---
    const clusters = /** @type {HTMLElement} */ (document.getElementById('clusters'));
    const coarsePointer = window.matchMedia('(hover: none), (pointer: coarse)');

    /** @param {HTMLElement} faceEl */
    const showTooltip = (faceEl) => {
        const rawSimilarity = faceEl.dataset.similarity;
        const similarity = rawSimilarity ? Number(rawSimilarity) : NaN;
        const date = (faceEl.dataset.date
            && facesWindow.PHOTO_ORGANIZER.utils?.date?.format(faceEl.dataset.date))
            || i18n.t('common:unknown');
        const hasSimilarity = Number.isFinite(similarity);
        const similarityText = hasSimilarity
            ? i18n.percent(similarity, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
            : '';
        const tooltipParts = [];
        const source = faceEl.dataset.source;
        if (source) {
            tooltipParts.push(buildPreview(source, parseRegion(faceEl.dataset.region)));
        } else if (faceEl.dataset.mediaType === 'video') {
            const note = document.createElement('div');
            note.className = 'face-preview-note';
            note.textContent = i18n.t('faces:assignment.videoPreviewUnavailable');
            tooltipParts.push(note);
        }
        if (hasSimilarity) {
            tooltipParts.push(document.createTextNode(
                i18n.t('common:similarityValue', { value: similarityText })
            ));
            tooltipParts.push(document.createElement('br'));
        }
        tooltipParts.push(document.createTextNode(i18n.t('common:dateValue', { value: date })));
        tooltip.replaceChildren(...tooltipParts);
        tooltip.dataset.faceId = faceEl.dataset.faceId || '';
        tooltip.classList.add('visible');

        if (coarsePointer.matches) {
            tooltip.classList.remove('tooltip-below');
            tooltip.style.removeProperty('left');
            tooltip.style.removeProperty('top');
            tooltip.style.removeProperty('transform');
            return;
        }

        // With a preview image the tooltip is tall enough to run off the top of
        // the viewport, so it flips below the face when it will not fit above.
        const rect = faceEl.getBoundingClientRect();
        const below = rect.top < tooltip.offsetHeight + 10;
        tooltip.classList.toggle('tooltip-below', below);
        tooltip.style.left = rect.left + rect.width / 2 + 'px';
        tooltip.style.top = below
            ? rect.bottom + window.scrollY + 10 + 'px'
            : rect.top + window.scrollY - 10 + 'px';
        tooltip.style.transform = below ? 'translate(-50%, 0)' : 'translate(-50%, -100%)';
    };

    const hideTooltip = () => {
        tooltip.classList.remove('visible');
        delete tooltip.dataset.faceId;
    };

    clusters.addEventListener('click', (e) => {
        const origin = e.target instanceof Element ? e.target : null;

        const previewButton = origin?.closest('.face-preview-button');
        if (previewButton) {
            e.preventDefault();
            e.stopPropagation();
            const faceEl = /** @type {HTMLElement | null} */ (previewButton.closest('.face'));
            if (!faceEl) return;
            if (tooltip.classList.contains('visible')
                && tooltip.dataset.faceId === faceEl.dataset.faceId) {
                hideTooltip();
            } else {
                showTooltip(faceEl);
            }
            return;
        }

        // Select-all chip: takes the whole cluster, or clears it once it is whole.
        if (origin?.closest('.cluster-select-all')) {
            selectWholeCluster(!wholeClusterSelected());
            return;
        }

        const faceEl = /** @type {HTMLElement | null} */ (origin?.closest('.face') ?? null);
        if (faceEl && activeGroup && activeGroup.contains(faceEl)) {
            if (e.shiftKey && lastClickedId != null) {
                const visible = /** @type {HTMLElement[]} */ (Array.from(activeGroup.querySelectorAll('.face')));
                const ids = visible.map(el => Number(el.dataset.faceId));
                const lastIndex = ids.indexOf(lastClickedId);
                const currentIndex = ids.indexOf(Number(faceEl.dataset.faceId));
                if (lastIndex !== -1 && currentIndex !== -1) {
                    const shouldSelect = !faceEl.classList.contains('selected');
                    const [start, end] = [Math.min(lastIndex, currentIndex), Math.max(lastIndex, currentIndex)];
                    for (let i = start; i <= end; i++) setVisibleSelected(visible[i], shouldSelect);
                }
                lastClickedId = null;
            } else if (e.shiftKey) {
                lastClickedId = Number(faceEl.dataset.faceId);
            } else {
                setVisibleSelected(faceEl, !faceEl.classList.contains('selected'));
                lastClickedId = Number(faceEl.dataset.faceId);
            }
            return;
        }

        const assignBtn = /** @type {HTMLElement | null} */ (origin?.closest('.assign-group-btn') ?? null);
        if (assignBtn) {
            e.preventDefault();
            submitFaces(assignBtn.dataset.personId ?? null, 'ASSIGNED');
            return;
        }

        if (origin?.closest('.shuffle-sample-btn')) {
            e.preventDefault();
            order = randomSample(faces, faces.length);
            page = 0;
            renderPage();
            return;
        }

        if (origin?.closest('.skip-cluster-btn')) {
            e.preventDefault();
            facesWindow.notification.info(i18n.t('faces:assignment.clusterSkipped'));
            advanceCluster();
            return;
        }

        if (origin?.closest('.cluster-first')) {
            e.preventDefault();
            if (page !== 0) { page = 0; renderPage(); }
            return;
        }

        if (origin?.closest('.cluster-prev')) {
            e.preventDefault();
            if (page > 0) { page -= 1; renderPage(); }
            return;
        }

        if (origin?.closest('.cluster-next')) {
            e.preventDefault();
            if (page < pageCount - 1) { page += 1; renderPage(); }
            return;
        }

        if (origin?.closest('.cluster-last')) {
            e.preventDefault();
            if (page !== pageCount - 1) { page = pageCount - 1; renderPage(); }
            return;
        }
    });

    // Tooltip on hover for precise pointers. Coarse pointers use the explicit
    // per-face preview button above so selecting and previewing stay distinct.
    clusters.addEventListener('mouseover', (e) => {
        if (coarsePointer.matches) return;
        const origin = e.target instanceof Element ? e.target : null;
        const faceEl = /** @type {HTMLElement | null} */ (origin?.closest('.face') ?? null);
        if (!faceEl) return;
        showTooltip(faceEl);
    });
    clusters.addEventListener('mouseout', (e) => {
        if (coarsePointer.matches) return;
        const origin = e.target instanceof Element ? e.target : null;
        if (origin?.closest('.face')) hideTooltip();
    });
    document.addEventListener('click', (e) => {
        const origin = e.target instanceof Element ? e.target : null;
        if (coarsePointer.matches && !origin?.closest('.face-preview-button, .face-tooltip')) {
            hideTooltip();
        }
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') hideTooltip();
    });

    // Threshold slider display
    const thresholdRange = document.getElementById('threshold-range');
    const thresholdValue = document.getElementById('threshold-value');
    if (thresholdRange && thresholdValue) {
        thresholdRange.addEventListener('input', (e) => {
            thresholdValue.textContent = /** @type {HTMLInputElement} */ (e.target).value;
        });
    }

    // Sidebar: assign from searchable select
    const assignSelectedBtn = document.getElementById('sidebar-assign-selected-btn');
    const sidebarPersonSelect = /** @type {HTMLSelectElement | null} */ (document.getElementById('sidebar-person-select'));
    const createPersonBtn = /** @type {HTMLButtonElement | null} */ (document.getElementById('create-person-btn'));

    if (assignSelectedBtn) {
        assignSelectedBtn.addEventListener('click', (e) => {
            e.preventDefault();
            const personId = sidebarPersonSelect?.value;
            if (!personId) {
                facesWindow.notification.error(i18n.t('faces:assignment.selectPersonFirst'));
                return;
            }
            submitFaces(personId, 'ASSIGNED');
        });
    }

    document.getElementById('sidebar-ignore-btn')?.addEventListener('click', (e) => {
        e.preventDefault();
        submitFaces(null, 'IGNORED');
    });

    // Keep the assign-person filter in the URL without reloading
    if (sidebarPersonSelect) {
        sidebarPersonSelect.addEventListener('change', (e) => {
            const personId = /** @type {HTMLSelectElement} */ (e.target).value;
            const url = new URL(window.location.href);
            if (personId) {
                url.searchParams.set('assign_person', personId);
            } else {
                url.searchParams.delete('assign_person');
            }
            history.replaceState(null, '', url.toString());
        });
    }

    if (createPersonBtn) {
        createPersonBtn.addEventListener('click', async (e) => {
            e.preventDefault();
            const inputElement = /** @type {HTMLInputElement} */ (document.getElementById('create-person-name'));
            const personName = inputElement.value;
            if (!personName || !personName.trim()) {
                facesWindow.notification.error(i18n.t('faces:people.nameRequired'));
                return;
            }
            createPersonBtn.disabled = true;
            try {
                const createResponse = await fetch(config.urls.api_people_create, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: personName.trim() })
                });
                if (createResponse.ok) {
                    facesWindow.notification.success(i18n.t('faces:people.created', { name: personName }));
                    setTimeout(() => window.location.reload(), 1500);
                } else {
                    const error = await createResponse.json();
                    facesWindow.notification.error(`${error?.error}`);
                }
            } catch (err) {
                facesWindow.notification.error(err instanceof Error ? err.message : String(err));
            } finally {
                createPersonBtn.disabled = false;
            }
        });
    }

    // Number-key shortcuts map to people. In "group by people" they are the
    // active cluster's matched people (rebuilt per cluster); in "group by
    // similarity" the cluster has none, so we fall back to the most-frequent
    // people passed from the server.
    /** @type {Map<string, { personId: string | number, element: HTMLElement | null }>} */
    const keyboardShortcutMap = new Map();
    const sidebarShortcutList = /** @type {HTMLElement} */ (document.getElementById('sidebar-shortcut-people'));
    const helpShortcutList = document.getElementById('help-shortcut-people');
    /** @param {HTMLElement | null} element */
    const flashElement = (element) => {
        if (!element) return;
        element.classList.add('keyboard-activated');
        setTimeout(() => element.classList.remove('keyboard-activated'), 300);
    };
    /** @param {string | number} s */
    const escapeHtml = (s) => String(s).replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] ?? c));

    /** @param {PersonShortcut[]} peopleList */
    const rebuildShortcuts = (peopleList) => {
        const limited = peopleList.slice(0, 9);
        keyboardShortcutMap.clear();
        sidebarShortcutList.innerHTML = limited.map((p, i) =>
            `<div class="shortcut-item" data-person-id="${p.id}" data-shortcut="${i + 1}">`
            + `<kbd>${i + 1}</kbd><span>${escapeHtml(p.name)}</span></div>`).join('');
        if (helpShortcutList) {
            helpShortcutList.innerHTML = limited.map((p, i) =>
                `<div class="help-shortcut-item"><kbd>${i + 1}</kbd>`
                + `<span>${escapeHtml(p.name)}</span></div>`).join('');
        }
        limited.forEach((p, i) => {
            const element = /** @type {HTMLElement | null} */ (sidebarShortcutList.querySelector(`[data-shortcut="${i + 1}"]`));
            keyboardShortcutMap.set(String(i + 1), { personId: p.id, element });
        });
    };

    const shortcutConfigTrigger = document.getElementById('configure-shortcuts-btn');
    const shortcutConfigList = /** @type {HTMLElement | null} */ (document.getElementById('shortcut-config-list'));
    const shortcutModalElement = document.getElementById('shortcutPeopleModal');
    const shortcutConfigModal = shortcutModalElement
        ? facesWindow.PHOTO_ORGANIZER.COMPONENTS.modal.init('shortcutPeopleModal')
        : null;
    const shortcutConfigForm = shortcutConfigModal?.formElement;
    const shortcutConfigReset = document.getElementById('shortcut-config-reset');
    const peopleById = new Map(allPeople.map(person => [String(person.id), person]));

    if (shortcutConfigTrigger && shortcutConfigModal) {
        shortcutConfigTrigger.addEventListener('click', () => shortcutConfigModal.open());
    }

    if (shortcutConfigList) {
        /** @type {HTMLElement | null} */
        let draggedShortcutRow = null;

        /**
         * @param {number} y
         * @returns {HTMLElement | null}
         */
        const rowAfterPoint = (y) => {
            const rows = /** @type {HTMLElement[]} */ (
                Array.from(shortcutConfigList.querySelectorAll('.shortcut-config-row:not(.dragging)'))
            );
            /** @type {{ offset: number, row: HTMLElement | null }} */
            const initialClosest = { offset: Number.NEGATIVE_INFINITY, row: null };
            return rows.reduce((closest, row) => {
                const box = row.getBoundingClientRect();
                const offset = y - box.top - box.height / 2;
                return offset < 0 && offset > closest.offset ? { offset, row } : closest;
            }, initialClosest).row;
        };

        shortcutConfigList.addEventListener('dragstart', (e) => {
            if (!(e.target instanceof Element)) return;
            draggedShortcutRow = /** @type {HTMLElement | null} */ (e.target.closest('.shortcut-config-row'));
            if (draggedShortcutRow) draggedShortcutRow.classList.add('dragging');
        });
        shortcutConfigList.addEventListener('dragend', () => {
            if (draggedShortcutRow) draggedShortcutRow.classList.remove('dragging');
            draggedShortcutRow = null;
        });
        shortcutConfigList.addEventListener('dragover', (e) => {
            if (!draggedShortcutRow) return;
            e.preventDefault();
            const after = rowAfterPoint(e.clientY);
            if (after == null) shortcutConfigList.appendChild(draggedShortcutRow);
            else shortcutConfigList.insertBefore(draggedShortcutRow, after);
        });
    }

    /** @param {PersonShortcut[]} peopleList */
    const syncShortcutConfigSelection = (peopleList) => {
        if (!shortcutConfigList) return;
        const selectedIds = new Set(peopleList.slice(0, 9).map(person => String(person.id)));
        /** @type {Record<string, HTMLElement>} */
        const rowsById = {};
        const rows = /** @type {HTMLElement[]} */ (
            Array.from(shortcutConfigList.querySelectorAll('.shortcut-config-row'))
        );
        rows.forEach((row) => {
            if (!(row instanceof HTMLElement) || !row.dataset.personId) return;
            rowsById[row.dataset.personId] = row;
            const toggle = row.querySelector('.shortcut-config-toggle');
            if (toggle instanceof HTMLInputElement) toggle.checked = selectedIds.has(row.dataset.personId);
        });
        /** @type {HTMLElement[]} */
        const selectedRows = [];
        peopleList.slice(0, 9).forEach((person) => {
            const row = rowsById[String(person.id)];
            if (row) selectedRows.push(row);
        });
        const remainingRows = rows.filter(row => !selectedIds.has(row.dataset.personId || ''));
        shortcutConfigList.replaceChildren(...selectedRows, ...remainingRows);
    };

    const selectedShortcutPeopleFromConfig = () => {
        if (!shortcutConfigList) return [];
        /** @type {PersonShortcut[]} */
        const selected = [];
        shortcutConfigList.querySelectorAll('.shortcut-config-row').forEach((row) => {
            if (!(row instanceof HTMLElement) || !row.dataset.personId) return;
            const toggle = row.querySelector('.shortcut-config-toggle');
            if (!(toggle instanceof HTMLInputElement) || !toggle.checked) return;
            const person = peopleById.get(row.dataset.personId);
            if (person) selected.push(person);
        });
        return selected.slice(0, 9);
    };

    if (shortcutConfigForm) {
        shortcutConfigForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const selected = selectedShortcutPeopleFromConfig();
            try {
                const response = await fetch(config.urls.face_shortcut_people_settings, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ person_ids: selected.map(person => person.id) }),
                });
                if (!response.ok) throw new Error('save failed');
                shortcutPeople = selected;
                shortcutPeopleCustomized = true;
                rebuildShortcuts(shortcutPeople);
                syncShortcutConfigSelection(shortcutPeople);
                shortcutConfigModal?.close();
            } catch {
                facesWindow.notification.error(i18n.t('media:filters.saveFailed'));
            }
        });
    }

    shortcutConfigReset?.addEventListener('click', async () => {
        try {
            const response = await fetch(config.urls.face_shortcut_people_settings, { method: 'DELETE' });
            if (!response.ok) throw new Error('reset failed');
            shortcutPeopleCustomized = false;
            rebuildShortcuts(automaticShortcutPeople);
            syncShortcutConfigSelection(automaticShortcutPeople);
            shortcutConfigModal?.close();
        } catch {
            facesWindow.notification.error(i18n.t('media:filters.saveFailed'));
        }
    });

    // Clicking a sidebar shortcut row assigns the same as pressing its key.
    sidebarShortcutList.addEventListener('click', (e) => {
        const origin = e.target instanceof Element ? e.target : null;
        const item = /** @type {HTMLElement | null} */ (origin?.closest('.shortcut-item[data-person-id]') ?? null);
        if (!item) return;
        flashElement(item);
        submitFaces(item.dataset.personId ?? null, 'ASSIGNED');
    });

    const keyboardHelpModal = facesWindow.PHOTO_ORGANIZER.COMPONENTS.modal.init('keyboardHelpModal');

    document.addEventListener('keydown', (e) => {
        const target = e.target instanceof HTMLElement ? e.target : null;
        if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT')) {
            return;
        }
        if (e.key >= '1' && e.key <= '9') {
            e.preventDefault();
            const shortcut = keyboardShortcutMap.get(e.key);
            if (shortcut) {
                flashElement(shortcut.element);
                submitFaces(shortcut.personId, 'ASSIGNED');
            }
        }
        if (e.key === 'i' || e.key === 'I' || e.key === '0') {
            e.preventDefault();
            submitFaces(null, 'IGNORED');
        }
        if (e.key === '?') {
            e.preventDefault();
            keyboardHelpModal.open();
        }
        if (e.key === 'Enter' && assignSelectedBtn) {
            assignSelectedBtn.click();
        }
    });

    // Paint the first cluster (which also builds its shortcuts). With no clusters
    // there's nothing to assign, but still show the frequent-people shortcuts.
    if (groups.length > 0) {
        activateGroup(groups[0]);
    } else {
        automaticShortcutPeople = initialShortcutPeople.slice(0, 9);
        rebuildShortcuts(shortcutPeopleCustomized ? shortcutPeople : automaticShortcutPeople);
        if (!shortcutPeopleCustomized) syncShortcutConfigSelection(automaticShortcutPeople);
    }

    return {
        submitFaces,
        selectWholeCluster,
        randomSample,
        getSelectedIds: () => new Set(selectedIds),
        getShortcutPeople: () => shortcutPeople.slice(),
    };
};
