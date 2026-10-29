import React, {useEffect, useState} from 'react';
import {Link, useParams} from 'react-router-dom';
import {api, dateTime, typeNames} from '../api';
import {Badge, Button, EmptyState, Icon, Loading} from '../components';
import {statisticLocation} from '../statistics-utils';

const numeric = value => value === null || value === undefined ? '暂无数据' : Math.abs(value) >= 1e9 || value !== 0 && Math.abs(value) < 1e-4 ? value.toExponential(4) : new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 4}).format(value);
function answerLabel(q, value) {
  if (value === null || value === undefined) return '未填写';
  if (q?.type === 'single') return q.options.find(o => o.id === value)?.label || value;
  if (q?.type === 'multiple') return value.map(id => q.options.find(o => o.id === id)?.label || id).join('、');
  return String(value);
}
export default function Results() {
  const {id} = useParams();
  const [survey, setSurvey] = useState(null), [stats, setStats] = useState(null), [responses, setResponses] = useState(null);
  const [tab, setTab] = useState('statistics'), [questionId, setQuestionId] = useState(''), [page, setPage] = useState(1);
  const [busy, setBusy] = useState(true), [error, setError] = useState(''), [refresh, setRefresh] = useState(0);
  useEffect(() => {let live = true; api.get(`/surveys/${id}`).then(value => {if (live) setSurvey(value);}).catch(e => {if (live) {setError(e.message); setBusy(false);}}); return () => {live = false;};}, [id, refresh]);
  useEffect(() => {
    if (!survey) return;
    let live = true; setBusy(true); setError('');
    const path = tab === 'statistics' ? `/surveys/${id}/statistics?page=${page}&pageSize=20${questionId ? `&questionId=${encodeURIComponent(questionId)}` : ''}` : `/surveys/${id}/responses?page=${page}&pageSize=10`;
    api.get(path).then(value => {if (live) tab === 'statistics' ? setStats(value) : setResponses(value);}).catch(e => {if (live) setError(e.message);}).finally(() => {if (live) setBusy(false);});
    return () => {live = false;};
  }, [id, survey, tab, questionId, page, refresh]);
  if (!survey) return error ? <EmptyState icon="alert-circle" title="无法打开结果" description={error} action={<Link to="/" className="btn btn-secondary">返回问卷列表</Link>}/> : <Loading/>;
  const total = stats?.totalResponses ?? responses?.total ?? 0;
  const detail = stats?.questions.find(q => q.questionId === questionId);
  const pages = tab === 'responses' ? Math.ceil((responses?.total || 0) / 10) : Math.ceil((detail?.valueTotal || 0) / 20);
  return <div className="results-page"><Link className="back-link" to="/"><Icon name="arrow-left" size={16}/>返回我的问卷</Link><div className="page-heading"><div><div className="eyebrow">RESPONSES & INSIGHTS</div><h1>答卷与统计<span className="heading-period">.</span></h1><p>{survey.title}</p></div><div className="heading-actions"><Button variant="secondary" icon="refresh" onClick={() => setRefresh(v => v + 1)}>刷新结果</Button><Link className="btn btn-primary" to={`/surveys/${id}/edit`}><Icon name="settings" size={17}/>管理问卷</Link></div></div>
    <div className="results-summary"><div className="results-total"><span className="metric-icon indigo"><Icon name="users" size={24}/></span><div><span>已收集答卷</span><strong>{total}<small>份</small></strong></div></div><div className="results-summary-item"><span>问卷题目</span><strong>{survey.questions.length} 道</strong></div><div className="results-summary-item"><span>填写方式</span><strong>{survey.settings.anonymous ? '整份匿名' : '实名填写'}</strong></div><div className="results-summary-item"><span>当前状态</span><Badge status={survey.status}/></div></div>
    <div className="section-toolbar results-toolbar"><div className="filter-tabs" role="tablist" aria-label="结果视图"><button role="tab" aria-selected={tab === 'statistics'} className={tab === 'statistics' ? 'active' : ''} onClick={() => {setTab('statistics'); setPage(1);}}>统计概览</button><button role="tab" aria-selected={tab === 'responses'} className={tab === 'responses' ? 'active' : ''} onClick={() => {setTab('responses'); setPage(1);}}>原始答卷</button></div>{tab === 'statistics' && <select aria-label="选择统计题目" value={questionId} onChange={e => {setQuestionId(e.target.value); setPage(1);}}><option value="">全部题目</option>{survey.questions.map((q, index) => <option key={q.id} value={q.id}>{index + 1}. {q.title}</option>)}</select>}</div>
    {error ? <EmptyState icon="alert-circle" title="暂时无法加载结果" description={error} action={<Button onClick={() => setRefresh(v => v + 1)}>重试</Button>}/>
      : busy ? <Loading label="正在整理回答…"/>
      : tab === 'statistics' ? <div className="statistics-list">{total === 0 && <div className="notice"><Icon name="clock" size={17}/>还没有收到答卷，分享已发布的问卷后，结果会出现在这里。</div>}{stats?.questions.map(q => <article className="stat-card" key={q.questionId}><header><span className="question-number">{survey.questions.findIndex(item => item.id === q.questionId) + 1}</span><div><h3>{q.title}</h3><span className="muted small">{typeNames[q.type]}<span className="text-divider">·</span>{q.answeredCount} 份有效回答</span></div></header>
      {(q.type === 'single' || q.type === 'multiple') && <div className="option-stat-list">{q.options.map((option, index) => {const percent = q.answeredCount ? option.count / q.answeredCount * 100 : 0; return <div className="option-stat" key={option.optionId}><div><span>{option.label}</span><strong>{option.count} <small>份 · {percent.toFixed(0)}%</small></strong></div><div className="stat-track"><span style={{width: `${percent}%`, background: ['#6366f1', '#8b8cf8', '#a3a3fa', '#b8b9fc'][index % 4]}}/></div></div>;})}{q.type === 'multiple' && <p className="field-hint">多选题每个选项分别计数，占比之和可能超过 100%。</p>}</div>}
      {q.type === 'number' && <div className="number-stat"><span>有效答案平均值</span><strong>{numeric(q.average)}</strong></div>}
      {(q.type === 'text' || q.type === 'number') && (q.values ? <div className="text-values">{q.values.length ? q.values.map((value, index) => <div className="text-value" key={index}><span>{(page - 1) * 20 + index + 1}</span><p>{String(value)}</p></div>) : <p className="muted">暂无填写内容</p>}</div> : <button className="text-link" onClick={() => {setQuestionId(q.questionId); setPage(1);}}>查看 {q.answeredCount} 条填写内容<Icon name="arrow-right" size={16}/></button>)}
      {survey.questions.find(item => item.id === q.questionId)?.versionId && <Link className="text-link cross-statistic-entry" to={statisticLocation(survey.questions.find(item => item.id === q.questionId).questionId, survey.questions.find(item => item.id === q.questionId).versionId)}>查看同版本跨问卷统计<Icon name="arrow-right" size={16}/></Link>}
      </article>)}</div>
      : <div className="response-list">{responses?.items.length ? responses.items.map((response, index) => <article className="response-card" key={response._id}><header><div><span className="response-icon"><Icon name="file" size={19}/></span><strong>答卷 {responses.total - (page - 1) * 10 - index}</strong><Badge tone="neutral">{response.respondentId ? `实名 · ${response.respondentName || '已登录用户'}` : '匿名答卷'}</Badge></div><time>{dateTime(response.submittedAt)}</time></header><dl>{response.answers.map(answer => {const q = survey.questions.find(q => q.id === answer.questionId); return <div key={answer.questionId}><dt>{q?.title || '题目'}</dt><dd>{answerLabel(q, answer.value)}</dd></div>;})}</dl></article>) : <EmptyState title="这里还没有答卷" description="问卷提交成功后，可在这里查看实际填写内容。"/>}</div>}
    {!busy && !error && pages > 1 && <div className="pagination"><span>第 {page} 页，共 {pages} 页</span><div><Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => setPage(v => v - 1)}>上一页</Button><Button variant="secondary" size="sm" disabled={page >= pages} onClick={() => setPage(v => v + 1)}>下一页</Button></div></div>}
  </div>;
}
