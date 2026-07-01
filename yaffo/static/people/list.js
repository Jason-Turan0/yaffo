// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.people = window.PHOTO_ORGANIZER.people || {};

/**
 * @param {I18nService} i18n
 * @param {AppConfig} config
 * @returns {PeopleListApi}
 */
window.PHOTO_ORGANIZER.people.initList = (i18n, config) => {
    const addModal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('addModal');
    const editModal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('editModal');
    window.PHOTO_ORGANIZER.COMPONENTS.intlDateInput?.initAll(i18n, addModal.element);
    window.PHOTO_ORGANIZER.COMPONENTS.intlDateInput?.initAll(i18n, editModal.element);

    /**
     * @param {ModalControl} modal
     * @param {string} name
     * @returns {HTMLInputElement}
     */
    const getInput = (modal, name) => {
        const input = modal.element.querySelector(`[name="${name}"]`);
        if (!(input instanceof HTMLInputElement)) {
            throw new Error(`Expected input ${name} in modal`);
        }
        return input;
    };

    /**
     * @param {ModalControl} modal
     * @param {string} name
     * @returns {HTMLSelectElement | null}
     */
    const getSelect = (modal, name) => {
        const select = modal.element.querySelector(`[name="${name}"]`);
        return select instanceof HTMLSelectElement ? select : null;
    };

    /**
     * @param {ModalControl} modal
     * @param {string | null | undefined} value
     */
    const setBirthdate = (modal, value) => {
        const control = modal.element.querySelector('.intl-date-input-control');
        if (control instanceof HTMLElement && control.intlDateInput) {
            control.intlDateInput.setValue(value || '');
        }
    };

    const openAddModal = () => {
        const personNameInput = getInput(addModal, 'name');
        const genderSelect = getSelect(addModal, 'gender');
        personNameInput.value = '';
        setBirthdate(addModal, '');
        if (genderSelect) {
            genderSelect.value = '';
            genderSelect.dispatchEvent(new Event('change', { bubbles: true }));
        }
        personNameInput.focus();
        addModal.open();
    };

    /**
     * @param {number} personId
     * @param {string} personName
     * @param {string | undefined} birthdate
     * @param {number | null | undefined} gender
     */
    const openEditModal = (personId, personName, birthdate, gender) => {
        const personNameInput = getInput(editModal, 'name');
        const genderSelect = getSelect(editModal, 'gender');
        editModal.setFormAction(config.buildUrl('people_update', {person_id: personId}));
        personNameInput.value = personName;
        setBirthdate(editModal, birthdate);
        if (genderSelect) {
            genderSelect.value = gender === null ? '' : String(gender);
            genderSelect.dispatchEvent(new Event('change', { bubbles: true }));
        }
        personNameInput.focus();
        editModal.open();
    };

    /**
     * @param {number} personId
     * @param {string} personName
     */
    const confirmDelete = async (personId, personName) => {
        const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
            title: i18n.t('people:delete.title'),
            message: i18n.t('people:delete.message', { name: personName }),
            confirmText: i18n.t('common:delete'),
            confirmClass: 'btn-danger',
        });
        if (!confirmed) return;

        const form = document.createElement('form');
        form.method = 'POST';
        form.action = config.buildUrl('people_delete', { person_id: personId });
        document.body.appendChild(form);
        form.submit();
    };

    document.querySelectorAll('.js-add-person').forEach((button) => {
        button.addEventListener('click', openAddModal);
    });

    document.querySelector('.people-table')?.addEventListener('click', (event) => {
        if (!(event.target instanceof Element)) return;
        const action = event.target.closest('[data-action]');
        if (!(action instanceof HTMLElement)) return;
        if (!action) return;
        event.preventDefault();

        if (action.dataset.action === 'edit') {
            const gender = action.dataset.personGender === ''
                ? null
                : Number(action.dataset.personGender);
            openEditModal(
                Number(action.dataset.personId),
                action.dataset.personName || '',
                action.dataset.personBirthdate,
                gender,
            );
        } else if (action.dataset.action === 'delete') {
            confirmDelete(Number(action.dataset.personId), action.dataset.personName || '');
        }
    });

    return {
        openAddModal,
        openEditModal,
        confirmDelete
    }
};
