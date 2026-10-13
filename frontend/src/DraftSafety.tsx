import { createContext, useContext, useEffect, type MutableRefObject } from 'react'
import { Check, Save } from 'lucide-react'

export type DraftHandle = { dirty: boolean, save: () => Promise<boolean>, busy: boolean }
export const DraftContext = createContext<MutableRefObject<DraftHandle | null> | null>(null)

export function useDraftSafety(dirty: boolean, save: () => Promise<boolean>, busy = false) {
  const draft = useContext(DraftContext)
  useEffect(() => { if (draft) draft.current = { dirty, save, busy } })
  useEffect(() => () => { if (draft) draft.current = null }, [draft])
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        if (draft?.current?.dirty && !draft.current.busy) void draft.current.save()
      }
    }
    window.addEventListener('keydown', shortcut)
    return () => window.removeEventListener('keydown', shortcut)
  }, [draft])
}

export function SaveDock({ dirty, busy, save, detail, error, label = '保存草稿', idleLabel = '所有修改已保存' }: {
  dirty: boolean, busy: boolean, save: () => void, detail?: string, error?: string, label?: string, idleLabel?: string
}) {
  return <aside className={'save-dock ' + (dirty ? 'unsaved' : '')} aria-label="草稿保存">
    <div role="status" aria-live="polite"><strong>{busy ? '正在保存…' : dirty ? '有未保存的修改' : idleLabel}</strong>
      <small className={error ? 'error' : ''}>{error || detail || '保存为草稿，发布后生效'}</small></div>
    <button className="primary" disabled={!dirty || busy} onClick={save}>{dirty ? <Save size={17}/> : <Check size={17}/>}{label}</button>
  </aside>
}
