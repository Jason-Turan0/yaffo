// @ts-check

/**
 * Selection bar over a server-rendered photo grid — the album's edit mode and the
 * album add screen.
 *
 * THE URL IS THE STATE. The selection lives in the querystring, like the filters
 * and the page number (routes/albums.py:SelectionView):
 *
 *   - explicit:  select_id=1&select_id=2   — the ids the user ticked
 *   - scope:     select=all[&exclude_id=5] — the WHOLE scope (every album member /
 *     every photo matching the filters, including rows on pages never rendered)
 *     minus the ids unticked out of it. Unticking narrows the scope; it does not
 *     collapse it to the visible page.
 *
 * The server renders which cards are ticked, the count, and the toggle's label from
 * those parameters, and the POST that acts on the selection reads them too. This
 * module's whole job is to keep the URL in step as the user clicks — so ticking a
 * card doesn't reload the page — and to keep the page's links (pagination) and the
 * action forms pointed at the current URL.
 *
 * Because the URL is the state, paginating, reloading and the Back button all just
 * work, and there is no client-side store to go stale.
 */

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {};

window.PHOTO_ORGANIZER.COMPONENTS.selectionBar = {
    /**
     * @param {SelectionBarOptions} options
     * @returns {SelectionBarApi | null}
     */
    init: (options) => {
        const grid = document.querySelector(options.grid);
        const bar = document.querySelector(options.bar);
        if (!(grid instanceof HTMLElement) || !(bar instanceof HTMLElement)) {
            return null;
        }

        const countEl = bar.querySelector('[data-selection-count]');
        const toggleButton = bar.querySelector('[data-selection-toggle]');
        const actions = bar.querySelectorAll('[data-selection-action]');

        const params = new URLSearchParams(window.location.search);
        let all = params.get('select') === 'all';
        /** @type {Set<string>} */
        let selected = new Set(params.getAll('select_id'));
        /** @type {Set<string>} */
        let excluded = new Set(params.getAll('exclude_id'));

        /** @returns {HTMLElement[]} */
        const cards = () => Array.from(grid.querySelectorAll('[data-select-id]'));

        /** @param {HTMLElement} card */
        const idOf = (card) => card.dataset.selectId || '';

        /** @param {string} id */
        const isSelected = (id) => (all ? !excluded.has(id) : selected.has(id));

        // In scope mode the count is the whole scope less the exclusions — what the
        // server will act on, not what is on this page.
        const count = () => (
            all ? Math.max(0, (options.totalCount ?? 0) - excluded.size) : selected.size
        );

        /** @returns {SelectionState} */
        const state = () => ({
            all,
            ids: all ? [] : Array.from(selected),
            excluded: all ? Array.from(excluded) : [],
        });

        /** The current URL with the selection parameters replaced. @returns {URL} */
        const urlWithSelection = () => {
            const url = new URL(window.location.href);
            url.searchParams.delete('select');
            url.searchParams.delete('select_id');
            url.searchParams.delete('exclude_id');
            url.searchParams.delete('added');  // a stale "3 photos added" note
            if (all) {
                url.searchParams.set('select', 'all');
                excluded.forEach((id) => url.searchParams.append('exclude_id', id));
            } else {
                selected.forEach((id) => url.searchParams.append('select_id', id));
            }
            return url;
        };

        const SELECTION_KEYS = ['select', 'select_id', 'exclude_id'];

        /**
         * Rewrite one URL so it carries the current selection instead of whatever
         * selection it was rendered with.
         * @param {string} href
         * @param {URLSearchParams} selectionQuery
         * @returns {string}
         */
        const withSelection = (href, selectionQuery) => {
            const target = new URL(href, window.location.origin);
            SELECTION_KEYS.forEach((key) => target.searchParams.delete(key));
            SELECTION_KEYS.forEach((key) => {
                selectionQuery.getAll(key).forEach((value) => target.searchParams.append(key, value));
            });
            return target.pathname + target.search;
        };

        /**
         * Point the page's own links and forms at the current selection: they were
         * rendered with the selection as it stood at page load, so they go stale the
         * moment a card is ticked. Pagination is the one that matters — following a
         * stale link would silently drop the selection.
         * @param {URL} url
         */
        const syncLinks = (url) => {
            const selectionQuery = url.searchParams;

            document.querySelectorAll('.pagination-container a').forEach((link) => {
                if (link instanceof HTMLAnchorElement) {
                    link.href = withSelection(link.href, selectionQuery);
                }
            });
            // The page-size control navigates to the option's value.
            document.querySelectorAll('.page-size-selector option').forEach((option) => {
                if (option instanceof HTMLOptionElement && option.value) {
                    option.value = withSelection(option.value, selectionQuery);
                }
            });
            document.querySelectorAll('[data-selection-form]').forEach((form) => {
                if (!(form instanceof HTMLFormElement)) return;
                // The action keeps its own path; the selection (and the filters
                // already on the URL) ride the querystring the server reads.
                const action = new URL(form.action, window.location.origin);
                form.action = action.pathname + url.search;
            });
        };

        // An htmx action (the remote gallery's Pull) needs its URL fixed at REQUEST
        // time, not by rewriting hx-post: htmx reads hx-post once, when it processes
        // the node, and captures the path in the trigger's closure — so a later
        // attribute change is ignored and the request would carry the selection as it
        // stood at PAGE LOAD. configRequest is the supported hook that runs per
        // request, so the posted URL is always the selection on screen.
        document.addEventListener('htmx:configRequest', (event) => {
            if (!(event instanceof CustomEvent)) return;
            const element = event.detail?.elt;
            if (!(element instanceof Element) || !element.closest('[data-selection-post]')) return;
            const path = new URL(event.detail.path, window.location.origin);
            event.detail.path = path.pathname + urlWithSelection().search;
        });

        const render = () => {
            cards().forEach((card) => {
                card.classList.toggle('is-selected', isSelected(idOf(card)));
            });
            if (countEl instanceof HTMLElement) {
                const label = countEl.dataset.countLabel || '{n}';
                countEl.textContent = label.replace('{n}', String(count()));
            }
            // The toggle always names its next action.
            if (toggleButton instanceof HTMLElement) {
                toggleButton.textContent = all
                    ? (toggleButton.dataset.selectNoneLabel || '')
                    : (toggleButton.dataset.selectAllLabel || '');
            }
            actions.forEach((action) => {
                if (action instanceof HTMLButtonElement) {
                    action.disabled = count() === 0;
                }
            });

            const url = urlWithSelection();
            // replaceState, not pushState: ticking cards should not fill the Back
            // button with one entry per click.
            window.history.replaceState({}, '', url.pathname + url.search);
            syncLinks(url);
            options.onChange?.(state());
        };

        /** @param {string} id */
        const toggle = (id) => {
            if (all) {
                // Narrow the scope rather than collapsing it: "everything except
                // this one" stays expressible, and stays true on the next page.
                if (excluded.has(id)) excluded.delete(id); else excluded.add(id);
            } else if (selected.has(id)) {
                selected.delete(id);
            } else {
                selected.add(id);
            }
            render();
        };

        // Capture phase: the cards carry their own click handler (open the photo),
        // so selection has to win before that runs.
        grid.addEventListener('click', (event) => {
            const target = event.target;
            if (!(target instanceof Element)) return;
            const card = target.closest('[data-select-id]');
            if (!(card instanceof HTMLElement)) return;
            event.preventDefault();
            event.stopPropagation();
            toggle(idOf(card));
        }, true);

        // Select-all / clear as one control: pressing it takes the whole scope,
        // pressing it again clears back to nothing.
        toggleButton?.addEventListener('click', () => {
            all = !all;
            selected = new Set();
            excluded = new Set();
            render();
        });

        render();

        return {
            getState: state,
            /** The URL the action forms should post to — selection included. */
            actionQuery: () => urlWithSelection().search,
        };
    },
};
