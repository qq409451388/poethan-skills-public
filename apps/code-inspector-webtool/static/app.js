(() => {
  let lastTrigger = null;

  function activateTab(scope, name) {
    if (!scope || !name) return;
    scope.querySelectorAll('[data-tab-target]').forEach((button) => {
      button.classList.toggle('active', button.dataset.tabTarget === name);
      button.setAttribute('aria-selected', button.dataset.tabTarget === name ? 'true' : 'false');
    });
    scope.querySelectorAll('[data-tab-pane]').forEach((pane) => {
      pane.classList.toggle('active', pane.dataset.tabPane === name);
    });
  }

  function openModal(id, trigger) {
    const modal = document.getElementById(id);
    if (!modal) return;
    lastTrigger = trigger || document.activeElement;
    modal.classList.add('open');
    document.body.classList.add('modal-open');
    activateTab(modal, trigger?.dataset.modalTab);
    const focusable = modal.querySelector('input:not([type="hidden"]), textarea, select, button');
    window.setTimeout(() => focusable?.focus(), 0);
  }

  function closeModal(modal) {
    if (!modal) return;
    modal.classList.remove('open');
    if (!document.querySelector('.modal-backdrop.open')) document.body.classList.remove('modal-open');
    lastTrigger?.focus();
  }

  document.addEventListener('click', (event) => {
    const reviewButton = event.target.closest('[data-candidate-review]');
    if (reviewButton) {
      const form = document.getElementById('candidate-review-form');
      if (form) {
        form.action = reviewButton.dataset.reviewUrl;
        form.querySelector('[data-review-status-input]').value = reviewButton.dataset.reviewStatus;
        document.querySelector('[data-candidate-name]').textContent = reviewButton.dataset.candidateTitle;
        document.querySelector('[data-review-status-label]').textContent = reviewButton.dataset.reviewStatus === 'ACCEPTED' ? '已接受' : '已拒绝';
        form.querySelector('textarea').value = '';
      }
    }

    const opener = event.target.closest('[data-modal-open]');
    if (opener) {
      event.preventDefault();
      openModal(opener.dataset.modalOpen, opener);
      return;
    }
    const closer = event.target.closest('[data-modal-close]');
    if (closer) {
      closeModal(closer.closest('.modal-backdrop'));
      return;
    }
    if (event.target.classList.contains('modal-backdrop')) {
      closeModal(event.target);
      return;
    }
    const tab = event.target.closest('[data-tab-target]');
    if (tab) {
      const scope = tab.closest('.modal-backdrop, .implementation-panel, .record-panel');
      activateTab(scope, tab.dataset.tabTarget);
      return;
    }
    const dismiss = event.target.closest('[data-dismiss]');
    if (dismiss) dismiss.parentElement.remove();
  });

  document.querySelectorAll('.clickable-row').forEach((row) => {
    const navigate = (event) => {
      if (event.target.closest('a, button, input, select, textarea')) return;
      window.location.href = row.dataset.href;
    };
    row.addEventListener('click', navigate);
    row.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        navigate(event);
      }
    });
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeModal(document.querySelector('.modal-backdrop.open'));
  });
})();
document.querySelectorAll('form[method="post" i]').forEach((form) => {
  if (form.querySelector('input[name="csrf_token"]')) return;
  const token = document.querySelector('meta[name="csrf-token"]')?.content;
  if (!token) return;
  const input = document.createElement('input');
  input.type = 'hidden';
  input.name = 'csrf_token';
  input.value = token;
  form.prepend(input);
});

// 将任务、问题和 Stage 的变化派发为浏览器事件；桌面提醒必须由用户主动开启。
(() => {
  const endpoint = document.body.dataset.browserEventsUrl;
  if (!endpoint) return;

  const notificationButton = document.querySelector('[data-browser-notifications]');
  const notificationPreference = 'code-inspector-browser-notifications';
  let cursor = null;
  let requestInFlight = false;

  function notificationsEnabled() {
    return 'Notification' in window
      && Notification.permission === 'granted'
      && window.localStorage.getItem(notificationPreference) === 'enabled';
  }

  function updateNotificationButton() {
    if (!notificationButton || !('Notification' in window)) return;
    notificationButton.hidden = false;
    const enabled = notificationsEnabled();
    notificationButton.classList.toggle('enabled', enabled);
    if (enabled) notificationButton.textContent = '桌面提醒已开启';
    else if (Notification.permission === 'denied') notificationButton.textContent = '桌面提醒被浏览器阻止';
    else notificationButton.textContent = '开启桌面提醒';
  }

  notificationButton?.addEventListener('click', async () => {
    if (!('Notification' in window)) return;
    if (notificationsEnabled()) {
      window.localStorage.removeItem(notificationPreference);
      updateNotificationButton();
      return;
    }
    const permission = await Notification.requestPermission();
    if (permission === 'granted') window.localStorage.setItem(notificationPreference, 'enabled');
    updateNotificationButton();
  });
  updateNotificationButton();

  function dispatchBrowserEvent(change) {
    const detail = Object.freeze({ ...change });
    window.dispatchEvent(new CustomEvent('code-inspector:changed', { detail }));
    window.dispatchEvent(new CustomEvent(`code-inspector:${change.kind}-updated`, { detail }));
    if (change.changeType === 'status') {
      window.dispatchEvent(new CustomEvent(`code-inspector:${change.kind}-status-changed`, { detail }));
    }
    if (notificationsEnabled()) {
      new Notification(change.title, {
        body: change.message,
        tag: `code-inspector-${change.eventId}`,
      });
    }
  }

  async function readChanges() {
    if (requestInFlight) return;
    requestInFlight = true;
    try {
      const url = new URL(endpoint, window.location.origin);
      if (cursor !== null) url.searchParams.set('after', cursor);
      const response = await fetch(url, { cache: 'no-store', credentials: 'same-origin' });
      if (!response.ok) return;
      const result = await response.json();
      cursor = result.cursor;
      result.events.forEach(dispatchBrowserEvent);
    } catch (_error) {
      // 本地服务短暂不可用时不打断页面，下一秒继续读取。
    } finally {
      requestInFlight = false;
    }
  }

  readChanges();
  window.setInterval(readChanges, 1000);
})();

// Issue 页面只更新发生变化的区域，避免整页刷新造成滚动跳动和输入丢失。
(() => {
  const marker = document.querySelector('[data-issue-auto-refresh]');
  if (!marker) return;

  const interval = Number(marker.dataset.refreshInterval) || 1000;
  let requestInFlight = false;

  function userIsEditing() {
    const active = document.activeElement;
    return document.hidden
      || document.body.classList.contains('modal-open')
      || document.querySelector('form[data-user-dirty="true"]')
      || active?.matches('input, textarea, select, [contenteditable="true"]');
  }

  document.addEventListener('input', (event) => {
    event.target.closest('form')?.setAttribute('data-user-dirty', 'true');
  });
  document.addEventListener('change', (event) => {
    event.target.closest('form')?.setAttribute('data-user-dirty', 'true');
  });

  function comparableHtml(region) {
    const copy = region.cloneNode(true);
    copy.querySelectorAll('input[name="csrf_token"]').forEach((input) => input.remove());
    copy.querySelectorAll('details').forEach((item) => item.removeAttribute('open'));
    copy.querySelectorAll('[data-tab-target], [data-tab-pane]').forEach((item) => {
      item.classList.remove('active');
      item.removeAttribute('aria-selected');
    });
    return copy.innerHTML;
  }

  function regionState(region) {
    return {
      openDetails: Array.from(region.querySelectorAll('details')).map((item) => item.open),
      activeTabs: Array.from(region.querySelectorAll('.tabs')).map(
        (tabs) => tabs.querySelector('.tab.active')?.dataset.tabTarget || null,
      ),
    };
  }

  function restoreRegionState(region, state) {
    region.querySelectorAll('details').forEach((item, index) => {
      if (state.openDetails[index] !== undefined) item.open = state.openDetails[index];
    });
    region.querySelectorAll('.tabs').forEach((tabs, index) => {
      const target = state.activeTabs[index];
      if (!target) return;
      const scope = tabs.closest('.implementation-panel, .record-panel');
      scope?.querySelectorAll('[data-tab-target]').forEach((button) => {
        button.classList.toggle('active', button.dataset.tabTarget === target);
      });
      scope?.querySelectorAll('[data-tab-pane]').forEach((pane) => {
        pane.classList.toggle('active', pane.dataset.tabPane === target);
      });
    });
  }

  function addCsrfTokens(scope) {
    const token = document.querySelector('meta[name="csrf-token"]')?.content;
    if (!token) return;
    scope.querySelectorAll('form[method="post" i]').forEach((form) => {
      if (form.querySelector('input[name="csrf_token"]')) return;
      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = 'csrf_token';
      input.value = token;
      form.prepend(input);
    });
  }

  function viewportAnchor() {
    const topOffset = document.querySelector('.topbar')?.getBoundingClientRect().bottom || 0;
    const regions = Array.from(document.querySelectorAll('[data-live-region]'));
    const element = regions.find((region) => region.getBoundingClientRect().bottom > topOffset);
    return element ? { key: element.dataset.liveRegion, top: element.getBoundingClientRect().top } : null;
  }

  async function refreshIssue() {
    if (requestInFlight || userIsEditing()) return;
    requestInFlight = true;
    try {
      const response = await fetch(window.location.href, {
        cache: 'no-store',
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'IssueAutoRefresh' },
      });
      if (!response.ok) return;
      const nextDocument = new DOMParser().parseFromString(await response.text(), 'text/html');
      if (!nextDocument.querySelector('[data-issue-auto-refresh]')) return;

      const anchor = viewportAnchor();
      let changed = false;
      document.querySelectorAll('[data-live-region]').forEach((current) => {
        const key = current.dataset.liveRegion;
        const incoming = nextDocument.querySelector(`[data-live-region="${CSS.escape(key)}"]`);
        if (!incoming || comparableHtml(current) === comparableHtml(incoming) || current.contains(document.activeElement)) return;
        const state = regionState(current);
        const replacement = incoming.cloneNode(true);
        current.replaceWith(replacement);
        restoreRegionState(replacement, state);
        addCsrfTokens(replacement);
        changed = true;
      });

      if (changed && anchor) {
        const currentAnchor = document.querySelector(`[data-live-region="${CSS.escape(anchor.key)}"]`);
        if (currentAnchor) window.scrollBy(0, currentAnchor.getBoundingClientRect().top - anchor.top);
      }
    } catch (_error) {
      // 短暂断连时保持当前画面，下一个周期自然重试，不打扰用户。
    } finally {
      requestInFlight = false;
    }
  }

  window.setInterval(refreshIssue, interval);
})();
