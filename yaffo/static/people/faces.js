// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.people = window.PHOTO_ORGANIZER.people || {};

/**
 * @param {I18nService} i18n
 * @param {AppConfig} config
 * @returns {PeopleFacesApi}
 */
window.PHOTO_ORGANIZER.people.initFaces = (i18n, config) => {
    const tooltip = document.createElement('div');
    tooltip.className = 'tooltip';
    document.body.appendChild(tooltip);

    /**
     * @param {string} rangeId
     * @param {string} valueId
     */
    const updateSimilarityDisplay = (rangeId, valueId) => {
        const range = document.getElementById(rangeId);
        const value = document.getElementById(valueId);
        if (!(range instanceof HTMLInputElement) || !value) return;
        range?.addEventListener('input', (event) => {
            value.textContent = i18n.percent(Number(range.value) / 100);
        });
    };

    updateSimilarityDisplay('min_similarity-range', 'min_similarity-value');
    updateSimilarityDisplay('max_similarity-range', 'max_similarity-value');

    const cards = /** @type {HTMLElement[]} */ (Array.from(document.querySelectorAll('.face-card')));
    /**
     * @param {Element} card
     * @returns {HTMLInputElement | null}
     */
    const getCheckbox = (card) => {
        const checkbox = card.querySelector('input[type="checkbox"]');
        return checkbox instanceof HTMLInputElement ? checkbox : null;
    };

    cards.forEach((card) => {
        card.addEventListener('click', () => {
            card.classList.toggle('selected');
            const checkbox = getCheckbox(card);
            if (!checkbox) return;
            checkbox.checked = !checkbox.checked;
        });

        card.addEventListener('mouseenter', () => {
            const similarity = card.dataset.similarity === ''
                ? Number.NaN
                : Number(card.dataset.similarity);
            const date = window.PHOTO_ORGANIZER.utils?.date?.format(card.dataset.date) || '';
            const similarityText = Number.isFinite(similarity)
                ? i18n.percent(similarity / 100)
                : i18n.t('common:notAvailable');
            tooltip.replaceChildren(
                document.createTextNode(i18n.t('common:dateValue', { value: date })),
                document.createElement('br'),
                document.createTextNode(i18n.t('common:similarityValue', { value: similarityText })),
            );
            tooltip.classList.add('visible');
        });

        card.addEventListener('mousemove', () => {
            const rect = card.getBoundingClientRect();
            tooltip.style.left = rect.left + rect.width / 2 + 'px';
            tooltip.style.top = rect.top - 10 + 'px';
            tooltip.style.transform = 'translate(-50%, -100%)';
        });

        card.addEventListener('mouseleave', () => {
            tooltip.classList.remove('visible');
        });
    });

    /**
     * @param {boolean} selected
     */
    const setAllSelected = (selected) => {
        cards.forEach((card) => {
            card.classList.toggle('selected', selected);
            const checkbox = getCheckbox(card);
            if (checkbox) checkbox.checked = selected;
        });
    };

    document.getElementById('select-all')?.addEventListener('click', (event) => {
        event.preventDefault();
        setAllSelected(true);
    });

    document.getElementById('deselect-all')?.addEventListener('click', (event) => {
        event.preventDefault();
        setAllSelected(false);
    });

    document.getElementById('remove-selected-faces')?.addEventListener('click', async () => {
        const selectedCheckboxes = document.querySelectorAll(
            '.face-card input[type="checkbox"]:checked',
        );
        if (selectedCheckboxes.length === 0) {
            window.notification.warning(i18n.t('people:faces.selectRequired'));
            return;
        }

        const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
            title: i18n.t('people:faces.removeTitle'),
            message: i18n.t('people:faces.removeMessage', { count: selectedCheckboxes.length }),
            confirmText: i18n.t('people:faces.removeConfirm'),
            confirmClass: 'btn-danger',
        });
        if (!confirmed) return;

        const container = document.getElementById('selected-faces-container');
        if (!container) return;
        container.replaceChildren();
        selectedCheckboxes.forEach((checkbox) => {
            if (!(checkbox instanceof HTMLInputElement)) return;
            const hidden = document.createElement('input');
            hidden.type = 'hidden';
            hidden.name = 'faces';
            hidden.value = checkbox.value;
            container.appendChild(hidden);
        });
        const form = document.getElementById('remove-form');
        if (form instanceof HTMLFormElement) form.submit();
    });

    return { setAllSelected };
};
