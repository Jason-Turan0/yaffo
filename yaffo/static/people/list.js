window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initPeopleList = (config) => {
    const deleteModal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('deleteModal');
    const addModal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('addModal');
    const editModal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('editModal');

    function openAddModal() {
        const personNameInput = addModal.element.querySelector('[name="name"]');
        const birthdateInput = addModal.element.querySelector('[name="birthdate"]');
        personNameInput.value = '';
        if (birthdateInput) birthdateInput.value = '';
        personNameInput.focus();
        addModal.open();
    }

    function openEditModal(personId, personName, birthdate) {
        const personNameInput = editModal.element.querySelector('[name="name"]');
        const birthdateInput = editModal.element.querySelector('[name="birthdate"]');
        editModal.setFormAction(config.buildUrl('people_update', {person_id: personId}));
        personNameInput.value = personName;
        if (birthdateInput) birthdateInput.value = birthdate || '';
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