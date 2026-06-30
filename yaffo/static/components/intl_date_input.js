// @ts-check

/**
 * @typedef {'day' | 'month' | 'year'} DateField
 *
 * @typedef {Object} DatePatternPart
 * @property {Intl.DateTimeFormatPartTypes} type
 * @property {string} value
 * @property {number} length
 *
 * @typedef {Object} I18nService
 * @property {string} locale
 * @property {(key: string, options?: Record<string, unknown>) => string} t
 *
 * @typedef {Object} IntlDateInputControl
 * @property {(isoValue: string | null | undefined) => void} setValue
 * @property {() => boolean} sync
 *
 * @typedef {Object} IntlDateInputApi
 * @property {(isoValue: string | null | undefined, locale: string) => string} formatValue
 * @property {(rawValue: string, locale: string) => string} formatPartial
 * @property {(root: HTMLElement, i18n: I18nService) => IntlDateInputControl} init
 * @property {(i18n: I18nService, root?: ParentNode) => IntlDateInputControl[]} initAll
 * @property {(rawValue: string, locale: string) => string | null} parseDate
 * @property {(locale: string) => string} placeholder
 *
 * @typedef {HTMLElement & { intlDateInput?: IntlDateInputControl }} IntlDateInputRoot
 */

const intlDateInputWindow = /** @type {Window & {
    PHOTO_ORGANIZER: {
        COMPONENTS?: {
            intlDateInput?: IntlDateInputApi,
        },
    },
}} */ (/** @type {unknown} */ (window));

intlDateInputWindow.PHOTO_ORGANIZER = intlDateInputWindow.PHOTO_ORGANIZER || {};
intlDateInputWindow.PHOTO_ORGANIZER.COMPONENTS = intlDateInputWindow.PHOTO_ORGANIZER.COMPONENTS || {};

intlDateInputWindow.PHOTO_ORGANIZER.COMPONENTS.intlDateInput = (() => {
    /**
     * @param {string} locale
     * @returns {Intl.DateTimeFormatPart[]}
     */
    const sampleParts = (locale) => new Intl.DateTimeFormat(locale, {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        timeZone: 'UTC',
    }).formatToParts(new Date(Date.UTC(2006, 10, 22)));

    /**
     * @param {string | null | undefined} isoValue
     * @param {string} locale
     * @returns {string}
     */
    const formatValue = (isoValue, locale) => {
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoValue || '');
        if (!match) return '';
        const [, year, month, day] = match;
        return new Intl.DateTimeFormat(locale, {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            timeZone: 'UTC',
        }).format(new Date(Date.UTC(Number(year), Number(month) - 1, Number(day))));
    };

    /**
     * @param {string} locale
     * @returns {string}
     */
    const placeholder = (locale) => sampleParts(locale).map((part) => {
        if (part.type === 'day') return 'DD';
        if (part.type === 'month') return 'MM';
        if (part.type === 'year') return 'YYYY';
        return part.value;
    }).join('');

    /**
     * @param {string} locale
     * @returns {DatePatternPart[]}
     */
    const pattern = (locale) => sampleParts(locale).map((part) => ({
        type: part.type,
        value: part.value,
        length: part.type === 'year' ? 4 : 2,
    }));

    /**
     * @param {string} locale
     * @returns {DateField[]}
     */
    const order = (locale) => sampleParts(locale)
        .filter((part) => ['day', 'month', 'year'].includes(part.type))
        .map((part) => /** @type {DateField} */ (part.type));

    /**
     * @param {string} locale
     * @returns {(value: string) => string}
     */
    const digitNormalizer = (locale) => {
        const formatter = new Intl.NumberFormat(locale, { useGrouping: false });
        const replacements = new Map();
        for (let digit = 0; digit <= 9; digit += 1) {
            replacements.set(formatter.format(digit), String(digit));
        }
        return (value) => Array.from(value).map((char) => replacements.get(char) || char).join('');
    };

    /**
     * @param {string} value
     * @param {string} locale
     * @returns {string}
     */
    const normalizedDigits = (value, locale) => digitNormalizer(locale)(value).replace(/\D/g, '');

    /**
     * @param {string} value
     * @param {string} locale
     * @returns {number}
     */
    const countDigits = (value, locale) => normalizedDigits(value, locale).length;

    /**
     * @param {string} value
     * @param {number} digitCount
     * @returns {number}
     */
    const caretForDigitCount = (value, digitCount) => {
        if (digitCount <= 0) return 0;
        let seen = 0;
        for (let index = 0; index < value.length; index += 1) {
            if (/\d/.test(value[index])) {
                seen += 1;
                if (seen === digitCount) {
                    let caret = index + 1;
                    while (caret < value.length && !/\d/.test(value[caret])) caret += 1;
                    return caret;
                }
            }
        }
        return value.length;
    };

    /**
     * @param {string} rawValue
     * @param {string} locale
     * @returns {string}
     */
    const formatPartial = (rawValue, locale) => {
        const digits = normalizedDigits(rawValue, locale).slice(0, 8);
        if (!digits) return '';

        let offset = 0;
        let formatted = '';
        for (const part of pattern(locale)) {
            if (['day', 'month', 'year'].includes(part.type)) {
                const chunk = digits.slice(offset, offset + part.length);
                if (!chunk) break;
                formatted += chunk;
                offset += chunk.length;
                continue;
            }
            if (offset > 0 && offset < digits.length) formatted += part.value;
        }
        return formatted;
    };

    /**
     * @param {number} year
     * @param {number} month
     * @param {number} day
     * @returns {boolean}
     */
    const isValidDate = (year, month, day) => {
        if (year < 1 || month < 1 || month > 12 || day < 1 || day > 31) return false;
        const date = new Date(Date.UTC(year, month - 1, day));
        return date.getUTCFullYear() === year
            && date.getUTCMonth() === month - 1
            && date.getUTCDate() === day;
    };

    /**
     * @param {string} rawValue
     * @param {string} locale
     * @returns {string | null}
     */
    const parseDate = (rawValue, locale) => {
        const normalized = digitNormalizer(locale)(rawValue.trim());
        if (!normalized) return '';

        const isoMatch = /^(\d{4})\D+(\d{1,2})\D+(\d{1,2})$/.exec(normalized);
        if (isoMatch) {
            const [, rawYear, rawMonth, rawDay] = isoMatch;
            const year = Number(rawYear);
            const month = Number(rawMonth);
            const day = Number(rawDay);
            if (!isValidDate(year, month, day)) return null;
            return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
        }

        const digits = normalized.replace(/\D/g, '');
        if (digits.length === 8) {
            const compactIso = {
                year: Number(digits.slice(0, 4)),
                month: Number(digits.slice(4, 6)),
                day: Number(digits.slice(6, 8)),
            };
            if (isValidDate(compactIso.year, compactIso.month, compactIso.day)) {
                return `${String(compactIso.year).padStart(4, '0')}-${String(compactIso.month).padStart(2, '0')}-${String(compactIso.day).padStart(2, '0')}`;
            }

            let offset = 0;
            /** @type {Record<DateField, number>} */
            const compactValues = { day: 0, month: 0, year: 0 };
            for (const part of order(locale)) {
                const length = part === 'year' ? 4 : 2;
                compactValues[part] = Number(digits.slice(offset, offset + length));
                offset += length;
            }
            if (isValidDate(compactValues.year, compactValues.month, compactValues.day)) {
                return `${String(compactValues.year).padStart(4, '0')}-${String(compactValues.month).padStart(2, '0')}-${String(compactValues.day).padStart(2, '0')}`;
            }
        }

        const tokens = normalized.split(/\D+/).filter(Boolean);
        if (tokens.length !== 3) return null;

        /** @type {Record<DateField, number>} */
        const values = { day: 0, month: 0, year: 0 };
        order(locale).forEach((part, index) => {
            values[part] = Number(tokens[index]);
        });
        if (!isValidDate(values.year, values.month, values.day)) return null;
        return `${String(values.year).padStart(4, '0')}-${String(values.month).padStart(2, '0')}-${String(values.day).padStart(2, '0')}`;
    };

    /**
     * @param {IntlDateInputRoot} root
     * @param {I18nService} i18n
     * @returns {IntlDateInputControl}
     */
    const init = (root, i18n) => {
        if (root.intlDateInput) return root.intlDateInput;

        const visible = /** @type {HTMLInputElement | null} */ (
            root.querySelector('.intl-date-input')
        );
        const hidden = /** @type {HTMLInputElement | null} */ (
            root.querySelector('input[type="hidden"]')
        );
        if (!visible || !hidden) {
            throw new Error('intl-date-input requires visible and hidden input elements');
        }
        const locale = i18n.locale;
        const invalidMessage = i18n.t('components:dateInput.invalidDate');

        visible.placeholder = placeholder(locale);
        visible.value = formatValue(hidden.value, locale);

        /**
         * @param {string | null | undefined} isoValue
         */
        const setValue = (isoValue) => {
            hidden.value = isoValue || '';
            visible.value = formatValue(hidden.value, locale);
            visible.setCustomValidity('');
            visible.classList.remove('is-invalid');
        };

        const sync = () => {
            const parsed = parseDate(visible.value, locale);
            if (parsed === null) {
                visible.setCustomValidity(invalidMessage);
                visible.classList.add('is-invalid');
                return false;
            }
            hidden.value = parsed;
            visible.value = formatValue(parsed, locale);
            visible.setCustomValidity('');
            visible.classList.remove('is-invalid');
            return true;
        };

        visible.addEventListener('input', () => {
            const digitCount = countDigits(visible.value.slice(0, visible.selectionStart || 0), locale);
            visible.value = formatPartial(visible.value, locale);
            visible.setSelectionRange(
                caretForDigitCount(visible.value, digitCount),
                caretForDigitCount(visible.value, digitCount),
            );
            visible.setCustomValidity('');
            visible.classList.remove('is-invalid');
        });
        visible.addEventListener('blur', sync);
        visible.form?.addEventListener('submit', (event) => {
            if (!sync()) {
                event.preventDefault();
                visible.reportValidity();
            }
        });

        const control = { setValue, sync };
        root.intlDateInput = control;
        return control;
    };

    /**
     * @param {I18nService} i18n
     * @param {ParentNode} root
     * @returns {IntlDateInputControl[]}
     */
    const initAll = (i18n, root = document) => Array.from(
        root.querySelectorAll('.intl-date-input-control')
    ).map((control) => init(/** @type {IntlDateInputRoot} */ (control), i18n));

    return {
        formatValue,
        formatPartial,
        init,
        initAll,
        parseDate,
        placeholder,
    };
})();
