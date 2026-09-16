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

  async function copyText(value) {
    if (navigator.clipboard?.writeText && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(value);
        return;
      } catch (_error) {
        // 权限被拒时继续尝试兼容复制路径。
      }
    }
    const temporary = document.createElement('textarea');
    temporary.value = value;
    temporary.setAttribute('readonly', '');
    temporary.style.position = 'fixed';
    temporary.style.opacity = '0';
    document.body.appendChild(temporary);
    temporary.select();
    const copied = document.execCommand('copy');
    temporary.remove();
    if (!copied) throw new Error('copy failed');
  }

  function showCopyResult(button, value, copied) {
    window.clearTimeout(Number(button.dataset.copyResetTimer || 0));
    button.classList.toggle('is-copied', copied);
    button.classList.toggle('copy-failed', !copied);
    button.title = copied ? '已复制' : '复制失败，请重试';
    button.setAttribute('aria-label', copied ? `已复制 ${value}` : `复制 ${value} 失败，请重试`);
    const feedback = button.querySelector('[data-copy-feedback]');
    if (feedback) feedback.textContent = copied ? `已复制 ${value}` : `复制 ${value} 失败`;
    button.dataset.copyResetTimer = window.setTimeout(() => {
      button.classList.remove('is-copied', 'copy-failed');
      button.title = '复制 Issue 号';
      button.setAttribute('aria-label', `复制 Issue 号 ${value}`);
      if (feedback) feedback.textContent = '';
    }, 1600);
  }

  document.addEventListener('click', async (event) => {
    const copyButton = event.target.closest('[data-copy-text]');
    if (copyButton) {
      event.preventDefault();
      event.stopPropagation();
      const value = copyButton.dataset.copyText;
      try {
        await copyText(value);
        showCopyResult(copyButton, value, true);
      } catch (_error) {
        showCopyResult(copyButton, value, false);
      }
      return;
    }

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

// “仅待办”切换后立即应用；切回待办时清除可能冲突的完成态精确筛选。
document.querySelectorAll('[data-only-pending-switch]').forEach((toggle) => {
  toggle.addEventListener('change', () => {
    const form = toggle.closest('form');
    if (!form) return;
    const completedInput = form.querySelector('[data-show-completed-input]');
    if (completedInput) completedInput.value = toggle.checked ? '0' : '1';
    const tabInput = form.elements.namedItem('tab');
    if (tabInput) tabInput.value = 'all';
    const statusInput = form.elements.namedItem('issue_status');
    if (statusInput) statusInput.value = '';
    form.requestSubmit();
  });
});

// GET 筛选会重新加载页面；仅在本次筛选跳转中恢复提交前的滚动位置。
(() => {
  const triggers = document.querySelectorAll('[data-preserve-scroll]');
  if (!triggers.length) return;

  const storageKey = (pathname) => `code-inspector-filter-scroll:${pathname}`;

  function saveScrollPosition(trigger, event) {
    if (trigger.matches('a')
        && (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)) return;
    const destination = trigger.matches('a')
      ? new URL(trigger.href, window.location.href)
      : new URL(trigger.action || window.location.href, window.location.href);
    try {
      window.sessionStorage.setItem(storageKey(destination.pathname), JSON.stringify({
        top: window.scrollY,
        savedAt: Date.now(),
      }));
    } catch (_error) {
      // 浏览器禁用存储时保持普通跳转，不影响筛选本身。
    }
  }

  triggers.forEach((trigger) => {
    trigger.addEventListener(trigger.matches('form') ? 'submit' : 'click', (event) => {
      saveScrollPosition(trigger, event);
    });
  });

  try {
    const key = storageKey(window.location.pathname);
    const saved = JSON.parse(window.sessionStorage.getItem(key));
    window.sessionStorage.removeItem(key);
    if (!saved || !Number.isFinite(saved.top) || !Number.isFinite(saved.savedAt)
        || Date.now() - saved.savedAt > 30_000) return;
    if ('scrollRestoration' in window.history) window.history.scrollRestoration = 'manual';
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => window.scrollTo(0, saved.top));
    });
  } catch (_error) {
    // 无有效记录时从页面默认位置开始。
  }
})();

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
