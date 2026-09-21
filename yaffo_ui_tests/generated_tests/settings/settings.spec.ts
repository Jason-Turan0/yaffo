import { test, expect, Page, Locator } from '@playwright/test';
import {
  BASE_URL,
  CONTRACT_WIDTHS,
  VIEWPORTS,
  expectFitsViewport,
  expectNoPageOverflow,
  expectRouteFits,
  withTouchContext,
} from '../_support/responsive';

const UNIQ = Date.now();
const SPEC_LABEL = `spec-set-label-${UNIQ}`;

// The suite mutates global application settings (locale, units, directories), so it
// runs serially and restores every setting it changes.
test.describe.configure({ mode: 'serial' });

async function openSettings(page: Page): Promise<void> {
  await page.goto('/settings');
  await expect(page.locator('.page-header')).toBeVisible();
}

function notification(page: Page): Locator {
  return page.locator('.notification.visible');
}

// Pick an option from the searchable-select widget wrapping a hidden native select.
async function pickSearchableOption(page: Page, selectSelector: string, optionText: string): Promise<void> {
  const wrapper = page.locator(`${selectSelector} + .searchable-select-wrapper`);
  await wrapper.locator('.searchable-select-display').click();
  await wrapper.locator('.searchable-select-option').filter({ hasText: optionText }).first().click();
}

// The sandbox root, derived from the Database Path shown in System Information
// (<root>/yaffo.db). Matches the code whose text ends with "/yaffo.db" rather
// than the label text, so this works regardless of the current UI locale.
async function sandboxRoot(page: Page): Promise<string> {
  await openSettings(page);
  const dbPath = (await page.locator('.system-path-item code')
    .filter({ hasText: /\/yaffo\.db$/ })
    .first()
    .textContent())!.trim();
  return dbPath.replace(/\/[^/]+$/, '');
}

function labelChip(page: Page, name: string): Locator {
  return page.locator('#labels-section .label-chip').filter({ hasText: name });
}

function mediaDirItem(page: Page, path: string): Locator {
  return page.locator('.media-dir-item').filter({ hasText: path });
}

async function removeMediaDirViaUI(page: Page, path: string): Promise<void> {
  const item = mediaDirItem(page, path);
  if (await item.count() === 0) return;
  await item.locator('[data-action="remove-media-dir"]').click();
  await expect(page.locator('#global-confirm-dialog')).toHaveClass(/active/);
  await page.locator('#confirm-dialog-confirm').click();
  await expect(mediaDirItem(page, path)).toHaveCount(0);
}

test.describe('Settings', () => {
  // Reset the locale to English before any test runs. The locale is stored
  // in the database (ApplicationSettings table) and persists across test
  // runs. A previous suite failure may have left it on a non-English value,
  // which would break the English text assertions throughout this suite.
  // Using page.evaluate routes the POST through the browser's overridden
  // fetch(), so security.js injects the X-CSRF-Token header automatically.
  test.beforeAll(async ({ browser }) => {
    const page = await browser.newPage();
    await page.goto('/settings');
    await page.evaluate(async () => {
      await fetch('/settings/locale', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ locale: 'en' }).toString(),
      });
    });
    await page.close();
  });

  test('settings_add_media_directory', async ({ page }) => {
    const scratchDir = `${await sandboxRoot(page)}/spec-test-media-${UNIQ}`;

    // An empty path is rejected with a validation error instead of being added.
    await page.locator('[data-action="add-media-dir"]').click();
    await expect(notification(page)).toContainText('Please enter a directory path');

    // A valid path is created and appears in the list (the server mkdirs it).
    await page.locator('#new-media-dir').fill(scratchDir);
    await Promise.all([
      page.waitForResponse(response =>
        response.url().includes('/api/settings/media-dirs') && response.request().method() === 'POST'),
      page.locator('[data-action="add-media-dir"]').click(),
    ]);
    await expect(notification(page)).toContainText('Media directory added successfully');
    await expect(mediaDirItem(page, scratchDir)).toHaveCount(1);

    // Cleanup: remove the scratch directory entry.
    await removeMediaDirViaUI(page, scratchDir);
  });

  test('settings_remove_media_directory', async ({ page }) => {
    // Never remove the seeded library directory — other suites depend on it. Add a
    // scratch directory through the browser UI and remove that one.
    const scratchDir = `${await sandboxRoot(page)}/spec-test-remove-${UNIQ}`;

    // Add the scratch directory via the browser UI so the CSRF token injected by
    // security.js is included in the POST (the Playwright APIRequestContext does
    // not share the browser session and cannot satisfy the CSRF check on its own).
    await page.locator('#new-media-dir').fill(scratchDir);
    await Promise.all([
      page.waitForResponse(response =>
        response.url().includes('/api/settings/media-dirs') && response.request().method() === 'POST'),
      page.locator('[data-action="add-media-dir"]').click(),
    ]);
    await expect(mediaDirItem(page, scratchDir)).toHaveCount(1);

    const seededCount = await page.locator('.media-dir-item').count();
    await mediaDirItem(page, scratchDir).locator('[data-action="remove-media-dir"]').click();

    // A confirmation dialog naming the directory precedes the removal.
    const dialog = page.locator('#global-confirm-dialog');
    await expect(dialog).toHaveClass(/active/);
    await expect(dialog).toContainText(scratchDir);
    await page.locator('#confirm-dialog-confirm').click();

    await expect(notification(page)).toContainText(`Removed: ${scratchDir}`);
    await expect(mediaDirItem(page, scratchDir)).toHaveCount(0);
    await expect(page.locator('.media-dir-item')).toHaveCount(seededCount - 1);

    // Note: the "No media directories configured" empty state requires removing the
    // seeded library directory, which would break the rest of the suite — not asserted.
  });

  test('settings_change_language', async ({ page }) => {
    await openSettings(page);
    const original = await page.locator('#application-locale').inputValue();
    const target = original === 'es' ? 'en' : 'es';
    const targetLabel = target === 'es' ? 'Español' : 'English';
    const localeSubmit = page.locator('form[action$="/settings/locale"] button[type="submit"]');

    try {
      await pickSearchableOption(page, '#application-locale', targetLabel);
      await localeSubmit.click();

      // The UI re-renders in the chosen locale (html lang + translated text).
      // IMPORTANT: the locale is a GLOBAL setting — while it is non-English,
      // every parallel suite that server-renders English text can fail. Keep
      // this window to the single redirect render: no extra navigations here.
      await expect(page.locator('html')).toHaveAttribute('lang', target);
      if (target === 'es') {
        await expect(page.locator('.page-header')).toContainText('Ajustes');
      }
    } finally {
      // Restore via the browser's fetch() so security.js injects the CSRF
      // token automatically. The page.request APIRequestContext shares cookies
      // but does not auto-populate the csrf_token form field, so it fails the
      // CSRF check. Using page.evaluate routes through the overridden fetch
      // which adds the X-CSRF-Token header.
      await page.evaluate(async (locale) => {
        await fetch('/settings/locale', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({ locale }).toString(),
        });
      }, original);
    }

    // The saved locale persists across navigation — verified with the restored
    // locale (same read-the-setting-per-request mechanism, zero Spanish window).
    await page.goto('/people');
    await expect(page.locator('html')).toHaveAttribute('lang', original);
    await openSettings(page);
    await expect(page.locator('#application-locale')).toHaveValue(original);
  });

  test('settings_change_distance_unit', async ({ page }) => {
    await openSettings(page);
    const original = await page.locator('#distance-unit').inputValue();
    const target = original === 'km' ? 'mi' : 'km';
    const labels: Record<string, string> = { km: 'Kilometers', mi: 'Miles' };
    const unitSubmit = page.locator('form[action$="/settings/distance-unit"] button[type="submit"]');

    try {
      await pickSearchableOption(page, '#distance-unit', labels[target]);
      await unitSubmit.click();

      // The preference is saved and survives a reload.
      await expect(page.locator('#distance-unit')).toHaveValue(target);
      await page.reload();
      await expect(page.locator('#distance-unit')).toHaveValue(target);

      // Note: distances rendered elsewhere (locations, automation distance fields)
      // read this preference live; asserting them here would couple this suite to
      // those pages, so persistence is the contract verified.
    } finally {
      await openSettings(page);
      await pickSearchableOption(page, '#distance-unit', labels[original]);
      await unitSubmit.click();
      await expect(page.locator('#distance-unit')).toHaveValue(original);
    }
  });

  test('settings_change_thumbnail_directory', async ({ page }) => {
    await openSettings(page);
    const originalDir = (await page.locator('#current-thumbnail-dir').textContent())!.trim();
    const newDir = `${originalDir}-spec-${UNIQ}`;

    // The thumbnail stats stream fills the file count and total size on load.
    await expect(page.locator('#thumbnail-count')).not.toHaveText('…', { timeout: 20_000 });
    await expect(page.locator('#thumbnail-size')).not.toHaveText(/Counting…/, { timeout: 20_000 });

    const moveTo = async (destination: string) => {
      await openSettings(page);
      await page.locator('#new-thumbnail-dir').fill(destination);
      await page.locator('[data-action="change-thumbnail-dir"]').click();

      // The confirmation dialog reports how many files and how much data will move.
      const dialog = page.locator('#global-confirm-dialog');
      await expect(dialog).toHaveClass(/active/);
      await expect(dialog).toContainText(`New location: ${destination}`);
      await expect(dialog).toContainText(/This will move [\d,.]+ files? \([\d,.]+ \w+\)/);

      await Promise.all([
        page.waitForResponse(response =>
          response.url().includes('/api/settings/thumbnail-dir') && response.ok()),
        page.locator('#confirm-dialog-confirm').click(),
      ]);
      // The success toast fires and the page reloads with the new current directory.
      await expect(page.locator('#current-thumbnail-dir')).toHaveText(destination, { timeout: 20_000 });
    };

    try {
      await moveTo(newDir);
    } finally {
      await moveTo(originalDir);
    }
  });

  test('settings_manage_classification_labels', async ({ page }) => {
    await openSettings(page);
    const section = page.locator('#labels-section');
    await expect(section).toBeVisible();

    // Add a new label to the vocabulary (the section swaps in place on save).
    await section.locator('input[name="name"]').fill(SPEC_LABEL);
    await Promise.all([
      page.waitForResponse(response =>
        response.url().includes('/settings/labels') && response.request().method() === 'POST'),
      section.locator('.add-label-form button[type="submit"]').click(),
    ]);
    const chip = page.locator('#labels-section .label-chip').filter({ hasText: SPEC_LABEL });
    await expect(chip).toHaveCount(1);

    // Removing it takes it back out of the vocabulary.
    await Promise.all([
      page.waitForResponse(response =>
        response.url().includes('/settings/labels') && response.request().method() === 'POST'),
      chip.locator('.label-chip-remove').click(),
    ]);
    await expect(page.locator('#labels-section .label-chip').filter({ hasText: SPEC_LABEL })).toHaveCount(0);
  });

  test('settings_system_information_displayed', async ({ page }) => {
    await openSettings(page);

    const expectedEntries = [
      'Build Version:',
      'Build Timestamp:',
      'Database Path:',
      'Task Queue Database Path:',
      'ExifTool Path:',
      'FFmpeg Path:',
      'Image Classification Model:',
      'Face Recognition Model:',
    ];
    for (const label of expectedEntries) {
      // Exact label match — "Database Path:" is a substring of "Task Queue Database Path:".
      const item = page.locator('.system-path-item').filter({ has: page.getByText(label, { exact: true }) });
      await expect(item).toHaveCount(1);
      await expect(item.locator('code').first()).not.toHaveText('');
    }
  });

  // ---------------------------------------------------------------------------
  // Responsive coverage (P7 — settings). The shared shell contract itself is
  // verified on Home (specs/photo_gallery.yaml); what follows is this page
  // family's own narrow-screen behaviour. Settings registers no page panel of
  // its own, so the peer-panel half of the page-family minimum is covered by the
  // themes suite — themes is the other half of this family.
  // ---------------------------------------------------------------------------
  test.describe('Responsive (P7)', () => {
    const PROMPT_LABEL = `spec-set-prompt-${UNIQ}`;
    const PROMPT_TEXT = 'people swimming in water at a lake on a bright summer afternoon';

    // A chip only renders its prompt marker when the label actually has a prompt,
    // and the seeded vocabulary is not guaranteed to contain one. Create it once
    // for the whole group rather than per test.
    test.beforeAll(async ({ browser }) => {
      const context = await browser.newContext({ baseURL: BASE_URL });
      const page = await context.newPage();
      await openSettings(page);
      const section = page.locator('#labels-section');
      await section.locator('input[name="name"]').fill(PROMPT_LABEL);
      await section.locator('input[name="prompt"]').fill(PROMPT_TEXT);
      await Promise.all([
        page.waitForResponse(response =>
          response.url().includes('/settings/labels') && response.request().method() === 'POST'),
        section.locator('.add-label-form button[type="submit"]').click(),
      ]);
      await expect(labelChip(page, PROMPT_LABEL)).toHaveCount(1);
      await context.close();
    });

    test.afterAll(async ({ browser }) => {
      const context = await browser.newContext({ baseURL: BASE_URL });
      const page = await context.newPage();
      await openSettings(page);
      const chip = labelChip(page, PROMPT_LABEL);
      if (await chip.count() > 0) {
        await Promise.all([
          page.waitForResponse(response =>
            response.url().includes('/settings/labels') && response.request().method() === 'POST'),
          chip.locator('.label-chip-remove').click(),
        ]);
        await expect(labelChip(page, PROMPT_LABEL)).toHaveCount(0);
      }
      await context.close();
    });

    test('settings_route_fits_every_contract_viewport - settings never scrolls the page sideways', async ({ page }) => {
      for (const width of CONTRACT_WIDTHS) {
        await page.setViewportSize({ width, height: 800 });
        await expectRouteFits(page, '/settings');
        await expect(page.locator('#labels-section')).toBeVisible();
        await expect(page.locator('#llm-section')).toBeVisible();
      }
    });

    test('settings_long_paths_wrap_instead_of_widening_the_page - a configured path cannot widen the column', async ({ page }) => {
      const longPath = `/Volumes/${'Photographs'.repeat(12)}/library`;
      await page.setViewportSize(VIEWPORTS.minimum);
      await openSettings(page);

      // Rewrite the *rendered* values rather than saving one: these are the only
      // strings on the page with no break opportunity in them, and the suite
      // should not have to leave a bogus media directory behind to prove it.
      await page.locator('.media-dir-path').first()
        .evaluate((element, value) => { element.textContent = value; }, longPath);
      await page.locator('.system-path-item code').first()
        .evaluate((element, value) => { element.textContent = value; }, longPath);
      await expectNoPageOverflow(page);

      // The row does NOT stack: dropping Remove onto its own line reads as a
      // second, page-wide action rather than as this row's control, and puts a
      // destructive button directly under the path it deletes. The path is what
      // gives way — it wraps onto as many lines as it needs.
      const row = page.locator('.media-dir-item').first();
      const geometry = await row.evaluate((element) => {
        const remove = element.querySelector<HTMLElement>('[data-action="remove-media-dir"]')!;
        const path = element.querySelector<HTMLElement>('.media-dir-path')!;
        const rowBox = element.getBoundingClientRect();
        const removeBox = remove.getBoundingClientRect();
        const pathBox = path.getBoundingClientRect();
        const primary = document.querySelector<HTMLElement>('.add-media-dir-form .btn-primary')!;
        // Count line boxes rather than dividing by `line-height`, which computes
        // to the keyword `normal` here and parses as NaN.
        const lines = document.createRange();
        lines.selectNodeContents(path);
        return {
          direction: getComputedStyle(element).flexDirection,
          rowWidth: rowBox.width,
          removeWidth: removeBox.width,
          // Vertical ranges overlapping means they share the row rather than
          // sitting one above the other.
          sameRow: removeBox.top < pathBox.bottom && pathBox.top < removeBox.bottom,
          pathLines: lines.getClientRects().length,
          pathClipped: path.scrollWidth - path.clientWidth,
          removeBackground: getComputedStyle(remove).backgroundColor,
          primaryBackground: getComputedStyle(primary).backgroundColor,
        };
      });
      expect(geometry.direction, 'the directory row stays a row on a phone').toBe('row');
      expect(geometry.sameRow, 'Remove stays beside the path it deletes').toBe(true);
      expect(geometry.pathLines, 'an unbreakable path should wrap onto several lines')
        .toBeGreaterThan(1);
      expect(geometry.pathClipped, 'the path is wrapped, never clipped').toBeLessThanOrEqual(1);
      expect(geometry.removeWidth, 'Remove keeps its own size rather than filling the row')
        .toBeLessThan(geometry.rowWidth / 2);
      // Destructive actions stay visually distinct at every width.
      expect(geometry.removeBackground).not.toBe(geometry.primaryBackground);
    });

    test('settings_label_help_icons_stay_visible_below_the_breakpoint - the prompt markers are real controls on a phone', async ({ page }) => {
      for (const viewport of [VIEWPORTS.minimum, VIEWPORTS.narrow, VIEWPORTS.tabletPortrait]) {
        await page.setViewportSize(viewport);
        await openSettings(page);
        await expect(page.locator('#labels-section .label-chip-info').first()).toBeAttached();
        // Report every collapsed marker at once, with what the cascade resolved:
        // the failure mode here is a 0x0 button, which no screenshot would show.
        const collapsed = await page.locator('.settings-section .help-tip').evaluateAll(elements =>
          elements
            .filter(element => {
              const box = element.getBoundingClientRect();
              return box.width < 1 || box.height < 1;
            })
            .map(element => `${element.className} → `
              + `${element.getBoundingClientRect().width}x${element.getBoundingClientRect().height} `
              + `(::before display ${getComputedStyle(element, '::before').display})`));
        expect(collapsed, `help tips collapsed to an invisible control at ${viewport.width}px`).toEqual([]);
      }
    });

    test('settings_label_prompt_tooltips_open_as_an_anchored_popover - tablet and desktop keep the prompt beside its chip', async ({ page }) => {
      for (const viewport of [VIEWPORTS.tabletPortrait, VIEWPORTS.tabletLandscape, VIEWPORTS.desktop]) {
        await page.setViewportSize(viewport);
        await openSettings(page);
        // Nothing is hovered yet. The pure-CSS bubble is laid out at opacity 0
        // even when hidden, so an absolutely positioned one widens the document
        // all on its own — which is why it stands down at these widths.
        await expectNoPageOverflow(page);

        const marker = labelChip(page, PROMPT_LABEL).locator('.label-chip-info');
        await marker.scrollIntoViewIfNeeded();
        expect(await marker.evaluate(element => getComputedStyle(element, '::after').content),
          'the pseudo-element bubble must not also render above the breakpoint').toBe('none');

        await marker.hover();
        const popover = page.locator('.data-tooltip-popover');
        await expect(popover).toHaveClass(/visible/);
        await expect(popover).toHaveText(PROMPT_TEXT);
        await expectFitsViewport(page, '.data-tooltip-popover');

        // Anchored, not parked at the bottom of the screen: it sits directly
        // above the chip, or directly below it when there is no room above.
        const geometry = await marker.evaluate((element) => {
          const anchor = element.getBoundingClientRect();
          const tip = document.querySelector('.data-tooltip-popover')!.getBoundingClientRect();
          return {
            gapAbove: anchor.top - tip.bottom,
            gapBelow: tip.top - anchor.bottom,
            viewportBottomGap: window.innerHeight - tip.bottom,
          };
        });
        const adjacent = Math.abs(geometry.gapAbove) <= 24 || Math.abs(geometry.gapBelow) <= 24;
        expect(adjacent, `the popover should touch its anchor, got ${JSON.stringify(geometry)}`).toBe(true);
        await expectNoPageOverflow(page);
      }
    });

    test('settings_label_prompt_popover_opens_on_a_tablet_tap - a coarse pointer above the breakpoint still gets the prompt', async ({ browser }) => {
      // A tablet is wide enough for the popover but has no hover at all, and
      // :focus-visible does not match a tap — so the press has to open it.
      await withTouchContext(browser, VIEWPORTS.tabletPortrait, async (page) => {
        await page.goto('/settings');
        const marker = labelChip(page, PROMPT_LABEL).locator('.label-chip-info');
        await marker.scrollIntoViewIfNeeded();
        const popover = page.locator('.data-tooltip-popover');

        await marker.tap();
        await expect(popover).toHaveClass(/visible/);
        await expect(popover).toHaveText(PROMPT_TEXT);
        await expectFitsViewport(page, '.data-tooltip-popover');

        // Pressing the same control again dismisses it; there is no hover to
        // leave, so without this a tablet reader could never close it.
        await marker.tap();
        await expect(popover).not.toHaveClass(/visible/);
        await expectNoPageOverflow(page);
      });
    });

    test('settings_label_prompts_open_on_a_coarse_pointer - tapping a marker reveals its prompt', async ({ browser }) => {
      await withTouchContext(browser, VIEWPORTS.narrow, async (page) => {
        await page.goto('/settings');
        const chip = labelChip(page, PROMPT_LABEL);
        const marker = chip.locator('.label-chip-info');
        await marker.scrollIntoViewIfNeeded();

        const undersized = await page.locator('#labels-section .label-chip-info, #labels-section .label-chip-remove')
          .evaluateAll(elements => elements
            .filter(element => {
              const box = element.getBoundingClientRect();
              return box.width < 44 || box.height < 44;
            })
            .map(element => `${element.className} → `
              + `${Math.round(element.getBoundingClientRect().width)}x`
              + `${Math.round(element.getBoundingClientRect().height)}`));
        expect(undersized, 'chip controls below the 44px touch target').toEqual([]);

        // At this width the presentation is still the pseudo-element pinned to the
        // bottom of the viewport (components/tooltip.js only takes over above
        // 640px). Hover never happens here and :focus-visible does not match a
        // tap, so without a touch-specific reveal the prompt is unreadable.
        expect(await marker.evaluate(element => getComputedStyle(element, '::after').position))
          .toBe('fixed');
        await marker.tap();
        await expect.poll(async () => marker.evaluate(element =>
          getComputedStyle(element, '::after').opacity)).toBe('1');
        await expectNoPageOverflow(page);
      });
    });

    test('settings_api_key_controls_stack_without_crowding_the_destructive_action - the key row is a real action row', async ({ page }) => {
      await page.setViewportSize(VIEWPORTS.desktop);
      await openSettings(page);
      const actions = page.locator('.api-key-actions');
      await expect(actions).toBeVisible();

      const wide = await actions.evaluate((element) => {
        const style = getComputedStyle(element);
        return { display: style.display, direction: style.flexDirection, gap: parseFloat(style.gap) };
      });
      expect(wide.display, 'the shared narrow layer sets flex-direction, which does nothing on a block').toBe('flex');
      expect(wide.direction).toBe('row');
      expect(wide.gap, 'Save and Clear must not butt against each other').toBeGreaterThan(0);

      await page.setViewportSize(VIEWPORTS.minimum);
      await openSettings(page);
      // An environment-provided key names its variable inline; the status line has
      // to wrap it rather than push the section wider.
      await page.locator('.api-key-status').evaluate((element) => {
        element.append(document.createTextNode(` ${'YAFFO_LONG_PROVIDER_ENVIRONMENT_VARIABLE_NAME'.repeat(3)}`));
      });
      const narrow = await actions.evaluate((element) => ({
        direction: getComputedStyle(element).flexDirection,
        rowWidth: element.getBoundingClientRect().width,
        children: Array.from(element.children).map(child => child.getBoundingClientRect().width),
      }));
      expect(narrow.direction).toBe('column');
      expect(narrow.children.length).toBeGreaterThan(0);
      for (const width of narrow.children) {
        expect(width).toBeCloseTo(narrow.rowWidth, 0);
      }
      await expectNoPageOverflow(page);
    });

    test('settings_entered_values_survive_a_resize_through_the_breakpoint - unsaved input is not lost', async ({ page }) => {
      const directory = `/Volumes/Unsaved/${'photos-'.repeat(10)}`;
      await page.setViewportSize(VIEWPORTS.narrow);
      await openSettings(page);
      await page.locator('#new-media-dir').fill(directory);
      await page.locator('#label-filter').fill('spec');
      await page.locator('#llm-api-key-input').fill('sk-unsaved-spec-key');

      for (const viewport of [VIEWPORTS.desktop, VIEWPORTS.minimum, VIEWPORTS.narrowLandscape, VIEWPORTS.desktop]) {
        await page.setViewportSize(viewport);
        await expect(page.locator('#new-media-dir')).toHaveValue(directory);
        await expect(page.locator('#label-filter')).toHaveValue('spec');
        await expect(page.locator('#llm-api-key-input')).toHaveValue('sk-unsaved-spec-key');
        await expectNoPageOverflow(page);
      }
    });

    test('settings_folder_picker_fits_a_narrow_viewport - the picker is a contained sheet that owns its own scrolling', async ({ page }) => {
      await page.setViewportSize(VIEWPORTS.narrow);
      await openSettings(page);
      await page.locator('.add-media-dir-form .file-browser-btn').click();

      const modal = page.locator('#folder-picker-modal');
      await expect(modal).toHaveClass(/active/);
      await expectFitsViewport(page, '#folder-picker-modal .modal-content');

      const list = modal.locator('.folder-picker-list');
      await expect(list).toBeVisible();
      const scrolling = await list.evaluate((element) => {
        const style = getComputedStyle(element);
        return {
          overflowY: style.overflowY,
          overscroll: style.overscrollBehaviorY,
          // `45dvh` resolves against the visual viewport; `45vh` would too here,
          // but the fallback pair is what keeps mobile browser chrome honest.
          withinViewport: element.getBoundingClientRect().height <= window.innerHeight,
        };
      });
      expect(scrolling.overflowY).toBe('auto');
      expect(scrolling.overscroll, 'the list must contain its own overscroll').toBe('contain');
      expect(scrolling.withinViewport).toBe(true);

      // A long path in the picker bar ellipsises instead of widening the dialog.
      await modal.locator('.folder-picker-path')
        .evaluate((element, value) => { element.textContent = value; }, `/Volumes/${'Photographs'.repeat(12)}`);
      await expectFitsViewport(page, '#folder-picker-modal .modal-content');
      expect(await modal.locator('.folder-picker-path').evaluate(element =>
        getComputedStyle(element).textOverflow)).toBe('ellipsis');

      await page.keyboard.press('Escape');
      await expect(modal).not.toHaveClass(/active/);
    });

    test('settings_translated_labels_do_not_widen_the_page - German and Arabic copy fits every contract width', async ({ page }) => {
      await openSettings(page);
      const originalLocale = await page.locator('html').getAttribute('lang') || 'en';
      const setLocale = async (locale: string) => {
        const ok = await page.evaluate(async (value) => {
          const response = await fetch('/settings/locale', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: new URLSearchParams({ locale: value }).toString(),
          });
          return response.ok;
        }, locale);
        expect(ok).toBe(true);
      };

      try {
        for (const locale of ['de', 'ar']) {
          await setLocale(locale);
          for (const width of CONTRACT_WIDTHS) {
            await page.setViewportSize({ width, height: 800 });
            await openSettings(page);
            await expect(page.locator('html')).toHaveAttribute('lang', locale);
            await expect(page.locator('html')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr');
            await expectNoPageOverflow(page);
          }
        }
      } finally {
        // The locale is a GLOBAL setting; never leave the application on one the
        // rest of the suite's English text assertions cannot read.
        await setLocale(originalLocale);
      }
      await openSettings(page);
      await expect(page.locator('html')).toHaveAttribute('lang', originalLocale);
    });

    // Blank the form's CSRF token and submit it for real, so the browser renders
    // the 403 shell the way a user would ever see it.
    const showCsrfScreen = async (page: Page): Promise<void> => {
      await openSettings(page);
      await page.locator('form[action$="/settings/locale"] input[name="csrf_token"]')
        .evaluate((element: HTMLInputElement) => { element.value = ''; });
      await Promise.all([
        page.waitForURL('**/settings/locale'),
        page.locator('form[action$="/settings/locale"] button[type="submit"]').click(),
      ]);
      await expect(page.locator('.demo-disabled-card h1')).toBeVisible();
    };

    test('settings_standalone_screens_fit_and_keep_their_action_reachable - the error and CSRF shells obey the same contract', async ({ page, browser }) => {
      for (const width of CONTRACT_WIDTHS) {
        await page.setViewportSize({ width, height: 800 });
        await expectRouteFits(page, `/no-such-route-${UNIQ}`);
        await expect(page.locator('.error-action')).toBeVisible();
        await expectFitsViewport(page, '.error-action');
      }

      // The request-not-verified shell loads its own stylesheets rather than
      // extending base.html, so it has to link button.css itself — without it its
      // single action renders as a bare link with no fill and no padding.
      for (const width of [320, 1440]) {
        await page.setViewportSize({ width, height: 800 });
        await showCsrfScreen(page);
        await expectNoPageOverflow(page);
        const style = await page.locator('.demo-disabled-card .btn-primary').evaluate((element) => {
          const computed = getComputedStyle(element);
          return { background: computed.backgroundColor, paddingInline: parseFloat(computed.paddingLeft) };
        });
        expect(style.background, 'a filled button, not a bare link').not.toBe('rgba(0, 0, 0, 0)');
        expect(style.paddingInline, 'a padded button, not a bare link').toBeGreaterThan(0);
      }

      // The 44px floor is a coarse-pointer guarantee, so measure it where it
      // actually applies rather than on a desktop mouse viewport.
      await withTouchContext(browser, VIEWPORTS.narrow, async (touchPage) => {
        await touchPage.goto(`/no-such-route-${UNIQ}`);
        await expectFitsViewport(touchPage, '.error-action');
        const errorAction = (await touchPage.locator('.error-action').boundingBox())!;
        expect(errorAction.height, 'the one way off the error page must be a real touch target')
          .toBeGreaterThanOrEqual(44);

        await showCsrfScreen(touchPage);
        const csrfAction = (await touchPage.locator('.demo-disabled-card .btn-primary').boundingBox())!;
        expect(csrfAction.height).toBeGreaterThanOrEqual(44);
      });
    });
  });
});
