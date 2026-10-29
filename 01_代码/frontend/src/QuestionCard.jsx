import {Button, Icon} from './components';
import {QUESTION_TYPES, MAX_OPTIONS, MAX_RULES, codePointLength, createOption, createRule, moveItem} from './editor-utils';
const TYPE_INFO = Object.fromEntries(QUESTION_TYPES.map(item => [item.value, item]));
export function Counter({ value, max }) {
  const length = codePointLength(value);
  return <span className={`field-counter ${length > max ? 'invalid' : ''}`}>{length} / {max}</span>;
}

function NumericField({ label, value, onChange, disabled, integer = false, placeholder = '不限制', id }) {
  return <label className="field" htmlFor={id}>
    <span className="field-label">{label}</span>
    <input id={id} aria-label={label} type="number" step={integer ? '1' : 'any'} value={value ?? ''}
      onChange={(event) => onChange(event.target.value)} disabled={disabled} placeholder={placeholder} />
  </label>;
}

export function Toggle({ checked, onChange, disabled, label, description, id }) {
  return <label className={`switch-row ${disabled ? 'disabled' : ''}`} htmlFor={id}>
    <span><span className="switch-title">{label}</span>{description && <span className="field-hint">{description}</span>}</span>
    <span className="toggle-control"><input id={id} type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} disabled={disabled} aria-label={label} /><span className="toggle-track" /></span>
  </label>;
}

function RuleEditor({ rule, question, questionIndex, ruleIndex, questions, onChange, onDelete, onMove, disabled }) {
  const prefix = `第${questionIndex + 1}题规则${ruleIndex + 1}`;
  const updateCondition = (changes) => onChange({ ...rule, condition: { ...rule.condition, ...changes } });
  const condition = rule.condition;
  const later = questions.slice(questionIndex + 1);
  const changeOperator = (operator) => {
    let next;
    if (question.type === 'multiple') next = { operator, values: condition.values || [] };
    else if (operator === 'between') next = { operator, min: condition.value ?? '', max: '' };
    else next = { operator, value: condition.value ?? condition.min ?? '' };
    onChange({ ...rule, condition: next });
  };
  return <div className="rule-card" data-testid="jump-rule-editor">
    <div className="rule-card-header">
      <span className="rule-priority">规则 {ruleIndex + 1}<span>按顺序匹配</span></span>
      {!disabled && <div className="rule-actions">
        <Button variant="ghost" icon="chevron-up" aria-label={`${prefix}上移`} title="提高优先级" disabled={ruleIndex === 0} onClick={() => onMove(-1)} />
        <Button variant="ghost" icon="chevron-down" aria-label={`${prefix}下移`} title="降低优先级" disabled={ruleIndex === question.jumpRules.length - 1} onClick={() => onMove(1)} />
        <Button variant="ghost" icon="trash" aria-label={`${prefix}删除`} title="删除规则" onClick={onDelete} />
      </div>}
    </div>
    <div className="rule-condition">
      <span className="rule-word">当答案</span>
      {question.type === 'single' && <label className="field rule-field"><span className="field-label">选择了</span>
        <select aria-label={`${prefix}匹配选项`} value={condition.value} disabled={disabled} onChange={(event) => updateCondition({ value: event.target.value })}>
          <option value="" disabled>请选择选项</option>{question.options.map((option, index) => <option key={option.id} value={option.id}>{option.label || `选项 ${index + 1}`}</option>)}
        </select></label>}
      {question.type === 'multiple' && <div className="rule-multiple-condition">
        <label className="field"><span className="field-label">满足条件</span><select aria-label={`${prefix}匹配方式`} value={condition.operator} disabled={disabled} onChange={(event) => changeOperator(event.target.value)}>
          <option value="containsAny">包含以下任意选项</option><option value="containsAll">包含以下全部选项</option><option value="setEquals">恰好选择以下选项</option>
        </select></label>
        <div className="rule-option-pills" role="group" aria-label={`${prefix}条件选项`}>{question.options.map((option, index) => <label key={option.id} className={`option-pill ${condition.values?.includes(option.id) ? 'selected' : ''}`}>
          <input type="checkbox" disabled={disabled} checked={condition.values?.includes(option.id) || false}
            onChange={(event) => updateCondition({ values: event.target.checked ? [...(condition.values || []), option.id] : condition.values.filter((id) => id !== option.id) })} />
          {option.label || `选项 ${index + 1}`}
        </label>)}</div>
      </div>}
      {question.type === 'number' && <>
        <label className="field rule-field"><span className="field-label">比较方式</span><select aria-label={`${prefix}比较方式`} value={condition.operator} disabled={disabled} onChange={(event) => changeOperator(event.target.value)}>
          <option value="gt">大于</option><option value="gte">大于或等于</option><option value="lt">小于</option><option value="lte">小于或等于</option><option value="eq">等于</option><option value="between">处于区间（含边界）</option>
        </select></label>
        {condition.operator === 'between' ? <>
          <NumericField id={`${question.id}-${rule.id}-min`} label={`${prefix}区间下限`} placeholder="下限" value={condition.min} disabled={disabled} onChange={(value) => updateCondition({ min: value })} />
          <NumericField id={`${question.id}-${rule.id}-max`} label={`${prefix}区间上限`} placeholder="上限" value={condition.max} disabled={disabled} onChange={(value) => updateCondition({ max: value })} />
        </> : <NumericField id={`${question.id}-${rule.id}-value`} label={`${prefix}比较值`} placeholder="输入数字" value={condition.value} disabled={disabled} onChange={(value) => updateCondition({ value })} />}
      </>}
    </div>
    <div className="rule-target"><Icon name="arrow-right" /><label className="field"><span className="field-label">则跳转到</span>
      <select aria-label={`${prefix}跳转目标`} disabled={disabled} value={rule.target.kind === 'end' ? '__end__' : rule.target.questionId}
        onChange={(event) => onChange({ ...rule, target: event.target.value === '__end__' ? { kind: 'end' } : { kind: 'question', questionId: event.target.value } })}>
        <option value="__end__">结束问卷 · 前往提交确认</option>
        {later.map((item, index) => <option key={item.id} value={item.id}>第 {questionIndex + index + 2} 题 · {item.title || '未命名题目'}</option>)}
      </select>
    </label></div>
  </div>;
}

export function QuestionCard({ question, index = 0, questions = [question], selected, locked, contentLocked = locked, contentOnly = false, onSelect, onChange, onMove, onDelete, onDeleteOption, onChangeType }) {
  const type = TYPE_INFO[question.type];
  const changeValidation = (key, value) => onChange({ ...question, validation: { ...question.validation, [key]: value } });
  const changeRule = (ruleIndex, rule) => onChange({ ...question, jumpRules: question.jumpRules.map((item, position) => position === ruleIndex ? rule : item) });
  return <section className={`question-card ${selected ? 'selected' : ''} ${locked ? 'locked' : ''}`} id={`question-${question.id}`}
    data-testid="question-editor" data-question-index={index + 1} onFocusCapture={onSelect}>
    <div className="question-card-header">
      <div className="question-heading"><span className="question-number">{String(index + 1).padStart(2, '0')}</span><span className="question-type-badge"><Icon name={type.icon} />{type.label}</span>{locked && <span className="muted small"><Icon name="lock" /> 已锁定</span>}</div>
      {!locked && !contentOnly && <div className="question-actions">
        <Button variant="ghost" icon="chevron-up" title="题目上移" aria-label={`第${index + 1}题上移`} disabled={index === 0} onClick={() => onMove(-1)} />
        <Button variant="ghost" icon="chevron-down" title="题目下移" aria-label={`第${index + 1}题下移`} disabled={index === questions.length - 1} onClick={() => onMove(1)} />
        <Button variant="ghost" icon="trash" title="删除题目" aria-label={`删除第${index + 1}题`} onClick={onDelete} />
      </div>}
    </div>
    <div className="question-card-body">
      <label className="field" htmlFor={`${question.id}-title`}><span className="field-label">题目 <span className="required-mark">*</span><Counter value={question.title} max={2000} /></span>
        <input id={`${question.id}-title`} aria-label={`第${index + 1}题题目`} value={question.title} placeholder="在这里输入你的问题" disabled={contentLocked} onChange={(event) => onChange({ ...question, title: event.target.value })} />
      </label>
      <label className="field" htmlFor={`${question.id}-description`}><span className="field-label">题目说明 <span className="optional-label">选填</span><Counter value={question.description} max={2000} /></span>
        <textarea id={`${question.id}-description`} aria-label={`第${index + 1}题说明`} value={question.description} rows={2} placeholder="补充填写提示或说明" disabled={contentLocked} onChange={(event) => onChange({ ...question, description: event.target.value })} />
      </label>
      {(question.type === 'single' || question.type === 'multiple') && <div className="question-options">
        <div className="section-row"><span className="field-label">选项</span><span className="field-hint">{question.options.length} / {MAX_OPTIONS} 项</span></div>
        {question.options.map((option, optionIndex) => <div className="option-row" key={option.id}>
          <span className={`option-marker ${question.type === 'multiple' ? 'square' : ''}`} aria-hidden="true" />
          <input aria-label={`第${index + 1}题选项${optionIndex + 1}`} value={option.label} placeholder={`选项 ${optionIndex + 1}`} disabled={contentLocked}
            onChange={(event) => onChange({ ...question, options: question.options.map((item) => item.id === option.id ? { ...item, label: event.target.value } : item) })} />
          {!contentLocked && <Button variant="ghost" icon="x" title={question.options.length <= 2 ? '至少保留两个选项' : '删除选项'} aria-label={`删除第${index + 1}题选项${optionIndex + 1}`} disabled={question.options.length <= 2} onClick={() => onDeleteOption(option.id)} />}
        </div>)}
        {!contentLocked && <Button variant="ghost" icon="plus" className="add-option" disabled={question.options.length >= MAX_OPTIONS} onClick={() => onChange({ ...question, options: [...question.options, createOption(`选项 ${question.options.length + 1}`)] })}>添加选项</Button>}
      </div>}
      <div className="question-configuration">
        {!contentOnly && <Toggle id={`${question.id}-required`} label="必答题" description="实际经过此题时，必须填写后才能继续" checked={question.required} disabled={locked} onChange={(required) => onChange({ ...question, required })} />}
        {question.type === 'multiple' && <div className="validation-fields">
          <div className="form-grid two-columns"><NumericField id={`${question.id}-min`} label={`第${index + 1}题最少选择项数`} value={question.validation.minSelections} disabled={contentLocked} integer placeholder="不限" onChange={(value) => changeValidation('minSelections', value)} /><NumericField id={`${question.id}-max`} label={`第${index + 1}题最多选择项数`} value={question.validation.maxSelections} disabled={contentLocked} integer placeholder={`最多 ${question.options.length} 项`} onChange={(value) => changeValidation('maxSelections', value)} /></div>
          <p className="field-hint">恰好选择 N 项：将最少与最多都设为 N。选填题留空时不受此限制。</p>
        </div>}
        {question.type === 'text' && <div className="validation-fields"><div className="form-grid two-columns"><NumericField id={`${question.id}-min`} label={`第${index + 1}题最少字符数`} value={question.validation.minLength} disabled={contentLocked} integer placeholder="不限" onChange={(value) => changeValidation('minLength', value)} /><NumericField id={`${question.id}-max`} label={`第${index + 1}题最多字符数`} value={question.validation.maxLength} disabled={contentLocked} integer placeholder="最多 10,000 字符" onChange={(value) => changeValidation('maxLength', value)} /></div><p className="field-hint">去除首尾空白后计算字符数。最多支持 10,000 字符。</p></div>}
        {question.type === 'number' && <div className="validation-fields">
          <div className="form-grid two-columns"><NumericField id={`${question.id}-min`} label={`第${index + 1}题最小值`} value={question.validation.min} disabled={contentLocked} onChange={(value) => changeValidation('min', value)} /><NumericField id={`${question.id}-max`} label={`第${index + 1}题最大值`} value={question.validation.max} disabled={contentLocked} onChange={(value) => changeValidation('max', value)} /></div>
          <label className="checkbox-label"><input type="checkbox" aria-label={`第${index + 1}题仅允许整数`} checked={question.validation.integerOnly || false} disabled={contentLocked} onChange={(event) => changeValidation('integerOnly', event.target.checked)} />仅允许整数</label>
        </div>}
      </div>
      {!contentOnly && question.type !== 'text' && <div className="question-rules">
        <div className="section-row"><div><h3><Icon name="arrow-right" /> 条件跳转</h3><p className="field-hint">从上到下匹配，首条命中生效；均未命中则进入下一题。</p></div>
          {!locked && <Button variant="secondary" icon="plus" disabled={question.jumpRules.length >= MAX_RULES} onClick={() => onChange({ ...question, jumpRules: [...question.jumpRules, createRule(question)] })}>添加跳转规则</Button>}
        </div>
        {!question.jumpRules.length && <p className="rule-empty">按题目顺序继续填写</p>}
        {question.jumpRules.map((rule, ruleIndex) => <RuleEditor key={rule.id} rule={rule} question={question} questionIndex={index} ruleIndex={ruleIndex} questions={questions} disabled={locked}
          onChange={(next) => changeRule(ruleIndex, next)} onDelete={() => onChange({ ...question, jumpRules: question.jumpRules.filter((item) => item.id !== rule.id) })}
          onMove={(offset) => onChange({ ...question, jumpRules: moveItem(question.jumpRules, ruleIndex, offset) })} />)}
      </div>}
      {!contentLocked && <div className="question-card-footer"><span className="field-hint">题型</span><select aria-label={`第${index + 1}题题型`} value={question.type} onChange={(event) => onChangeType(event.target.value)}>{QUESTION_TYPES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></div>}
    </div>
  </section>;
}

