import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { api, ApiError } from '../api';
import { useAuth, useToast } from '../context';
import { Badge, Button, Icon, Modal } from '../components';
import {QuestionCard, Counter, Toggle} from '../QuestionCard';
import QuestionPicker from '../QuestionPicker';
import {
  QUESTION_TYPES, MAX_QUESTIONS, EditorValidationError, buildSurveyPayload,
  invalidJumpTargets, moveItem, removeInvalidJumpTargets, surveyToForm, versionToInstance,
} from '../editor-utils';

const TYPE_INFO = Object.fromEntries(QUESTION_TYPES.map((item) => [item.value, item]));
const STATUS_LABELS = { draft: '草稿', published: '收集中', closed: '已结束' };

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
  const [picker, setPicker] = useState(null);
  const [copyFeedback, setCopyFeedback] = useState(false);
  const [needsReload, setNeedsReload] = useState(false);
  const latestForm = useRef(form);
  const recordId = useRef(null);
  latestForm.current = form;
  const dirty = JSON.stringify(form) !== baseline;
  const status = record?.status || 'draft';
  const legacy = record?.schemaVersion === 1;
  const locked = status !== 'draft' || legacy;
  const closed = status === 'closed';
  const shareUrl = record?.publicId ? `${window.location.origin}/survey/${record.publicId}` : '';

  const applyRecord = useCallback((value) => {
    recordId.current = value?._id || null;
    const next = surveyToForm(value);
    setRecord(value); setForm(next); setBaseline(JSON.stringify(next));
    setError(null); setIssues([]);
  }, []);

  const load = useCallback(async () => {
    const targetId = surveyId || recordId.current;
    if (!targetId) return;
    setLoading(true); setError(null);
    try { applyRecord(await api.get(`/surveys/${targetId}`)); setNeedsReload(false); }
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
  const selectVersion = (questionId, version) => {
    const previous = picker?.instanceId ? form.questions.find(item => item.id === picker.instanceId) : null;
    if (previous?.questionId === questionId && previous.versionId === version._id) {setPicker(null); return;}
    const instance = versionToInstance(questionId, version, previous);
    const questions = previous ? form.questions.map(item => item.id === previous.id ? instance : item) : [...form.questions, instance];
    changeForm({questions}); setPicker(null); focusQuestion(instance.id);
  };
  const updateQuestionOrder = (index, offset) => {
    const next = moveItem(form.questions, index, offset);
    const invalid = invalidJumpTargets(next);
    if (invalid.length) setModal({ type: 'reorder', questions: next, count: invalid.length });
    else changeForm({ questions: next });
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
    const saved = record ? await api.mutate(user._id, `survey:${record._id}:save`, 'PATCH', `/surveys/${record._id}`, { revision: record.revision, ...payload }, '保存问卷修改') : await api.mutate(user._id, 'survey:new:save', 'POST', '/surveys', payload, '创建问卷草稿');
    applyRecord(saved); setNeedsReload(true);
    if (!surveyId && !forPublication) navigate(`/surveys/${saved._id}/edit`, { replace: true });
    const current = await api.get(`/surveys/${saved._id}`);
    applyRecord(current); setNeedsReload(false);
    return current;
  };
  const save = async () => {
    setBusy('save'); setError(null); setIssues([]);
    try { await persist(); toast(status === 'draft' ? '草稿已保存' : '修改已保存'); }
    catch (failure) { handleFailure(failure); }
    finally { setBusy(''); }
  };
  const requestPublish = () => {
    try { buildSurveyPayload(form, { locked, publish: true }); setModal({ type: 'publish' }); }
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
    setBusy(modal.type); setError(null);
    let savedForPublication = null;
    try {
      if (modal.type === 'publish') {
        const saved = dirty || !record ? await persist(true) : record;
        savedForPublication = saved;
        const published = await api.mutate(user._id, `survey:${saved._id}:publish`, 'POST', `/surveys/${saved._id}/publish`, { revision: saved.revision }, '发布问卷');
        applyRecord(published); setNeedsReload(true);
        applyRecord(await api.get(`/surveys/${published._id}`)); setNeedsReload(false);
        setModal({ type: 'share' }); toast('问卷已发布，可以分享给填写者了');
        if (!surveyId) navigate(`/surveys/${published._id}/edit`, { replace: true, state: { showShare: true } });
      } else if (modal.type === 'close') {
        const updated = await api.mutate(user._id, `survey:${record._id}:close`, 'POST', `/surveys/${record._id}/close`, { revision: record.revision }, '关闭问卷');
        applyRecord(updated); setNeedsReload(true);
        applyRecord(await api.get(`/surveys/${updated._id}`)); setNeedsReload(false);
        setModal(null); toast('问卷已关闭，历史答卷仍可查看');
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
        {!closed && <Button variant={status === 'draft' ? 'secondary' : 'primary'} icon="check" loading={busy === 'save'} disabled={needsReload || !!busy || (!!record && !dirty)} onClick={save}>{status === 'draft' ? '保存草稿' : '保存修改'}</Button>}
        {status === 'draft' && <Button icon="arrow-right" disabled={needsReload || !!busy} onClick={requestPublish}>发布问卷</Button>}
        {status === 'published' && <Button variant="secondary" icon="copy" onClick={() => setModal({ type: 'share' })}>分享问卷</Button>}
      </div>
    </div>

    {legacy && <div className="notice info-notice"><Icon name="clock"/><span>这是旧版问卷，可继续发布、填写及查看原统计。修改题目结构前需显式迁移；本轮未自动转换旧数据。</span></div>}
    {!legacy && locked && <div className={`notice ${closed ? 'neutral-notice' : 'info-notice'}`}><Icon name="lock" /><span>{closed ? '问卷已结束，配置已锁定。你仍然可以查看历史答卷与统计结果。' : '问卷已发布。题目、选项与规则已锁定；标题、说明和未来的截止时间仍可修改。'}</span></div>}
    {issues.length > 0 && <div className="notice error-notice editor-validation" role="alert"><Icon name="x" /><div><strong>还有 {issues.length} 处需要完善</strong><ul>{issues.slice(0, 8).map((issue, index) => <li key={index}>{issue.questionId ? <button onClick={() => focusQuestion(issue.questionId)}>{issue.message}</button> : issue.message}</li>)}</ul>{issues.length > 8 && <p>其余问题将在继续完善后显示。</p>}</div></div>}
    {error && <div className="notice error-notice" role="alert"><Icon name="x" /><div><strong>{error.message || '操作未完成'}</strong>{error instanceof ApiError && error.status === 409 && <p>问卷可能已被其他操作更新。重新加载会放弃当前未保存的修改。</p>}</div>{record && <Button variant="secondary" onClick={() => dirty ? setModal({ type: 'reload' }) : load()}>重新加载</Button>}</div>}

    <div className="editor-layout">
      <aside className="editor-outline">
        <div className="outline-header"><span>问卷大纲</span><span className="count-badge">{form.questions.length}</span></div>
        <button className="outline-item outline-info" onClick={() => { setSelected(null); document.getElementById('survey-information')?.scrollIntoView({ behavior: 'smooth' }); }}><Icon name="file" /><span>问卷信息</span></button>
        <nav className="outline-list" aria-label="问卷题目大纲">{form.questions.map((question, index) => <button key={question.id} className={`outline-item ${selected === question.id ? 'active' : ''}`} onClick={() => focusQuestion(question.id)}><span className="outline-number">{String(index + 1).padStart(2, '0')}</span><span className="outline-title">{question.title || '未命名题目'}<small>{TYPE_INFO[question.type].short}{question.required ? ' · 必答' : ' · 选填'}</small></span><Icon name="chevron-down" /></button>)}</nav>
        {!form.questions.length && <p className="outline-placeholder">从一道好问题开始。</p>}
        {!locked && <div className="outline-add"><Button variant="secondary" icon="plus" disabled={!!busy || form.questions.length >= MAX_QUESTIONS} onClick={() => setPicker({})}>从题目列表选用</Button><p className="field-hint">最多 {MAX_QUESTIONS} 题 · 同一道题最多选用一次</p></div>}
        <div className="outline-tip"><span className="outline-tip-icon"><Icon name="sparkles" /></span><strong>让问题更清晰</strong><p>简洁的题干与恰当的跳转，让每一次回答都有意义。</p></div>
        {user && <div className="editor-owner"><Icon name="users" /><span>由 {user.username} 创建</span></div>}
      </aside>

      <div className="editor-main">
        <section className="editor-section survey-information" id="survey-information">
          <div className="section-heading"><span className="section-icon"><Icon name="file" /></span><div><h2>先给问卷一个名字</h2><p>告诉填写者，你想了解什么。</p></div></div>
          <label className="field" htmlFor="survey-title"><span className="field-label">问卷标题 <span className="required-mark">*</span><Counter value={form.title} max={200} /></span><input className="survey-title-input" id="survey-title" aria-label="问卷标题" placeholder="例如：一起聊聊这门课程" value={form.title} disabled={closed || !!busy} onChange={(event) => changeForm({ title: event.target.value })} /></label>
          <label className="field" htmlFor="survey-description"><span className="field-label">问卷说明 <span className="optional-label">选填</span><Counter value={form.description} max={5000} /></span><textarea id="survey-description" aria-label="问卷说明" rows={3} placeholder="介绍问卷目的、预计用时，或对填写者说声谢谢。" value={form.description} disabled={closed || !!busy} onChange={(event) => changeForm({ description: event.target.value })} /></label>
        </section>

        {form.questions.map((question, index) => <div key={question.id}>
          {question.versionNo && <div className="instance-version-row"><span>固定使用 v{question.versionNo} · 内容与校验来自正式版本</span>{!locked && <Button size="sm" variant="secondary" disabled={!!busy} onClick={() => setPicker({questionId: question.questionId, instanceId: question.id})}>选择其他版本</Button>}</div>}
          <QuestionCard question={question} index={index} questions={form.questions} selected={selected === question.id} locked={locked || !!busy} contentLocked onSelect={() => setSelected(question.id)}
            onChange={(next) => changeQuestion(question.id, next)} onMove={(offset) => updateQuestionOrder(index, offset)} onDelete={() => setModal({ type: 'delete', questionId: question.id })}/>
        </div>)}
        {!locked && <section className="editor-add-section"><div className="add-question-heading"><span className="add-question-icon"><Icon name="plus"/></span><h2>{form.questions.length ? '继续选用题目' : '选择第一道题目'}</h2><p>先创建独立题目，再选定具体版本。旧版本也可直接选用。</p></div><div className="button-row"><Button icon="plus" disabled={!!busy || form.questions.length >= MAX_QUESTIONS} onClick={() => setPicker({})}>从题目列表选用</Button><Button variant="secondary" onClick={() => navigateSafely('/questions/new')}>创建独立题目</Button></div></section>}

        <section className="editor-section survey-settings" id="survey-settings">
          <div className="section-heading"><span className="section-icon"><Icon name="settings" /></span><div><h2>填写设置</h2><p>让每一位填写者知道如何参与。</p></div>{locked && <span className="lock-note"><Icon name="lock" /> 身份与重复设置已锁定</span>}</div>
          <div className="field"><span className="field-label">填写者身份</span><div className="identity-choices" role="radiogroup" aria-label="问卷匿名方式">
            {[{ value: true, title: '匿名问卷', icon: 'lock', detail: '答卷不记录填写者身份' }, { value: false, title: '实名问卷', icon: 'users', detail: '答卷关联填写者账号' }].map((choice) => <label key={String(choice.value)} className={`identity-choice ${form.settings.anonymous === choice.value ? 'selected' : ''} ${locked ? 'disabled' : ''}`}><input type="radio" name="anonymous" aria-label={choice.title} checked={form.settings.anonymous === choice.value} disabled={locked} onChange={() => changeForm({ settings: { ...form.settings, anonymous: choice.value } })} /><Icon name={choice.icon} /><span><strong>{choice.title}</strong><small>{choice.detail}</small></span>{form.settings.anonymous === choice.value && <Icon name="check" />}</label>)}
          </div><p className="field-hint">两种方式都需要先登录。匿名方式对整份问卷统一生效。</p></div>
          <Toggle id="allow-repeat" label="允许同一账号多次填写" description={form.settings.allowRepeat ? '每次重新填写都会生成一份独立答卷' : '每个账号最多成功提交一次'} checked={form.settings.allowRepeat} disabled={locked} onChange={(allowRepeat) => changeForm({ settings: { ...form.settings, allowRepeat } })} />
          <div className="deadline-setting"><Toggle id="enable-deadline" label="设置截止时间" description="到达截止时间后停止接收新的提交" checked={!!form.deadline} disabled={closed} onChange={(checked) => { if (!checked) changeForm({ deadline: '' }); else { const date = new Date(Date.now() + 7 * 24 * 3600 * 1000); date.setSeconds(0, 0); const offset = date.getTimezoneOffset() * 60000; changeForm({ deadline: new Date(date.getTime() - offset).toISOString().slice(0, 16) }); } }} />
            {form.deadline && <label className="field deadline-field" htmlFor="survey-deadline"><span className="field-label">截止日期与时间</span><input id="survey-deadline" aria-label="截止日期与时间" type="datetime-local" step="any" value={form.deadline} disabled={closed} onChange={(event) => changeForm({ deadline: event.target.value })} /><span className="field-hint">按当前设备的本地时区显示；截止前打开、截止后提交也无法接收。</span></label>}
          </div>
          {status === 'published' && <div className="close-survey-row"><div><strong>提前结束收集</strong><p className="field-hint">关闭后无法重新开放，已有答卷会保留。</p></div><Button variant="danger" disabled={needsReload || !!busy} onClick={() => setModal({ type: 'close' })}>关闭问卷</Button></div>}
        </section>
        <div className="editor-footer-note"><Icon name="check" /><span>{closed ? '历史答卷会一直保留' : '请手动保存修改，发布前再检查一次题目与填写设置。'}</span></div>
      </div>
    </div>

    <QuestionPicker open={!!picker} questionId={picker?.questionId} currentInstance={form.questions.find(item => item.id === picker?.instanceId)} excluded={form.questions.map(item => item.questionId)} onClose={() => setPicker(null)} onSelect={selectVersion}/>
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
