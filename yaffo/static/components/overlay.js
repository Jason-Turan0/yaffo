// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {};

window.PHOTO_ORGANIZER.COMPONENTS.overlay = {
    /**
     * @param {string} targetElementId
     * @param {string} overlayContent
     * @param {OverlayOptions} [options]
     * @returns {OverlayControl}
     */
    init: (targetElementId, overlayContent, options = {}) => {
        const {
            placement = 'bottom',
            offset = 8,
            closeOnEsc = true,
            closeOnOutsideClick = true
        } = options;

        const overlay = document.createElement('div');
        overlay.className = 'overlay';
        overlay.innerHTML = `<div class="overlay-content">${overlayContent}</div>`;

        const targetElement = document.getElementById(targetElementId);
        if (!targetElement) {
            throw new Error(`Failed to find DOM element with id '${targetElementId}'`);
        }

        // Append to body instead of the target
        document.body.appendChild(overlay);

        const positionOverlay = () => {
            const targetRect = targetElement.getBoundingClientRect();
            const overlayRect = overlay.getBoundingClientRect();
            const viewportWidth = window.innerWidth;
            const viewportHeight = window.innerHeight;
            const scrollX = window.scrollX;
            const scrollY = window.scrollY;

            let finalPlacement = placement;
            let top, left;

            /**
             * @param {OverlayPlacement} pos
             */
            const calculatePosition = (pos) => {
                let t, l;
                switch (pos) {
                    case 'top':
                        t = targetRect.top + scrollY - overlayRect.height - offset;
                        l = targetRect.left + scrollX + (targetRect.width / 2) - (overlayRect.width / 2);
                        break;
                    case 'bottom':
                        t = targetRect.bottom + scrollY + offset;
                        l = targetRect.left + scrollX + (targetRect.width / 2) - (overlayRect.width / 2);
                        break;
                    case 'left':
                        t = targetRect.top + scrollY + (targetRect.height / 2) - (overlayRect.height / 2);
                        l = targetRect.left + scrollX - overlayRect.width - offset;
                        break;
                    case 'right':
                        t = targetRect.top + scrollY + (targetRect.height / 2) - (overlayRect.height / 2);
                        l = targetRect.right + scrollX + offset;
                        break;
                    default:
                        t = targetRect.bottom + scrollY + offset;
                        l = targetRect.left + scrollX + (targetRect.width / 2) - (overlayRect.width / 2);
                }
                return { top: t, left: l };
            };

            /**
             * @param {OverlayPlacement} pos
             */
            const willOverflow = (pos) => {
                const { top: t, left: l } = calculatePosition(pos);
                const bottom = t + overlayRect.height;
                const right = l + overlayRect.width;
                return {
                    top: t < scrollY,
                    bottom: bottom > scrollY + viewportHeight,
                    left: l < scrollX,
                    right: right > scrollX + viewportWidth
                };
            };

            const overflow = willOverflow(placement);
            if (placement === 'bottom' && overflow.bottom && !overflow.top) finalPlacement = 'top';
            else if (placement === 'top' && overflow.top && !overflow.bottom) finalPlacement = 'bottom';
            else if (placement === 'left' && overflow.left && !overflow.right) finalPlacement = 'right';
            else if (placement === 'right' && overflow.right && !overflow.left) finalPlacement = 'left';

            const pos = calculatePosition(finalPlacement);
            top = pos.top;
            left = pos.left;

            // Constrain inside viewport
            if (left < scrollX) left = scrollX + offset;
            else if (left + overlayRect.width > scrollX + viewportWidth)
                left = scrollX + viewportWidth - overlayRect.width - offset;

            if (top < scrollY) top = scrollY + offset;
            else if (top + overlayRect.height > scrollY + viewportHeight)
                top = scrollY + viewportHeight - overlayRect.height - offset;

            overlay.style.top = `${top}px`;
            overlay.style.left = `${left}px`;
            overlay.dataset.placement = finalPlacement;
        };

        const handleOutsideClick = (/** @type {MouseEvent} */ e) => {
            if (e.target instanceof Node && !overlay.contains(e.target) && !targetElement.contains(e.target)) {
                close();
            }
        };

        const handleKeyDown = (/** @type {KeyboardEvent} */ e) => {
            if (e.key === 'Escape' && closeOnEsc) close();
        };

        const handleReposition = () => {
            requestAnimationFrame(positionOverlay);
        };

        const close = () => {
            overlay.classList.remove('active');
            overlay.classList.add('closing');

            /* transitionend alone is not a guarantee the node ever goes away:
               it does not fire when the close transition is cancelled, when
               the element is already hidden, or when reduced motion has
               flattened the duration. Every overlay that failed to fire it
               stayed in the document forever, holding its content's element
               ids — so a second open found duplicate ids, and the caller's
               "is one already open?" check read a corpse as a live overlay.
               The timer is the floor; transitionend just gets there sooner. */
            let removed = false;
            const remove = () => {
                if (removed) return;
                removed = true;
                overlay.remove();
            };
            const longestTransitionMs = getComputedStyle(overlay)
                .transitionDuration
                .split(',')
                .reduce((longest, value) => {
                    const seconds = parseFloat(value) || 0;
                    return Math.max(longest, value.includes('ms') ? seconds : seconds * 1000);
                }, 0);

            if (longestTransitionMs > 0) {
                overlay.addEventListener('transitionend', remove, { once: true });
                setTimeout(remove, longestTransitionMs + 50);
            } else {
                remove();
            }

            if (closeOnOutsideClick) document.removeEventListener('click', handleOutsideClick);
            window.removeEventListener('resize', handleReposition);
            window.removeEventListener('scroll', handleReposition);
            if (closeOnEsc) document.removeEventListener('keydown', handleKeyDown);
        };

        // Activate and position
        requestAnimationFrame(() => {
            overlay.classList.add('active');
            positionOverlay();
        });

        // Listeners
        setTimeout(() => {
            if (closeOnOutsideClick) document.addEventListener('click', handleOutsideClick);
            window.addEventListener('resize', handleReposition);
            window.addEventListener('scroll', handleReposition);
            if (closeOnEsc) document.addEventListener('keydown', handleKeyDown);
        }, 0);

        return { overlay, close, reposition: positionOverlay };
    }
};
