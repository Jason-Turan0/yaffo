window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {}

window.PHOTO_ORGANIZER.COMPONENTS.modal =
      {
        init : (modalId) => {
            const modalElement = document.getElementById(modalId);
            if(modalElement == null){
                throw new Error(`Failed to find dom element ${modalId}`);
            }
            const cancelElements = modalElement.querySelectorAll('[name="cancel"]');

            const formElement = modalElement.querySelector('form');

            const close = () => {
                modalElement.classList.remove('active');
            }
            cancelElements.forEach((cancelElement) => {
                cancelElement.addEventListener('click', (e) => {
                    close();
                })
            });

            const open = () => {
                modalElement.classList.add('active');
            }
            const setFormAction = (url) => {
                if(formElement == null){
                    throw new Error(`Modal ${modalId} has no form to set an action on`);
                }
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