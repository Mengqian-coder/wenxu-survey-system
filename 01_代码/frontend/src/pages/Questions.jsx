import {useCallback, useEffect, useRef, useState} from 'react';
import {Link, useNavigate, useParams, useSearchParams} from 'react-router-dom';
import {api, dateTime, typeNames} from '../api';
import {useAuth, useToast} from '../context';
import {Button, EmptyState, Icon, Loading, Modal} from '../components';
import {QuestionCard} from '../QuestionCard';
import {buildQuestionContent, createQuestion, removeQuestionOption} from '../editor-utils';
import QuestionManagement, {AddQuestionToBank} from '../QuestionManagement';
import {versionSourceLabel} from '../management-utils';
import {statisticLocation} from '../statistics-utils';

const operationNames = {create: '首次创建', edit: '编辑为新版本', restore: '恢复旧内容', copy: '另存副本', migrate: '历史迁移'};

export function QuestionList() {
  const [data, setData] = useState({items: [], total: 0}), [search, setSearch] = useState(''), [archive, setArchive] = useState('exclude'), [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [refresh, setRefresh] = useState(0);
  const [scope, setScope] = useState('owned');
  useEffect(() => {
    let live = true; setLoading(true); setError('');
    const query = new URLSearchParams({page, pageSize: 12, scope, archive, search: search.trim()});
    api.get(`/questions?${query}`).then(value => {if (live) setData(value);}).catch(e => {if (live) setError(e.message);}).finally(() => {if (live) setLoading(false);});
    return () => {live = false;};
  }, [page, search, scope, archive, refresh]);
  return <div className="dashboard"><div className="page-heading"><div><div className="eyebrow">QUESTION LIBRARY</div><h1>我的题目<span className="heading-period">.</span></h1><p>先保存独立题目，再将具体版本选入问卷。</p></div><Link className="btn btn-primary" to="/questions/new"><Icon name="plus" size={18}/>创建独立题目</Link></div>
    <div className="section-toolbar management-filters"><label className="search-input"><Icon name="search"/><input aria-label="搜索我的题目" placeholder="搜索最新版本题干…" value={search} onChange={e => {setSearch(e.target.value); setPage(1);}}/></label><select aria-label="题目来源筛选" value={scope} onChange={e => {setScope(e.target.value); setPage(1);}}><option value="owned">我的题目</option><option value="shared">共享给我的题目</option><option value="all">全部可访问题目</option></select><select aria-label="题目归档筛选" value={archive} onChange={e => {setArchive(e.target.value); setPage(1);}}><option value="exclude">可使用题目</option><option value="include">全部题目</option><option value="only">已归档题目</option></select></div>
    {error ? <EmptyState title="暂时无法读取题目" description={error} action={<Button onClick={() => setRefresh(value => value + 1)}>重试</Button>}/> : loading ? <Loading/> : !data.items.length ? <EmptyState title="这里还没有题目" description="独立题目可以在多份问卷中重复选用。" action={<Link className="btn btn-primary" to="/questions/new">创建独立题目</Link>}/> : <div className="survey-grid">{data.items.map(item => <article className="survey-card" key={item._id}><div className="survey-card-top"><span className="question-type-badge">{typeNames[item.latestVersion.type]}</span><span className="field-hint">{item.archivedAt ? '已归档' : `最新 v${item.latestVersion.versionNo}`}</span></div><Link className="survey-card-title" to={`/questions/${item._id}`}><h3>{item.latestVersion.title}</h3></Link><p className="field-hint">{item.permissions?.canManage ? '我的题目' : '共享给我'} · 更新于 {dateTime(item.updatedAt)}</p><div className="survey-card-actions"><Link className="card-action" to={`/questions/${item._id}`}><Icon name="clock" size={16}/>内容与版本历史</Link></div></article>)}</div>}
    {!loading && data.total > 12 && <div className="pagination"><span>共 {data.total} 道题目</span><div><Button variant="secondary" disabled={page === 1} onClick={() => setPage(page - 1)}>上一页</Button><span>{page} / {Math.ceil(data.total / 12)}</span><Button variant="secondary" disabled={page * 12 >= data.total} onClick={() => setPage(page + 1)}>下一页</Button></div></div>}
  </div>;
}

export default function QuestionDetail() {
  const {id} = useParams(), navigate = useNavigate(), toast = useToast(), {user} = useAuth();
  const [searchParams] = useSearchParams(), requestedVersionId = searchParams.get('versionId');
  const [question, setQuestion] = useState(null), [version, setVersion] = useState(null), [versions, setVersions] = useState({items: [], total: 0}), [page, setPage] = useState(1);
  const [draft, setDraft] = useState(() => createQuestion('single')), [editing, setEditing] = useState(!id), [loading, setLoading] = useState(!!id), [busy, setBusy] = useState(''), [error, setError] = useState(''), [confirm, setConfirm] = useState(null);
  const [versionArchive, setVersionArchive] = useState('include'), [sourceVersion, setSourceVersion] = useState(null);
  const activeId = useRef(id), loadGeneration = useRef(0); activeId.current = id;
  useEffect(() => {setQuestion(null); setVersion(null); setVersions({items: [], total: 0}); setPage(1);}, [id]);
  const load = useCallback(async selectedId => {
    if (!id) return;
    const generation = ++loadGeneration.current;
    const currentRequest = () => activeId.current === id && generation === loadGeneration.current;
    setLoading(true); setError('');
    try {
      const [current, history] = await Promise.all([api.get(`/questions/${id}`), api.get(`/questions/${id}/versions?archive=${versionArchive}&page=${page}&pageSize=10`)]);
      const detail = await api.get(`/questions/${id}/versions/${selectedId || requestedVersionId || current.latestVersionId}`);
      if (currentRequest()) {setQuestion({...current, revision: detail.questionRevision}); setVersions(history); setVersion(detail.version);}
    } catch (e) {if (currentRequest()) setError(e.message);} finally {if (currentRequest()) setLoading(false);}
  }, [id, page, versionArchive, requestedVersionId]);
  useEffect(() => {setBusy(''); setEditing(!id); if (id) load(); else {setQuestion(null); setVersion(null); setDraft(createQuestion('single')); setLoading(false);} return () => {loadGeneration.current++;};}, [id, load]);
  useEffect(() => {
    let live = true; setSourceVersion(null);
    if (id && version?.baseVersionId) api.get(`/questions/${id}/versions/${version.baseVersionId}`).then(value => {if (live) setSourceVersion(value.version);}).catch(() => {});
    return () => {live = false;};
  }, [id, version?.baseVersionId]);
  const chooseVersion = async value => {
    const generation = ++loadGeneration.current;
    const currentRequest = () => activeId.current === id && generation === loadGeneration.current;
    setBusy('view'); setError('');
    try {const result = await api.get(`/questions/${id}/versions/${value._id}`); if (currentRequest()) {setVersion(result.version); setQuestion(current => ({...current, revision: result.questionRevision}));}}
    catch (e) {if (currentRequest()) setError(e.message);} finally {if (currentRequest()) setBusy('');}
  };
  const write = async (slot, method, path, body, label) => api.mutate(user._id, `question:${id || 'new'}:${slot}`, method, path, body, label);
  const save = async () => {
    if (busy) return;
    setBusy('save'); setError('');
    try {
      const content = buildQuestionContent(draft);
      const value = id ? await write('version', 'POST', `/questions/${id}/versions`, {revision: question.revision, baseVersionId: version._id, content}, '保存题目新版本') : await write('create', 'POST', '/questions', {content}, '创建独立题目');
      setEditing(false); toast(id ? '已保存为新版本，旧版本保持原样。' : '独立题目已创建。');
      if (!id) navigate(`/questions/${value.question._id}`, {replace: true}); else await load(value.version._id);
    } catch (e) {setError(e.message);} finally {setBusy('');}
  };
  const applyAction = async () => {
    const action = confirm; setBusy(action); setError(''); setConfirm(null);
    try {
      let value;
      if (action === 'restore') value = await write('restore', 'POST', `/questions/${id}/restore`, {revision: question.revision, sourceVersionId: version._id}, `将 v${version.versionNo} 恢复成新版本`);
      if (action === 'copy') value = await write(`copy:${version._id}`, 'POST', `/questions/${id}/copies`, {versionId: version._id}, `将 v${version.versionNo} 另存独立题目`);
      if (action === 'archiveQuestion') await write('archive', 'PATCH', `/questions/${id}`, {revision: question.revision, archived: !question.archivedAt}, question.archivedAt ? '取消题目归档' : '归档题目');
      if (action === 'archiveVersion') await write(`archive:${version._id}`, 'PATCH', `/questions/${id}/versions/${version._id}`, {revision: question.revision, archived: !version.archivedAt}, version.archivedAt ? '取消版本归档' : '归档版本');
      toast('操作已完成。');
      if (action === 'copy') navigate(`/questions/${value.question._id}`); else await load(value?.version?._id || version?._id);
    } catch (e) {setError(e.message);} finally {setBusy('');}
  };
  const changeType = type => setDraft(current => ({...createQuestion(type), id: current.id, title: current.title, description: current.description}));
  const mayManage = !!question?.permissions?.canManage;
  const displayed = editing ? draft : version ? {...version.content, id: 'content', required: false, jumpRules: []} : null;
  return <div className="question-detail"><div className="page-heading"><div><div className="eyebrow">QUESTION STUDIO</div><h1>{id ? '题目与版本' : '创建独立题目'}</h1><p>{editing ? '保存后生成正式版本；后续修改会继续创建新版本。' : `当前查看 v${version?.versionNo ?? '…'}${version?.archivedAt ? ' · 已归档' : ''}，最新版本 v${question?.latestVersion?.versionNo ?? '…'}`}</p></div><Link className="btn btn-secondary" to="/questions">返回我的题目</Link></div>
    {error && <div className="notice error-notice" role="alert"><div>{error}<p className="field-hint">若结果尚未确认，请使用上方“确认原操作”。修订冲突时，先重新加载最新内容。</p></div>{id && <Button variant="secondary" disabled={!!busy} onClick={() => {setEditing(false); load();}}>重新加载</Button>}</div>}
    {loading ? <Loading/> : <>{id && question && <div className="notice info-notice"><Icon name="clock"/><span>历史版本内容永久保留。选用旧版本只改变草稿引用；恢复旧内容会在这道题下创建一个新版本。{question.archivedAt && ' 题目已归档，已有问卷引用仍可使用。'}</span></div>}
      {id && question && !mayManage && <div className="notice info-notice">此题由他人共享。你可以查看、选用所有可用版本，或另存自己的独立副本。</div>}
      {id && version && !editing && <p className="version-source"><Link className="text-link" to={statisticLocation(id, version._id)}>查看本版本在本人问卷中的统计<Icon name="arrow-right" size={16}/></Link><span>仅统计本人已保存的引用</span></p>}
      {question?.origin?.kind === 'copy' && <div className="notice info-notice copy-origin"><div><strong>独立副本</strong><p>本题保留另存时的来源，拥有自己的版本、共享和归档设置。</p><Link className="text-link" to={`/questions/${question.origin.questionId}?versionId=${question.origin.versionId}`}>查看副本来源版本</Link><p className="field-hint">查看来源需保有原题的当前访问权限。</p></div></div>}
      {version?.baseVersionId && <p className="version-source">{versionSourceLabel(version, sourceVersion ? [sourceVersion] : versions.items)}{sourceVersion && <Button size="sm" variant="ghost" disabled={!!busy || editing} onClick={() => chooseVersion(sourceVersion)}>查看来源版本</Button>}</p>}
      <div className="question-detail-layout"><div>{displayed && <QuestionCard question={displayed} locked={!editing || !!busy} contentOnly onChange={setDraft} onDeleteOption={optionId => setDraft(current => removeQuestionOption(current, optionId))} onChangeType={changeType}/>}
        <div className="button-row library-actions">{editing ? <><Button loading={busy === 'save'} disabled={!!busy} onClick={save}>{id ? '保存为新版本' : '保存独立题目'}</Button>{id && <Button variant="secondary" disabled={!!busy} onClick={() => setEditing(false)}>取消编辑</Button>}</> : version && <>{mayManage && <><Button disabled={!!busy} onClick={() => {setDraft({...version.content, id: 'content', required: false, jumpRules: []}); setEditing(true);}}>基于 v{version.versionNo} 编辑为新版本</Button><Button variant="secondary" disabled={!!busy} onClick={() => setConfirm('restore')}>将 v{version.versionNo} 恢复为新版本</Button></>}<Button variant="secondary" disabled={!!busy || question.archivedAt || version.archivedAt || version.permissions?.canUse === false} onClick={() => setConfirm('copy')}>另存为独立题目</Button><AddQuestionToBank questionId={id} disabled={!!busy}/>{mayManage && <><Button variant="ghost" disabled={!!busy} onClick={() => setConfirm('archiveQuestion')}>{question.archivedAt ? '取消题目归档' : '归档题目'}</Button><Button variant="ghost" disabled={!!busy} onClick={() => setConfirm('archiveVersion')}>{version.archivedAt ? '取消本版本归档' : '归档本版本'}</Button></>}</>}</div>
      </div>{id && <aside className="editor-section version-history"><h2>版本历史</h2><p className="field-hint">每次保存都会新增一版。</p><label className="field compact-filter"><span className="field-label">版本状态</span><select aria-label="历史版本归档筛选" value={versionArchive} disabled={editing || !!busy} onChange={e => {setVersionArchive(e.target.value); setPage(1);}}><option value="include">全部版本</option><option value="exclude">可用版本</option><option value="only">已归档版本</option></select></label>{versions.items.map(item => <button className={`version-row ${version?._id === item._id ? 'active' : ''}`} key={item._id} disabled={!!busy || editing} onClick={() => chooseVersion(item)}><strong>v{item.versionNo} · {item.title}</strong><span>{operationNames[item.operation]}{item.archivedAt ? ' · 已归档' : ''}</span><small>{dateTime(item.createdAt)}</small></button>)}{versions.total > 10 && <div className="button-row"><Button size="sm" variant="secondary" disabled={page === 1 || editing || !!busy} onClick={() => setPage(page - 1)}>上一页</Button><span>{page}</span><Button size="sm" variant="secondary" disabled={page * 10 >= versions.total || editing || !!busy} onClick={() => setPage(page + 1)}>下一页</Button></div>}</aside>}</div>
      {id && question && mayManage && !editing && <QuestionManagement key={`${id}:${question.revision}`} question={question} onChanged={() => load(version?._id)}/>}
    </>}
    <Modal open={!!confirm} title={{restore: '将历史内容恢复为新版本？', copy: '另存为独立题目？', archiveQuestion: question?.archivedAt ? '取消题目归档？' : '归档这道题目？', archiveVersion: version?.archivedAt ? '取消版本归档？' : '归档这个版本？'}[confirm]} onClose={() => setConfirm(null)} onConfirm={applyAction} busy={!!busy} confirmText="确认操作"><p>{confirm === 'restore' ? `复制 v${version?.versionNo} 的内容创建新版本，当前所有历史版本及已有问卷引用均保留。题目当前的归档状态也会保留。` : confirm === 'copy' ? '将当前版本保存到新的题目身份下。副本有自己的版本历史，可独立编辑。' : '归档会阻止新的选用，已经保存到问卷中的引用保持有效。取消归档后可重新选用。'}</p></Modal>
  </div>;
}
