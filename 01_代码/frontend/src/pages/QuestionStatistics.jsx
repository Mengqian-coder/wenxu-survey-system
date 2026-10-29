import {useEffect, useState} from 'react';
import {Link, useLocation, useNavigate} from 'react-router-dom';
import {api, typeNames} from '../api';
import {Badge, Button, EmptyState, Icon, Loading} from '../components';
import {statisticLocation, statisticNumber, statisticPercent, statisticSelection} from '../statistics-utils';

function Pagination({data, page, onChange, label}) {
  if (!data || data.total <= data.pageSize) return null;
  const pages = Math.ceil(data.total / data.pageSize);
  return <div className="pagination" aria-label={label}><span>共 {data.total} 条 · 第 {page} / {pages} 页</span><div><Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => onChange(page - 1)}>上一页</Button><Button size="sm" variant="secondary" disabled={page >= pages} onClick={() => onChange(page + 1)}>下一页</Button></div></div>;
}

function Failure({error, retry}) {
  return <EmptyState icon="alert-circle" title={error.code === 'NOT_FOUND' ? '尚无可统计的本人引用' : '暂时无法读取统计'} description={error.code === 'NOT_FOUND' ? '只有你自己问卷中已保存的题目与版本会进入统计。请从统计目录选择，或先将该版本保存到自己的问卷。' : error.message} action={<Button variant="secondary" onClick={retry}>重新加载</Button>}/>;
}

export default function QuestionStatistics() {
  const location = useLocation(), navigate = useNavigate();
  const {questionId, versionId} = statisticSelection(location.search);
  const [search, setSearch] = useState(''), [questionPage, setQuestionPage] = useState(1), [versionPage, setVersionPage] = useState(1), [valuePage, setValuePage] = useState(1);
  const [directory, setDirectory] = useState(null), [versions, setVersions] = useState(null), [statistics, setStatistics] = useState(null), [values, setValues] = useState(null);
  const [directoryError, setDirectoryError] = useState(null), [versionError, setVersionError] = useState(null), [statisticError, setStatisticError] = useState(null), [valueError, setValueError] = useState(null);
  const [directoryBusy, setDirectoryBusy] = useState(true), [versionBusy, setVersionBusy] = useState(false), [statisticBusy, setStatisticBusy] = useState(false), [valueBusy, setValueBusy] = useState(false), [refresh, setRefresh] = useState(0);
  const versionKey = `${questionId}:${versionId}`;
  const currentVersions = versions?.questionId === questionId ? versions : null;
  const currentStats = statistics?.questionId === questionId && statistics?.versionId === versionId ? statistics : null;
  const currentValues = values?.questionId === questionId && values?.versionId === versionId && values?.page === valuePage ? values : null;

  useEffect(() => {
    let live = true; setDirectoryBusy(true); setDirectoryError(null);
    const query = new URLSearchParams({search: search.trim(), page: questionPage, pageSize: 12});
    api.get(`/question-statistics?${query}`).then(value => {if (live) setDirectory(value);}).catch(error => {if (live) setDirectoryError(error);}).finally(() => {if (live) setDirectoryBusy(false);});
    return () => {live = false;};
  }, [search, questionPage, refresh]);

  useEffect(() => {setVersionPage(1);}, [questionId]);
  useEffect(() => {setValuePage(1);}, [versionKey]);
  useEffect(() => {
    if (!questionId) return;
    let live = true; setVersionBusy(true); setVersionError(null);
    api.get(`/question-statistics/${questionId}/versions?page=${versionPage}&pageSize=20`).then(value => {
      if (!live) return;
      setVersions({...value, questionId});
    }).catch(error => {if (live) setVersionError(error);}).finally(() => {if (live) setVersionBusy(false);});
    return () => {live = false;};
  }, [questionId, versionPage, refresh]);
  useEffect(() => {
    if (!questionId || versionId || !currentVersions?.items.length || versionBusy || versionError) return;
    navigate(statisticLocation(questionId, currentVersions.items[0].versionId), {replace: true});
  }, [questionId, versionId, currentVersions, versionBusy, versionError, navigate]);
  useEffect(() => {
    if (!questionId || !versionId) return;
    let live = true; setStatisticBusy(true); setStatisticError(null);
    api.get(`/question-statistics/${questionId}/versions/${versionId}`).then(value => {if (live) setStatistics(value);}).catch(error => {if (live) setStatisticError(error);}).finally(() => {if (live) setStatisticBusy(false);});
    return () => {live = false;};
  }, [questionId, versionId, refresh]);
  useEffect(() => {
    if (!currentStats || !['text', 'number'].includes(currentStats.type)) return;
    let live = true; setValueBusy(true); setValueError(null);
    api.get(`/question-statistics/${questionId}/versions/${versionId}/values?page=${valuePage}&pageSize=20`).then(value => {if (live) setValues(value);}).catch(error => {if (live) setValueError(error);}).finally(() => {if (live) setValueBusy(false);});
    return () => {live = false;};
  }, [questionId, versionId, currentStats, valuePage, refresh]);
  const retry = () => setRefresh(value => value + 1);

  return <div className="cross-statistics-page"><div className="page-heading"><div><div className="eyebrow">INSIGHTS ACROSS SURVEYS</div><h1>跨问卷统计<span className="heading-period">.</span></h1><p>将同一道题的同一版本，在你的多份问卷中合并查看。</p></div><Button variant="secondary" icon="refresh" onClick={retry}>刷新统计</Button></div>
    <div className="notice info-notice cross-statistics-note"><Icon name="lock" size={19}/><div>只统计你自己问卷中的已提交答卷；草稿引用也会列入目录。<br/>回答次数与答卷份数均不代表独立人数。同一人填写多份问卷分别计次，不同版本、恢复出的新版本和独立副本分别统计。</div></div>
    {!questionId ? <><div className="section-toolbar"><label className="search-input"><Icon name="search"/><input aria-label="搜索统计题目" placeholder="搜索本人引用过的题干…" value={search} maxLength={200} onChange={event => {setSearch(event.target.value); setQuestionPage(1);}}/></label><span className="field-hint">目录来自本人问卷的已保存快照</span></div>
      {directoryError ? <Failure error={directoryError} retry={retry}/> : directoryBusy ? <Loading label="正在整理本人引用…"/> : directory?.items.length ? <div className="survey-grid statistic-question-grid">{directory.items.map(item => <article className="survey-card" key={item.questionId}><div className="survey-card-top"><Badge tone="neutral">{item.usedVersionCount} 个已用版本</Badge><span className="field-hint">{item.surveyCount} 份问卷引用</span></div><Link className="survey-card-title" to={statisticLocation(item.questionId)}><h3>{item.title}</h3></Link><p className="field-hint">展示本人已用版本 v{item.displayVersionNo} 的题干</p><div className="survey-card-actions"><Link className="card-action" to={statisticLocation(item.questionId)}><Icon name="chart" size={16}/>选择版本并统计</Link></div></article>)}</div> : <EmptyState icon="chart" title={search.trim() ? '没有找到匹配的引用题目' : '还没有可合并的题目引用'} description={search.trim() ? '试试其他题干。目录只搜索你自己问卷的快照。' : '先创建独立题目，将具体版本保存到问卷。共享给你的题目在撤权后，已有引用仍可在这里统计。'} action={<Link className="btn btn-primary" to="/surveys/new">创建问卷</Link>}/>}
      {!directoryBusy && !directoryError && <Pagination data={directory} page={questionPage} onChange={setQuestionPage} label="统计题目分页"/>}
    </> : <><Link className="back-link" to="/question-statistics"><Icon name="arrow-left" size={16}/>返回统计题目目录</Link><div className="cross-statistics-layout"><aside className="editor-section statistic-version-list"><h2>本人引用过的版本</h2><p className="field-hint">仅列出本人问卷实际保存的版本。题目归档、共享撤权不影响已有引用统计。</p>{versionError ? <Failure error={versionError} retry={retry}/> : versionBusy || !currentVersions ? <Loading/> : <>{currentVersions.items.map(item => <Link key={item.versionId} className={`version-row ${versionId === item.versionId ? 'active' : ''}`} to={statisticLocation(questionId, item.versionId)} aria-current={versionId === item.versionId ? 'true' : undefined}><strong>v{item.versionNo} · {item.title}</strong><span>{typeNames[item.type]} · {item.surveyCount} 份问卷引用</span></Link>)}<Pagination data={currentVersions} page={versionPage} onChange={setVersionPage} label="统计版本分页"/></>}</aside>
      <section className="cross-statistic-detail" aria-label="精确版本统计">{statisticError ? <Failure error={statisticError} retry={retry}/> : statisticBusy || !currentStats ? versionError ? null : <Loading label="正在合并已提交答卷…"/> : <><article className="stat-card cross-stat-card"><header><span className="question-number"><Icon name={currentStats.type} size={19}/></span><div><h2>{currentStats.title}</h2><span className="muted small">{typeNames[currentStats.type]} · 精确版本 v{currentStats.versionNo}</span></div></header><dl className="cross-stat-counts"><div><dt>引用问卷</dt><dd>{currentStats.surveyCount}<small>份</small></dd></div><div><dt>已提交答卷</dt><dd>{currentStats.totalResponses}<small>份</small></dd></div><div><dt>有效回答</dt><dd>{currentStats.answeredCount}<small>次</small></dd></div><div><dt>留空或跳过</dt><dd>{currentStats.unansweredCount}<small>次</small></dd></div></dl>
          {currentStats.totalResponses === 0 && <div className="notice"><Icon name="clock" size={17}/>这些问卷还没有已提交答卷，后续成功提交会汇总在这里。</div>}
          {['single', 'multiple'].includes(currentStats.type) && <div className="option-stat-list">{currentStats.options.map((option, index) => <div className="option-stat" key={option.optionId}><div><span>{option.label}</span><strong>{option.count}<small> 次 · {statisticPercent(option.ratio)}</small></strong></div><div className="stat-track"><span style={{width: `${(option.ratio ?? 0) * 100}%`, background: ['#6366f1', '#8b8cf8', '#a3a3fa', '#b8b9fc'][index % 4]}}/></div></div>)}<p className="field-hint">比例以本版本的有效回答次数为分母。{currentStats.type === 'multiple' ? '多选题各选项分别计数，比例之和可能超过 100%。' : ''}没有有效回答时显示“暂无比例”。</p></div>}
          {currentStats.type === 'number' && <dl className="cross-number-metrics"><div><dt>最小值</dt><dd>{statisticNumber(currentStats.minimum)}</dd></div><div><dt>最大值</dt><dd>{statisticNumber(currentStats.maximum)}</dd></div><div><dt>平均值</dt><dd>{statisticNumber(currentStats.average)}</dd></div></dl>}
        </article>{['text', 'number'].includes(currentStats.type) && <article className="stat-card statistic-values"><h2>有效回答原值</h2><p className="field-hint">按提交时间倒序展示，不包含留空或跳过，也不展示填写者身份。</p>{valueError ? <Failure error={valueError} retry={retry}/> : valueBusy || !currentValues ? <Loading/> : <>{currentValues.items.length ? <div className="text-values">{currentValues.items.map((item, index) => <div className="text-value" key={`${valuePage}:${index}`}><span>{(valuePage - 1) * 20 + index + 1}</span><p>{String(item.value)}</p></div>)}</div> : <p className="muted statistic-empty-values">暂无有效回答原值</p>}<Pagination data={currentValues} page={valuePage} onChange={setValuePage} label="回答原值分页"/></>}</article>}</>}
      </section></div></>}
  </div>;
}
