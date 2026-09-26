import { loadModule } from '../support/load_module.js';

// Ask Yaffo change-plan cards: wording steps from server facts, the approval
// confirmations, and the card after the plan ran (outcome, Undo).

const step = (overrides = {}) => ({
  seq: 0,
  name: 'tag_media_items',
  summary: 'Tag 212 photo(s)',
  count: 212,
  facts: { names: ['Yellowstone'], more: 0 },
  risk: 'low',
  reversible: true,
  state: 'pending',
  error: null,
  starts_job: false,
  job_id: null,
  job_page: null,
  ...overrides,
});

const plan = (overrides = {}) => ({
  id: 12,
  status: 'PENDING',
  risk: 'low',
  count: 212,
  reversible: true,
  read_only: false,
  confirm: null,
  steps: [step()],
  error: null,
  created_at: '2026-09-26T09:12:00+00:00',
  expires_at: '2026-09-26T09:42:00+00:00',
  finished_at: null,
  ...overrides,
});

// Keys plus the values the card interpolates, so wording choices are visible.
const interpolate = (key, options = {}) =>
  [key, options.names, options.album, options.name, options.automation, options.formattedCount, options.error]
    .filter((value) => value !== undefined && value !== '')
    .join(' ');

const render = async (value, extra = {}) => {
  const i18n = window.testHelpers.createTestI18n({ t: interpolate });
  const org = await loadModule('assistant/plan_card.js');
  const handlers = { onApprove: vi.fn(), onDecline: vi.fn(), onUndo: vi.fn() };
  const card = org.assistant.renderPlanCard(value, { i18n, ...handlers, ...extra });
  document.body.replaceChildren(card);
  return { card, ...handlers };
};

const buttons = (card) => [...card.querySelectorAll('button')];
const byText = (card, key) => buttons(card).find((b) => b.textContent === key);

describe('plan card', () => {
  it('words each step from the server facts, not the model', async () => {
    const { card } = await render(plan({
      steps: [
        step({ name: 'create_album', count: 1, facts: { album: 'Yellowstone 2019' } }),
        step({ seq: 1, name: 'add_to_album', count: 212, facts: { album: 'Yellowstone 2019', new_album: true } }),
        step({ seq: 2, facts: { names: ['a', 'b', 'c'], more: 2 } }),
        step({ seq: 3, name: 'set_favorites', count: 4, facts: { value: true } }),
        step({ seq: 4, name: 'set_location_names', count: 4, facts: {} }),
        step({ seq: 5, name: 'delete_album', summary: 'Delete an album', count: 1, facts: { album: null } }),
      ],
    }));

    const lines = [...card.querySelectorAll('.assistant-plan-step')].map((li) => li.textContent);
    expect(lines).toEqual([
      'assistant:plan.steps.create_album Yellowstone 2019 1',
      'assistant:plan.steps.add_to_album Yellowstone 2019 212',
      'assistant:plan.steps.tag_media_items “a”, “b”, “c”, and assistant:plan.more 212',
      'assistant:plan.steps.set_favorites_on 4',
      'assistant:plan.steps.set_location_names_mixed 4',
      'Delete an album',  // the album is gone: the server's own summary
    ]);
    expect(card.querySelector('ol')).not.toBeNull();
    expect(card.querySelector('.assistant-plan-facts').textContent).toBe('assistant:plan.reversible');
  });

  it('approves and declines with one click when no confirmation is needed', async () => {
    const value = plan();
    const { card, onApprove, onDecline } = await render(value);

    byText(card, 'assistant:plan.approve').click();
    expect(onApprove).toHaveBeenCalledWith(value, null);
    expect(buttons(card).every((b) => b.disabled)).toBe(true);

    const second = await render(value);
    byText(second.card, 'assistant:plan.decline').click();
    expect(second.onDecline).toHaveBeenCalledWith(value);
    expect(onDecline).not.toHaveBeenCalled();
  });

  it('asks for a tick above the threshold and sends the count', async () => {
    const { card, onApprove } = await render(plan({ confirm: 'check', count: 1840 }));
    const approve = byText(card, 'assistant:plan.approve');
    expect(approve.disabled).toBe(true);

    const box = card.querySelector('input[type="checkbox"]');
    box.checked = true;
    box.dispatchEvent(new Event('change'));
    expect(approve.disabled).toBe(false);
    approve.click();
    expect(onApprove).toHaveBeenCalledWith(expect.objectContaining({ id: 12 }), 1840);
  });

  it('asks for the count to be typed for high-risk changes', async () => {
    const { card, onApprove } = await render(plan({
      risk: 'high', reversible: false, confirm: 'type', count: 38,
      steps: [step({ name: 'delete_media_items', risk: 'high', reversible: false, count: 38, facts: {} })],
    }));
    expect(card.classList.contains('assistant-plan-high')).toBe(true);
    expect(card.querySelector('.assistant-plan-facts').textContent)
      .toBe('assistant:plan.irreversible · assistant:plan.highRisk');
    const approve = byText(card, 'assistant:plan.approveHigh');
    const input = card.querySelector('input[type="text"]');

    input.value = '37';
    input.dispatchEvent(new Event('input'));
    expect(approve.disabled).toBe(true);
    input.value = ' 38 ';
    input.dispatchEvent(new Event('input'));
    expect(approve.disabled).toBe(false);
    approve.click();
    expect(onApprove).toHaveBeenCalledWith(expect.anything(), 38);
  });

  it('confirms one automation run without calling it one changed item', async () => {
    const { card, onApprove } = await render(plan({
      risk: 'high', reversible: false, confirm: 'type', count: 1,
      steps: [step({ name: 'run_automation', risk: 'high', reversible: false, count: 1,
        facts: { automation: 'Export photo tag' } })],
    }));
    expect(card.querySelector('.assistant-plan-confirm span').textContent)
      .toBe('assistant:plan.confirmRun 1');
    const approve = byText(card, 'assistant:plan.approveHigh');
    expect(approve.disabled).toBe(true);
    const input = card.querySelector('input[type="text"]');
    input.value = '1';
    input.dispatchEvent(new Event('input'));
    approve.click();
    expect(onApprove).toHaveBeenCalledWith(expect.anything(), 1);
  });

  it('keeps the buttons off while busy', async () => {
    const { card } = await render(plan(), { busy: true });
    expect(buttons(card).every((b) => b.disabled)).toBe(true);
    expect(card.querySelector('.hint-text').textContent).toBe('assistant:plan.waitForReply');
  });

  it('shows what ran and offers Undo', async () => {
    const { card, onUndo } = await render(plan({
      status: 'PARTIAL',
      steps: [step({ state: 'done' }), step({ seq: 1, state: 'failed', error: 'disk full' }),
        step({ seq: 2, state: 'not_run' })],
      finished_at: '2026-09-26T09:14:00+00:00',
    }));
    expect(card.querySelector('.assistant-plan-outcome').textContent).toBe('assistant:plan.partial disk full');
    expect([...card.querySelectorAll('.assistant-plan-step')].map((li) => li.className))
      .toEqual(['assistant-plan-step assistant-plan-step-done', 'assistant-plan-step assistant-plan-step-failed',
        'assistant-plan-step assistant-plan-step-not_run']);
    const undo = byText(card, 'assistant:plan.undoDone');
    undo.click();
    expect(onUndo).toHaveBeenCalled();
    expect(undo.disabled).toBe(true);
  });

  it('offers nothing for declined, expired, undone or irreversible plans', async () => {
    for (const status of ['DECLINED', 'EXPIRED', 'UNDONE']) {
      const { card } = await render(plan({ status }));
      expect(buttons(card)).toHaveLength(0);
      expect(card.querySelector('.assistant-plan-outcome').textContent).toContain('assistant:plan.');
    }
    const { card } = await render(plan({ status: 'EXECUTED', reversible: false }));
    expect(buttons(card)).toHaveLength(0);
  });

  it('words people changes by the faces they move, and says nothing about files', async () => {
    const { card } = await render(plan({
      risk: 'high', reversible: false, confirm: 'type', count: 2,
      steps: [
        step({ name: 'create_person', count: 1, facts: { person: 'Chase' } }),
        step({ seq: 1, name: 'merge_people', risk: 'high', reversible: false, count: 2,
          facts: { person: 'Dup', target: 'Chase', faces: 2 } }),
        step({ seq: 2, name: 'delete_person', risk: 'high', reversible: false, count: 1,
          facts: { person: 'Empty', faces: 0 } }),
        step({ seq: 3, name: 'rename_person', summary: 'Rename a person', count: 1, facts: { person: null } }),
      ],
    }));
    const lines = [...card.querySelectorAll('.assistant-plan-step')].map((li) => li.textContent);
    expect(lines).toEqual([
      'assistant:plan.steps.create_person 1',
      'assistant:plan.steps.merge_people 2',
      'assistant:plan.steps.delete_person_noFaces 0',
      'Rename a person',
    ]);
    expect(card.querySelector('.assistant-plan-facts').textContent).toBe('assistant:plan.irreversible');
  });

  it('says background work runs on, and links to where its progress shows', async () => {
    const sync = { name: 'run_sync', count: 1, facts: {}, risk: 'medium', reversible: false, starts_job: true,
      job_page: '/utilities/index-photos' };
    const pending = await render(plan({ reversible: false, steps: [step(sync)] }));
    expect(pending.card.querySelector('.assistant-plan-facts').textContent)
      .toBe('assistant:plan.irreversible · assistant:plan.background');
    expect(pending.card.querySelector('.assistant-plan-job')).toBeNull();  // nothing started yet

    const { card } = await render(plan({
      status: 'EXECUTED', reversible: false, finished_at: '2026-09-26T09:14:00+00:00',
      steps: [step({ ...sync, state: 'done', job_id: 'job-1' })],
    }));
    const link = card.querySelector('a.assistant-plan-job');
    expect(link.getAttribute('href')).toBe('/utilities/index-photos');
    expect(link.textContent).toBe('assistant:plan.jobProgress');
  });

  it('links a queued automation to its Run history without claiming a job id', async () => {
    const { card } = await render(plan({
      status: 'EXECUTED', risk: 'high', reversible: false,
      steps: [step({ name: 'run_automation', count: 1,
        facts: { automation: 'Export photo tag' }, starts_job: false,
        job_page: '/utilities/automations/export_photo_tag', state: 'done', job_id: null })],
    }));
    expect(card.querySelector('.assistant-plan-facts').textContent)
      .toBe('assistant:plan.irreversible · assistant:plan.background');
    const link = card.querySelector('a.assistant-plan-job');
    expect(link.getAttribute('href')).toBe('/utilities/automations/export_photo_tag');
    expect(link.textContent).toBe('assistant:plan.runHistory');
    expect(card.querySelector('.assistant-plan-step').textContent)
      .toBe('assistant:plan.steps.run_automation Export photo tag 1');
    expect(card.querySelectorAll('button')).toHaveLength(0);
  });

  it('words automation switches with the automation\'s name', async () => {
    const { card } = await render(plan({ steps: [
      step({ name: 'set_automation_enabled', count: 1, facts: { automation: 'File Sync', value: false } }),
      step({ seq: 1, name: 'set_automation_enabled', summary: 'Turn on automation x', count: 1,
        facts: { automation: null, value: true } }),
    ] }));
    const lines = [...card.querySelectorAll('.assistant-plan-step')].map((li) => li.textContent);
    expect(lines).toEqual(['assistant:plan.steps.set_automation_enabled_off File Sync 1', 'Turn on automation x']);
  });

  it('offers the script under a multi-step plan', async () => {
    const { card } = await render(plan({ steps: [step(), step({ seq: 1 })] }), { script: 'create_album("x")' });
    expect(card.querySelector('.assistant-tool-script pre').textContent).toBe('create_album("x")');
  });
});
