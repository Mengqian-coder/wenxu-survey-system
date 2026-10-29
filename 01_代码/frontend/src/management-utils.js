// A receipt can contain an older representation. Navigate to a current GET view.
export function mutationTarget(entry, value) {
  const path = entry.request.path.split('?')[0];
  if (/^\/question-banks(?:\/|$)/.test(path)) {
    if (value.deleted) return '/question-banks';
    const id = value.bankId || value._id || path.split('/')[2];
    return id ? `/question-banks/${id}` : '/question-banks';
  }
  if (/^\/questions(?:\/|$)/.test(path)) {
    const id = value.question?._id || value.version?.questionId || path.split('/')[2];
    return id ? `/questions/${id}` : '/questions';
  }
  if (/^\/surveys(?:\/|$)/.test(path)) {
    const id = value._id || path.split('/')[2];
    return id ? `/surveys/${id}/edit` : '/';
  }
  return null;
}

export function selectableQuestion(item) {
  return item.available !== false && item.permissions?.canUse !== false && !item.archivedAt;
}

export function versionSourceLabel(version, versions = []) {
  if (!version?.baseVersionId) return '';
  const source = versions.find(item => item._id === version.baseVersionId);
  return source ? `内容来源：v${source.versionNo}` : '内容来源：历史版本';
}

export async function loadBanks(get) {
  const items = [];
  let page = 1, total = 0;
  do {
    const result = await get(`/question-banks?page=${page}&pageSize=100`);
    items.push(...result.items); total = result.total; page++;
    if (!result.items.length) break;
  } while (items.length < total);
  return items;
}
