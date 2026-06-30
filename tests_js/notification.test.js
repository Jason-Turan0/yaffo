import { loadModule } from './support/load_module.js';

const loadNotification = async () => {
  await loadModule('notification.js');
  return window.notification;
};

beforeEach(() => {
  document.body.innerHTML = '';
  sessionStorage.clear();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  sessionStorage.clear();
});

describe('notification.js', () => {
  it('creates the notification element on load', async () => {
    const notification = await loadNotification();

    const element = document.getElementById('app-notification');
    expect(element).not.toBeNull();
    expect(notification).toBe(window.notification);
  });

  it('shows and auto-hides a notification', async () => {
    const notification = await loadNotification();

    notification.show('Saved', 'success', 100);

    const element = document.getElementById('app-notification');
    expect(element.textContent).toBe('Saved');
    expect(element.className).toBe('notification success visible');

    vi.advanceTimersByTime(100);

    expect(element.classList.contains('visible')).toBe(false);
  });

  it('clears the visible state immediately when hidden', async () => {
    const notification = await loadNotification();

    notification.error('Nope', 1000);
    notification.hide();
    vi.advanceTimersByTime(1000);

    const element = document.getElementById('app-notification');
    expect(element.className).toBe('notification error');
  });

  it('exposes convenience methods and the backward-compatible function', async () => {
    const notification = await loadNotification();
    const showSpy = vi.spyOn(notification, 'show');

    notification.warning('Careful', 0);
    expect(showSpy).toHaveBeenLastCalledWith('Careful', 'warning', 0);

    window.showNotification('FYI', 'info', 0);
    expect(showSpy).toHaveBeenLastCalledWith('FYI', 'info', 0);
  });

  it('queues a flash notification for the next page load', async () => {
    const notification = await loadNotification();

    notification.flash('Imported', 'success', 0);
    document.body.innerHTML = '';
    await loadNotification();

    const element = document.getElementById('app-notification');
    expect(element.textContent).toBe('Imported');
    expect(element.className).toBe('notification success visible');
    expect(sessionStorage.getItem('app-notification-flash')).toBeNull();
  });

  it('ignores malformed queued flash payloads', async () => {
    sessionStorage.setItem('app-notification-flash', 'not json');

    await loadNotification();

    const element = document.getElementById('app-notification');
    expect(element.textContent).toBe('');
    expect(element.className).toBe('notification');
    expect(sessionStorage.getItem('app-notification-flash')).toBeNull();
  });
});
