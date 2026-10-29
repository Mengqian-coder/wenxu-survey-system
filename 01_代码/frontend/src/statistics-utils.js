export function statisticSelection(search) {
  const params = new URLSearchParams(search);
  return {questionId: params.get('questionId') || '', versionId: params.get('versionId') || ''};
}

export function statisticLocation(questionId, versionId) {
  const params = new URLSearchParams();
  if (questionId) params.set('questionId', questionId);
  if (questionId && versionId) params.set('versionId', versionId);
  return `/question-statistics${params.size ? `?${params}` : ''}`;
}

export function statisticPercent(ratio) {
  if (ratio === null || ratio === undefined) return '暂无比例';
  return `${new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 1}).format(ratio * 100)}%`;
}

export function statisticNumber(value) {
  if (value === null || value === undefined) return '暂无数据';
  return Math.abs(value) >= 1e9 || value !== 0 && Math.abs(value) < 1e-4
    ? value.toExponential(4)
    : new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 4}).format(value);
}
