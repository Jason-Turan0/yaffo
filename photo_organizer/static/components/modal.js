window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {}

window.PHOTO_ORGANIZER.COMPONENTS.modal =
      {
        init : (modalId) => {
            const modalElement = document.getElementById(modalId);
            if(modalElement == null){
                throw new Error(`Failed to find dom element ${modalId}`);
            }
            const cancelElement = modalElement.querySelector('.btn-secondary');
            if(cancelElement == null){
                throw new Error(`Failed to find cancel button in modal element ${modalId}`);
            }
            const formElement = modalElement.querySelector('form');
            if(formElement == null){
                throw new Error(`Failed to find form element in modal element ${modalId}`);
            }

            const close = () => {
                modalElement.classList.remove('active');
            }
            cancelElement.addEventListener('click', () => {
                close();
            })
            const open = () => {
                modalElement.classList.add('active');
            }
            const setFormAction = (url) => {
                formElement.action = url;
            }

             // Close modals with Escape key
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') {
                    close();
                }
            });

            modalElement.addEventListener('click', (e) => {
                if (e.target === modalElement) close();
            });

            return {
                element: modalElement,
                formElement,
                close,
                open,
                setFormAction
            };
        }
    };