import {useEffect, useState} from 'react';
import {Link, useLocation, useNavigate} from 'react-router-dom';
import {api} from './api';
import {pendingMutations} from './mutations';
import {useAuth, useToast} from './context';
import {Button, Icon, Modal} from './components';
import {mutationTarget} from './management-utils';

export default function PendingMutations() {
  const {user} = useAuth(), toast = useToast(), location = useLocation();
  const navigate = useNavigate();
  const [items, setItems] = useState([]), [busy, setBusy] = useState(''), [result, setResult] = useState(null), [error, setError] = useState('');
  useEffect(() => {
    const refresh = () => setItems(pendingMutations(user._id));
    refresh(); window.addEventListener('wenxu:mutations', refresh); window.addEventListener('storage', refresh);
    return () => {window.removeEventListener('wenxu:mutations', refresh); window.removeEventListener('storage', refresh);};
  }, [user._id]);
  useEffect(() => {setResult(null); setError('');}, [location.pathname]);
  const retry = async entry => {
    setBusy(entry.key); setError('');
    try {
      const value = await api.retryMutation(entry);
      const target = mutationTarget(entry, value);
      toast('原操作已确认成功，正在打开最新内容。');
      if (target === location.pathname) window.location.reload();
      else if (target) navigate(target, {replace: true});
      else setResult(value);
    }
    catch (e) {setError(e.message);} finally {setBusy('');}
  };
  const target = result?.question?._id ? `/questions/${result.question._id}` : result?._id ? `/surveys/${result._id}/edit` : null;
  return <>{items.length > 0 && <section className="notice info-notice pending-notice"><Icon name="clock"/><div><strong>有 {items.length} 项管理操作正在等待确认</strong><p>请求内容与确认凭据已保留。网络恢复后可重试原操作，避免重复创建。</p>{items.map(entry => <div className="pending-row" key={entry.key}><span>{entry.label}</span><Button size="sm" variant="secondary" disabled={!!busy} loading={busy === entry.key} onClick={() => retry(entry)}>确认原操作</Button></div>)}{error && <p role="alert">{error}</p>}</div></section>}
    <Modal open={!!result} title="原操作已确认成功" onClose={() => setResult(null)} footer={<>{target && <Link className="btn btn-primary" to={target} onClick={() => {setResult(null); if (location.pathname === target) window.location.reload();}}>打开最新内容</Link>}<Button variant="secondary" onClick={() => setResult(null)}>完成</Button></>}><p>服务器已返回原操作的成功结果。继续编辑前，请打开最新内容。</p></Modal>
  </>;
}
