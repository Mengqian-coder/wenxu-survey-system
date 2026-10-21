import React, {useState} from 'react';
import {Link, Navigate, Outlet, Route, Routes, useLocation, useParams} from 'react-router-dom';
import {useAuth} from './context';
import {Badge, Brand, Button, EmptyState, Icon, Loading, Modal} from './components';
import Auth from './pages/Auth';
import Dashboard from './pages/Dashboard';
import Editor from './pages/Editor';
import Fill from './pages/Fill';
import Results from './pages/Results';

function Protected({children}) {
  const {user, loading, error, reload} = useAuth();
  const location = useLocation();
  if (loading) return <Loading label="正在打开工作空间…"/>;
  if (error) return <EmptyState icon="alert-circle" title="暂时无法连接服务" description={error} action={<Button onClick={reload}>重新连接</Button>}/>;
  if (!user) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace/>;
  return children;
}

function Shell() {
  const {user, logout} = useAuth();
  const location = useLocation();
  const [mobile, setMobile] = useState(false), [guide, setGuide] = useState(false);
  const currentStatus = new URLSearchParams(location.search).get('status') || '';
  const section = location.pathname.includes('/results') ? '答卷与统计' : location.pathname.includes('/surveys/') ? '问卷编辑' : '我的问卷';
  const nav = [{to: '/', title: '全部问卷', icon: 'grid', status: ''}, {to: '/?status=published', title: '收集中', icon: 'chart', status: 'published'}, {to: '/?status=draft', title: '草稿箱', icon: 'pencil', status: 'draft'}, {to: '/?status=closed', title: '已关闭', icon: 'done', status: 'closed'}];
  return <div className="app-shell">
    {mobile && <button className="sidebar-scrim" aria-label="关闭导航" onClick={() => setMobile(false)}/>}
    <aside className={`sidebar ${mobile ? 'is-open' : ''}`}>
      <div className="sidebar-brand"><Brand/></div>
      <div className="workspace-label"><span className="workspace-avatar"><Icon name="grid" size={17}/></span><div>个人工作空间<small>你的每一份问卷，都在这里</small></div></div>
      <div className="nav-heading">工作空间</div>
      <nav aria-label="主导航">{nav.map(item => <Link key={item.status} to={item.to} onClick={() => setMobile(false)} className={`nav-item ${location.pathname === '/' && currentStatus === item.status ? 'active' : ''}`}><Icon name={item.icon} size={19}/>{item.title}{location.pathname === '/' && currentStatus === item.status && <span className="nav-active-dot"/>}</Link>)}</nav>
      <Link className="sidebar-create" to="/surveys/new" onClick={() => setMobile(false)}><Icon name="plus" size={18}/>创建一份新问卷</Link>
      <div className="sidebar-bottom"><div className="sidebar-help"><span className="help-spark"><Icon name="sparkles" size={20}/></span><strong>从提问，到理解</strong><p>用清晰的问题，收集有价值的回答。</p><button onClick={() => setGuide(true)}>查看使用指南<Icon name="arrow-up-right" size={15}/></button></div>
        <div className="sidebar-user"><span className="user-avatar">{Array.from(user.username)[0].toUpperCase()}</span><div><strong>{user.username}</strong><small>个人账号</small></div><button className="icon-button" aria-label="退出登录" onClick={logout}><Icon name="log-out" size={18}/></button></div>
      </div>
    </aside>
    <div className="workspace-main"><header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-menu" aria-label="打开导航" onClick={() => setMobile(true)}><Icon name="menu"/></button><span>工作空间</span><span className="breadcrumb-divider">/</span><strong>{section}</strong></div><div className="topbar-right"><span className="online-dot"/>个人工作空间<span className="topbar-avatar">{Array.from(user.username)[0].toUpperCase()}</span></div></header>
      <main className="page-content"><Outlet/></main><footer className="workspace-footer"><span>问序 · FORM & FIELD</span><span>每一份回答，都值得被认真对待。</span></footer>
    </div>
    <Modal open={guide} title="开始使用问序" onClose={() => setGuide(false)} footer={<Button onClick={() => setGuide(false)}>知道了</Button>}><ol className="guide-list"><li><strong>创建与编辑</strong><p>添加单选、多选、文本或数字题，设置必答、校验和跳转规则。</p></li><li><strong>保存并发布</strong><p>发布后题目结构锁定，复制填写链接即可分享给其他登录用户。</p></li><li><strong>收集与查看</strong><p>在结果页查看每道题的统计和已提交答卷。</p></li><li><strong>保护填写体验</strong><p>匿名方式对整份问卷生效。未提交的填写内容不会自动保存。</p></li></ol></Modal>
  </div>;
}

function EditorRoute() {const {id} = useParams(); return <Editor surveyId={id}/>;}
export default function App() {
  return <Routes>
    <Route path="/login" element={<Auth/>}/><Route path="/register" element={<Auth register/>}/>
    <Route path="/survey/:publicId" element={<Fill/>}/>
    <Route element={<Protected><Shell/></Protected>}>
      <Route path="/" element={<Dashboard/>}/><Route path="/surveys/new" element={<Editor/>}/>
      <Route path="/surveys/:id/edit" element={<EditorRoute/>}/><Route path="/surveys/:id/results" element={<Results/>}/>
    </Route>
    <Route path="*" element={<div className="standalone"><Brand/><EmptyState title="这个页面还没有被写下" description="请检查链接，或返回你的问卷工作空间。" action={<Link to="/" className="btn btn-primary">返回首页</Link>}/></div>}/>
  </Routes>;
}
