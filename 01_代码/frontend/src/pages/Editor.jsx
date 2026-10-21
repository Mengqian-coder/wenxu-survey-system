import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { api, ApiError } from '../api';
import { useAuth, useToast } from '../context';
import { Badge, Button, Icon, Modal } from '../components';
import {
  QUESTION_TYPES, MAX_QUESTIONS, MAX_OPTIONS, MAX_RULES,
  EditorValidationError, buildSurveyPayload, codePointLength, createOption,
  createQuestion, createRule, invalidJumpTargets, moveItem, removeInvalidJumpTargets,
  removeQuestionOption, rulesReferencingOption, surveyToForm,
} from '../editor-utils';

const TYPE_INFO = Object.fromEntries(QUESTION_TYPES.map((item) => [item.value, item]));
const STATUS_LABELS = { draft: '草稿', published: '收集中', closed: '已结束' };

function Counter({ value, max }) {
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

function Toggle({ checked, onChange, disabled, label, description, id }) {
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

function QuestionCard({ question, index, questions, selected, locked, onSelect, onChange, onMove, onDelete, onDeleteOption, onChangeType }) {
  const type = TYPE_INFO[question.type];
  const changeValidation = (key, value) => onChange({ ...question, validation: { ...question.validation, [key]: value } });
  const changeRule = (ruleIndex, rule) => onChange({ ...question, jumpRules: question.jumpRules.map((item, position) => position === ruleIndex ? rule : item) });
  return <section className={`question-card ${selected ? 'selected' : ''} ${locked ? 'locked' : ''}`} id={`question-${question.id}`}
    data-testid="question-editor" data-question-index={index + 1} onFocusCapture={onSelect}>
    <div className="question-card-header">
      <div className="question-heading"><span className="question-number">{String(index + 1).padStart(2, '0')}</span><span className="question-type-badge"><Icon name={type.icon} />{type.label}</span>{locked && <span className="muted small"><Icon name="lock" /> 已锁定</span>}</div>
      {!locked && <div className="question-actions">
        <Button variant="ghost" icon="chevron-up" title="题目上移" aria-label={`第${index + 1}题上移`} disabled={index === 0} onClick={() => onMove(-1)} />
        <Button variant="ghost" icon="chevron-down" title="题目下移" aria-label={`第${index + 1}题下移`} disabled={index === questions.length - 1} onClick={() => onMove(1)} />
        <Button variant="ghost" icon="trash" title="删除题目" aria-label={`删除第${index + 1}题`} onClick={onDelete} />
      </div>}
    </div>
    <div className="question-card-body">
      <label className="field" htmlFor={`${question.id}-title`}><span className="field-label">题目 <span className="required-mark">*</span><Counter value={question.title} max={2000} /></span>
        <input id={`${question.id}-title`} aria-label={`第${index + 1}题题目`} value={question.title} placeholder="在这里输入你的问题" disabled={locked} onChange={(event) => onChange({ ...question, title: event.target.value })} />
      </label>
      <label className="field" htmlFor={`${question.id}-description`}><span className="field-label">题目说明 <span className="optional-label">选填</span><Counter value={question.description} max={2000} /></span>
        <textarea id={`${question.id}-description`} aria-label={`第${index + 1}题说明`} value={question.description} rows={2} placeholder="补充填写提示或说明" disabled={locked} onChange={(event) => onChange({ ...question, description: event.target.value })} />
      </label>
      {(question.type === 'single' || question.type === 'multiple') && <div className="question-options">
        <div className="section-row"><span className="field-label">选项</span><span className="field-hint">{question.options.length} / {MAX_OPTIONS} 项</span></div>
        {question.options.map((option, optionIndex) => <div className="option-row" key={option.id}>
          <span className={`option-marker ${question.type === 'multiple' ? 'square' : ''}`} aria-hidden="true" />
          <input aria-label={`第${index + 1}题选项${optionIndex + 1}`} value={option.label} placeholder={`选项 ${optionIndex + 1}`} disabled={locked}
            onChange={(event) => onChange({ ...question, options: question.options.map((item) => item.id === option.id ? { ...item, label: event.target.value } : item) })} />
          {!locked && <Button variant="ghost" icon="x" title={question.options.length <= 2 ? '至少保留两个选项' : '删除选项'} aria-label={`删除第${index + 1}题选项${optionIndex + 1}`} disabled={question.options.length <= 2} onClick={() => onDeleteOption(option.id)} />}
        </div>)}
        {!locked && <Button variant="ghost" icon="plus" className="add-option" disabled={question.options.length >= MAX_OPTIONS} onClick={() => onChange({ ...question, options: [...question.options, createOption(`选项 ${question.options.length + 1}`)] })}>添加选项</Button>}
      </div>}
      <div className="question-configuration">
        <Toggle id={`${question.id}-required`} label="必答题" description="实际经过此题时，必须填写后才能继续" checked={question.required} disabled={locked} onChange={(required) => onChange({ ...question, required })} />
        {question.type === 'multiple' && <div className="validation-fields">
          <div className="form-grid two-columns"><NumericField id={`${question.id}-min`} label={`第${index + 1}题最少选择项数`} value={question.validation.minSelections} disabled={locked} integer placeholder="不限" onChange={(value) => changeValidation('minSelections', value)} /><NumericField id={`${question.id}-max`} label={`第${index + 1}题最多选择项数`} value={question.validation.maxSelections} disabled={locked} integer placeholder={`最多 ${question.options.length} 项`} onChange={(value) => changeValidation('maxSelections', value)} /></div>
          <p className="field-hint">恰好选择 N 项：将最少与最多都设为 N。选填题留空时不受此限制。</p>
        </div>}
        {question.type === 'text' && <div className="validation-fields"><div className="form-grid two-columns"><NumericField id={`${question.id}-min`} label={`第${index + 1}题最少字符数`} value={question.validation.minLength} disabled={locked} integer placeholder="不限" onChange={(value) => changeValidation('minLength', value)} /><NumericField id={`${question.id}-max`} label={`第${index + 1}题最多字符数`} value={question.validation.maxLength} disabled={locked} integer placeholder="最多 10,000 字符" onChange={(value) => changeValidation('maxLength', value)} /></div><p className="field-hint">去除首尾空白后计算字符数。最多支持 10,000 字符。</p></div>}
        {question.type === 'number' && <div className="validation-fields">
          <div className="form-grid two-columns"><NumericField id={`${question.id}-min`} label={`第${index + 1}题最小值`} value={question.validation.min} disabled={locked} onChange={(value) => changeValidation('min', value)} /><NumericField id={`${question.id}-max`} label={`第${index + 1}题最大值`} value={question.validation.max} disabled={locked} onChange={(value) => changeValidation('max', value)} /></div>
          <label className="checkbox-label"><input type="checkbox" aria-label={`第${index + 1}题仅允许整数`} checked={question.validation.integerOnly || false} disabled={locked} onChange={(event) => changeValidation('integerOnly', event.target.checked)} />仅允许整数</label>
        </div>}
      </div>
      {question.type !== 'text' && <div className="question-rules">
        <div className="section-row"><div><h3><Icon name="arrow-right" /> 条件跳转</h3><p className="field-hint">从上到下匹配，首条命中生效；均未命中则进入下一题。</p></div>
          {!locked && <Button variant="secondary" icon="plus" disabled={question.jumpRules.length >= MAX_RULES} onClick={() => onChange({ ...question, jumpRules: [...question.jumpRules, createRule(question)] })}>添加跳转规则</Button>}
        </div>
        {!question.jumpRules.length && <p className="rule-empty">按题目顺序继续填写</p>}
        {question.jumpRules.map((rule, ruleIndex) => <RuleEditor key={rule.id} rule={rule} question={question} questionIndex={index} ruleIndex={ruleIndex} questions={questions} disabled={locked}
          onChange={(next) => changeRule(ruleIndex, next)} onDelete={() => onChange({ ...question, jumpRules: question.jumpRules.filter((item) => item.id !== rule.id) })}
          onMove={(offset) => onChange({ ...question, jumpRules: moveItem(question.jumpRules, ruleIndex, offset) })} />)}
      </div>}
      {!locked && <div className="question-card-footer"><span className="field-hint">题型</span><select aria-label={`第${index + 1}题题型`} value={question.type} onChange={(event) => onChangeType(event.target.value)}>{QUESTION_TYPES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></div>}
    </div>
  </section>;
}

export default function Editor({ surveyId: providedSurveyId }) {
  const params = useParams();
  const surveyId = providedSurveyId || params.id || params.surveyId;
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const { user } = useAuth();
  const [record, setRecord] = useState(null);
  const [form, setForm] = useState(() => surveyToForm(null));
  const [baseline, setBaseline] = useState(() => JSON.stringify(surveyToForm(null)));
  const [loading, setLoading] = useState(!!surveyId);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState(null);
  const [issues, setIssues] = useState([]);
  const [selected, setSelected] = useState(null);
  const [modal, setModal] = useState(null);
  const [copyFeedback, setCopyFeedback] = useState(false);
  const latestForm = useRef(form);
  latestForm.current = form;
  const dirty = JSON.stringify(form) !== baseline;
  const status = record?.status || 'draft';
  const locked = status !== 'draft';
  const closed = status === 'closed';
  const shareUrl = record?.publicId ? `${window.location.origin}/survey/${record.publicId}` : '';

  const applyRecord = useCallback((value) => {
    const next = surveyToForm(value);
    setRecord(value); setForm(next); setBaseline(JSON.stringify(next));
    setError(null); setIssues([]);
  }, []);

  const load = useCallback(async () => {
    if (!surveyId) return;
    setLoading(true); setError(null);
    try { applyRecord(await api.get(`/surveys/${surveyId}`)); }
    catch (failure) { setError(failure); }
    finally { setLoading(false); }
  }, [surveyId, applyRecord]);

  useEffect(() => {
    if (surveyId) load();
    else { applyRecord(null); setSelected(null); }
  }, [surveyId, load, applyRecord]);
  useEffect(() => {
    if (record?.status === 'published' && location.state?.showShare) {
      setModal({ type: 'share' });
      navigate(location.pathname, { replace: true, state: null });
    }
  }, [record?.status, location.state, location.pathname, navigate]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event) => { event.preventDefault(); event.returnValue = ''; };
    const intercept = (event) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = event.target.closest?.('a[href]');
      if (!link || link.target === '_blank' || link.hasAttribute('download')) return;
      const target = new URL(link.href, window.location.href);
      if (target.origin !== window.location.origin || (target.pathname === window.location.pathname && target.search === window.location.search)) return;
      event.preventDefault(); event.stopPropagation();
      setModal({ type: 'leave', destination: `${target.pathname}${target.search}${target.hash}` });
    };
    window.addEventListener('beforeunload', warn);
    document.addEventListener('click', intercept, true);
    return () => { window.removeEventListener('beforeunload', warn); document.removeEventListener('click', intercept, true); };
  }, [dirty]);

  const changeForm = (changes) => { if (busy) return; setForm((current) => ({ ...current, ...changes })); setIssues([]); setError(null); };
  const changeQuestion = (qid, value) => { if (busy) return; setForm((current) => ({ ...current, questions: current.questions.map((question) => question.id === qid ? value : question) })); setIssues([]); setError(null); };
  const focusQuestion = (qid) => {
    setSelected(qid);
    window.setTimeout(() => document.getElementById(`question-${qid}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 30);
  };
  const navigateSafely = (destination) => dirty ? setModal({ type: 'leave', destination }) : navigate(destination);
  const addQuestion = (type) => {
    if (busy || form.questions.length >= MAX_QUESTIONS) return;
    const question = createQuestion(type);
    changeForm({ questions: [...form.questions, question] }); focusQuestion(question.id);
  };
  const updateQuestionOrder = (index, offset) => {
    const next = moveItem(form.questions, index, offset);
    const invalid = invalidJumpTargets(next);
    if (invalid.length) setModal({ type: 'reorder', questions: next, count: invalid.length });
    else changeForm({ questions: next });
  };
  const deleteOption = (question, optionId) => {
    const count = rulesReferencingOption(question, optionId).length;
    if (count) setModal({ type: 'option', questionId: question.id, optionId, count });
    else changeQuestion(question.id, removeQuestionOption(question, optionId));
  };
  const handleFailure = (failure) => {
    if (failure instanceof EditorValidationError) {
      setIssues(failure.issues); setError(null);
      // Keep the actual error explanation in view; each message then links to its question.
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } else { setError(failure); toast(failure.message || '操作未完成，请重试', 'error'); }
  };
  const persist = async (forPublication = false) => {
    const payload = buildSurveyPayload(latestForm.current, { locked, publish: forPublication });
    const saved = record ? await api.patch(`/surveys/${record._id}`, { revision: record.revision, ...payload }) : await api.post('/surveys', payload);
    applyRecord(saved);
    if (!surveyId && !forPublication) navigate(`/surveys/${saved._id}/edit`, { replace: true });
    return saved;
  };
  const save = async () => {
    setBusy('save'); setError(null); setIssues([]);
    try { await persist(); toast(status === 'draft' ? '草稿已保存' : '修改已保存'); }
    catch (failure) { handleFailure(failure); }
    finally { setBusy(''); }
  };
  const requestPublish = () => {
    try { buildSurveyPayload(form, { publish: true }); setModal({ type: 'publish' }); }
    catch (failure) { handleFailure(failure); }
  };
  const confirmModal = async () => {
    if (!modal) return;
    if (modal.type === 'leave') { const target = modal.destination; setModal(null); navigate(target); return; }
    if (modal.type === 'reload') { setModal(null); await load(); return; }
    if (modal.type === 'delete') {
      const next = form.questions.filter((question) => question.id !== modal.questionId);
      changeForm({ questions: removeInvalidJumpTargets(next) });
      if (selected === modal.questionId) setSelected(null);
      setModal(null); return;
    }
    if (modal.type === 'reorder') { changeForm({ questions: removeInvalidJumpTargets(modal.questions) }); setModal(null); return; }
    if (modal.type === 'option') {
      const question = form.questions.find((item) => item.id === modal.questionId);
      changeQuestion(question.id, removeQuestionOption(question, modal.optionId)); setModal(null); return;
    }
    if (modal.type === 'type') {
      const question = form.questions.find((item) => item.id === modal.questionId);
      const replacement = createQuestion(modal.nextType);
      changeQuestion(question.id, { ...replacement, id: question.id, title: question.title, description: question.description, required: question.required });
      setModal(null); return;
    }
    setBusy(modal.type); setError(null);
    let savedForPublication = null;
    try {
      if (modal.type === 'publish') {
        const saved = dirty || !record ? await persist(true) : record;
        savedForPublication = saved;
        const published = await api.post(`/surveys/${saved._id}/publish`, { revision: saved.revision });
        applyRecord(published); setModal({ type: 'share' }); toast('问卷已发布，可以分享给填写者了');
        if (!surveyId) navigate(`/surveys/${published._id}/edit`, { replace: true, state: { showShare: true } });
      } else if (modal.type === 'close') {
        const updated = await api.post(`/surveys/${record._id}/close`, { revision: record.revision });
        applyRecord(updated); setModal(null); toast('问卷已关闭，历史答卷仍可查看');
      }
    } catch (failure) {
      setModal(null); handleFailure(failure);
      if (savedForPublication && !surveyId) navigate(`/surveys/${savedForPublication._id}/edit`, { replace: true });
    }
    finally { setBusy(''); }
  };
  const copyShare = async () => {
    try { await navigator.clipboard.writeText(shareUrl); setCopyFeedback(true); toast('问卷链接已复制'); window.setTimeout(() => setCopyFeedback(false), 2000); }
    catch { toast('请选中下方链接后手动复制', 'error'); setModal({ type: 'share' }); }
  };

  if (loading) return <div className="page-loading" role="status"><span className="spinner" />正在打开问卷…</div>;
  if (error && !record && surveyId) return <div className="editor-page"><div className="empty-state"><Icon name="file" /><h2>暂时无法打开问卷</h2><p>{error.message}</p><div className="button-row"><Button variant="secondary" onClick={() => navigate('/')}>返回我的问卷</Button><Button onClick={load}>重试</Button></div></div></div>;

  const deleteCount = modal?.type === 'delete' ? invalidJumpTargets(form.questions.filter((question) => question.id !== modal.questionId)).length : 0;
  const modalTitle = { publish: '准备好发布了吗？', close: '关闭这份问卷？', leave: '还有未保存的修改', reload: '重新加载问卷？', delete: '删除这道题目？', reorder: '调整题目顺序', option: '删除选项及关联规则？', type: '更换题型？', share: '让答案从这里开始' }[modal?.type] || '';
  const confirmationText = { publish: '确认发布', close: '确认关闭', leave: '放弃修改并离开', reload: '放弃修改并重新加载', delete: '删除题目', reorder: '调整并移除失效规则', option: '确认删除', type: '确认更换' }[modal?.type] || '确认';
  return <div className="editor-page">
    <div className="editor-toolbar">
      <div className="editor-title-group"><button className="icon-back" aria-label="返回我的问卷" onClick={() => navigateSafely('/')}><Icon name="arrow-left" /></button><div><div className="eyebrow">SURVEY STUDIO</div><div className="editor-page-heading"><h1>{record ? '编辑问卷' : '创建问卷'}</h1><Badge status={status}>{STATUS_LABELS[status]}</Badge></div></div></div>
      <div className="editor-toolbar-actions"><span className={`save-status ${dirty ? 'unsaved' : ''}`}><span />{dirty ? '有未保存的修改' : record ? '所有修改已保存' : '尚未保存'}</span>
        {record && status !== 'draft' && <Button variant="secondary" icon="chart" onClick={() => navigateSafely(`/surveys/${record._id}/results`)}>查看统计</Button>}
        {!closed && <Button variant={status === 'draft' ? 'secondary' : 'primary'} icon="check" loading={busy === 'save'} disabled={!!busy || (!!record && !dirty)} onClick={save}>{status === 'draft' ? '保存草稿' : '保存修改'}</Button>}
        {status === 'draft' && <Button icon="arrow-right" disabled={!!busy} onClick={requestPublish}>发布问卷</Button>}
        {status === 'published' && <Button variant="secondary" icon="copy" onClick={() => setModal({ type: 'share' })}>分享问卷</Button>}
      </div>
    </div>

    {locked && <div className={`notice ${closed ? 'neutral-notice' : 'info-notice'}`}><Icon name="lock" /><span>{closed ? '问卷已结束，配置已锁定。你仍然可以查看历史答卷与统计结果。' : '问卷已发布。题目、选项与规则已锁定；标题、说明和未来的截止时间仍可修改。'}</span></div>}
    {issues.length > 0 && <div className="notice error-notice editor-validation" role="alert"><Icon name="x" /><div><strong>还有 {issues.length} 处需要完善</strong><ul>{issues.slice(0, 8).map((issue, index) => <li key={index}>{issue.questionId ? <button onClick={() => focusQuestion(issue.questionId)}>{issue.message}</button> : issue.message}</li>)}</ul>{issues.length > 8 && <p>其余问题将在继续完善后显示。</p>}</div></div>}
    {error && <div className="notice error-notice" role="alert"><Icon name="x" /><div><strong>{error.message || '操作未完成'}</strong>{error instanceof ApiError && error.status === 409 && <p>问卷可能已被其他操作更新。重新加载会放弃当前未保存的修改。</p>}</div>{record && <Button variant="secondary" onClick={() => dirty ? setModal({ type: 'reload' }) : load()}>重新加载</Button>}</div>}

    <div className="editor-layout">
      <aside className="editor-outline">
        <div className="outline-header"><span>问卷大纲</span><span className="count-badge">{form.questions.length}</span></div>
        <button className="outline-item outline-info" onClick={() => { setSelected(null); document.getElementById('survey-information')?.scrollIntoView({ behavior: 'smooth' }); }}><Icon name="file" /><span>问卷信息</span></button>
        <nav className="outline-list" aria-label="问卷题目大纲">{form.questions.map((question, index) => <button key={question.id} className={`outline-item ${selected === question.id ? 'active' : ''}`} onClick={() => focusQuestion(question.id)}><span className="outline-number">{String(index + 1).padStart(2, '0')}</span><span className="outline-title">{question.title || '未命名题目'}<small>{TYPE_INFO[question.type].short}{question.required ? ' · 必答' : ' · 选填'}</small></span><Icon name="chevron-down" /></button>)}</nav>
        {!form.questions.length && <p className="outline-placeholder">从一道好问题开始。</p>}
        {!locked && <div className="outline-add"><div className="eyebrow">添加题目</div><div className="editor-add-grid">{QUESTION_TYPES.map((type) => <button key={type.value} className="add-type-button" aria-label={`从大纲添加${type.label}`} disabled={!!busy || form.questions.length >= MAX_QUESTIONS} onClick={() => addQuestion(type.value)}><Icon name={type.icon} /><span>{type.short}</span><Icon name="plus" /></button>)}</div><p className="field-hint">最多 {MAX_QUESTIONS} 题 · 只支持向后跳转</p></div>}
        <div className="outline-tip"><span className="outline-tip-icon"><Icon name="sparkles" /></span><strong>让问题更清晰</strong><p>简洁的题干与恰当的跳转，让每一次回答都有意义。</p></div>
        {user && <div className="editor-owner"><Icon name="users" /><span>由 {user.username} 创建</span></div>}
      </aside>

      <div className="editor-main">
        <section className="editor-section survey-information" id="survey-information">
          <div className="section-heading"><span className="section-icon"><Icon name="file" /></span><div><h2>先给问卷一个名字</h2><p>告诉填写者，你想了解什么。</p></div></div>
          <label className="field" htmlFor="survey-title"><span className="field-label">问卷标题 <span className="required-mark">*</span><Counter value={form.title} max={200} /></span><input className="survey-title-input" id="survey-title" aria-label="问卷标题" placeholder="例如：一起聊聊这门课程" value={form.title} disabled={closed || !!busy} onChange={(event) => changeForm({ title: event.target.value })} /></label>
          <label className="field" htmlFor="survey-description"><span className="field-label">问卷说明 <span className="optional-label">选填</span><Counter value={form.description} max={5000} /></span><textarea id="survey-description" aria-label="问卷说明" rows={3} placeholder="介绍问卷目的、预计用时，或对填写者说声谢谢。" value={form.description} disabled={closed || !!busy} onChange={(event) => changeForm({ description: event.target.value })} /></label>
        </section>

        {form.questions.map((question, index) => <QuestionCard key={question.id} question={question} index={index} questions={form.questions} selected={selected === question.id} locked={locked || !!busy} onSelect={() => setSelected(question.id)}
          onChange={(next) => changeQuestion(question.id, next)} onMove={(offset) => updateQuestionOrder(index, offset)} onDelete={() => setModal({ type: 'delete', questionId: question.id })}
          onDeleteOption={(optionId) => deleteOption(question, optionId)} onChangeType={(nextType) => setModal({ type: 'type', questionId: question.id, nextType })} />)}
        {!locked && <section className="editor-add-section">
          <div className="add-question-heading"><span className="add-question-icon"><Icon name="plus" /></span><h2>{form.questions.length ? '接下来，想问些什么？' : '添加你的第一道问题'}</h2><p>选择一种题型，开始了解更多。</p></div>
          <div className="question-type-grid">{QUESTION_TYPES.map((type) => <button key={type.value} className="question-type-card" aria-label={`添加${type.label}`} disabled={!!busy || form.questions.length >= MAX_QUESTIONS} onClick={() => addQuestion(type.value)}><span className={`type-icon type-${type.value}`}><Icon name={type.icon} /></span><strong>{type.label}</strong><small>{type.description}</small><Icon name="plus" /></button>)}</div>
          {form.questions.length >= MAX_QUESTIONS && <p className="field-hint">已达到 100 题上限</p>}
        </section>}

        <section className="editor-section survey-settings" id="survey-settings">
          <div className="section-heading"><span className="section-icon"><Icon name="settings" /></span><div><h2>填写设置</h2><p>让每一位填写者知道如何参与。</p></div>{locked && <span className="lock-note"><Icon name="lock" /> 身份与重复设置已锁定</span>}</div>
          <div className="field"><span className="field-label">填写者身份</span><div className="identity-choices" role="radiogroup" aria-label="问卷匿名方式">
            {[{ value: true, title: '匿名问卷', icon: 'lock', detail: '答卷不记录填写者身份' }, { value: false, title: '实名问卷', icon: 'users', detail: '答卷关联填写者账号' }].map((choice) => <label key={String(choice.value)} className={`identity-choice ${form.settings.anonymous === choice.value ? 'selected' : ''} ${locked ? 'disabled' : ''}`}><input type="radio" name="anonymous" aria-label={choice.title} checked={form.settings.anonymous === choice.value} disabled={locked} onChange={() => changeForm({ settings: { ...form.settings, anonymous: choice.value } })} /><Icon name={choice.icon} /><span><strong>{choice.title}</strong><small>{choice.detail}</small></span>{form.settings.anonymous === choice.value && <Icon name="check" />}</label>)}
          </div><p className="field-hint">两种方式都需要先登录。匿名方式对整份问卷统一生效。</p></div>
          <Toggle id="allow-repeat" label="允许同一账号多次填写" description={form.settings.allowRepeat ? '每次重新填写都会生成一份独立答卷' : '每个账号最多成功提交一次'} checked={form.settings.allowRepeat} disabled={locked} onChange={(allowRepeat) => changeForm({ settings: { ...form.settings, allowRepeat } })} />
          <div className="deadline-setting"><Toggle id="enable-deadline" label="设置截止时间" description="到达截止时间后停止接收新的提交" checked={!!form.deadline} disabled={closed} onChange={(checked) => { if (!checked) changeForm({ deadline: '' }); else { const date = new Date(Date.now() + 7 * 24 * 3600 * 1000); date.setSeconds(0, 0); const offset = date.getTimezoneOffset() * 60000; changeForm({ deadline: new Date(date.getTime() - offset).toISOString().slice(0, 16) }); } }} />
            {form.deadline && <label className="field deadline-field" htmlFor="survey-deadline"><span className="field-label">截止日期与时间</span><input id="survey-deadline" aria-label="截止日期与时间" type="datetime-local" step="any" value={form.deadline} disabled={closed} onChange={(event) => changeForm({ deadline: event.target.value })} /><span className="field-hint">按当前设备的本地时区显示；截止前打开、截止后提交也无法接收。</span></label>}
          </div>
          {status === 'published' && <div className="close-survey-row"><div><strong>提前结束收集</strong><p className="field-hint">关闭后无法重新开放，已有答卷会保留。</p></div><Button variant="danger" disabled={!!busy} onClick={() => setModal({ type: 'close' })}>关闭问卷</Button></div>}
        </section>
        <div className="editor-footer-note"><Icon name="check" /><span>{closed ? '历史答卷会一直保留' : '请手动保存修改，发布前再检查一次题目与填写设置。'}</span></div>
      </div>
    </div>

    <Modal open={!!modal} title={modalTitle} onClose={() => !busy && setModal(null)} onConfirm={confirmModal} confirmText={confirmationText} busy={!!busy}
      danger={['close', 'leave', 'reload', 'delete', 'option', 'type'].includes(modal?.type)}
      footer={modal?.type === 'share' ? <div className="button-row"><Button variant="secondary" onClick={() => setModal(null)}>完成</Button><Button icon={copyFeedback ? 'check' : 'copy'} onClick={copyShare}>{copyFeedback ? '已复制' : '复制链接'}</Button></div> : undefined}>
      {modal?.type === 'publish' && <div className="confirmation-content"><p>发布后，题目、选项、校验规则、跳转和匿名设置将锁定。</p><div className="publish-summary"><strong>{form.title || '未命名问卷'}</strong><span>{form.questions.length} 道题目 · {form.settings.anonymous ? '匿名填写' : '实名填写'} · {form.settings.allowRepeat ? '允许重复填写' : '每人限填一次'}</span></div><p className="field-hint">未保存的修改会先保存，再发布问卷。</p></div>}
      {modal?.type === 'close' && <p>关闭后无法再提交或重新开放。历史答卷和统计会保留。{dirty && '当前未保存的标题、说明或截止时间修改将被放弃。'}</p>}
      {modal?.type === 'leave' && <p>当前修改还没有保存。离开后，这些修改将丢失。</p>}
      {modal?.type === 'reload' && <p>将读取服务器上的最新问卷，并放弃当前未保存的修改。</p>}
      {modal?.type === 'delete' && <p>这道题目及其配置将从草稿中移除。{deleteCount > 0 && `另外 ${deleteCount} 条指向此题的跳转规则也会移除。`}保存后生效。</p>}
      {modal?.type === 'reorder' && <p>新顺序会使 {modal.count} 条跳转规则指向前面或当前题目。确认后会移除这些失效规则，其余跳转保持不变。</p>}
      {modal?.type === 'option' && <p>此选项被 {modal.count} 条跳转规则引用。确认后，选项和这些规则将一并移除。</p>}
      {modal?.type === 'type' && <p>题干、说明和必答设置会保留。当前选项、校验配置及本题跳转规则将重置。</p>}
      {modal?.type === 'share' && <div className="share-content"><span className="share-illustration"><Icon name="copy" /></span><p>复制链接，邀请他人填写。填写者需要登录后参与。</p><label className="field"><span className="field-label">问卷分享链接</span><input aria-label="问卷分享链接" readOnly value={shareUrl} onFocus={(event) => event.target.select()} /></label><span className="share-status"><span className="status-dot" />{closed ? '收集已结束' : '问卷正在收集中'}</span></div>}
    </Modal>
  </div>;
}
