window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initPeopleList = (config) => {
    const deleteModal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('deleteModal');
    const addModal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('addModal');
    const editModal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('editModal');

    function openAddModal() {
        const personNameInput = addModal.element.querySelector('[name="name"]');
        const birthdateInput = addModal.element.querySelector('[name="birthdate"]');
        const genderSelect = addModal.element.querySelector('[name="gender"]');
        personNameInput.value = '';
        if (birthdateInput) birthdateInput.value = '';
        if (genderSelect) genderSelect.value = '';
        personNameInput.focus();
        addModal.open();
    }

    function openEditModal(personId, personName, birthdate, gender) {
        const personNameInput = editModal.element.querySelector('[name="name"]');
        const birthdateInput = editModal.element.querySelector('[name="birthdate"]');
        const genderSelect = editModal.element.querySelector('[name="gender"]');
        editModal.setFormAction(config.buildUrl('people_update', {person_id: personId}));
        personNameInput.value = personName;
        if (birthdateInput) birthdateInput.value = birthdate || '';
        if (genderSelect) genderSelect.value = gender === null ? '' : String(gender);
        personNameInput.focus();
        editModal.open();
    }

    function confirmDelete(personId, personName) {
        document.getElementById('deletePersonName').textContent = personName;
        deleteModal.setFormAction(config.buildUrl("people_delete", {person_id: personId}))
        deleteModal.open();
    }

    return {
        openAddModal,
        openEditModal,
        confirmDelete
    }
};
