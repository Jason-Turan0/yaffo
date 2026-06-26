import { loadModule } from './support/load_module.js';

// utils.js reads window.APP_CONFIG.i18n.locale at evaluation time, so the shared
// setup (which defines APP_CONFIG) must run first — it does, via beforeEach.
const loadUtils = async () => (await loadModule('utils.js')).utils;

describe('PHOTO_ORGANIZER.utils.date.format', () => {
  it('formats an ISO date in the application locale', async () => {
    const { date } = await loadUtils();
    const formatted = date.format('2024-03-15T10:30:00Z');
    expect(formatted).toMatch(/2024/);
    expect(formatted).toMatch(/Mar/);
  });

  it('returns an empty string for empty input', async () => {
    const { date } = await loadUtils();
    expect(date.format('')).toBe('');
    expect(date.format(null)).toBe('');
    expect(date.format(undefined)).toBe('');
  });

  it('returns an empty string for an unparseable date', async () => {
    const { date } = await loadUtils();
    expect(date.format('not-a-date')).toBe('');
  });

  it('honors Intl option overrides', async () => {
    const { date } = await loadUtils();
    const formatted = date.format('2024-03-15T10:30:00Z', { month: 'long' });
    expect(formatted).toMatch(/March/);
  });
});

describe('PHOTO_ORGANIZER.utils.date.formatWithTime', () => {
  it('includes the date and a time component', async () => {
    const { date } = await loadUtils();
    const formatted = date.formatWithTime('2024-03-15T10:30:00Z');
    expect(formatted).toMatch(/2024/);
    // A time is rendered as digits separated by a colon (e.g. "10:30 AM").
    expect(formatted).toMatch(/\d:\d/);
  });

  it('returns an empty string for invalid input', async () => {
    const { date } = await loadUtils();
    expect(date.formatWithTime('')).toBe('');
  });
});

describe('PHOTO_ORGANIZER.utils.date.formatRelative', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2024-03-20T12:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders "yesterday" one day back', async () => {
    const { date } = await loadUtils();
    expect(date.formatRelative('2024-03-19T12:00:00Z')).toBe('yesterday');
  });

  it('renders "N days ago" for multi-day deltas within a month', async () => {
    const { date } = await loadUtils();
    expect(date.formatRelative('2024-03-18T12:00:00Z')).toBe('2 days ago');
  });

  it('renders hours for same-day deltas', async () => {
    const { date } = await loadUtils();
    expect(date.formatRelative('2024-03-20T10:00:00Z')).toBe('2 hours ago');
  });

  it('falls back to an absolute date beyond 30 days', async () => {
    const { date } = await loadUtils();
    const formatted = date.formatRelative('2024-01-01T12:00:00Z');
    expect(formatted).toMatch(/2024/);
    expect(formatted).not.toMatch(/ago/);
  });

  it('returns an empty string for invalid input', async () => {
    const { date } = await loadUtils();
    expect(date.formatRelative('')).toBe('');
  });
});
