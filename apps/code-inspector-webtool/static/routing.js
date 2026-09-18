// 模型路由配置页交互：Agent 分组、多 Model、Reasoning 联动、等级滑杆。
// 能力数据来自页面内嵌的 Skill capability 表，前端不写死档位列表。
(() => {
  const form = document.getElementById('routing-rows-form');
  const container = document.getElementById('routing-agents');
  const rowTemplate = document.getElementById('routing-row-template');
  const capabilitiesNode = document.getElementById('routing-capabilities');
  if (!form || !container || !rowTemplate || !capabilitiesNode) return;

  let payload = { capabilities: {}, fallbackReasonings: [] };
  try {
    payload = JSON.parse(capabilitiesNode.dataset.payload || '{}');
  } catch (_error) {
    payload = { capabilities: {}, fallbackReasonings: [] };
  }
  const capabilities = payload.capabilities || {};
  const fallbackReasonings = payload.fallbackReasonings || [];

  const emptyNote = document.getElementById('routing-empty-note');

  function reasoningOptions(agent, model) {
    const models = capabilities[agent] || [];
    const match = models.find((item) => item.model === model);
    return (match && match.supportedReasonings) || fallbackReasonings;
  }

  function fillSelect(select, options, current) {
    if (!select) return;
    const desired = current || select.value;
    select.textContent = '';
    options.forEach((value) => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
    if (desired && options.includes(desired)) select.value = desired;
  }

  function syncReasoning(row) {
    const agentField = row.querySelector('[data-field="agent"]');
    const modelSelect = row.querySelector('[data-model-select]');
    const reasoningSelect = row.querySelector('[data-reasoning-select]');
    if (!agentField || !modelSelect || !reasoningSelect) return;
    fillSelect(
      reasoningSelect,
      reasoningOptions(agentField.value, modelSelect.value),
      reasoningSelect.value,
    );
  }

  function syncSlider(row) {
    const range = row.querySelector('[data-level-range]');
    const output = row.querySelector('[data-level-output]');
    if (!range) return;
    if (output) output.textContent = range.value;
    const min = Number(range.min) || 1;
    const max = Number(range.max) || 5;
    const percent = ((Number(range.value) - min) / Math.max(max - min, 1)) * 100;
    range.style.setProperty('--routing-level-percent', `${percent}%`);
  }

  function refreshCounts() {
    container.querySelectorAll('.routing-agent').forEach((group) => {
      const count = group.querySelectorAll('[data-profile-row]').length;
      const label = group.querySelector('small');
      if (label) label.textContent = `${count} 个 Model`;
    });
  }

  function renumber() {
    const rows = container.querySelectorAll('[data-profile-row]');
    rows.forEach((row, index) => {
      row.querySelectorAll('input, select').forEach((field) => {
        const name = field.dataset.field;
        if (name) field.name = `p${index}_${name}`;
      });
      syncSlider(row);
    });
    refreshCounts();
    if (emptyNote) emptyNote.hidden = rows.length > 0;
  }

  function buildRow(agent, model, reasoning) {
    const fragment = rowTemplate.content.cloneNode(true);
    const row = fragment.querySelector('[data-profile-row]');
    const agentField = row.querySelector('[data-field="agent"]');
    if (agentField) agentField.value = agent;
    const modelSelect = row.querySelector('[data-model-select]');
    const models = (capabilities[agent] || []).map((item) => item.model);
    fillSelect(modelSelect, models.length ? models : [model].filter(Boolean), model);
    const chosen = modelSelect.value || model;
    fillSelect(row.querySelector('[data-reasoning-select]'), reasoningOptions(agent, chosen), reasoning);
    syncSlider(row);
    return fragment;
  }

  container.addEventListener('change', (event) => {
    if (event.target.matches('[data-model-select]')) {
      const row = event.target.closest('[data-profile-row]');
      if (row) syncReasoning(row);
    }
  });

  container.addEventListener('input', (event) => {
    if (event.target.matches('[data-level-range]')) {
      const row = event.target.closest('[data-profile-row]');
      if (row) syncSlider(row);
    }
  });

  form.addEventListener('click', (event) => {
    const remove = event.target.closest('[data-remove-row]');
    if (remove) {
      event.preventDefault();
      const group = remove.closest('.routing-agent');
      remove.closest('[data-profile-row]')?.remove();
      if (group && !group.querySelector('[data-profile-row]')) group.remove();
      renumber();
      return;
    }

    const addModel = event.target.closest('[data-add-model]');
    if (addModel) {
      event.preventDefault();
      const group = addModel.closest('.routing-agent');
      const body = group?.querySelector('[data-agent-body]');
      if (body) {
        body.appendChild(buildRow(group.dataset.agent || '', '', ''));
        renumber();
        body.querySelector('tr:last-child [data-model-select]')?.focus();
      }
      return;
    }

    const addAgent = event.target.closest('[data-add-agent]');
    if (addAgent) {
      event.preventDefault();
      const picker = document.getElementById('routing-new-agent');
      const agent = picker?.value;
      if (!agent) return;
      let body = container.querySelector(
        `.routing-agent[data-agent="${CSS.escape(agent)}"] [data-agent-body]`,
      );
      if (!body) {
        const section = document.createElement('section');
        section.className = 'routing-agent';
        section.dataset.agent = agent;
        section.innerHTML = '<header class="routing-agent-head"><div><strong></strong><small></small></div>'
          + '<button type="button" class="btn btn-small" data-add-model>＋ Model</button></header>'
          + '<div class="table-wrap"><table class="routing-table">'
          + '<thead><tr><th>Model</th><th>Reasoning</th><th>等级</th><th>启用</th><th>操作</th></tr></thead>'
          + '<tbody data-agent-body></tbody></table></div>';
        section.querySelector('strong').textContent = agent;
        section.querySelector('small').textContent = '0 个 Model';
        container.appendChild(section);
        body = section.querySelector('[data-agent-body]');
      }
      body.appendChild(buildRow(agent, '', ''));
      renumber();
    }
  });

  renumber();
})();
