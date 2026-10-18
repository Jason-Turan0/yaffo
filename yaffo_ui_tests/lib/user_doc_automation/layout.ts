import type {Page} from "@playwright/test";
import type {Shot} from "./types";

/**
 * What a shot shows as text and controls rather than pixels: one line per visible
 * element in the crop, in document order, as `role "name" WxH`.
 *
 *     heading "People" 92×31
 *     button "Add Person" 150×44
 *     cell "Female" 64×24
 *
 * The pixel comparison cannot tell a table whose columns widened by a fraction of a
 * pixel from one whose cells were re-rendered: glyphs drawn at a new sub-pixel
 * offset differ everywhere. This can. When every element is still there with the
 * same role, the same text, and the same size, nothing a page could quote or a
 * reader could notice changed, however the pixels moved.
 *
 * Sizes are CSS pixels, rounded. Text is measured by its own extent rather than its
 * container, so a cell does not "resize" because its column widened, while text
 * that rewraps does. Controls keep their box: a button that grows is a change.
 * Positions are deliberately absent. Moving is what this is meant to forgive.
 *
 * Elements inside the shot's ignore regions are left out. They hold what changes
 * run to run (timestamps, live map tiles), and would otherwise churn the lockfile.
 */
export type LayoutLine = string;

export interface LayoutElement {
    role: string;
    name: string;
    width: number;
    height: number;
}

/** Lines that differ between two layouts, and the elements whose size changed. */
export interface LayoutDiff {
    same: boolean;
    added: LayoutElement[];
    removed: LayoutElement[];
    resized: Array<{before: LayoutElement; after: LayoutElement}>;
}

/** How far a size may drift before it counts: rounding can flip either way. */
export const SIZE_TOLERANCE = 1;

export const formatElement = (element: LayoutElement): LayoutLine =>
    `${element.role} ${JSON.stringify(element.name)} ${element.width}×${element.height}`;

const LINE = /^(\S+) (".*") (\d+)×(\d+)$/;

export const parseElement = (line: LayoutLine): LayoutElement | null => {
    const match = LINE.exec(line);
    if (!match) return null;
    return {role: match[1], name: JSON.parse(match[2]) as string, width: Number(match[3]), height: Number(match[4])};
};

/**
 * Runs in the page. Self-contained: Playwright serializes it, so it may not close
 * over anything in this module.
 */
export const collectLayout = ({clip, ignore}: {clip: string | null; ignore: string[]}): LayoutElement[] => {
    const root = clip ? document.querySelector(clip) : document.body;
    if (!root) return [];
    const frame = root.getBoundingClientRect();
    const implicit: Record<string, string> = {
        A: "link", BUTTON: "button", SUMMARY: "button", IMG: "img", SELECT: "combobox", TEXTAREA: "textbox",
        H1: "heading", H2: "heading", H3: "heading", H4: "heading", H5: "heading", H6: "heading",
        TH: "columnheader", TD: "cell", LI: "listitem", LABEL: "label", OPTION: "option", LEGEND: "legend",
    };
    const inputRoles: Record<string, string> = {
        checkbox: "checkbox", radio: "radio", range: "slider", button: "button", submit: "button", reset: "button",
    };
    // Controls are judged by their box; everything else by the extent of its text.
    const boxed = new Set(["button", "checkbox", "radio", "slider", "textbox", "combobox", "img", "switch", "tab"]);
    const collapse = (text: string) => text.replace(/\s+/g, " ").trim();

    const roleOf = (element: Element): string | null => {
        const explicit = element.getAttribute("role");
        if (explicit) return explicit.split(" ")[0];
        if (element.tagName === "INPUT") {
            const type = (element as HTMLInputElement).type;
            return type === "hidden" ? null : inputRoles[type] ?? "textbox";
        }
        return implicit[element.tagName] ?? null;
    };
    const visible = (element: Element): boolean => {
        const style = getComputedStyle(element);
        if (style.visibility === "hidden" || style.display === "none" || Number(style.opacity) === 0) return false;
        const box = element.getBoundingClientRect();
        return box.width > 0 && box.height > 0 &&
            box.right > frame.left && box.left < frame.right && box.bottom > frame.top && box.top < frame.bottom;
    };
    const textExtent = (element: Element, ownOnly: boolean): {width: number; height: number} | null => {
        let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
        const nodes = ownOnly
            ? Array.from(element.childNodes).filter((node) => node.nodeType === Node.TEXT_NODE)
            : (() => {
                const found: Node[] = [];
                const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
                for (let node = walker.nextNode(); node; node = walker.nextNode()) found.push(node);
                return found;
            })();
        for (const node of nodes) {
            if (!collapse(node.textContent ?? "")) continue;
            const range = document.createRange();
            range.selectNodeContents(node);
            for (const rect of Array.from(range.getClientRects())) {
                if (!rect.width || !rect.height) continue;
                left = Math.min(left, rect.left); top = Math.min(top, rect.top);
                right = Math.max(right, rect.right); bottom = Math.max(bottom, rect.bottom);
            }
        }
        return right > left ? {width: right - left, height: bottom - top} : null;
    };
    const nameOf = (element: Element, role: string): string => {
        const label = element.getAttribute("aria-label");
        if (label) return collapse(label);
        if (element.tagName === "IMG") return collapse(element.getAttribute("alt") ?? "");
        if (element.tagName === "INPUT" || element.tagName === "TEXTAREA") {
            const field = element as HTMLInputElement;
            return collapse(field.type === "checkbox" || field.type === "radio"
                ? `${field.checked ? "checked" : "unchecked"}`
                : field.value || field.placeholder || "");
        }
        if (element.tagName === "SELECT") {
            const select = element as HTMLSelectElement;
            return collapse(select.selectedOptions[0]?.textContent ?? "");
        }
        return role ? collapse((element as HTMLElement).innerText ?? element.textContent ?? "") : "";
    };

    const out: LayoutElement[] = [];
    const visit = (element: Element, named: boolean): void => {
        if (ignore.some((selector) => element.closest(selector))) return;
        if (!visible(element)) return;
        const role = roleOf(element);
        let nameTaken = named;
        if (role) {
            const name = nameOf(element, role);
            const size = boxed.has(role) ? element.getBoundingClientRect() : textExtent(element, false);
            if (size && (name || boxed.has(role))) {
                out.push({role, name, width: Math.round(size.width), height: Math.round(size.height)});
                // Its text is in its name; the elements it contains add only their own roles.
                nameTaken = true;
            }
        } else if (!named) {
            const own = collapse(Array.from(element.childNodes)
                .filter((node) => node.nodeType === Node.TEXT_NODE)
                .map((node) => node.textContent ?? "").join(" "));
            const size = own ? textExtent(element, true) : null;
            if (own && size) out.push({role: "text", name: own, width: Math.round(size.width), height: Math.round(size.height)});
        }
        for (const child of Array.from(element.children)) visit(child, nameTaken);
    };
    visit(root, false);
    return out;
};

/**
 * collectLayout evaluated in the page. Passed as source rather than as a function:
 * tsx compiles named helpers into calls to an `__name` helper that exists in Node
 * but not in the page, so serializing the function as-is throws there.
 */
export const evaluateLayout = (page: Page, clip: string | null, ignore: string[]): Promise<LayoutElement[]> =>
    page.evaluate(`(() => {
        const __name = (target) => target;
        return (${collectLayout.toString()})(${JSON.stringify({clip, ignore})});
    })()`) as Promise<LayoutElement[]>;

/** The shot's layout lines, captured from the page as it is about to be photographed. */
export const captureLayout = async (page: Page, shot: Shot): Promise<LayoutLine[]> =>
    (await evaluateLayout(page, shot.clip ?? null, shot.ignoreRegions ?? [])).map(formatElement);

const key = (element: LayoutElement) => `${element.role}\u0000${element.name}`;

/**
 * Elements matched in order by role and name (a longest common subsequence, so one
 * insertion does not unmatch everything after it), then compared by size.
 * A reordering reads as a removal and an addition, which errs towards "changed".
 */
export const compareLayouts = (before: LayoutLine[], after: LayoutLine[]): LayoutDiff => {
    const a = before.map(parseElement).filter((element): element is LayoutElement => element !== null);
    const b = after.map(parseElement).filter((element): element is LayoutElement => element !== null);
    const lengths = Array.from({length: a.length + 1}, () => new Array<number>(b.length + 1).fill(0));
    for (let i = a.length - 1; i >= 0; i--) {
        for (let j = b.length - 1; j >= 0; j--) {
            lengths[i][j] = key(a[i]) === key(b[j])
                ? lengths[i + 1][j + 1] + 1
                : Math.max(lengths[i + 1][j], lengths[i][j + 1]);
        }
    }
    const diff: LayoutDiff = {same: true, added: [], removed: [], resized: []};
    let i = 0, j = 0;
    while (i < a.length && j < b.length) {
        if (key(a[i]) === key(b[j])) {
            if (Math.abs(a[i].width - b[j].width) > SIZE_TOLERANCE || Math.abs(a[i].height - b[j].height) > SIZE_TOLERANCE) {
                diff.resized.push({before: a[i], after: b[j]});
            }
            i++; j++;
        } else if (lengths[i + 1][j] >= lengths[i][j + 1]) {
            diff.removed.push(a[i++]);
        } else {
            diff.added.push(b[j++]);
        }
    }
    diff.removed.push(...a.slice(i));
    diff.added.push(...b.slice(j));
    diff.same = !diff.added.length && !diff.removed.length && !diff.resized.length;
    return diff;
};

/** The differences as the model and the run summary should read them. */
export const describeLayoutDiff = (diff: LayoutDiff, limit = 12): string[] => {
    const lines = [
        ...diff.removed.map((element) => `- removed: ${element.role} ${JSON.stringify(element.name)}`),
        ...diff.added.map((element) => `- added: ${element.role} ${JSON.stringify(element.name)}`),
        ...diff.resized.map(({before, after}) =>
            `- resized: ${before.role} ${JSON.stringify(before.name)} ${before.width}×${before.height} → ${after.width}×${after.height}`),
    ];
    return lines.length > limit ? [...lines.slice(0, limit), `- … ${lines.length - limit} more`] : lines;
};
