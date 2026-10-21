/** Normalize inputs exactly enough to decide whether an existing answer changed.
 * This module has no browser or React dependency, so it can also be tested in Node.
 */
export function canonicalAnswer(question, rawValue) {
  if (rawValue == null) return null

  if (question.type === 'text') {
    const value = String(rawValue).trim()
    return value || null
  }

  if (question.type === 'number') {
    if (typeof rawValue === 'string' && !rawValue.trim()) return null
    if (typeof rawValue !== 'number' && typeof rawValue !== 'string') {
      throw new Error('请输入有效数字。')
    }
    const text = String(rawValue).trim()
    if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(text)) {
      throw new Error('请输入有效数字，例如 12 或 3.5。')
    }
    const value = Number(text)
    if (!Number.isFinite(value)) throw new Error('数字超出可接受范围，请填写有限数字。')
    if (question.validation?.integerOnly && !Number.isSafeInteger(value)) {
      throw new Error('请填写安全范围内的整数。')
    }
    return Object.is(value, -0) ? 0 : value
  }

  if (question.type === 'multiple') {
    if (!Array.isArray(rawValue)) throw new Error('请选择有效的选项。')
    if (rawValue.length === 0) return null
    if (new Set(rawValue).size !== rawValue.length) throw new Error('选项不能重复。')
    const positions = new Map((question.options || []).map((option, index) => [option.id, index]))
    if (rawValue.some(id => !positions.has(id))) throw new Error('选项已经失效，请重新进入问卷。')
    return [...rawValue].sort((a, b) => positions.get(a) - positions.get(b))
  }

  if (question.type === 'single') {
    if (rawValue === '') return null
    if (!(question.options || []).some(option => option.id === rawValue)) {
      throw new Error('请选择有效的选项。')
    }
    return rawValue
  }

  throw new Error('暂不支持此题型。')
}

export function answersEqual(question, left, right) {
  try {
    return JSON.stringify(canonicalAnswer(question, left)) === JSON.stringify(canonicalAnswer(question, right))
  } catch {
    return false
  }
}

export function answerInput(question, value) {
  if (question.type === 'multiple') return Array.isArray(value) ? [...value] : []
  if (question.type === 'number') return value == null ? '' : String(value)
  return value == null ? '' : value
}

export function answerLabel(question, value) {
  if (value == null || value === '' || (Array.isArray(value) && !value.length)) return '未填写（选填）'
  if (question.type === 'single') return question.options?.find(option => option.id === value)?.label || String(value)
  if (question.type === 'multiple') {
    return question.options?.filter(option => value.includes(option.id)).map(option => option.label).join('、') || '未填写（选填）'
  }
  return String(value)
}

export function questionHint(question) {
  const validation = question.validation || {}
  if (question.type === 'single') return question.required ? '请选择一项。' : '请选择一项，也可以留空。'
  if (question.type === 'multiple') {
    const { minSelections, maxSelections } = validation
    if (minSelections != null && minSelections === maxSelections) return `请选择 ${minSelections} 项。`
    if (minSelections != null && maxSelections != null) return `请选择 ${minSelections}～${maxSelections} 项。`
    if (maxSelections != null) return `最多选择 ${maxSelections} 项。`
    if (minSelections != null) return `至少选择 ${minSelections} 项。`
    return question.required ? '可多选，请至少选择一项。' : '可多选，也可以留空。'
  }
  if (question.type === 'text') {
    const { minLength, maxLength } = validation
    if (minLength != null && maxLength != null) return `非空时需填写 ${minLength}～${maxLength} 个字符。`
    if (maxLength != null) return `最多填写 ${maxLength} 个字符。`
    if (minLength != null) return `非空时至少填写 ${minLength} 个字符。`
    return question.required ? '请填写你的回答。' : '选填，可以留空。'
  }
  if (question.type === 'number') {
    const { min, max, integerOnly } = validation
    const kind = integerOnly ? '整数' : '数字'
    if (min != null && max != null) return `请填写 ${min}～${max} 之间的${kind}，包含边界。`
    if (min != null) return `请填写不小于 ${min} 的${kind}。`
    if (max != null) return `请填写不大于 ${max} 的${kind}。`
    return `请填写有效${kind}${question.required ? '。' : '，也可以留空。'}`
  }
  return ''
}
