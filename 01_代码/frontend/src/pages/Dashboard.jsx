import React, {useEffect, useState} from 'react';
import {Link, useSearchParams} from 'react-router-dom';
import {api, dateTime} from '../api';
import {useAuth, useToast} from '../context';
import {Badge, Button, EmptyState, Icon, Loading} from '../components';

export default function Dashboard() {
  const {user} = useAuth(), toast = useToast();
  const [params, setParams] = useSearchParams();
  const status = params.get('status') || '', page = Math.max(1, Number(params.get('page')) || 1);
  const [search, setSearch] = useState(''), [debounced, setDebounced] = useState('');
  const [data, setData] = useState({items: [], total: 0}), [counts, setCounts] = useState({all: 0, published: 0, draft: 0, closed: 0});
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [refresh, setRefresh] = useState(0);
  useEffect(() => {const timer = setTimeout(() => setDebounced(search.trim()), 250); return () => clearTimeout(timer);}, [search]);
  useEffect(() => {
    let live = true;
    Promise.all(['', 'published', 'draft', 'closed'].map(status => api.get(`/surveys?pageSize=1${status ? `&status=${status}` : ''}`))).then(items => {
      if (live) setCounts({all: items[0].total, published: items[1].total, draft: items[2].total, closed: items[3].total});
    }).catch(() => {});
    return () => {live = false;};
  }, [refresh]);
  useEffect(() => {
    let live = true; setLoading(true); setError('');
    const query = new URLSearchParams({page, pageSize: 9});
    if (status) query.set('status', status);
    if (debounced) query.set('search', debounced);
    api.get(`/surveys?${query}`).then(result => {if (live) setData(result);}).catch(e => {if (live) setError(e.message);}).finally(() => {if (live) setLoading(false);});
    return () => {live = false;};
  }, [status, page, debounced, refresh]);
  const changeStatus = value => setParams(value ? {status: value} : {});
  const changePage = value => setParams({...Object.fromEntries(params), page: value});
  const copy = async survey => {
    const link = `${window.location.origin}/survey/${survey.publicId}`;
    try {await navigator.clipboard.writeText(link); toast('填写链接已复制，可分享给其他登录用户。');}
    catch {toast('浏览器未允许复制，请在问卷管理页打开填写链接后复制地址。', 'error');}
  };
  const metrics = [{label: '全部问卷', value: counts.all, icon: 'file', status: '', tone: 'indigo'}, {label: '正在收集', value: counts.published, icon: 'chart', status: 'published', tone: 'green'}, {label: '待完善草稿', value: counts.draft, icon: 'pencil', status: 'draft', tone: 'amber'}, {label: '已关闭问卷', value: counts.closed, icon: 'done', status: 'closed', tone: 'gray'}];
  return <div className="dashboard"><div className="page-heading"><div><div className="eyebrow">YOUR WORKSPACE</div><h1>我的问卷<span className="heading-period">.</span></h1><p>整理每一个问题，听见每一份回答。</p></div><Link to="/surveys/new" className="btn btn-primary"><Icon name="plus" size={18}/>创建问卷</Link></div>
    <section className="welcome-banner"><div><div className="welcome-label"><span/>创作，从一个好问题开始</div><h2>{user.username}，准备好发起新的探索了吗？</h2><p>把想了解的事整理成问卷，让答案为下一步提供方向。</p><Link to="/surveys/new">新建一份问卷<Icon name="arrow-right" size={16}/></Link></div><div className="banner-art" aria-hidden="true"><div className="banner-sheet back"/><div className="banner-sheet"><span/><span/><i/><i/><i/></div><div className="banner-check"><Icon name="check" size={25}/></div><div className="banner-circle"/></div></section>
    <section className="metrics-grid" aria-label="问卷状态统计">{metrics.map(metric => <button key={metric.status} className={`metric-card ${status === metric.status ? 'selected' : ''}`} onClick={() => changeStatus(metric.status)}><span className={`metric-icon ${metric.tone}`}><Icon name={metric.icon} size={22}/></span><div><span>{metric.label}</span><strong>{metric.value.toLocaleString()}</strong></div><Icon name="arrow-up-right" size={17}/></button>)}</section>
    <section className="survey-section"><div className="section-toolbar"><div className="filter-tabs" role="tablist" aria-label="问卷状态筛选">{[{id:'',name:'全部问卷'}, {id:'published',name:'收集中'}, {id:'draft',name:'草稿'}, {id:'closed',name:'已关闭'}].map(tab => <button role="tab" aria-selected={status === tab.id} className={status === tab.id ? 'active' : ''} key={tab.id} onClick={() => changeStatus(tab.id)}>{tab.name}{tab.id === '' && <span>{counts.all}</span>}</button>)}</div><div className="list-tools"><label className="search-input"><Icon name="search" size={17}/><input aria-label="搜索问卷" placeholder="搜索问卷标题…" value={search} onChange={e => {setSearch(e.target.value); if (page !== 1) changePage(1);}}/>{search && <button aria-label="清空搜索" onClick={() => setSearch('')}><Icon name="x" size={15}/></button>}</label><button className="icon-button refresh-button" aria-label="刷新问卷" onClick={() => setRefresh(v => v + 1)}><Icon name="refresh" size={17}/></button></div></div>
    {error ? <EmptyState icon="alert-circle" title="问卷暂时没有加载成功" description={error} action={<Button onClick={() => setRefresh(v => v + 1)}>重新加载</Button>}/>
      : loading ? <Loading label="正在整理你的问卷…"/>
      : !data.items.length ? <EmptyState title={debounced ? '没有找到匹配的问卷' : status ? '这里暂时没有问卷' : '写下你的第一个问题'} description={debounced ? '换个关键词，或查看其他状态的问卷。' : '从单选、多选到条件跳转，按自己的方式开始。'} action={<Link to="/surveys/new" className="btn btn-primary"><Icon name="plus" size={18}/>创建问卷</Link>}/>
      : <div className="survey-grid">{data.items.map(survey => <article className="survey-card" key={survey._id}><div className="survey-card-top"><span className={`survey-symbol ${survey.status}`}><Icon name={survey.status === 'draft' ? 'pencil' : survey.status === 'closed' ? 'done' : 'file'} size={23}/></span><Badge status={survey.status}/></div><Link className="survey-card-title" to={`/surveys/${survey._id}/edit`}><h3>{survey.title}</h3></Link><p className="survey-card-description">{survey.description || '为好问题留一点空间。'}</p><div className="survey-card-details"><span><Icon name="list" size={15}/>{survey.questionCount ?? '—'} 道题目</span><span><Icon name="users" size={15}/>{survey.responseCount ?? 0} 份答卷</span><span><Icon name={survey.settings.anonymous ? 'lock' : 'users'} size={14}/>{survey.settings.anonymous ? '匿名' : '实名'}</span></div><div className="survey-card-date"><Icon name="clock" size={14}/>{survey.deadlineAt ? `截止于 ${dateTime(survey.deadlineAt)}` : `创建于 ${dateTime(survey.createdAt)}`}</div><div className="survey-card-actions"><Link className="card-action" to={`/surveys/${survey._id}/edit`}><Icon name="pencil" size={16}/>{survey.status === 'draft' ? '继续编辑' : '问卷管理'}</Link><div><button className="icon-button" title="复制填写链接" aria-label={`复制 ${survey.title} 的填写链接`} disabled={survey.status !== 'published'} onClick={() => copy(survey)}><Icon name="link" size={17}/></button><Link className="icon-button" title="查看统计" aria-label={`查看 ${survey.title} 的统计`} to={`/surveys/${survey._id}/results`}><Icon name="chart" size={18}/></Link></div></div></article>)}</div>}
    {!loading && !error && data.total > 0 && <div className="pagination"><span>共 {data.total} 份问卷</span><div><Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => changePage(page - 1)}>上一页</Button><span>{page} / {Math.max(1, Math.ceil(data.total / 9))}</span><Button variant="secondary" size="sm" disabled={page * 9 >= data.total} onClick={() => changePage(page + 1)}>下一页</Button></div></div>}
    </section>
  </div>;
}
