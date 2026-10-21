import React, {useState} from 'react';
import {Link, Navigate, useNavigate, useSearchParams} from 'react-router-dom';
import {useAuth} from '../context';
import {Brand, Button, Icon, Loading} from '../components';

export default function Auth({register = false}) {
  const auth = useAuth(), navigate = useNavigate();
  const [params] = useSearchParams();
  const rawNext = params.get('next') || '/';
  const next = /^\/(?!\/)/.test(rawNext) && !rawNext.includes('\\') && !/^\/(login|register)/.test(rawNext) ? rawNext : '/';
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [visible, setVisible] = useState(false);
  if (auth.loading) return <Loading/>;
  if (auth.user) return <Navigate to={next} replace/>;
  const submit = async event => {
    event.preventDefault(); setError('');
    if (register && password !== confirm) {setError('两次输入的密码不一致。'); return;}
    setBusy(true);
    try {await auth[register ? 'register' : 'login'](username, password); navigate(next, {replace: true});}
    catch (e) {setError(e.message);}
    finally {setBusy(false);}
  };
  return <div className="auth-page"><section className="auth-story"><Brand/><div className="auth-story-copy"><span className="eyebrow">ASK WELL. UNDERSTAND MORE.</span><h1>好问题，<br/>让理解更进一步。</h1><p>从第一个问题，到每一份真实回答。<br/>让问卷的创建、收集与分析，井然有序。</p><div className="auth-art" aria-hidden="true"><div className="art-floating art-dot-grid"/><div className="art-survey"><span className="art-tiny-label">一份关于想法的问卷</span><div className="art-title-line"/><div className="art-option"><i/><span/></div><div className="art-option selected"><i/><span/><Icon name="check" size={13}/></div><div className="art-option"><i/><span/></div><div className="art-progress"><i/></div></div><div className="art-answer"><span className="art-answer-icon"><Icon name="chart" size={22}/></span><div>让答案被看见<small>每一个想法都有意义</small></div></div><span className="art-plus">+</span></div></div><p className="auth-story-foot">问序 · FORM & FIELD</p></section>
    <section className="auth-form-side"><div className="auth-mobile-brand"><Brand/></div><div className="auth-form-wrap"><span className="eyebrow">你的问卷工作空间</span><h2>{register ? '创建你的账号' : '欢迎回来'}</h2><p className="muted">{register ? '从这里开始，整理问题，发现答案。' : '登录后，继续你的每一次探索。'}</p>{next.startsWith('/survey/') && <div className="notice"><Icon name="lock" size={16}/>这份问卷需要登录后填写，登录后将自动返回。</div>}
      <form onSubmit={submit}><label className="field"><span className="field-label">用户名</span><input aria-label="用户名" autoComplete="username" name="username" value={username} onChange={e => setUsername(e.target.value)} placeholder="请输入用户名" required minLength={register ? 3 : 1} maxLength={32}/>{register && <span className="field-hint">3–32 个字符，不含空白。</span>}</label>
      <label className="field"><span className="field-label">密码</span><div className="password-field"><input autoComplete={register ? 'new-password' : 'current-password'} name="password" value={password} onChange={e => setPassword(e.target.value)} placeholder={register ? '设置至少 8 位密码' : '请输入密码'} type={visible ? 'text' : 'password'} required minLength={register ? 8 : 1} maxLength={128}/><button type="button" aria-label={visible ? '隐藏密码' : '显示密码'} onClick={() => setVisible(v => !v)}><Icon name="eye" size={18}/></button></div></label>
      {register && <label className="field"><span className="field-label">确认密码</span><input type="password" autoComplete="new-password" name="confirmPassword" value={confirm} onChange={e => setConfirm(e.target.value)} placeholder="请再次输入密码" required minLength={8} maxLength={128}/></label>}
      {error && <div className="error-notice" role="alert"><Icon name="alert-circle" size={17}/>{error}</div>}
      <Button type="submit" className="auth-submit" loading={busy}>{register ? '注册并开始使用' : '登录'}<Icon name="arrow-right" size={17}/></Button></form>
      <p className="auth-switch">{register ? '已有账号？' : '还没有账号？'} <Link to={`${register ? '/login' : '/register'}${next !== '/' ? `?next=${encodeURIComponent(next)}` : ''}`}>{register ? '立即登录' : '创建账号'}</Link></p>
      <div className="auth-trust"><Icon name="lock" size={14}/>密码加密保存 · 登录状态仅在当前浏览器会话保留</div></div><div className="auth-footer">简单提问，认真倾听。</div></section>
  </div>;
}
