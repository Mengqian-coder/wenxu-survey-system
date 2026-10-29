import {useEffect, useState} from 'react';
import {Link} from 'react-router-dom';
import {api, dateTime} from './api';
import {useAuth, useToast} from './context';
import {Badge, Button, Loading, Modal} from './components';
import {loadBanks} from './management-utils';

export function Pager({page, total, size = 10, busy, onChange}) {
  if (total <= size) return null;
  return <div className="button-row management-pagination"><Button size="sm" variant="secondary" disabled={busy || page === 1} onClick={() => onChange(page - 1)}>上一页</Button><span>{page} / {Math.ceil(total / size)}</span><Button size="sm" variant="secondary" disabled={busy || page * size >= total} onClick={() => onChange(page + 1)}>下一页</Button></div>;
}

export default function QuestionManagement({question, onChanged}) {
  const {user} = useAuth(), toast = useToast(), id = question._id;
  const [shares, setShares] = useState(null), [usage, setUsage] = useState(null), [state, setState] = useState('all');
  const [sharePage, setSharePage] = useState(1), [usagePage, setUsagePage] = useState(1), [username, setUsername] = useState('');
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [confirm, setConfirm] = useState(null), [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let live = true; setShares(null); setError('');
    api.get(`/questions/${id}/shares?state=${state}&page=${sharePage}&pageSize=10`).then(value => {if (live) setShares(value);}).catch(e => {if (live) setError(e.message);});
    return () => {live = false;};
  }, [id, state, sharePage, refresh]);
  useEffect(() => {
    let live = true; setUsage(null);
    api.get(`/questions/${id}/usage?page=${usagePage}&pageSize=10`).then(value => {if (live) setUsage(value);}).catch(e => {if (live) setError(e.message);});
    return () => {live = false;};
  }, [id, usagePage, refresh]);
  const mutate = async action => {
    if (busy) return;
    setBusy(action.kind); setError(''); setConfirm(null);
    try {
      const revision = shares?.questionRevision ?? question.revision;
      if (action.kind === 'revoke') await api.mutate(user._id, `question:${id}:revoke:${action.share._id}`, 'POST', `/questions/${id}/shares/${action.share._id}/revoke`, {revision}, `撤销对 ${action.share.recipient.username} 的题目共享`);
      else await api.mutate(user._id, `question:${id}:share:${action.username}`, 'POST', `/questions/${id}/shares`, {revision, recipientUsername: action.username}, `共享整道题目给 ${action.username}`);
      setUsername(''); setRefresh(value => value + 1); await onChanged(); toast('共享权限已更新。');
    } catch (e) {setError(e.message);} finally {setBusy('');}
  };
  return <div className="management-sections">
    <section className="editor-section" aria-labelledby="sharing-heading"><div className="management-heading"><div><h2 id="sharing-heading">共享管理</h2><p className="field-hint">共享整道题目的所有历史及未来版本。接收者可以选用或另存自己的独立副本。</p></div></div>
      {error && <div role="alert" className="error-notice">{error}<Button size="sm" variant="secondary" disabled={!!busy} onClick={() => setRefresh(value => value + 1)}>重新加载管理信息</Button></div>}
      <form className="share-form" onSubmit={event => {event.preventDefault(); if (username.trim() && !busy) setConfirm({kind: 'grant', username: username.trim()});}}><label className="field"><span className="field-label">接收者用户名</span><input aria-label="接收者用户名" maxLength={128} value={username} onChange={e => setUsername(e.target.value)} placeholder="输入已注册用户的完整用户名" disabled={!!busy}/></label><button type="submit" className="btn btn-primary" disabled={!username.trim() || !!busy}>共享整道题目</button></form>
      <label className="field compact-filter"><span className="field-label">授权状态</span><select aria-label="共享状态筛选" value={state} onChange={e => {setState(e.target.value); setSharePage(1);}}><option value="all">全部授权</option><option value="active">有效授权</option><option value="revoked">已撤销授权</option></select></label>
      {!shares ? <Loading label="正在读取共享信息…"/> : <><div className="library-list">{shares.items.map(share => <div className="library-row" key={share._id}><div><strong>{share.recipient.username}</strong><p className="field-hint">{share.revokedAt ? `撤销于 ${dateTime(share.revokedAt)}` : `授权于 ${dateTime(share.grantedAt)}`}</p></div><span className={`badge badge-${share.revokedAt ? 'neutral' : 'success'}`}>{share.revokedAt ? '已撤销' : '有效'}</span><Button size="sm" variant="secondary" disabled={!!busy} onClick={() => setConfirm(share.revokedAt ? {kind: 'grant', username: share.recipient.username} : {kind: 'revoke', share})}>{share.revokedAt ? '重新授权' : '撤销授权'}</Button></div>)}{!shares.items.length && <p className="field-hint">当前筛选下没有授权记录。</p>}</div><Pager page={sharePage} total={shares.total} busy={!!busy} onChange={setSharePage}/></>}
    </section>
    <section className="editor-section" aria-labelledby="usage-heading"><h2 id="usage-heading">引用查询</h2><p className="field-hint">仅展示你自己问卷的明细；其他用户的问卷只计数。题目共享不包含答卷权限。</p>
      {!usage ? <Loading label="正在统计引用…"/> : <><div className="usage-counts"><div><strong>{usage.totalSurveyCount}</strong><span>引用问卷总数</span></div><div><strong>{usage.visibleSurveyCount}</strong><span>我的问卷</span></div><div><strong>{usage.otherSurveyCount}</strong><span>其他用户问卷</span></div></div><div className="library-list">{usage.items.map(item => <div className="library-row" key={item.surveyId}><div><Link className="text-link" to={`/surveys/${item.surveyId}/edit`}>{item.title}</Link><p className="field-hint">固定使用 v{item.versionNo}</p></div><Badge status={item.status}/></div>)}{!usage.items.length && <p className="field-hint">你的问卷尚未引用这道题目。</p>}</div><Pager page={usagePage} total={usage.total} onChange={setUsagePage}/></>}
    </section>
    <Modal open={!!confirm} title={confirm?.kind === 'revoke' ? '撤销整道题目的授权？' : '共享整道题目？'} onClose={() => setConfirm(null)} busy={!!busy} onConfirm={() => mutate(confirm)} confirmText={confirm?.kind === 'revoke' ? '确认撤销' : '确认共享'}><p>{confirm?.kind === 'revoke' ? '撤销后对方不能再新增引用或切换版本。已经保存引用的草稿仍可发布，已经另存的独立副本继续属于对方。' : `将全部历史及未来版本授权给「${confirm?.username}」。对方可选用或另存自己的独立副本，并共享该副本。`}</p></Modal>
  </div>;
}

export function AddQuestionToBank({questionId, disabled}) {
  const {user} = useAuth(), toast = useToast();
  const [open, setOpen] = useState(false), [banks, setBanks] = useState(null), [busy, setBusy] = useState(''), [error, setError] = useState('');
  useEffect(() => {
    if (!open) return;
    let live = true; setBanks(null); setError('');
    loadBanks(api.get).then(value => {if (live) setBanks(value);}).catch(e => {if (live) setError(e.message);});
    return () => {live = false;};
  }, [open]);
  const add = async bank => {
    setBusy(bank._id); setError('');
    try {await api.mutate(user._id, `bank:${bank._id}:add:${questionId}`, 'PUT', `/question-banks/${bank._id}/items/${questionId}`, undefined, `将题目加入题库「${bank.name}」`); await api.get(`/question-banks/${bank._id}`); toast(`已加入「${bank.name}」。`);}
    catch (e) {setError(e.message);} finally {setBusy('');}
  };
  return <><Button variant="secondary" disabled={disabled} onClick={() => setOpen(true)}>加入题库</Button><Modal open={open} title="将题目加入题库" onClose={() => setOpen(false)} busy={!!busy} footer={<Button variant="secondary" disabled={!!busy} onClick={() => setOpen(false)}>完成</Button>}><p className="field-hint">同一道题可加入多个题库。题库只管理分类，题目权限保持独立。</p>{error && <p role="alert" className="error-notice">{error}</p>}{!banks ? <Loading/> : <div className="library-list">{banks.map(bank => <div className="library-row" key={bank._id}><div><strong>{bank.name}</strong></div><Button size="sm" variant="secondary" loading={busy === bank._id} disabled={!!busy} onClick={() => add(bank)}>加入此题库</Button></div>)}{!banks.length && <p>还没有题库。<Link className="text-link" to="/question-banks">创建题库</Link></p>}</div>}</Modal></>;
}
