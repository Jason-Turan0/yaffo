// @ts-check

/**
 * Anchored popover for `[data-tooltip]`, above the mobile breakpoint.
 *
 * The pure-CSS bubble in components/tooltip.css is a fixed-width box centred on
 * its anchor, which a CSS-only rule cannot keep inside the viewport: an anchor
 * near the inline end pushes the bubble past the document edge and the page
 * scrolls sideways with nothing hovered. Pinning it to the viewport at every
 * width solves that but strands the text far from the control it explains.
 *
 * So the two presentations are split by width, which is also how people expect
 * to read them:
 *   - phones (<= 640px): the CSS bar pinned to the bottom of the viewport, which
 *     responsive.css already declares. This module stays out of the way.
 *   - tablet and desktop: this popover, anchored to its control and clamped —
 *     and flipped below the anchor when there is no room above it.
 *
 * Reveal covers both pointer kinds: hover and focus for a mouse or a keyboard,
 * and a press for a coarse pointer, where `:hover` never happens and
 * `:focus-visible` does not match a tap.
 */

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {};

(() => {
    /** Below this width the CSS bottom-anchored bar owns the presentation. */
    const POPOVER_QUERY = '(min-width: 641px)';
    const GUTTER = 12;
    const GAP = 10;

    /** @type {HTMLElement | null} */
    let popover = null;
    /** @type {HTMLElement | null} */
    let anchor = null;

    // Built once. `pointerover` fires on every element boundary the cursor
    // crosses, and minting a MediaQueryList per event is real work on a page
    // that is mostly cursor movement.
    const wideQuery = window.matchMedia(POPOVER_QUERY);
    /** A device with no hover at all — the press is its only way in. */
    const hoverlessQuery = window.matchMedia('(hover: none)');
    const wide = () => wideQuery.matches;
    const coarse = () => hoverlessQuery.matches;

    const element = () => {
        if (popover) return popover;
        popover = document.createElement('div');
        popover.className = 'tooltip data-tooltip-popover';
        popover.setAttribute('role', 'tooltip');
        document.body.appendChild(popover);
        return popover;
    };

    /**
     * Place the popover above its anchor, or below it when it does not fit, and
     * keep it inside the viewport either way. Positions are documented in page
     * coordinates because the element is absolutely positioned in the document.
     * @param {HTMLElement} target
     */
    const place = (target) => {
        const tip = element();
        const box = target.getBoundingClientRect();
        const width = tip.offsetWidth;
        const height = tip.offsetHeight;

        // The centre is clamped so neither edge leaves the viewport. When the
        // popover is wider than the space available, both bounds collapse onto
        // the same value and it simply sits in the middle.
        const maximumCentre = Math.max(width / 2 + GUTTER, window.innerWidth - width / 2 - GUTTER);
        const centreX = Math.min(
            Math.max(box.left + box.width / 2, width / 2 + GUTTER),
            maximumCentre
        );

        const spaceAbove = box.top - GUTTER;
        const spaceBelow = window.innerHeight - box.bottom - GUTTER;
        const below = spaceAbove < height + GAP && spaceBelow > spaceAbove;
        const unclampedTop = below ? box.bottom + GAP : box.top - height - GAP;
        const viewportTop = Math.min(
            Math.max(unclampedTop, GUTTER),
            Math.max(GUTTER, window.innerHeight - height - GUTTER)
        );

        tip.classList.toggle('tooltip-below', below);
        tip.style.left = `${centreX + window.scrollX}px`;
        tip.style.top = `${viewportTop + window.scrollY}px`;
        tip.style.transform = 'translateX(-50%)';
    };

    /** @param {HTMLElement} target */
    const show = (target) => {
        const text = target.getAttribute('data-tooltip');
        if (!text) return;
        const tip = element();
        anchor = target;
        // textContent, not innerHTML: the string is a label, and on the labels
        // screen it is user-entered.
        tip.textContent = text;
        tip.classList.add('visible');
        place(target);
    };

    const hide = () => {
        if (!popover) return;
        anchor = null;
        popover.classList.remove('visible', 'tooltip-below');
        popover.style.removeProperty('left');
        popover.style.removeProperty('top');
        popover.style.removeProperty('transform');
    };

    /**
     * @param {EventTarget | null} target
     * @returns {HTMLElement | null}
     */
    const tooltipFor = (target) =>
        target instanceof Element
            ? /** @type {HTMLElement | null} */ (target.closest('[data-tooltip]'))
            : null;

    // Delegated, so a section swapped in by htmx (the labels list does exactly
    // that on every add and remove) needs no re-initialisation.
    document.addEventListener('pointerover', (event) => {
        // Cheapest discriminator first: most of these events are a mouse moving
        // across a page with no tooltip anywhere near it.
        if (event.pointerType !== 'mouse' || !wide()) return;
        const target = tooltipFor(event.target);
        if (target) show(target); else if (anchor) hide();
    });

    // Keyboard focus only. A press focuses the control too, and treating that as
    // a reveal would race the click handler below: the popover would open on
    // focus and the same tap would immediately toggle it shut again.
    // `:focus-visible` is exactly the line between the two.
    document.addEventListener('focusin', (event) => {
        if (!wide()) return;
        const target = tooltipFor(event.target);
        if (target && target.matches(':focus-visible')) show(target);
        else if (anchor) hide();
    });

    document.addEventListener('focusout', () => { if (anchor) hide(); });

    // The press is the coarse pointer's only way in, and it doubles as the way
    // out: pressing the same control again closes it. A mouse never needs this —
    // hover already opens and closes it, and toggling on click would shut the
    // popover while the reader is still pointing at the control.
    document.addEventListener('click', (event) => {
        if (!wide() || !coarse()) return;
        const target = tooltipFor(event.target);
        if (!target) { if (anchor) hide(); return; }
        if (target === anchor) hide(); else show(target);
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && anchor) hide();
    });

    // Anchored to a page position, so anything that moves the page invalidates
    // it. Re-placing on scroll would fight momentum scrolling; dismissing is
    // both cheaper and what a reader expects.
    window.addEventListener('scroll', () => { if (anchor) hide(); }, { passive: true });
    window.addEventListener('resize', () => {
        if (!anchor) return;
        if (wide()) place(anchor); else hide();
    });

    // Tells the stylesheet this presentation is live, so the pure-CSS bubble can
    // stand down above the breakpoint without leaving a no-JS page mute.
    document.documentElement.classList.add('js-tooltip');
})();
