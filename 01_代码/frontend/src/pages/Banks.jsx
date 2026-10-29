import {useCallback, useEffect, useRef, useState} from 'react';
import {Link, useNavigate, useParams} from 'react-router-dom';
import {api, dateTime, typeNames} from '../api';
import {useAuth, useToast} from '../context';
import {Button, EmptyState, Icon, Loading, Modal} from '../components';
import {Pager} from '../QuestionManagement';

function QuestionSelector({open, excluded, onClose, onAdd, busy}) {
  const [data, setData] = useState(null), [search, setSearch] = useState(''), [scope, setScope] = useState('all'), [page, setPage] = useState(1), [error, setError] = useState('');
  useEffect(() => {if (open) {setSearch(''); setPage(1);}}, [open]);
  useEffect(() => {
    if (!open) return;
    let live = true; setData(null); setError('');
    const query = new URLSearchParams({scope, archive: 'include', search: search.trim(), page, pageSize: 10});
    api.get(`/questions?${query}`).then(value => {if (live) setData(value);}).catch(e => {if (live) setError(e.message);});
    return () => {live = false;};
  }, [open, scope, search, page]);
  return <Modal open={open} title="向题库添加题目" onClose={onClose} busy={busy} footer={<Button variant="secondary" disabled={busy} onClick={onClose}>完成</Button>}><p className="field-hint">加入分类不会创建题目或授予权限。同一道题目可以加入多个题库。</p><div className="management-filters"><input aria-label="搜索加入题库的题目" placeholder="搜索最新版本题干" value={search} onChange={e => {setSearch(e.target.value); setPage(1);}}/><select aria-label="加入题库的题目范围" value={scope} onChange={e => {setScope(e.target.value); setPage(1);}}><option value="all">全部可访问题目</option><option value="owned">我的题目</option><option value="shared">共享给我的题目</option></select></div>{error && <p className="error-notice" role="alert">{error}</p>}{!data ? !error && <Loading/> : <><div className="library-list">{data.items.map(item => <div className="library-row" key={item._id}><div><strong>{item.latestVersion.title}</strong><p className="field-hint">{typeNames[item.latestVersion.type]}</p></div><Button size="sm" disabled={busy || excluded.includes(item._id)} variant="secondary" onClick={() => onAdd(item)}>{excluded.includes(item._id) ? '已在此题库' : '加入题库'}</Button></div>)}{!data.items.length && <p>没有匹配的可访问题目。</p>}</div><Pager page={page} total={data.total} busy={busy} onChange={setPage}/></>}</Modal>;
}

export default function Banks() {
  const {id} = useParams(), navigate = useNavigate(), {user} = useAuth(), toast = useToast();
  const [banks, setBanks] = useState(null), [bank, setBank] = useState(null), [items, setItems] = useState(null);
  const [page, setPage] = useState(1), [busy, setBusy] = useState(''), [error, setError] = useState(''), [refresh, setRefresh] = useState(0);
  const [edit, setEdit] = useState(null), [confirm, setConfirm] = useState(null), [picker, setPicker] = useState(false);
  const activeId = useRef(id), loadGeneration = useRef(0); activeId.current = id;
  const load = useCallback(async () => {
    const generation = ++loadGeneration.current;
    const current = () => activeId.current === id && loadGeneration.current === generation;
    setError('');
    try {
      if (id) {const [value, contents] = await Promise.all([api.get(`/question-banks/${id}`), api.get(`/question-banks/${id}/items?page=${page}&pageSize=12`)]); if (current()) {setBank(value); setItems(contents);}}
      else {const value = await api.get(`/question-banks?${new URLSearchParams({page, pageSize: 12})}`); if (current()) setBanks(value);}
    } catch (e) {if (current()) setError(e.message);}
  }, [id, page]);
  useEffect(() => {setBank(null); setItems(null); setBanks(null); setEdit(null); setConfirm(null); setPicker(false); setPage(1);}, [id]);
  useEffect(() => {load(); return () => {loadGeneration.current++;};}, [load, refresh]);
  const write = (slot, method, path, body, label) => api.mutate(user._id, `bank:${id || 'new'}:${slot}`, method, path, body, label);
  const save = async () => {
    if (!edit?.name.trim() || busy) return;
    setBusy('save'); setError('');
    try {
      const value = edit.creating ? await write('create', 'POST', '/question-banks', {name: edit.name.trim(), description: edit.description}, '创建题库') : await write('edit', 'PATCH', `/question-banks/${id}`, {revision: bank.revision, name: edit.name.trim(), description: edit.description}, `编辑题库「${bank.name}」`);
      setEdit(null); toast('题库已保存。');
      if (edit.creating) navigate(`/question-banks/${value._id}`); else await load();
    } catch (e) {setError(e.message);} finally {setBusy('');}
  };
  const add = async question => {
    setBusy(`add:${question._id}`); setError('');
    try {await write(`add:${question._id}`, 'PUT', `/question-banks/${id}/items/${question._id}`, undefined, `将题目加入「${bank.name}」`); await load(); toast('题目已加入题库。');}
    catch (e) {setError(e.message);} finally {setBusy('');}
  };
  const apply = async () => {
    const action = confirm; setConfirm(null); setBusy(action.kind); setError('');
    try {
      if (action.kind === 'delete') {await write('delete', 'DELETE', `/question-banks/${id}?revision=${bank.revision}`, undefined, `删除题库「${bank.name}」`); navigate('/question-banks'); setRefresh(value => value + 1);}
      else {await write(`remove:${action.questionId}`, 'DELETE', `/question-banks/${id}/items/${action.questionId}`, undefined, '从题库移除题目'); await load();}
      toast('操作已完成。');
    } catch (e) {setError(e.message);} finally {setBusy('');}
  };
  return <div className="banks-page"><div className="page-heading"><div><div className="eyebrow">QUESTION BANKS</div><h1>{id ? bank?.name || '题库详情' : '我的题库'}<span className="heading-period">.</span></h1><p>{id ? bank?.description || '以分类整理自己的题目与共享给你的题目。' : '题库只管理分类；同一道题可加入多个题库。'}</p></div><div className="button-row">{id ? <Link className="btn btn-secondary" to="/question-banks">返回题库列表</Link> : <Button icon="plus" onClick={() => setEdit({creating: true, name: '', description: ''})}>创建题库</Button>}</div></div>
    {error && <div className="error-notice" role="alert">{error}<Button variant="secondary" size="sm" disabled={!!busy} onClick={load}>重新加载</Button></div>}
    {!id ? <>{!banks ? !error && <Loading/> : !banks.items.length ? <EmptyState title="还没有匹配的题库" description="创建分类后，可以加入已有题目。"/> : <div className="survey-grid bank-grid">{banks.items.map(value => <article className="survey-card" key={value._id}><Link className="survey-card-title" to={`/question-banks/${value._id}`} onClick={() => setPage(1)}><h3>{value.name}</h3></Link><p className="bank-description">{value.description || '暂无说明'}</p><p className="field-hint">{value.itemCount} 道题目 · 更新于 {dateTime(value.updatedAt)}</p><div className="survey-card-actions"><Link className="card-action" to={`/question-banks/${value._id}`} onClick={() => setPage(1)}>管理题库</Link></div></article>)}</div>}{banks && <Pager page={page} total={banks.total} size={12} onChange={setPage}/>}</>
      : !bank || !items ? !error && <Loading/> : <><div className="management-heading"><p className="field-hint">共 {items.total} 道题目 · 删除题库不会删除任何题目或版本。</p><div className="button-row"><Button disabled={!!busy} onClick={() => setPicker(true)}>添加题目到题库</Button><Button variant="secondary" disabled={!!busy} onClick={() => setEdit({name: bank.name, description: bank.description})}>编辑题库</Button><Button variant="ghost" disabled={!!busy} onClick={() => setConfirm({kind: 'delete'})}>删除题库</Button></div></div><section className="editor-section"><div className="library-list">{items.items.map(item => <div className="library-row" key={item.questionId}><div>{item.available ? <><Link className="text-link" to={`/questions/${item.questionId}`}>{item.question.latestVersion.title}</Link><p className="field-hint">{typeNames[item.question.latestVersion.type]} · 最新 v{item.question.latestVersion.versionNo}{item.question.archivedAt ? ' · 已归档' : ''}</p></> : <><strong>题目暂不可用</strong><p className="field-hint">可从此分类移除该项。</p></>}</div><Button size="sm" variant="secondary" disabled={!!busy} onClick={() => setConfirm({kind: 'remove', questionId: item.questionId})}>从题库移除</Button></div>)}{!items.items.length && <EmptyState title="此题库还没有题目" description="添加已有题目，开始整理分类。"/>}</div><Pager page={page} total={items.total} size={12} busy={!!busy} onChange={setPage}/></section></>}
    <Modal open={!!edit} title={edit?.creating ? '创建题库' : '编辑题库'} onClose={() => setEdit(null)} busy={!!busy} onConfirm={save} confirmText="保存题库"><div className="form-stack"><label className="field"><span className="field-label">题库名称</span><input aria-label="题库名称" maxLength={200} value={edit?.name || ''} onChange={e => setEdit({...edit, name: e.target.value})}/></label><label className="field"><span className="field-label">题库说明</span><textarea aria-label="题库说明" maxLength={2000} value={edit?.description || ''} onChange={e => setEdit({...edit, description: e.target.value})}/></label>{error && <p className="error-notice" role="alert">{error}</p>}</div></Modal>
    <Modal open={!!confirm} title={confirm?.kind === 'delete' ? '删除这个题库？' : '从题库移除题目？'} onClose={() => setConfirm(null)} onConfirm={apply} busy={!!busy} danger confirmText="确认删除"><p>{confirm?.kind === 'delete' ? '只删除题库容器及其中的分类关系。所有题目、历史版本、共享授权和问卷引用均继续保留。' : '只移除此题库中的分类关系，题目及其他题库中的分类关系继续保留。'}</p></Modal>
    <QuestionSelector open={picker} busy={!!busy} excluded={items?.items.map(item => item.questionId) || []} onClose={() => setPicker(false)} onAdd={add}/>
  </div>;
}
