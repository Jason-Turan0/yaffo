window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

window.PHOTO_ORGANIZER.initPersonFaces = (i18n, config) => {
    const tooltip = document.createElement('div');
    tooltip.className = 'tooltip';
    document.body.appendChild(tooltip);

    const updateSimilarityDisplay = (rangeId, valueId) => {
        const range = document.getElementById(rangeId);
        const value = document.getElementById(valueId);
        range?.addEventListener('input', (event) => {
            value.textContent = i18n.percent(Number(event.target.value) / 100);
        });
    };

    updateSimilarityDisplay('min_similarity-range', 'min_similarity-value');
    updateSimilarityDisplay('max_similarity-range', 'max_similarity-value');

    const cards = Array.from(document.querySelectorAll('.face-card'));
    cards.forEach((card) => {
        card.addEventListener('click', () => {
            card.classList.toggle('selected');
            const checkbox = card.querySelector('input[type="checkbox"]');
            checkbox.checked = !checkbox.checked;
        });

        card.addEventListener('mouseenter', () => {
            const similarity = card.dataset.similarity === ''
                ? Number.NaN
                : Number(card.dataset.similarity);
            const date = window.PHOTO_ORGANIZER.utils.date.format(card.dataset.date);
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

    const setAllSelected = (selected) => {
        cards.forEach((card) => {
            card.classList.toggle('selected', selected);
            card.querySelector('input[type="checkbox"]').checked = selected;
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
            notification.warning(i18n.t('people:faces.selectRequired'));
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
        container.replaceChildren();
        selectedCheckboxes.forEach((checkbox) => {
            const hidden = document.createElement('input');
            hidden.type = 'hidden';
            hidden.name = 'faces';
            hidden.value = checkbox.value;
            container.appendChild(hidden);
        });
        document.getElementById('remove-form').submit();
    });

    return { setAllSelected };
};
