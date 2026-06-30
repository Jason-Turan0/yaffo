// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {}

window.PHOTO_ORGANIZER.COMPONENTS.modal =
      {
        /**
         * @param {string} modalId
         * @returns {ModalControl}
         */
        init : (modalId) => {
            const modalElement = document.getElementById(modalId);
            if(modalElement == null){
                throw new Error(`Failed to find dom element ${modalId}`);
            }
            const cancelElements = modalElement.querySelectorAll('[name="cancel"]');

            const formElement = modalElement.querySelector('form');

            // A JS-handled form (render_modal posts=false) never submits to the
            // server; stop the default so a missed preventDefault in the page's own
            // submit handler can't navigate the page away.
            if (formElement && formElement.hasAttribute('data-js-form')) {
                formElement.addEventListener('submit', (e) => e.preventDefault());
            }

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
            const setFormAction = (/** @type {string} */ url) => {
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
