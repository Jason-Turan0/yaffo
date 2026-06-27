window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {};

window.PHOTO_ORGANIZER.COMPONENTS.intlDateInput = (() => {
    const sampleParts = (locale) => new Intl.DateTimeFormat(locale, {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        timeZone: 'UTC',
    }).formatToParts(new Date(Date.UTC(2006, 10, 22)));

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

    const placeholder = (locale) => sampleParts(locale).map((part) => {
        if (part.type === 'day') return 'DD';
        if (part.type === 'month') return 'MM';
        if (part.type === 'year') return 'YYYY';
        return part.value;
    }).join('');

    const pattern = (locale) => sampleParts(locale).map((part) => ({
        type: part.type,
        value: part.value,
        length: part.type === 'year' ? 4 : 2,
    }));

    const order = (locale) => sampleParts(locale)
        .filter((part) => ['day', 'month', 'year'].includes(part.type))
        .map((part) => part.type);

    const digitNormalizer = (locale) => {
        const formatter = new Intl.NumberFormat(locale, { useGrouping: false });
        const replacements = new Map();
        for (let digit = 0; digit <= 9; digit += 1) {
            replacements.set(formatter.format(digit), String(digit));
        }
        return (value) => Array.from(value).map((char) => replacements.get(char) || char).join('');
    };

    const normalizedDigits = (value, locale) => digitNormalizer(locale)(value).replace(/\D/g, '');

    const countDigits = (value, locale) => normalizedDigits(value, locale).length;

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

    const isValidDate = (year, month, day) => {
        if (year < 1 || month < 1 || month > 12 || day < 1 || day > 31) return false;
        const date = new Date(Date.UTC(year, month - 1, day));
        return date.getUTCFullYear() === year
            && date.getUTCMonth() === month - 1
            && date.getUTCDate() === day;
    };

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
            const compactValues = {};
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

        const values = {};
        order(locale).forEach((part, index) => {
            values[part] = Number(tokens[index]);
        });
        if (!isValidDate(values.year, values.month, values.day)) return null;
        return `${String(values.year).padStart(4, '0')}-${String(values.month).padStart(2, '0')}-${String(values.day).padStart(2, '0')}`;
    };

    const init = (root, i18n) => {
        const visible = root.querySelector('.intl-date-input');
        const hidden = root.querySelector('input[type="hidden"]');
        const locale = i18n.locale;
        const invalidMessage = i18n.t('components:dateInput.invalidDate');

        visible.placeholder = placeholder(locale);
        visible.value = formatValue(hidden.value, locale);

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

        root.intlDateInput = { setValue, sync };
        return root.intlDateInput;
    };

    const initAll = (i18n, root = document) => Array.from(
        root.querySelectorAll('.intl-date-input-control')
    ).map((control) => init(control, i18n));

    return {
        formatValue,
        formatPartial,
        init,
        initAll,
        parseDate,
        placeholder,
    };
})();
