// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
/** @type {UtilitiesNamespace} */
const utilitiesNamespace = window.PHOTO_ORGANIZER.utilities || {};
window.PHOTO_ORGANIZER.utilities = utilitiesNamespace;

/**
 * The New automation dialog's type choice: Custom, or a sync (Import from… /
 * Send to…), which also needs a service. Only the chosen direction's service list
 * is enabled, so exactly one adapter_id is posted.
 * @param {HTMLElement} modalElement
 */
const initNewAutomationKinds = (modalElement) => {
    const update = () => {
        const checked = /** @type {HTMLInputElement | null} */ (
            modalElement.querySelector('input[name="kind"]:checked'));
        const kind = checked ? checked.value : 'custom';
        modalElement.querySelectorAll('[data-sync-services]').forEach((element) => {
            const group = /** @type {HTMLElement} */ (element);
            const active = group.dataset.syncServices === kind;
            group.hidden = !active;
            group.querySelectorAll('input').forEach((input) => {
                /** @type {HTMLInputElement} */ (input).disabled = !active;
            });
        });
        modalElement.querySelectorAll('[data-kind-hint]').forEach((element) => {
            const hint = /** @type {HTMLElement} */ (element);
            hint.hidden = hint.dataset.kindHint !== kind;
        });
    };
    modalElement.querySelectorAll('input[name="kind"]').forEach((input) => {
        input.addEventListener('change', update);
    });
    update();
    return { update };
};
utilitiesNamespace.initNewAutomationKinds = initNewAutomationKinds;

document.addEventListener('yaffo:app-init-complete', (event) => {
    const app = event.detail.app;

    const button = document.getElementById('new-automation-button');
    if (!button) return;
    const modal = app.COMPONENTS.modal.init('newAutomationModal');
    const modalElement = document.getElementById('newAutomationModal');
    if (modalElement) initNewAutomationKinds(modalElement);
    button.addEventListener('click', modal.open);
});
