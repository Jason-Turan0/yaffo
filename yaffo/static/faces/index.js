window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

// One cluster is shown at a time. For each cluster we only paint a random
// sample of up to `sampleSize` thumbnails (a 50k batch would otherwise melt the
// DOM), but selection/assignment span the WHOLE cluster: `selectedIds` starts as
// every face id and clicking a visible face just removes it. Assigning advances
// to the next cluster; when none remain we reload to pull the next batch.
window.PHOTO_ORGANIZER.initFaceAssignment = (sampleSize, topPeople, i18n, config) => {
    const tooltip = document.createElement('div');
    tooltip.className = 'tooltip';
    document.body.appendChild(tooltip);

    const groups = Array.from(document.querySelectorAll('.suggestion-group'));
    let activeGroup = null;
    let faces = [];            // current cluster: [{id, photo_date, similarity}]
    let order = [];            // `faces` in display order (shuffle reorders this)
    let page = 0;              // 0-based page within `order`
    let pageCount = 1;
    let selectedIds = new Set();
    let lastClickedId = null;

    const thumbUrl = (id) => config.buildUrl('face_thumbnail', { face_id: id });
    const placeholderUrl = config.urls.placeholder;

    // Re-load /faces with the current filters, resetting to page 1: assignments
    // shrink the unassigned set, so page 1 always holds the next batch and the
    // server renders the "All Faces Assigned!" panel once it's empty.
    const loadNextBatch = () => {
        const url = new URL(window.location.href);
        url.searchParams.delete('page');
        window.location.assign(url.toString());
    };

    const randomSample = (arr, n) => {
        const copy = arr.slice();
        for (let i = copy.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [copy[i], copy[j]] = [copy[j], copy[i]];
        }
        return copy.slice(0, n);
    };

    const updatePager = () => {
        activeGroup.querySelector('.cluster-page-label').textContent = i18n.t('common:pageOf', {
            page: i18n.number(page + 1),
            total: i18n.number(pageCount),
        });
        const atFirst = page === 0;
        const atLast = page >= pageCount - 1;
        // `.disabled` matches the home pager's look; the `disabled` attribute
        // blocks the click so no extra guard is needed in the handlers.
        const states = [
            ['.cluster-first', atFirst], ['.cluster-prev', atFirst],
            ['.cluster-next', atLast], ['.cluster-last', atLast],
        ];
        states.forEach(([selector, off]) => {
            const btn = activeGroup.querySelector(selector);
            btn.disabled = off;
            btn.classList.toggle('disabled', off);
        });
    };

    const renderPage = () => {
        if (!activeGroup) return;
        const grid = activeGroup.querySelector('.grid');
        const start = page * sampleSize;
        const shown = order.slice(start, start + sampleSize);
        grid.innerHTML = shown.map(face => {
            const similarity = face.similarity != null ? face.similarity : '';
            const selected = selectedIds.has(face.id) ? ' selected' : '';
            return `<div class="face${selected}" data-face-id="${face.id}"`
                + ` data-similarity="${similarity}" data-date="${face.photo_date || ''}">`
                + `<img src="${thumbUrl(face.id)}" data-fallback="${placeholderUrl}" width="100" height="100">`
                + `</div>`;
        }).join('');
        window.PHOTO_ORGANIZER.utils.initImageFallbacks();
        activeGroup.querySelector('.sample-range').textContent =
            shown.length
                ? i18n.number(start + 1) + '–' + i18n.number(start + shown.length)
                : i18n.number(0);
        updatePager();
        lastClickedId = null;
    };

    const activateGroup = (group) => {
        groups.forEach(g => { g.hidden = g !== group; });
        activeGroup = group;
        faces = JSON.parse(group.dataset.faces || '[]');
        order = faces.slice();
        page = 0;
        pageCount = Math.max(1, Math.ceil(faces.length / sampleSize));
        selectedIds = new Set(faces.map(f => f.id));
        const groupCheckbox = group.querySelector('.group-select-checkbox');
        if (groupCheckbox) groupCheckbox.checked = true;
        // Shuffling only matters when the cluster spills past a single page.
        const shuffleBtn = group.querySelector('.shuffle-sample-btn');
        if (shuffleBtn) shuffleBtn.disabled = faces.length <= sampleSize;
        // People-mode clusters carry their matched people on the assign buttons;
        // similarity-mode clusters have none, so fall back to the frequent people.
        const clusterPeople = Array.from(group.querySelectorAll('.assign-group-btn'))
            .map(b => ({ id: b.dataset.personId, name: b.dataset.personName }));
        rebuildShortcuts(clusterPeople.length ? clusterPeople : topPeople);
        renderPage();
    };

    const setVisibleSelected = (faceEl, on) => {
        const id = Number(faceEl.dataset.faceId);
        if (on) selectedIds.add(id); else selectedIds.delete(id);
        faceEl.classList.toggle('selected', on);
    };

    const selectWholeCluster = (on) => {
        selectedIds = on ? new Set(faces.map(f => f.id)) : new Set();
        if (!activeGroup) return;
        activeGroup.querySelectorAll('.face').forEach(el => el.classList.toggle('selected', on));
        const groupCheckbox = activeGroup.querySelector('.group-select-checkbox');
        if (groupCheckbox) groupCheckbox.checked = on;
    };

    const advanceCluster = () => {
        const index = groups.indexOf(activeGroup);
        const next = groups[index + 1];
        if (next) {
            activateGroup(next);
        } else {
            loadNextBatch();
        }
    };

    const submitFaces = async (personId, faceStatus) => {
        const faceIds = Array.from(selectedIds);
        // Nothing selected: treat the cluster as skipped. The faces stay
        // unassigned (they'll resurface in a later batch) and we move on.
        if (faceIds.length === 0) {
            showNotification('Skipped — no faces selected', 'info');
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
                showNotification(result.message, 'success');
                advanceCluster();
            } else {
                showNotification(result.message, 'error');
            }
        } catch (error) {
            showNotification(`Error: ${error.message}`, 'error');
            console.error('Error submitting faces:', error);
        }
    };

    // --- Click handling on the active cluster (delegated; the grid is re-rendered on every shuffle) ---
    const clusters = document.getElementById('clusters');

    clusters.addEventListener('click', (e) => {
        const faceEl = e.target.closest('.face');
        if (faceEl && activeGroup && activeGroup.contains(faceEl)) {
            if (e.shiftKey && lastClickedId != null) {
                const visible = Array.from(activeGroup.querySelectorAll('.face'));
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

        const assignBtn = e.target.closest('.assign-group-btn');
        if (assignBtn) {
            e.preventDefault();
            submitFaces(assignBtn.dataset.personId, 'ASSIGNED');
            return;
        }

        if (e.target.closest('.shuffle-sample-btn')) {
            e.preventDefault();
            order = randomSample(faces, faces.length);
            page = 0;
            renderPage();
            return;
        }

        if (e.target.closest('.skip-cluster-btn')) {
            e.preventDefault();
            showNotification('Cluster skipped', 'info');
            advanceCluster();
            return;
        }

        if (e.target.closest('.cluster-first')) {
            e.preventDefault();
            if (page !== 0) { page = 0; renderPage(); }
            return;
        }

        if (e.target.closest('.cluster-prev')) {
            e.preventDefault();
            if (page > 0) { page -= 1; renderPage(); }
            return;
        }

        if (e.target.closest('.cluster-next')) {
            e.preventDefault();
            if (page < pageCount - 1) { page += 1; renderPage(); }
            return;
        }

        if (e.target.closest('.cluster-last')) {
            e.preventDefault();
            if (page !== pageCount - 1) { page = pageCount - 1; renderPage(); }
            return;
        }
    });

    clusters.addEventListener('change', (e) => {
        if (e.target.classList.contains('group-select-checkbox')) {
            selectWholeCluster(e.target.checked);
        }
    });

    // Tooltip on hover (delegated)
    clusters.addEventListener('mouseover', (e) => {
        const faceEl = e.target.closest('.face');
        if (!faceEl) return;
        const similarity = Number(faceEl.dataset.similarity);
        const date = PHOTO_ORGANIZER.utils.date.format(faceEl.dataset.date);
        const hasSimilarity = Number.isFinite(similarity);
        const similarityText = hasSimilarity
            ? i18n.percent(similarity, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
            : '';
        const tooltipParts = [];
        if (hasSimilarity) {
            tooltipParts.push(document.createTextNode(
                i18n.t('common:similarityValue', { value: similarityText })
            ));
            tooltipParts.push(document.createElement('br'));
        }
        tooltipParts.push(document.createTextNode(i18n.t('common:dateValue', { value: date })));
        tooltip.replaceChildren(...tooltipParts);
        tooltip.classList.add('visible');
        const rect = faceEl.getBoundingClientRect();
        tooltip.style.left = rect.left + rect.width / 2 + 'px';
        tooltip.style.top = rect.top + window.scrollY - 10 + 'px';
        tooltip.style.transform = 'translate(-50%, -100%)';
    });
    clusters.addEventListener('mouseout', (e) => {
        if (e.target.closest('.face')) tooltip.classList.remove('visible');
    });

    // Threshold slider display
    const thresholdRange = document.getElementById('threshold-range');
    const thresholdValue = document.getElementById('threshold-value');
    if (thresholdRange) {
        thresholdRange.addEventListener('input', (e) => {
            thresholdValue.textContent = e.target.value;
        });
    }

    // Sidebar: assign from searchable select
    const assignSelectedBtn = document.getElementById('sidebar-assign-selected-btn');
    const sidebarPersonSelect = document.getElementById('sidebar-person-select');
    const createPersonBtn = document.getElementById('create-person-btn');

    if (assignSelectedBtn) {
        assignSelectedBtn.addEventListener('click', (e) => {
            e.preventDefault();
            const personId = sidebarPersonSelect.value;
            if (!personId) {
                showNotification('Please select a person first', 'error');
                return;
            }
            submitFaces(personId, 'ASSIGNED');
        });
    }

    document.getElementById('sidebar-ignore-btn').addEventListener('click', (e) => {
        e.preventDefault();
        submitFaces(null, 'IGNORED');
    });

    // Keep the assign-person filter in the URL without reloading
    if (sidebarPersonSelect) {
        sidebarPersonSelect.addEventListener('change', (e) => {
            const personId = e.target.value;
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
            const inputElement = document.getElementById('create-person-name');
            const personName = inputElement.value;
            if (!personName || !personName.trim()) {
                notification.error('Please enter a person name');
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
                    notification.success(`Created "${personName}"`);
                    setTimeout(() => window.location.reload(), 1500);
                } else {
                    const error = await createResponse.json();
                    notification.error(`${error?.error}`);
                }
            } catch (err) {
                notification.error(err.message);
            } finally {
                createPersonBtn.disabled = false;
            }
        });
    }

    // Number-key shortcuts map to people. In "group by people" they are the
    // active cluster's matched people (rebuilt per cluster); in "group by
    // similarity" the cluster has none, so we fall back to the most-frequent
    // people passed from the server.
    const keyboardShortcutMap = new Map();
    const sidebarShortcutList = document.getElementById('sidebar-shortcut-people');
    const helpShortcutList = document.getElementById('help-shortcut-people');
    const flashElement = (element) => {
        if (!element) return;
        element.classList.add('keyboard-activated');
        setTimeout(() => element.classList.remove('keyboard-activated'), 300);
    };
    const escapeHtml = (s) => String(s).replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

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
            const element = sidebarShortcutList.querySelector(`[data-shortcut="${i + 1}"]`);
            keyboardShortcutMap.set(String(i + 1), { personId: p.id, element });
        });
    };

    // Clicking a sidebar shortcut row assigns the same as pressing its key.
    sidebarShortcutList.addEventListener('click', (e) => {
        const item = e.target.closest('.shortcut-item[data-person-id]');
        if (!item) return;
        flashElement(item);
        submitFaces(item.dataset.personId, 'ASSIGNED');
    });

    const keyboardHelpModal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('keyboardHelpModal');

    document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
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
        rebuildShortcuts(topPeople);
    }
};
