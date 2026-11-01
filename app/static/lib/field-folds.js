export function foldSection(section, title) {
  const fold = document.createElement('details');
  fold.className = 'card form-fold';
  const summary = document.createElement('summary');
  summary.append(document.createTextNode(title + ' '));
  const status = document.createElement('span'); status.className = 'fold-status';
  summary.append(status); fold.append(summary);
  section.before(fold); fold.append(section);
  section.classList.remove('card');
  return fold;
}

export function revealField(field) {
  if (!field) return;
  for (let parent = field.parentElement; parent; parent = parent.parentElement) {
    if (parent.tagName === 'DETAILS') parent.open = true;
  }
  field.focus(); field.scrollIntoView?.({block:'center'});
}

export function bindFoldSummaries(form) {
  function update() {
    form.querySelectorAll('.form-fold').forEach(fold => {
      const values = [...fold.querySelectorAll('input,select,textarea')].filter(el => {
        if (el.type === 'radio' || el.type === 'checkbox') return false;
        return String(el.value || '').trim() && !['none','未评'].includes(el.value);
      });
      const status = fold.querySelector(':scope > summary .fold-status');
      if (status) status.textContent = values.length ? `已填写 ${values.length} 项` : '';
    });
  }
  form.addEventListener('input', update); form.addEventListener('change', update);
  form.addEventListener('invalid', e => revealField(e.target), true);
  update(); return update;
}
