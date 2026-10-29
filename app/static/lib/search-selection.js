// Selection order is preserved so switching to single keeps the latest choice.
export const selectionValues = value => [...new Set(String(value || '').split(',').map(v=>v.trim()).filter(Boolean))];
export function chooseFilter(value, id, multiple) {
  if (!id) return '';
  if (!multiple) return id;
  const values = selectionValues(value);
  return (values.includes(id) ? values.filter(v => v !== id) : [...values, id]).join(',');
}
export const singleFilter = value => selectionValues(value).slice(-1).join(',');
export const matchesFilter = (value, id) => !value || selectionValues(value).includes(id);
