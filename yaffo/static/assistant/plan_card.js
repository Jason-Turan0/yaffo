// @ts-check

/**
 * Ask Yaffo change-plan cards (docs/development/ai-assistant.md → Action cards).
 *
 * A run_script run that recorded changes carries its plan on the tool event
 * (`payload.plan`, the server's PlanView). This module draws that plan as a card:
 * what will happen, step by step, worded here in the user's language from each
 * step's name, count and facts (never from the model's text), whether it can be
 * undone, and Approve / Decline. After approval the same card shows what ran, and
 * Undo. The card holds no state of its own: the host re-renders it from the next
 * poll after every action.
 *
 * Approval confirmation mirrors the server's rule: `confirm: "type"` (high risk)
 * asks for the item count to be typed, `confirm: "check"` (more items than the
 * Settings threshold) asks for a tick. Either way Approve sends the count, and the
 * server refuses without it.
 */

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
const planCards = window.PHOTO_ORGANIZER.assistant =
    /** @type {AssistantNamespace} */ (window.PHOTO_ORGANIZER.assistant || {});

// Steps that change files on disk, noted on the card.
const FILE_STEPS = new Set(['rename_files', 'move_media_items', 'delete_media_items']);

/**
 * @param {string} tag
 * @param {string} [className]
 * @param {string} [text]
 * @returns {HTMLElement}
 */
const planEl = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
};

/**
 * One step, worded from its facts. Falls back to the server's English summary
 * when a fact the wording needs is missing (e.g. an album that no longer exists).
 * @param {I18nService} i18n
 * @param {AssistantPlanStep} step
 * @returns {string}
 */
const stepText = (i18n, step) => {
    const facts = step.facts || {};
    const count = Number(step.count) || 0;
    const quoted = (/** @type {string[]} */ names) => names.map((name) => `“${name}”`);
    /** @type {Record<string, unknown>} */
    const values = { count, formattedCount: i18n.number(count), defaultValue: step.summary };
    let key = step.name;
    if (Array.isArray(facts.names)) {
        const names = quoted(facts.names);
        const more = Number(facts.more) || 0;
        if (more) names.push(i18n.t('assistant:plan.more', { count: more }));
        if (names.length) values.names = i18n.list(names);
        else if (step.name === 'assign_faces') key = 'assign_faces_noNames';
    }
    if (['create_album', 'update_album', 'add_to_album', 'remove_from_album', 'delete_album',
        'set_album_cover', 'reorder_album'].includes(step.name)) {
        if (!facts.album) return step.summary;
        values.album = facts.album;
        values.name = facts.name || '';
        if (step.name === 'set_album_cover' && facts.cleared) key = 'set_album_cover_clear';
    }
    if (['create_person', 'rename_person', 'merge_people', 'delete_person'].includes(step.name)) {
        if (!facts.person || (step.name === 'merge_people' && !facts.target)) return step.summary;
        values.person = facts.person;
        values.target = facts.target || '';
        values.name = facts.name || '';
        if (step.name === 'merge_people' || step.name === 'delete_person') {
            // Worded by the faces the change moves or unassigns.
            const faces = Number(facts.faces) || 0;
            values.count = faces;
            values.formattedCount = i18n.number(faces);
            if (!faces) key = `${step.name}_noFaces`;
        }
    }
    if (step.name === 'set_automation_enabled') {
        if (!facts.automation) return step.summary;
        values.automation = facts.automation;
        key = facts.value ? 'set_automation_enabled_on' : 'set_automation_enabled_off';
    }
    if (step.name === 'cancel_job') {
        if (!facts.job) return step.summary;
        values.job = facts.job;
    }
    if (step.name === 'run_automation') {
        if (!facts.automation) return step.summary;
        values.automation = facts.automation;
        values.scope = facts.scope || '';
    }
    if (['set_favorites', 'set_media_dates', 'set_location_names'].includes(step.name)) {
        if (!('value' in facts)) key = `${step.name}_mixed`;
        else if (facts.value === null) key = `${step.name}_clear`;
        else if (step.name === 'set_favorites') key = facts.value ? 'set_favorites_on' : 'set_favorites_off';
        else if (step.name === 'set_media_dates') values.date = i18n.date(String(facts.value), { dateStyle: 'medium' });
        else values.name = String(facts.value);
    }
    return i18n.t(`assistant:plan.steps.${key}`, values);
};

/**
 * The line under a finished plan: what ran, what failed, what was undone.
 * @param {I18nService} i18n
 * @param {AssistantPlan} plan
 * @returns {string}
 */
const outcomeText = (i18n, plan) => {
    const done = plan.steps.filter((s) => s.state === 'done' || s.state === 'undone').length;
    const failed = plan.steps.find((s) => s.state === 'failed');
    const time = plan.finished_at ? i18n.date(plan.finished_at, { timeStyle: 'short' }) : '';
    switch (plan.status) {
    case 'EXECUTED':
        return i18n.t('assistant:plan.executed', { time });
    case 'PARTIAL':
        return i18n.t('assistant:plan.partial', {
            done, total: plan.steps.length, step: (failed?.seq ?? 0) + 1, error: failed?.error || '',
        });
    case 'FAILED':
        return i18n.t('assistant:plan.failed', { error: failed?.error || plan.error || '' });
    case 'DECLINED':
        return i18n.t('assistant:plan.declined');
    case 'EXPIRED':
        return i18n.t('assistant:plan.expired');
    case 'UNDONE':
        return plan.error
            ? i18n.t('assistant:plan.undoneWithErrors', { error: plan.error })
            : i18n.t('assistant:plan.undone');
    case 'APPROVED':
        return i18n.t('assistant:plan.applying');
    default:
        return '';
    }
};

/**
 * @param {AssistantPlan} plan
 * @param {AssistantPlanCardOptions} options
 * @returns {HTMLElement}
 */
planCards.renderPlanCard = (plan, options) => {
    const { i18n, script = '', busy = false } = options;
    const card = planEl('section', `assistant-plan assistant-plan-${plan.status.toLowerCase()}`);
    card.dataset.planId = String(plan.id);
    if (plan.risk === 'high') card.classList.add('assistant-plan-high');
    const pending = plan.status === 'PENDING';

    const title = pending
        ? i18n.t('assistant:plan.proposed', { count: plan.steps.length })
        : i18n.t('assistant:plan.changes', { count: plan.steps.length });
    card.appendChild(planEl('h3', 'assistant-plan-title', title));

    const steps = planEl(plan.steps.length > 1 ? 'ol' : 'ul', 'assistant-plan-steps');
    for (const step of plan.steps) {
        const item = planEl('li', `assistant-plan-step assistant-plan-step-${step.state}`, stepText(i18n, step));
        if (!pending) item.title = i18n.t(`assistant:plan.stepState.${step.state}`);
        steps.appendChild(item);
    }
    card.appendChild(steps);

    const facts = planEl('p', 'assistant-plan-facts');
    const notes = [i18n.t(plan.read_only ? 'assistant:plan.readOnly'
        : plan.reversible ? 'assistant:plan.reversible' : 'assistant:plan.irreversible')];
    if (plan.steps.some((s) => FILE_STEPS.has(s.name))) notes.push(i18n.t('assistant:plan.highRisk'));
    if (plan.steps.some((s) => s.starts_job || s.name === 'run_automation')) {
        notes.push(i18n.t('assistant:plan.background'));
    }
    facts.textContent = notes.join(' · ');
    card.appendChild(facts);

    if (script && plan.steps.length > 1) {
        const details = planEl('details', 'assistant-tool-script');
        details.appendChild(planEl('summary', undefined, i18n.t('assistant:activity.showScript')));
        details.appendChild(planEl('pre', 'assistant-tool-detail', script));
        card.appendChild(details);
    }

    const outcome = outcomeText(i18n, plan);
    if (outcome) card.appendChild(planEl('p', 'assistant-plan-outcome', outcome));

    // Background work that ran: where its progress or run history shows.
    const jobPages = new Set(plan.steps.filter((s) => s.job_id && s.job_page).map((s) => String(s.job_page)));
    for (const page of jobPages) {
        const link = /** @type {HTMLAnchorElement} */ (planEl('a', 'assistant-plan-job', i18n.t('assistant:plan.jobProgress')));
        link.href = page;
        card.appendChild(link);
    }
    const runPages = new Set(plan.steps.filter((s) => s.name === 'run_automation'
        && s.state === 'done' && s.job_page).map((s) => String(s.job_page)));
    for (const page of runPages) {
        const link = /** @type {HTMLAnchorElement} */ (planEl('a', 'assistant-plan-job',
            i18n.t('assistant:plan.runHistory')));
        link.href = page;
        card.appendChild(link);
    }

    const actions = planEl('div', 'assistant-plan-actions');
    if (pending) {
        /** @type {() => boolean} */
        let confirmed = () => true;
        const approve = /** @type {HTMLButtonElement} */ (planEl('button', 'btn-primary btn-sm',
            i18n.t(plan.risk === 'high' ? 'assistant:plan.approveHigh' : 'assistant:plan.approve')));
        approve.type = 'button';
        const decline = /** @type {HTMLButtonElement} */ (planEl('button', 'btn-secondary btn-sm',
            i18n.t('assistant:plan.decline')));
        decline.type = 'button';
        const refresh = () => { approve.disabled = busy || !confirmed(); };

        if (plan.confirm === 'type') {
            const label = planEl('label', 'assistant-plan-confirm');
            const confirmationKey = plan.steps.length === 1 && plan.steps[0].name === 'run_automation'
                ? 'assistant:plan.confirmRun' : 'assistant:plan.confirmType';
            label.appendChild(planEl('span', undefined,
                i18n.t(confirmationKey, { count: plan.count, formattedCount: i18n.number(plan.count) })));
            const input = /** @type {HTMLInputElement} */ (planEl('input'));
            input.type = 'text';
            input.inputMode = 'numeric';
            input.autocomplete = 'off';
            input.disabled = busy;
            input.addEventListener('input', refresh);
            label.appendChild(input);
            card.appendChild(label);
            confirmed = () => Number(input.value.trim()) === plan.count;
        } else if (plan.confirm === 'check') {
            const label = planEl('label', 'checkbox-label assistant-plan-confirm');
            const box = /** @type {HTMLInputElement} */ (planEl('input'));
            box.type = 'checkbox';
            box.disabled = busy;
            box.addEventListener('change', refresh);
            label.appendChild(box);
            label.appendChild(document.createTextNode(
                i18n.t('assistant:plan.confirmCheck', { count: plan.count, formattedCount: i18n.number(plan.count) })));
            card.appendChild(label);
            confirmed = () => box.checked;
        }
        refresh();
        decline.disabled = busy;
        approve.addEventListener('click', () => {
            approve.disabled = true;
            decline.disabled = true;
            options.onApprove(plan, plan.confirm ? plan.count : null);
        });
        decline.addEventListener('click', () => {
            approve.disabled = true;
            decline.disabled = true;
            options.onDecline(plan);
        });
        actions.append(decline, approve);
        if (busy) actions.appendChild(planEl('span', 'hint-text', i18n.t('assistant:plan.waitForReply')));
    } else if ((plan.status === 'EXECUTED' || plan.status === 'PARTIAL') && plan.reversible && !plan.read_only) {
        const undo = /** @type {HTMLButtonElement} */ (planEl('button', 'btn-secondary btn-sm',
            i18n.t(plan.status === 'PARTIAL' ? 'assistant:plan.undoDone' : 'assistant:plan.undo')));
        undo.type = 'button';
        undo.disabled = busy;
        undo.addEventListener('click', () => {
            undo.disabled = true;
            options.onUndo(plan);
        });
        actions.appendChild(undo);
    }
    if (actions.childElementCount) card.appendChild(actions);
    return card;
};
