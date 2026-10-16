// 保持真实百分比：事件只做提示，只有评估值变化才移动进度端点。
function animateProgressChanges(previous, incoming) {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const oldBars = Array.from(previous.querySelectorAll('[role="progressbar"]'));
  incoming.querySelectorAll('[role="progressbar"]').forEach((bar, index) => {
    const oldValue = Number(oldBars[index]?.getAttribute('aria-valuenow'));
    const value = Number(bar.getAttribute('aria-valuenow'));
    const fill = bar.querySelector('.issue-progress-fill');
    const progress = bar.closest('.issue-progress');
    function showDamage(until) {
      progress.dataset.damageUntil = String(until);
      progress.style.setProperty('--damage-delay', `${-(4000 - (until - Date.now()))}ms`);
      progress.classList.add('is-damaged');
      window.setTimeout(() => {
        if (Number(progress.dataset.damageUntil) === until) {
          progress.classList.remove('is-damaged');
          delete progress.dataset.damageUntil;
        }
      }, Math.max(0, until - Date.now()));
    }
    const previousDamage = Number(oldBars[index]?.closest('.issue-progress')?.dataset.damageUntil || 0);
    if (previousDamage > Date.now()) showDamage(previousDamage);
    if (!oldBars[index] || !fill || !Number.isFinite(oldValue) || oldValue === value) return;
    const increasing = value > oldValue;
    const gainDuration = Math.max(1100, (value - oldValue) * 30);
    if (increasing) {
      const label = progress.querySelector('.issue-progress-label strong');
      if (label?.firstChild) {
        label.firstChild.nodeValue = String(oldValue);
        for (let step = 1; step <= value - oldValue; step += 1) {
          const elapsed = 1 - Math.cbrt(1 - step / (value - oldValue));
          const displayed = oldValue + step;
          window.setTimeout(() => {
            if (label.isConnected) label.firstChild.nodeValue = String(displayed);
          }, gainDuration * elapsed);
        }
      }
    }
    fill.animate([{ width: `${oldValue}%` }, { width: `${value}%` }], {
      duration: increasing ? gainDuration : 240,
      easing: increasing ? 'cubic-bezier(.33,1,.68,1)' : 'cubic-bezier(.2,.8,.2,1)',
    });
    if (increasing) {
      const width = bar.getBoundingClientRect().width;
      if (!width) return;
      const layer = document.createElement('span');
      layer.className = 'progress-particle-layer';
      layer.setAttribute('aria-hidden', 'true');
      bar.appendChild(layer);
      const flights = Array.from({ length: 22 }, (_, particleIndex) => {
        const dot = document.createElement('i');
        dot.className = 'progress-gain-particle';
        const size = 2 + particleIndex % 3;
        dot.style.width = `${size}px`;
        dot.style.height = `${size}px`;
        layer.appendChild(dot);
        const delay = particleIndex * 32;
        const duration = 400;
        const elapsed = Math.min(1, (delay + duration) / gainDuration);
        const arrival = 1 - Math.pow(1 - elapsed, 3);
        const target = width * (oldValue + (value - oldValue) * arrival) / 100;
        const origin = Math.max(target + 10, width - (particleIndex % 5) * 6);
        return dot.animate([
          { transform: `translate(${origin}px, ${(particleIndex % 3 - 1) * 2}px) scale(.5)`, opacity: 0 },
          { opacity: 1, offset: .16 },
          { transform: `translate(${target}px, 0) scale(1.2)`, opacity: .95, offset: .9 },
          { transform: `translate(${target - 2}px, 0) scale(.2)`, opacity: 0 },
        ], { duration, delay, easing: 'cubic-bezier(.25,.4,.4,1)', fill: 'both' });
      });
      Promise.allSettled(flights.map((animation) => animation.finished)).then(() => layer.remove());
    } else {
      showDamage(Date.now() + 4000);
      const damage = document.createElement('span');
      damage.className = 'progress-damage-trail';
      damage.style.width = `${oldValue}%`;
      damage.setAttribute('aria-hidden', 'true');
      bar.prepend(damage);
      const drain = damage.animate([
        { width: `${oldValue}%`, opacity: 1 },
        { width: `${value}%`, opacity: .25 },
      ], { duration: 850, delay: 350, easing: 'cubic-bezier(.55,0,.8,.45)', fill: 'both' });
      bar.animate([
        { transform: 'translateX(0)' }, { transform: 'translateX(-2px)' },
        { transform: 'translateX(2px)' }, { transform: 'translateX(-1px)' },
        { transform: 'translateX(0)' },
      ], { duration: 260, easing: 'ease-out' });
      Promise.allSettled([drain.finished]).then(() => damage.remove());
    }
  });
}

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
    if (opener && document.getElementById(opener.dataset.modalOpen)) {
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
    const progressSwitch = event.target.closest('[data-workflow-progress]');
    if (progressSwitch) {
      closeModal(document.getElementById('issue-workflow-modal'));
      openModal('issue-progress-modal');
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
    copy.querySelectorAll('.record-expander').forEach((item) => item.remove());
    copy.querySelectorAll('.record-over-limit, .records-expanded').forEach((item) => {
      item.classList.remove('record-over-limit', 'records-expanded');
    });
    // 记录视图是纯客户端状态：不归一化就会被判成"内容变了"，导致每秒重建整个区域。
    copy.querySelectorAll('.record-panel').forEach((panel) => {
      panel.setAttribute('data-records-view', 'all');
    });
    // classList 改写会顺带压缩属性里的空白，先统一两侧，避免被误判成内容变化。
    copy.querySelectorAll('[class]').forEach((element) => {
      const value = element.getAttribute('class');
      const normalized = value.trim().replace(/\s+/g, ' ');
      if (normalized !== value) element.setAttribute('class', normalized);
    });
    return copy.innerHTML;
  }

  function recordPanels(scope) {
    const ownPanel = scope.matches?.('.record-panel') ? [scope] : [];
    return [...ownPanel, ...scope.querySelectorAll('.record-panel')];
  }

  function regionState(region) {
    return {
      rootDetailsOpen: region.matches('details') ? region.open : null,
      openDetails: Array.from(region.querySelectorAll('details')).map((item) => item.open),
      activeTabs: Array.from(region.querySelectorAll('.tabs')).map(
        (tabs) => tabs.querySelector('.tab.active')?.dataset.tabTarget || null,
      ),
      recordViews: recordPanels(region).map((panel) => ({
        view: panel.dataset.recordsView || 'all',
        expanded: panel.classList.contains('records-expanded'),
      })),
    };
  }

  function restoreRegionState(region, state) {
    if (state.rootDetailsOpen !== null && region.matches('details')) region.open = state.rootDetailsOpen;
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
    recordPanels(region).forEach((panel, index) => {
      const saved = state.recordViews?.[index];
      if (!saved) return;
      panel.dataset.recordsView = saved.view;
      panel.classList.toggle('records-expanded', saved.expanded);
      applyRecordView(panel);
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

  const RECORD_PREVIEW_LIMIT = 5;

  function recordInView(record, view) {
    if (view === 'discussion') return record.hasAttribute('data-in-discussion');
    if (view === 'history') return record.hasAttribute('data-in-history');
    if (view === 'decisions') return record.hasAttribute('data-in-decisions');
    return record.hasAttribute('data-in-all');
  }

  // 四个标签共用同一份记录 DOM：切换视图只改 data-records-view，由 CSS 过滤，
  // 因此预览条数必须按"当前视图里可见的记录"重算，否则筛选后可能一条都看不到。
  function applyRecordView(panel) {
    const view = panel.dataset.recordsView || 'all';
    const stream = panel.querySelector('.record-stream');
    if (!stream) return;
    const records = Array.from(stream.querySelectorAll('.event'));
    const matching = records.filter((record) => recordInView(record, view));
    const empty = stream.querySelector('.record-empty');
    if (empty) empty.hidden = matching.length > 0;
    const expanded = panel.classList.contains('records-expanded');
    const expander = stream.querySelector(':scope > .record-expander');
    if (matching.length <= RECORD_PREVIEW_LIMIT) {
      records.forEach((record) => record.classList.remove('record-over-limit'));
      panel.classList.remove('records-expanded');
      expander?.remove();
      return;
    }
    const matchingSet = new Set(matching);
    matching.forEach((record, index) => {
      record.classList.toggle('record-over-limit', !expanded && index >= RECORD_PREVIEW_LIMIT);
    });
    records.forEach((record) => {
      if (!matchingSet.has(record)) record.classList.remove('record-over-limit');
    });
    if (expander) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'record-expander';
    button.innerHTML = '<span class="record-expander-more">点击展开完整内容</span>'
      + '<span class="record-expander-less">收起</span>';
    stream.append(button);
  }

  function renderRecordPreviews(scope) {
    recordPanels(scope).forEach((panel) => applyRecordView(panel));
  }

  document.addEventListener('click', (event) => {
    const expander = event.target.closest('.record-expander');
    if (!expander) return;
    const panel = expander.closest('.record-panel');
    if (!panel) return;
    panel.classList.toggle('records-expanded');
    applyRecordView(panel);
  });

  // 记录标签只切换 data-records-view（CSS 负责过滤），所以必须和 applyRecordView 同作用域。
  document.addEventListener('click', (event) => {
    const tab = event.target.closest('.record-panel [data-tab-target]');
    if (!tab) return;
    const panel = tab.closest('.record-panel');
    panel.dataset.recordsView = tab.dataset.tabTarget;
    applyRecordView(panel);
  });

  function openHashTarget() {
    if (window.location.hash.length < 2) return;
    let target = null;
    try {
      target = document.querySelector(window.location.hash);
    } catch (_error) {
      return;
    }
    if (target?.matches('details.collapsible-panel')) target.open = true;
    const queueKeys = ['pending-review', 'confirmation', 'blocked', 'candidate-review'];
    const key = window.location.hash.slice(1);
    const modal = target?.matches('.modal-backdrop') ? target
      : queueKeys.includes(key) ? document.getElementById(`${key}-modal`) : null;
    if (modal) openModal(modal.id);
  }

  // tab 行要贴在吸顶标题下面，标题高度随语言和换行变化，所以按实际高度算偏移。
  function syncStickyOffsets() {
    const topbar = document.querySelector('.topbar');
    const top = Math.round(topbar?.getBoundingClientRect().height || 58);
    document.documentElement.style.setProperty('--sticky-head-top', `${top}px`);
    document.querySelectorAll('.record-panel').forEach((panel) => {
      const summary = panel.querySelector(':scope > summary');
      if (summary) {
        panel.style.setProperty('--record-tabs-top', `${top + Math.round(summary.offsetHeight)}px`);
      }
    });
  }

  renderRecordPreviews(document);
  openHashTarget();
  syncStickyOffsets();
  window.addEventListener('hashchange', openHashTarget);
  window.addEventListener('resize', syncStickyOffsets);

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
        if (current.contains(document.activeElement)) return;
        if (!incoming) {
          current.remove();
          changed = true;
          syncStickyOffsets();
          return;
        }
        if (comparableHtml(current) === comparableHtml(incoming)) return;
        const state = regionState(current);
        const replacement = incoming.cloneNode(true);
        current.replaceWith(replacement);
        animateProgressChanges(current, replacement);
        restoreRegionState(replacement, state);
        renderRecordPreviews(replacement);
        syncStickyOffsets();
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

// 将旧 Stage 描述分成玻璃碎片，露出下面的新描述；不影响链接和读屏内容。
function animateStageReveal(snapshot, row) {
  const latest = row.querySelector('.ranking-stage-current, .ranking-stage-inactive');
  if (!snapshot || !latest || snapshot.text === latest.textContent.trim()
      || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const wrap = document.createElement('span');
  wrap.className = 'stage-reveal-wrap';
  latest.before(wrap);
  wrap.appendChild(latest);
  const overlay = document.createElement('span');
  overlay.className = 'stage-glass-overlay';
  overlay.setAttribute('aria-hidden', 'true');
  overlay.setAttribute('inert', '');
  overlay.style.width = `${snapshot.width}px`;
  overlay.style.height = `${snapshot.height}px`;
  wrap.appendChild(overlay);
  const shapes = [
    ['0 0, 38% 0, 48% 46%', -20, -18, -12],
    ['38% 0, 72% 0, 48% 46%', 5, -27, 8],
    ['72% 0, 100% 0, 100% 48%, 48% 46%', 28, -14, 14],
    ['100% 48%, 100% 100%, 68% 100%, 48% 46%', 31, 20, 16],
    ['68% 100%, 31% 100%, 48% 46%', 1, 30, -8],
    ['31% 100%, 0 100%, 0 60%, 48% 46%', -25, 22, -16],
    ['0 60%, 0 0, 48% 46%', -32, -2, -10],
  ];
  const animations = shapes.map(([polygon, x, y, rotation], index) => {
    const shard = document.createElement('span');
    shard.className = 'stage-glass-shard';
    shard.style.clipPath = `polygon(${polygon})`;
    shard.appendChild(snapshot.node.cloneNode(true));
    overlay.appendChild(shard);
    return shard.animate([
      { transform: 'translate(0, 0) rotate(0deg)', opacity: 1, filter: 'brightness(1)' },
      { transform: 'translate(0, 0) rotate(0deg)', opacity: 1, filter: 'brightness(1.25)', offset: .16 },
      { transform: `translate(${x}px, ${y}px) rotate(${rotation}deg)`, opacity: 0, filter: 'brightness(1.4)' },
    ], { duration: 850, delay: index * 18, easing: 'cubic-bezier(.22,.65,.32,1)', fill: 'forwards' });
  });
  const reveal = latest.animate([
    { opacity: 0, transform: 'translateY(5px)', filter: 'blur(2px)' },
    { opacity: 1, transform: 'translateY(0)', filter: 'blur(0)' },
  ], { duration: 650, delay: 160, easing: 'ease-out', fill: 'backwards' });
  Promise.allSettled([...animations, reveal].map((animation) => animation.finished)).then(() => {
    overlay.remove();
    if (wrap.isConnected) wrap.replaceWith(latest);
  });
}

// 首页复用审计事件流：仅对本次页面打开后收到的提交做动效提示。
(() => {
  if (!document.querySelector('[data-active-issues]')) return;
  const submissions = new Map();
  const submissionActions = new Set(['design.submit', 'stage.submit', 'implementation.submit']);
  let dirty = false;
  let refreshing = false;

  function paintSubmissions() {
    const now = Date.now();
    for (const [key, notice] of submissions) {
      if (notice.expires <= now) submissions.delete(key);
    }
    document.querySelectorAll('[data-active-issues] [data-issue-key]').forEach((row) => {
      const notice = submissions.get(row.dataset.issueKey);
      const fresh = Boolean(notice);
      row.classList.toggle('has-new-submission', fresh);
      if (notice && row.dataset.submissionEvent !== String(notice.eventId)) {
        row.dataset.submissionEvent = String(notice.eventId);
        row.style.setProperty('--submission-delay', `${-(now - notice.started)}ms`);
        // 新的提交重播提示；同一事件轮询不重复播放。
        row.classList.remove('is-event-active');
        void row.offsetWidth;
        row.classList.add('is-event-active');
      }
      if (!notice || now - notice.started >= 4200) row.classList.remove('is-event-active');
      const badge = row.querySelector('.issue-new-submission');
      if (badge) badge.hidden = !fresh;
    });
  }

  async function refreshRanking() {
    paintSubmissions();
    if (!dirty || refreshing || document.hidden) return;
    const region = document.querySelector('[data-active-issues]');
    // 避免更新时移走键盘焦点；待用户离开列表后再更新。
    if (!region || region.contains(document.activeElement)) return;
    refreshing = true;
    dirty = false;
    try {
      const response = await fetch(window.location.href, { cache: 'no-store', credentials: 'same-origin' });
      if (!response.ok) throw new Error('ranking unavailable');
      const next = new DOMParser().parseFromString(await response.text(), 'text/html')
        .querySelector('[data-active-issues]');
      if (!next) throw new Error('ranking missing');
      if (region.contains(document.activeElement)) { dirty = true; return; }
      const stageSnapshots = new Map();
      region.querySelectorAll('[data-issue-key]').forEach((row) => {
        const stage = row.querySelector('.ranking-stage-current, .ranking-stage-inactive');
        if (stage) {
          const rect = stage.getBoundingClientRect();
          stageSnapshots.set(row.dataset.issueKey, {
            node: stage.cloneNode(true), text: stage.textContent.trim(), width: rect.width, height: rect.height,
          });
        }
      });
      region.replaceWith(next);
      next.querySelectorAll('[data-issue-key]').forEach((row) => {
        const oldRow = Array.from(region.querySelectorAll('[data-issue-key]'))
          .find((item) => item.dataset.issueKey === row.dataset.issueKey);
        if (oldRow) animateProgressChanges(oldRow, row);
        animateStageReveal(stageSnapshots.get(row.dataset.issueKey), row);
      });
      paintSubmissions();
    } catch (_error) {
      dirty = true;
    } finally {
      refreshing = false;
    }
  }

  window.addEventListener('code-inspector:changed', ({ detail }) => {
    if (detail.issueKey && submissionActions.has(detail.action)) {
      const started = Date.now();
      submissions.set(detail.issueKey, { started, expires: started + 20000, eventId: detail.eventId });
      paintSubmissions();
    }
    dirty = true;
    refreshRanking();
  });
  window.setInterval(refreshRanking, 1000);
})();
