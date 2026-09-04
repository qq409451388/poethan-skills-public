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
      const scope = tab.closest('.modal-backdrop, .implementation-panel');
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
