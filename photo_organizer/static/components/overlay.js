window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {}
window.PHOTO_ORGANIZER.COMPONENTS.overlay = {
    init: (targetElementId, overlayContent, placement = 'bottom') => {
        const overlay = document.createElement('div');
        overlay.className = 'overlay';
        overlay.innerHTML = overlayContent;

        const targetElement = document.getElementById(targetElementId);
        if (targetElement == null) {
            throw new Error(`Failed to find dom element ${targetElementId}`);
        }

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

            const calculatePosition = (pos) => {
                const spacing = 8;
                let calculatedTop, calculatedLeft;

                switch (pos) {
                    case 'top':
                        calculatedTop = targetRect.top + scrollY - overlayRect.height - spacing;
                        calculatedLeft = targetRect.left + scrollX + (targetRect.width / 2) - (overlayRect.width / 2);
                        break;
                    case 'bottom':
                        calculatedTop = targetRect.bottom + scrollY + spacing;
                        calculatedLeft = targetRect.left + scrollX + (targetRect.width / 2) - (overlayRect.width / 2);
                        break;
                    case 'left':
                        calculatedTop = targetRect.top + scrollY + (targetRect.height / 2) - (overlayRect.height / 2);
                        calculatedLeft = targetRect.left + scrollX - overlayRect.width - spacing;
                        break;
                    case 'right':
                        calculatedTop = targetRect.top + scrollY + (targetRect.height / 2) - (overlayRect.height / 2);
                        calculatedLeft = targetRect.right + scrollX + spacing;
                        break;
                    default:
                        calculatedTop = targetRect.bottom + scrollY + spacing;
                        calculatedLeft = targetRect.left + scrollX + (targetRect.width / 2) - (overlayRect.width / 2);
                }

                return { top: calculatedTop, left: calculatedLeft };
            };

            const willOverflow = (pos) => {
                const { top: calcTop, left: calcLeft } = calculatePosition(pos);
                const bottom = calcTop + overlayRect.height;
                const right = calcLeft + overlayRect.width;

                return {
                    top: calcTop < scrollY,
                    bottom: bottom > scrollY + viewportHeight,
                    left: calcLeft < scrollX,
                    right: right > scrollX + viewportWidth
                };
            };

            const overflow = willOverflow(placement);

            if (placement === 'bottom' && overflow.bottom && !overflow.top) {
                finalPlacement = 'top';
            } else if (placement === 'top' && overflow.top && !overflow.bottom) {
                finalPlacement = 'bottom';
            } else if (placement === 'left' && overflow.left && !overflow.right) {
                finalPlacement = 'right';
            } else if (placement === 'right' && overflow.right && !overflow.left) {
                finalPlacement = 'left';
            }

            const position = calculatePosition(finalPlacement);
            top = position.top;
            left = position.left;

            if (left < scrollX) {
                left = scrollX + 8;
            } else if (left + overlayRect.width > scrollX + viewportWidth) {
                left = scrollX + viewportWidth - overlayRect.width - 8;
            }

            if (top < scrollY) {
                top = scrollY + 8;
            } else if (top + overlayRect.height > scrollY + viewportHeight) {
                top = scrollY + viewportHeight - overlayRect.height - 8;
            }

            overlay.style.top = `${top}px`;
            overlay.style.left = `${left}px`;
            overlay.dataset.placement = finalPlacement;
        };

        overlay.classList.add('active');
        requestAnimationFrame(() => {
            positionOverlay();
        });

        const handleOutsideClick = (event) => {
            if (!overlay.contains(event.target) && !targetElement.contains(event.target)) {
                close();
            }
        };

        setTimeout(() => {
            document.addEventListener('click', handleOutsideClick);
        }, 0);

        const handleResize = () => {
            positionOverlay();
        };
        window.addEventListener('resize', handleResize);
        window.addEventListener('scroll', handleResize);

        const close = () => {
            overlay.remove();
            document.removeEventListener('click', handleOutsideClick);
            window.removeEventListener('resize', handleResize);
            window.removeEventListener('scroll', handleResize);
        };

        return {
            overlay,
            close,
            reposition: positionOverlay
        };
    }
};