import { useEffect, useState } from 'react'
import { ChevronRight, Copy, MessageCircle, Shield } from 'lucide-react'
import { api, type Data } from './api'

export function ForumLogin({ onLogin, localLogin }: { onLogin: (user: Data) => void, localLogin: () => void }) {
  const [attempt, setAttempt] = useState<Data | null>(null), [proof, setProof] = useState<Data | null>(null)
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [code, setCode] = useState(''), [copied, setCopied] = useState(false)
  const [check, setCheck] = useState(0)
  useEffect(() => {
    if (!attempt || proof) return
    let stopped = false, timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const result = await api('/auth/forum/status/' + attempt.id)
        if (stopped) return
        setError('')
        if (result.state === 'verified') { setProof(result); return }
        if (result.state !== 'pending') { setError('该登录请求已失效，请重新获取验证码'); setAttempt(null); return }
        timer = setTimeout(poll, 5000)
      } catch (e) {
        if (stopped) return
        const status = (e as Error & { status?: number }).status
        if (status === 404 || status === 410 || Date.now() / 1000 >= attempt.expires) {
          setError('登录请求已失效，请重新获取验证码'); setAttempt(null)
        } else {
          setError('暂时无法检查私信，正在重试；请保留本页。')
          timer = setTimeout(poll, 5000)
        }
      }
    }
    void poll()
    return () => { stopped = true; clearTimeout(timer) }
  }, [attempt, proof, check])
  const start = async () => {
    setBusy(true); setError(''); setProof(null); setCopied(false); setCode('')
    try { setAttempt(await api('/auth/forum/start', 'POST', {})) }
    catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }
  const finish = async () => {
    setBusy(true); setError('')
    try { onLogin(await api('/auth/forum/finish/' + attempt!.id, 'POST', { code })) }
    catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }
  const message = attempt ? '我要登录 SuenMeow 后台，验证码是 ' + attempt.code : ''
  const forumLink = attempt ? attempt.forum_url + '/new-message?' + new URLSearchParams({ username: attempt.bot_username, title: 'SuenMeow 后台登录', body: message }) : ''
  return <section className="login-card forum-login">
    <span className="badge green"><Shield size={13}/> 论坛身份验证</span><h2>用论坛身份，来坐坐。</h2>
    <p>给 SuenMeow 发一条验证私信，使用论坛昵称和头像进入工作区。首次登录会自动创建受限编辑者。</p>
    {!attempt ? <button className="primary" disabled={busy} onClick={() => void start()}><MessageCircle size={16}/>{busy ? '正在准备…' : '获取登录验证码'}</button> : proof ? <>
      <div className="info-note"><Shield size={20}/><div><strong>已确认：{proof.profile.name || proof.profile.username}</strong><p>论坛账号 @{proof.profile.username}。请核对这是你自己的账号。</p></div></div>
      {proof.totp_required && <label className="field"><span>两步验证码</span><input maxLength={6} inputMode="numeric" autoComplete="one-time-code" value={code} onChange={e => setCode(e.target.value)}/></label>}
      <button className="primary" disabled={busy} onClick={() => void finish()}>{busy ? '正在登录…' : '确认身份并进入'}<ChevronRight size={16}/></button>
    </> : <>
      <label className="field"><span>复制后，发给 @{attempt.bot_username}</span><textarea readOnly rows={3} value={message}/></label>
      <div className="forum-login-actions"><button onClick={() => { void navigator.clipboard.writeText(message).then(() => setCopied(true)).catch(() => setError('请手动选中并复制上方内容')) }}><Copy size={15}/>{copied ? '已复制' : '复制私信内容'}</button><a className="button primary" href={forumLink} target="_blank" rel="noopener noreferrer">打开论坛私信<ChevronRight size={15}/></a></div>
      <p role="status" className="muted">等待你的新私信… 验证码 5 分钟有效。本页会自动确认，无需机器人回复。</p>
      <button disabled={busy} onClick={() => setCheck(value => value + 1)}>我已发送，立即检查</button>
      <small>请新建一条仅你和 SuenMeow 参与的私信。只发送自己当前页面的验证码，不转发他人的验证码；不要发送密码。</small>
    </>}
    {error && <p role="alert" className="error">{error}</p>}
    <div className="forum-login-footer">{attempt && <button disabled={busy} onClick={() => void start()}>重新获取验证码</button>}<button disabled={busy} onClick={localLogin}>管理员 / 旧账户登录</button></div>
  </section>
}
