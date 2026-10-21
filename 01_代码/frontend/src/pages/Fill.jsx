import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import { useAuth, useToast } from '../context'
import { Badge, Button, Icon, Modal } from '../components'
import { answerInput, answerLabel, answersEqual, canonicalAnswer, questionHint } from '../fill-utils'

const typeLabels = { single: '单选题', multiple: '多选题', text: '文本填空', number: '数字填空' }

function dateLabel(value) {
  if (!value) return '不限时间'
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return '查看问卷说明'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date) + ' 截止'
}

function errorMessage(error) {
  if (error instanceof ApiError && error.message) return error.message
  return '暂时无法连接服务器，请检查网络后重试。'
}

export default function Fill() {
  const { publicId } = useParams()
  const navigate = useNavigate()
  const { user, loading: authLoading, login, logout } = useAuth()
  const toast = useToast()
  const base = `/public/surveys/${publicId}`
  const [survey, setSurvey] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)
  const [reload, setReload] = useState(0)
  const [phase, setPhase] = useState('intro')
  const [attempt, setAttempt] = useState(null)
  const [progress, setProgress] = useState({ answers: [], nextQuestionId: null, complete: false })
  const [currentId, setCurrentId] = useState(null)
  const [drafts, setDrafts] = useState({})
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState('')
  const [needsLogin, setNeedsLogin] = useState(false)
  const [submitUncertain, setSubmitUncertain] = useState(false)
  const [unavailable, setUnavailable] = useState(null)
  const [exitOpen, setExitOpen] = useState(false)
  const [loginOpen, setLoginOpen] = useState(false)
  const [loginName, setLoginName] = useState('')
  const [loginPassword, setLoginPassword] = useState('')
  const [loginError, setLoginError] = useState('')
  const [loginBusy, setLoginBusy] = useState(false)
  const submission = useRef(null)
  const participant = useRef(null)
  const questionHeading = useRef(null)
  const operation = useRef(false)

  const questions = survey?.questions || []
  const questionMap = useMemo(() => new Map(questions.map(question => [question.id, question])), [survey])
  const question = questionMap.get(currentId)
  const currentPosition = questions.findIndex(item => item.id === currentId)
  const currentValue = question
    ? (Object.prototype.hasOwnProperty.call(drafts, currentId)
        ? drafts[currentId]
        : answerInput(question, progress.answers.find(answer => answer.questionId === currentId)?.value))
    : ''
  const savedAnswer = progress.answers.find(answer => answer.questionId === currentId)
  const currentChanged = Boolean(question && savedAnswer && !answersEqual(question, currentValue, savedAnswer.value))
  const pathIndex = progress.answers.findIndex(answer => answer.questionId === currentId)
  const canGoBack = phase === 'answering' && (pathIndex > 0 || (pathIndex === -1 && progress.answers.length > 0))
  const active = Boolean(attempt && phase !== 'success')

  useEffect(() => {
    if (authLoading) return
    if (!user) {
      if (attempt) {
        setNeedsLogin(true)
        setLoading(false)
        setFormError('登录凭据已失效。当前答案仍保留在本页，请使用原账号重新登录后继续。')
        return
      }
      navigate('/login?next=' + encodeURIComponent('/survey/' + publicId), { replace: true })
      return
    }
    let canceled = false
    setLoading(true)
    setLoadError(null)
    api.get(base).then(data => {
      if (!canceled) setSurvey(data)
    }).catch(error => {
      if (!canceled) setLoadError(error)
    }).finally(() => {
      if (!canceled) setLoading(false)
    })
    return () => { canceled = true }
  }, [authLoading, user, publicId, reload])

  useEffect(() => {
    setSurvey(null)
    setPhase('intro')
    setAttempt(null)
    setProgress({ answers: [], nextQuestionId: null, complete: false })
    setCurrentId(null)
    setDrafts({})
    setFormError('')
    setNeedsLogin(false)
    setSubmitUncertain(false)
    setUnavailable(null)
    setLoginOpen(false)
    setLoginPassword('')
    submission.current = null
    participant.current = null
  }, [publicId])

  useEffect(() => {
    if (!survey) return
    const previous = document.title
    document.title = survey.title + ' · 问序'
    return () => { document.title = previous }
  }, [survey?.title])

  useEffect(() => {
    if (!active) return
    const warn = event => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [active])

  useEffect(() => {
    if (phase === 'answering' || phase === 'review' || phase === 'success') {
      questionHeading.current?.focus({ preventScroll: true })
      window.scrollTo({ top: 0, behavior: 'smooth' })
    }
  }, [phase, currentId])

  function setValue(value) {
    setDrafts(previous => ({ ...previous, [currentId]: value }))
    setFormError('')
  }

  function selectQuestion(id) {
    setCurrentId(id)
    setFormError('')
    setPhase('answering')
  }

  function handleError(error, { submitting = false } = {}) {
    const message = errorMessage(error)
    setFormError(message)
    if (error?.status === 401 && error?.code !== 'INVALID_SUBMISSION_TOKEN') setNeedsLogin(true)
    if (error?.status === 410 || error?.code === 'ALREADY_SUBMITTED') {
      setUnavailable({ title: error?.code === 'ALREADY_SUBMITTED' ? '你已完成这份问卷' : '这份问卷已结束', message })
    }
    if (submitting && (!error?.status || error.status >= 500)) setSubmitUncertain(true)
  }

  async function startAttempt() {
    if (operation.current) return
    operation.current = true
    setBusy(true)
    setFormError('')
    setNeedsLogin(false)
    try {
      const credentials = await api.post(base + '/start')
      setAttempt(credentials)
      participant.current = { id: user._id, username: user.username }
      setProgress({ answers: [], nextQuestionId: questions[0]?.id || null, complete: false })
      setCurrentId(questions[0]?.id || null)
      setDrafts({})
      setUnavailable(null)
      setSubmitUncertain(false)
      submission.current = null
      setPhase('answering')
    } catch (error) {
      handleError(error)
    } finally {
      operation.current = false
      setBusy(false)
    }
  }

  async function nextQuestion(event) {
    event?.preventDefault()
    if (operation.current || !question) return
    let value
    try {
      value = canonicalAnswer(question, currentValue)
    } catch (error) {
      setFormError(error.message)
      return
    }
    const wasAnswered = progress.answers.some(answer => answer.questionId === question.id)
    const changed = wasAnswered && !answersEqual(question, value, savedAnswer.value)
    const answers = wasAnswered
      ? progress.answers.map(answer => answer.questionId === question.id ? { questionId: question.id, value } : answer)
      : [...progress.answers, { questionId: question.id, value }]
    operation.current = true
    setBusy(true)
    setFormError('')
    try {
      const result = await api.post(base + '/preview', {
        answers,
        ...(changed ? { changedQuestionId: question.id } : {}),
      })
      setProgress(result)
      // Only confirmed answers remain in the local cache. A changed earlier
      // answer drops every later draft, including answers on the new same path.
      setDrafts(previous => {
        const retained = {}
        const cleared = new Set(result.clearedQuestionIds || [])
        for (const [id, raw] of Object.entries(previous)) {
          if (!cleared.has(id) && (!changed || questions.findIndex(item => item.id === id) <= currentPosition)) retained[id] = raw
        }
        retained[question.id] = answerInput(question, value)
        return retained
      })
      const index = result.answers.findIndex(answer => answer.questionId === question.id)
      const nextId = result.answers[index + 1]?.questionId || result.nextQuestionId
      if (nextId) setCurrentId(nextId)
      else if (result.complete) setPhase('review')
    } catch (error) {
      handleError(error)
    } finally {
      operation.current = false
      setBusy(false)
    }
  }

  function previousQuestion() {
    if (!canGoBack || busy) return
    const index = pathIndex === -1 ? progress.answers.length : pathIndex
    selectQuestion(progress.answers[index - 1].questionId)
  }

  async function submitAnswers() {
    if (operation.current || !attempt) return
    if (!submission.current) {
      submission.current = {
        ...attempt,
        answers: progress.answers.map(answer => ({ ...answer, value: Array.isArray(answer.value) ? [...answer.value] : answer.value })),
      }
    }
    operation.current = true
    setBusy(true)
    setFormError('')
    try {
      await api.post(base + '/submit', submission.current)
      setSubmitUncertain(false)
      setPhase('success')
      toast('提交成功，感谢你的认真填写。')
    } catch (error) {
      handleError(error, { submitting: true })
      if (error?.status === 422) {
        submission.current = null
        setSubmitUncertain(false)
      }
    } finally {
      operation.current = false
      setBusy(false)
    }
  }

  function leave() {
    if (active) setExitOpen(true)
    else navigate('/')
  }

  function openLogin() {
    setLoginName(participant.current?.username || user?.username || '')
    setLoginPassword('')
    setLoginError('')
    setLoginOpen(true)
  }

  async function reauthenticate(event) {
    event.preventDefault()
    if (loginBusy) return
    setLoginBusy(true)
    setLoginError('')
    try {
      const account = await login(loginName, loginPassword)
      if (participant.current && account._id !== participant.current.id) {
        logout()
        setLoginError('请使用开始本次填写时的原账号登录，其他账号无法继续这次填写。')
        return
      }
      setLoginOpen(false)
      setLoginPassword('')
      setNeedsLogin(false)
      setFormError('')
      toast('登录已恢复，可以继续填写。')
    } catch (error) {
      setLoginError(errorMessage(error))
    } finally {
      setLoginBusy(false)
    }
  }

  const errorPanel = formError && (
    <div className="fill-error" id="answer-error" role="alert">
      <Icon name="x" size={17} />
      <div>
        <p>{formError}</p>
        {needsLogin && <button className="text-link" type="button" onClick={openLogin}>重新登录，保留当前答案</button>}
      </div>
    </div>
  )

  return (
    <div className="fill-shell">
      <header className="fill-nav">
        <button className="fill-brand brand" type="button" onClick={leave} aria-label="返回我的问卷">
          <span className="brand-mark"><i /><i /><i /></span>
          <span>问序<small>FORM &amp; FIELD</small></span>
        </button>
        <div className="fill-nav-meta"><Icon name="lock" size={14} /><span>{user?.username || '登录后填写'}</span></div>
      </header>

      <main className="fill-container">
        {(authLoading || loading) && (
          <div className="fill-card fill-skeleton" role="status" aria-label="正在加载问卷">
            <div className="skeleton-line" style={{ width: '32%' }} />
            <div className="skeleton-line" style={{ width: '70%', height: 34 }} />
            <div className="skeleton-line" />
            <div className="skeleton-line" style={{ width: '85%' }} />
            <p className="fill-hint">正在准备问卷…</p>
          </div>
        )}

        {!authLoading && !loading && loadError && (
          <section className="fill-card fill-status-card">
            <span className="fill-success-mark"><Icon name={loadError.status === 410 ? 'clock' : 'file'} size={29} /></span>
            <h1>{loadError.status === 410 ? '这份问卷已结束' : loadError.status === 404 ? '暂时找不到这份问卷' : '问卷暂时无法加载'}</h1>
            <p>{errorMessage(loadError)}</p>
            <div className="fill-review-actions">
              {![404, 410].includes(loadError.status) && <Button onClick={() => setReload(value => value + 1)} variant="primary">重新加载</Button>}
              <Button onClick={() => navigate('/')} variant="secondary">返回我的问卷</Button>
            </div>
          </section>
        )}

        {!loading && !loadError && survey && unavailable && (
          <section className="fill-card fill-status-card">
            <span className="fill-success-mark"><Icon name="clock" size={29} /></span>
            <h1>{unavailable.title}</h1>
            <p>{unavailable.message}</p>
            <p className="fill-hint">未成功提交的内容不会保存到服务器。</p>
            <Button variant="secondary" onClick={leave}>返回我的问卷</Button>
          </section>
        )}

        {!loading && !loadError && survey && !unavailable && phase === 'intro' && (
          <section className="fill-card fill-intro">
            <div className="fill-eyebrow"><span className="eyebrow-dot" /> 一份认真倾听的问卷</div>
            <h1 className="fill-hero">{survey.title}</h1>
            {survey.description && <p className="fill-description">{survey.description}</p>}
            <div className="fill-meta">
              <span><Icon name="list" size={17} />{questions.length} 道题目</span>
              <span><Icon name="clock" size={17} />约 {Math.max(1, Math.ceil(questions.length * 0.5))} 分钟</span>
              <span><Icon name="lock" size={17} />{survey.settings.anonymous ? '整份匿名' : '实名填写'}</span>
            </div>
            <div className="fill-notice">
              <Icon name="lock" size={20} />
              <div>
                <strong>{survey.settings.anonymous ? '你的答卷不记录账号身份' : '本问卷将记录填写账号'}</strong>
                <p>{survey.settings.anonymous ? '登录仅用于核验填写资格。匿名答卷不会保存你的用户 ID。' : '创建者可以看到你的账号身份及本次填写内容。'}</p>
              </div>
            </div>
            <div className="fill-intro-details">
              <p><Icon name="clock" size={15} />{dateLabel(survey.deadlineAt)}</p>
              <p><Icon name="users" size={15} />{survey.settings.allowRepeat ? '允许同一账号多次填写' : '每个账号限提交一次'}</p>
            </div>
            {errorPanel}
            <div className="fill-footer">
              <p className="fill-hint">未提交的答案不自动保存，退出后需要重新填写。</p>
              {survey.canSubmit === false
                ? <Button disabled>你已提交过这份问卷</Button>
                : <Button icon="arrow-right" loading={busy} onClick={startAttempt}>开始填写</Button>}
            </div>
          </section>
        )}

        {!loading && !loadError && !unavailable && phase === 'answering' && question && (
          <>
            <div className="fill-progress" aria-label={`当前第 ${currentPosition + 1} 题，共 ${questions.length} 题`}>
              <div className="fill-step-label"><span>{survey.title}</span><span>{String(currentPosition + 1).padStart(2, '0')} / {String(questions.length).padStart(2, '0')}</span></div>
              <div className="fill-progress-track"><div className="fill-progress-bar" style={{ width: `${((currentPosition + 1) / questions.length) * 100}%` }} /></div>
            </div>
            <form className="fill-card fill-question-card" onSubmit={nextQuestion} noValidate>
              <div className="fill-eyebrow">QUESTION {String(currentPosition + 1).padStart(2, '0')} <Badge>{typeLabels[question.type]}</Badge>{question.required && <Badge>必答</Badge>}</div>
              <h1 ref={questionHeading} tabIndex={-1} id="fill-question-heading">{question.title}</h1>
              {question.description && <p className="fill-description">{question.description}</p>}
              <p className="fill-hint" id="answer-hint">{questionHint(question)}</p>

              {['single', 'multiple'].includes(question.type) && (
                <fieldset className="fill-options" aria-labelledby="fill-question-heading" aria-describedby="answer-hint" disabled={busy}>
                  {question.options.map((option, index) => {
                    const checked = question.type === 'single' ? currentValue === option.id : currentValue.includes(option.id)
                    return (
                      <label className={`fill-option${checked ? ' is-selected' : ''}`} key={option.id}>
                        <input className="fill-option-control" type={question.type === 'single' ? 'radio' : 'checkbox'} name={question.id} value={option.id} checked={checked} onChange={() => {
                          if (question.type === 'single') setValue(option.id)
                          else setValue(checked ? currentValue.filter(id => id !== option.id) : [...currentValue, option.id])
                        }} />
                        <span className="fill-option-marker" data-type={question.type} aria-hidden="true">{checked ? <Icon name="check" size={15} /> : String(index + 1).padStart(2, '0')}</span>
                        <span className="fill-option-copy">{option.label}</span>
                      </label>
                    )
                  })}
                  {!question.required && <button className="text-link" type="button" onClick={() => setValue(question.type === 'multiple' ? [] : '')}>清空选择</button>}
                </fieldset>
              )}

              {question.type === 'text' && (
                <div className="fill-field">
                  <label className="sr-only" htmlFor="fill-text-answer">{question.title}</label>
                  <textarea id="fill-text-answer" rows={5} value={currentValue} disabled={busy} onChange={event => setValue(event.target.value)} placeholder="在这里写下你的回答…" aria-invalid={Boolean(formError)} aria-describedby={`answer-hint${formError ? ' answer-error' : ''}`} />
                  <div className="fill-text-count">{Array.from((currentValue || '').trim()).length} / {question.validation?.maxLength ?? 10000} 字符（去除首尾空白）</div>
                </div>
              )}

              {question.type === 'number' && (
                <div className="fill-field">
                  <label className="sr-only" htmlFor="fill-number-answer">{question.title}</label>
                  <input id="fill-number-answer" className="fill-number-input" type="text" inputMode={question.validation?.integerOnly ? 'numeric' : 'decimal'} autoComplete="off" value={currentValue} disabled={busy} onChange={event => setValue(event.target.value)} placeholder={question.validation?.integerOnly ? '请输入整数' : '请输入数字'} aria-invalid={Boolean(formError)} aria-describedby={`answer-hint${formError ? ' answer-error' : ''}`} />
                </div>
              )}

              {currentChanged && <p className="fill-change-note"><Icon name="arrow-right" size={15} />继续后将清空本题之后的答案，并重新计算填写路径。</p>}
              {errorPanel}
              <div className="fill-footer">
                <Button variant="ghost" icon="arrow-left" type="button" disabled={!canGoBack || busy} onClick={previousQuestion}>上一题</Button>
                <Button type="submit" icon="arrow-right" loading={busy} disabled={needsLogin}>下一步</Button>
              </div>
            </form>
            <p className="fill-bottom-note"><Icon name="lock" size={13} /> {survey.settings.anonymous ? '匿名填写' : '实名填写'} · 仅在确认提交后保存</p>
          </>
        )}

        {!loading && !loadError && !unavailable && phase === 'review' && (
          <section className="fill-card">
            <div className="fill-eyebrow"><Icon name="check" size={17} /> 最后确认</div>
            <h1 ref={questionHeading} tabIndex={-1}>回答已就绪，提交前再看一眼</h1>
            <p className="fill-description">{survey.title} · 本次经过 {progress.answers.length} 道题目，跳过的题目无需作答。</p>
            <ol className="fill-review-list">
              {progress.answers.map(answer => {
                const item = questionMap.get(answer.questionId)
                return (
                  <li key={answer.questionId} className="fill-review-item">
                    <div className="fill-review-question"><span>{questions.findIndex(question => question.id === item.id) + 1}. {item.title}</span><Button variant="ghost" icon="pencil" disabled={busy || submitUncertain || Boolean(submission.current)} onClick={() => selectQuestion(item.id)} aria-label={`修改：${item.title}`}>修改</Button></div>
                    <p className={`fill-review-answer${answer.value == null ? ' is-empty' : ''}`}>{answerLabel(item, answer.value)}</p>
                  </li>
                )
              })}
            </ol>
            <div className="fill-notice"><Icon name="lock" size={20} /><div><strong>{survey.settings.anonymous ? '这份答卷统一匿名' : '这份答卷为实名提交'}</strong><p>提交后无法修改或撤回，请确认内容准确。</p></div></div>
            {submitUncertain && <p className="fill-change-note" role="status">提交结果暂未确认。请保留本页并重试，系统会确认同一次提交，不会重复生成答卷。</p>}
            {errorPanel}
            <div className="fill-footer">
              <p className="fill-hint">{submitUncertain ? '正在保留原提交凭据及原答案。' : '你的每一份回答，都让结果更完整。'}</p>
              <Button icon={submitUncertain ? 'arrow-right' : 'check'} loading={busy} disabled={needsLogin} onClick={submitAnswers}>{submitUncertain ? '重试原提交' : '确认提交'}</Button>
            </div>
          </section>
        )}

        {!loading && !loadError && !unavailable && phase === 'success' && (
          <section className="fill-card fill-complete">
            <span className="fill-success-mark"><Icon name="check" size={34} /></span>
            <div className="fill-eyebrow">THANK YOU FOR YOUR TIME</div>
            <h1 ref={questionHeading} tabIndex={-1}>已收到你的回答</h1>
            <p>感谢认真填写「{survey.title}」。<br />答卷已成功提交并保存。</p>
            <div className="fill-success-details"><Icon name="lock" size={16} />{survey.settings.anonymous ? '本次答卷未记录你的账号身份' : '本次答卷已按实名方式记录'}</div>
            {errorPanel}
            <div className="fill-review-actions">
              {survey.settings.allowRepeat && <Button onClick={startAttempt} loading={busy} icon="plus">再填一份</Button>}
              <Button variant="secondary" onClick={() => navigate('/')}>返回我的问卷</Button>
            </div>
          </section>
        )}
      </main>

      <footer className="fill-site-footer">问序 · 让每一个回答都有价值</footer>
      <Modal open={exitOpen} title="离开当前填写？" busy={busy} onClose={() => setExitOpen(false)} onConfirm={() => { setExitOpen(false); navigate('/') }} confirmText="确认离开" cancelText="继续填写">
        <p>未提交的答案不会自动保存，离开后需要重新开始填写。</p>
      </Modal>
      <Modal open={loginOpen} title="重新登录，继续当前填写" busy={loginBusy} onClose={() => { setLoginOpen(false); setLoginPassword('') }} footer={<><Button variant="secondary" disabled={loginBusy} onClick={() => { setLoginOpen(false); setLoginPassword('') }}>暂不登录</Button><Button type="submit" form="fill-login-form" loading={loginBusy}>登录并继续</Button></>}>
        <p className="fill-hint">当前答案仍保留在本页。请使用{participant.current?.username ? `「${participant.current.username}」` : '原账号'}登录；本次填写凭据不会重新生成。</p>
        <form id="fill-login-form" className="fill-login-form" onSubmit={reauthenticate}>
          <label htmlFor="fill-login-name">用户名</label>
          <input id="fill-login-name" autoComplete="username" value={loginName} disabled={loginBusy} onChange={event => setLoginName(event.target.value)} required />
          <label htmlFor="fill-login-password">密码</label>
          <input id="fill-login-password" type="password" autoComplete="current-password" value={loginPassword} disabled={loginBusy} onChange={event => setLoginPassword(event.target.value)} required />
          {loginError && <p className="fill-error" role="alert">{loginError}</p>}
        </form>
      </Modal>
    </div>
  )
}
