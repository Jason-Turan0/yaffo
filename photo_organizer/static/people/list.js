window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initPeopleList = (config) => {
    const deleteModal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('deleteModal');
    const addModal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('addModal');
    const editModal = window.PHOTO_ORGANIZER.COMPONENTS.modal.init('editModal');

    function openAddModal() {
        const personNameInput = addModal.element.querySelector('[name="name"]');
        personNameInput.value = '';
        personNameInput.focus();
        addModal.open();
    }

    function openEditModal(personId, personName) {
        const personNameInput = editModal.element.querySelector('[name="name"]');
        deleteModal.setFormAction(config.buildUrl('people_update', {person_id: personId}));
        personNameInput.value = personName;
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