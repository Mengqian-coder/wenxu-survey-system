import React, {createContext, useCallback, useContext, useEffect, useRef, useState} from 'react';
import {api, ApiError} from './api';
import {Button, Icon, Modal} from './components';

const AuthContext = createContext(null);
const ToastContext = createContext(() => {});
export const useAuth = () => useContext(AuthContext);
export const useToast = () => useContext(ToastContext);

export function Providers({children}) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [toasts, setToasts] = useState([]);
  const currentUser = useRef(null);
  currentUser.current = user;
  const [reauthUser, setReauthUser] = useState(null);
  const [reauthPassword, setReauthPassword] = useState('');
  const [reauthError, setReauthError] = useState('');
  const [reauthBusy, setReauthBusy] = useState(false);
  const toast = useCallback((message, type = 'success') => {
    const id = crypto.randomUUID();
    setToasts(items => [...items, {id, message, type}]);
    setTimeout(() => setToasts(items => items.filter(item => item.id !== id)), 5000);
  }, []);
  const reload = useCallback(async () => {
    setLoading(true); setError('');
    if (!sessionStorage.getItem('wenxu.access')) { setUser(null); setLoading(false); return; }
    try { setUser(await api.get('/auth/me')); }
    catch (e) { if (e.status !== 401) setError(e.message); else setUser(null); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => {
    reload();
    const expired = () => {
      sessionStorage.removeItem('wenxu.access');
      // Keep private editor state mounted while the same account signs in again.
      // Filling has its own account-bound reauthentication dialog.
      if (currentUser.current && !window.location.pathname.startsWith('/survey/')) {
        setReauthUser(currentUser.current); setReauthPassword(''); setReauthError('');
      } else setUser(null);
    };
    window.addEventListener('wenxu:unauthorized', expired);
    return () => window.removeEventListener('wenxu:unauthorized', expired);
  }, [reload]);
  const login = async (username, password, expectedUserId) => {
    const data = await api.post('/auth/login', {username, password});
    sessionStorage.setItem('wenxu.access', data.access_token);
    const me = await api.get('/auth/me');
    if (expectedUserId && me._id !== expectedUserId) {
      sessionStorage.removeItem('wenxu.access');
      throw new ApiError('请使用原账号登录，以保留当前页面的修改。', {status: 403, code: 'ACCOUNT_MISMATCH'});
    }
    setUser(me); setError(''); setReauthUser(null); setReauthPassword('');
    return me;
  };
  const register = async (username, password) => {
    await api.post('/auth/register', {username, password});
    return login(username, password);
  };
  const logout = () => {sessionStorage.removeItem('wenxu.access'); setUser(null); setError(''); setReauthUser(null); setReauthPassword('');};
  const reauthenticate = async event => {
    event.preventDefault(); setReauthBusy(true); setReauthError('');
    try {await login(reauthUser.username, reauthPassword, reauthUser._id); toast('登录已恢复，当前修改仍保留。');}
    catch (e) {setReauthError(e.message);}
    finally {setReauthBusy(false);}
  };
  return <AuthContext.Provider value={{user, loading, error, reload, login, register, logout}}>
    <ToastContext.Provider value={toast}>{children}
      <Modal open={!!reauthUser} title="登录已过期，当前修改已保留" busy={reauthBusy}
        footer={<><Button variant="secondary" disabled={reauthBusy} onClick={logout}>退出并放弃当前页面</Button><Button type="submit" form="workspace-reauth" loading={reauthBusy}>重新登录</Button></>}>
        <p>请使用原账号「{reauthUser?.username}」重新登录，然后继续刚才的操作。页面中的未保存修改仍保留在这里。</p>
        <form id="workspace-reauth" onSubmit={reauthenticate} className="fill-login-form"><label className="field"><span className="field-label">原账号密码</span><input aria-label="原账号密码" type="password" autoComplete="current-password" required maxLength={128} value={reauthPassword} onChange={e => setReauthPassword(e.target.value)}/></label>{reauthError && <div className="error-notice" role="alert">{reauthError}</div>}</form>
      </Modal>
      <div className="toast-stack" aria-live="polite">{toasts.map(item => <div key={item.id} className={`toast ${item.type}`} role="status">
        <Icon name={item.type === 'error' ? 'alert-circle' : 'check'} size={18}/><span>{item.message}</span>
        <button type="button" aria-label="关闭提示" onClick={() => setToasts(items => items.filter(t => t.id !== item.id))}><Icon name="x" size={16}/></button>
      </div>)}</div>
    </ToastContext.Provider>
  </AuthContext.Provider>;
}
