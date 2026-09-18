// 模型路由配置页的纯交互：增删行与 enabled 行号重排。
// 校验与写盘全部由后端完成，前端不复制任何配置规则。
(() => {
  const form = document.getElementById('routing-rows-form');
  const body = document.getElementById('routing-agents-body');
  const template = document.getElementById('routing-row-template');
  if (!form || !body || !template) return;

  const emptyNote = document.getElementById('routing-empty-note');

  function renumber() {
    body.querySelectorAll('tr').forEach((row, index) => {
      const checkbox = row.querySelector('input[name="agent_enabled_row"]');
      if (checkbox) checkbox.value = `row${index}`;
    });
    if (emptyNote) emptyNote.hidden = body.querySelectorAll('tr').length > 0;
  }

  form.addEventListener('click', (event) => {
    if (event.target.closest('[data-routing-add]')) {
      event.preventDefault();
      body.appendChild(template.content.cloneNode(true));
      renumber();
      const last = body.querySelector('tr:last-child input[name="agent_id"]');
      last?.focus();
      return;
    }
    const remove = event.target.closest('[data-routing-remove]');
    if (remove) {
      event.preventDefault();
      remove.closest('tr')?.remove();
      renumber();
    }
  });

  renumber();
})();
