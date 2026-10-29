import {useEffect, useState} from 'react';
import {Link} from 'react-router-dom';
import {api, dateTime, typeNames} from './api';
import {Button, Loading, Modal} from './components';
import {QuestionCard} from './QuestionCard';
import {loadBanks, selectableQuestion} from './management-utils';

export default function QuestionPicker({open, questionId, currentInstance, excluded = [], onClose, onSelect}) {
  const [selected, setSelected] = useState(null), [search, setSearch] = useState(''), [page, setPage] = useState(1);
  const [scope, setScope] = useState('all'), [bankId, setBankId] = useState(''), [banks, setBanks] = useState([]);
  const [data, setData] = useState({items: [], total: 0}), [loading, setLoading] = useState(false), [error, setError] = useState('');
  const [preview, setPreview] = useState(null), [previewBusy, setPreviewBusy] = useState(false);
  const [confirmSwitch, setConfirmSwitch] = useState(false);
  useEffect(() => {if (open) {setSelected(questionId || null); setSearch(''); setPage(1); setPreview(null); setBankId(''); setConfirmSwitch(false);}}, [open, questionId]);
  useEffect(() => {
    if (!open || questionId) return;
    let live = true;
    loadBanks(api.get).then(value => {if (live) setBanks(value);}).catch(e => {if (live) setError(e.message);});
    return () => {live = false;};
  }, [open, questionId]);
  useEffect(() => {
    if (!open) return;
    let live = true; setLoading(true); setError('');
    const query = new URLSearchParams({page, pageSize: 8});
    let path;
    if (selected) {query.set('archive', 'exclude'); path = `/questions/${selected}/versions?${query}`;}
    else if (bankId) path = `/question-banks/${bankId}/items?${query}`;
    else {query.set('archive', 'exclude'); query.set('scope', scope); if (search.trim()) query.set('search', search.trim()); path = `/questions?${query}`;}
    api.get(path).then(result => {if (live) setData(bankId && !selected ? {...result, items: result.items.map(item => item.available ? item.question : {_id: item.questionId, available: false})} : result);}).catch(e => {if (live) setError(e.message);}).finally(() => {if (live) setLoading(false);});
    return () => {live = false;};
  }, [open, selected, page, search, scope, bankId]);
  const view = async version => {
    setPreviewBusy(true); setError(''); setConfirmSwitch(false);
    try {setPreview((await api.get(`/questions/${selected}/versions/${version._id}`)).version);}
    catch (e) {setError(e.message);} finally {setPreviewBusy(false);}
  };
  const select = async () => {
    if (currentInstance?.versionId === preview?._id) return;
    setPreviewBusy(true); setError('');
    try {
      const current = (await api.get(`/questions/${selected}/versions/${preview._id}`)).version;
      if (current.archivedAt || current.permissions?.canUse === false) {setPreview(current); setError('此版本当前不能新增选用，请选择其他题目或版本。'); return;}
      onSelect(selected, current);
    } catch (e) {setError(e.message);} finally {setPreviewBusy(false);}
  };
  const switching = currentInstance && preview && currentInstance.versionId !== preview._id;
  const ruleCount = switching ? currentInstance.jumpRules.length : 0;
  const removedOptions = switching ? (currentInstance.options || []).filter(option => !preview.content.options?.some(next => next.id === option.id)) : [];
  const requestSelect = () => ruleCount ? setConfirmSwitch(true) : select();
  const impact = switching && <div className={`notice ${ruleCount ? 'error-notice' : 'info-notice'}`} role="note" aria-label="换版影响" data-testid="version-switch-impact"><div>
    <strong>换版影响：v{currentInstance.versionNo} → v{preview.versionNo}</strong>
    {currentInstance.type !== preview.content.type && <p>题型将从{typeNames[currentInstance.type]}变为{typeNames[preview.content.type]}。</p>}
    <p>题型、题干、说明、选项和答案校验将使用新版本。请重新核对填写限制。</p>
    {removedOptions.length > 0 && <p>新版本已移除旧选项：{removedOptions.map(option => option.label).join('、')}。</p>}
    <p>{ruleCount ? `本题现有 ${ruleCount} 条跳转规则将全部清空，包括仍可能适用的规则；需要按新版本重新配置。` : '本题没有跳转规则，无需清空。'}</p>
    <p>必答设置、题目位置以及其他题指向本题的跳转会保留。确认换版只修改当前草稿，保存后生效。</p>
  </div></div>;
  const close = () => {if (!previewBusy) onClose();};
  return <Modal open={open} title={confirmSwitch ? '更换题目版本？' : preview ? `选用 v${preview.versionNo}` : selected ? '选择正式版本' : '从我的题目选用'} onClose={close} busy={previewBusy}
    footer={<><Button variant="secondary" disabled={previewBusy} onClick={close}>取消</Button>{preview && <Button variant={confirmSwitch ? 'danger' : 'primary'} disabled={previewBusy || preview.archivedAt || preview.permissions?.canUse === false || currentInstance?.versionId === preview._id} onClick={confirmSwitch ? select : requestSelect}>{currentInstance?.versionId === preview._id ? '当前使用版本' : confirmSwitch ? '确认换版并清空规则' : `使用 v${preview.versionNo}`}</Button>}</>}>
    <p className="field-hint">问卷固定使用所选版本。之后题目产生新版本，当前问卷不会自动替换。</p>
    <p className="field-hint">题目版本只包含题型、题干、说明、选项和答案校验；必答、顺序和跳转由本问卷单独设置。</p>
    {error && <p className="error-notice" role="alert">{error}</p>}
    {preview ? <>{impact}{confirmSwitch ? <p>取消可保留当前版本和全部规则。确认后，请在保存或发布前重新检查本题跳转。</p> : <><Button variant="ghost" disabled={previewBusy} onClick={() => setPreview(null)}>返回版本列表</Button><QuestionCard question={{...preview.content, id: 'preview', required: false, jumpRules: []}} locked contentOnly/></>}</>
      : <>{!questionId && selected && <Button variant="ghost" onClick={() => {setSelected(null); setPage(1);}}>返回题目列表</Button>}
        {!selected && <><div className="management-filters"><label className="field"><span className="field-label">题目来源</span><select aria-label="选题来源" value={scope} disabled={!!bankId} onChange={e => {setScope(e.target.value); setPage(1);}}><option value="all">全部可访问题目</option><option value="owned">我的题目</option><option value="shared">共享给我的题目</option></select></label><label className="field"><span className="field-label">按题库选题</span><select aria-label="按题库选题" value={bankId} onChange={e => {setBankId(e.target.value); setPage(1);}}><option value="">全部分类</option>{banks.map(bank => <option key={bank._id} value={bank._id}>{bank.name}</option>)}</select></label></div>{!bankId && <label className="field"><span className="field-label">搜索题目</span><input aria-label="搜索可选题目" value={search} onChange={e => {setSearch(e.target.value); setPage(1);}}/></label>}</>}
        {loading ? <Loading/> : <div className="library-list">{data.items.map(item => <div className="library-row" key={item._id}><div><strong>{item.available === false ? '题目暂不可用' : selected ? `v${item.versionNo} · ${item.title}` : item.latestVersion.title}</strong>{item.available !== false && <p className="field-hint">{typeNames[selected ? item.type : item.latestVersion.type]} · {dateTime(item.createdAt)}</p>}</div><Button size="sm" variant="secondary" disabled={previewBusy || !selectableQuestion(item) || (!selected && excluded.includes(item._id))} onClick={() => selected ? view(item) : (setSelected(item._id), setPage(1))}>{item.available === false ? '暂不可选' : selected ? '查看并选用' : excluded.includes(item._id) ? '已在问卷中' : '选择版本'}</Button></div>)}{!data.items.length && <p>暂无可选{selected ? '版本' : '题目'}。{!selected && <Link to="/questions/new">先创建独立题目</Link>}</p>}</div>}
        {data.total > 8 && <div className="button-row"><Button size="sm" variant="secondary" disabled={page === 1 || loading} onClick={() => setPage(page - 1)}>上一页</Button><span>{page} / {Math.ceil(data.total / 8)}</span><Button size="sm" variant="secondary" disabled={page * 8 >= data.total || loading} onClick={() => setPage(page + 1)}>下一页</Button></div>}
      </>}
  </Modal>;
}
