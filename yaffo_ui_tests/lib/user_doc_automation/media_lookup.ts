/**
 * Resolve a media item id from something that survives a reseed.
 *
 * Ids are assigned at index time, so a walkthrough that hardcodes `/media/view/31`
 * documents whichever photo happens to land at 31 next time. Filenames are stable —
 * the same reasoning behind `PRIMARY_DETAIL_IMAGE` in
 * `generated_tests/_support/media-test-data.ts`, which the Playwright specs pin against.
 */
export const mediaIdByFilename = async (
    baseUrl: string,
    filename: string
): Promise<number> => {
    const url = `${baseUrl}/?path=${encodeURIComponent(filename)}&page-size=250`;
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url} -> HTTP ${response.status}`);
    const match = (await response.text()).match(/\/media\/view\/(\d+)/);
    if (!match) throw new Error(`the gallery has no media item named ${filename}`);
    return Number(match[1]);
};
