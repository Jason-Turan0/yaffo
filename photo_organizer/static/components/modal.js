window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {}

window.PHOTO_ORGANIZER.COMPONENTS.modal =
      {
        init : (modalId) => {
            const modalElement = document.getElementById(modalId);
            if(modalElement == null){
                throw new Error(`Failed to find dom element ${modalId}`);
            }
            const formElement = modalElement.querySelector('form');

            const close = () => {
                modalElement.classList.remove('active');
            }
            const open = () => {
                modalElement.classList.add('active');
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
                open
            };
        }
    };