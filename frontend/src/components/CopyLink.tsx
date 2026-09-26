/**
 * Ссылка с кнопкой «Скопировать».
 *
 * Используется на экране создания дважды: для ученической ссылки
 * и для секретной ссылки на результаты.
 */

import { useState } from 'react'

type Props = {
  /** Подпись над полем. */
  label: string
  /** Сама ссылка. */
  url: string
  /** Пояснение под полем — например, предупреждение «не отправляйте ученикам». */
  hint?: string
  /** Выделить блок как секретный. */
  secret?: boolean
}

export default function CopyLink({ label, url, hint, secret = false }: Props) {
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      setError('')
      // Через две секунды убираем надпись «Скопировано».
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      // Браузер может запретить доступ к буферу обмена (например, без https).
      setError('Браузер не дал скопировать — выделите ссылку и скопируйте вручную.')
    }
  }

  return (
    <div className={secret ? 'linkblock linkblock--secret' : 'linkblock'}>
      <p className="label">{label}</p>
      <div className="row row--tight">
        {/* readOnly-поле, а не просто текст: так ссылку удобно выделить целиком. */}
        <input className="linkbox" value={url} readOnly onFocus={(e) => e.target.select()} />
        <button type="button" className="btn btn--primary" onClick={handleCopy}>
          Скопировать
        </button>
        {copied && <span className="copied">Скопировано</span>}
      </div>
      {hint !== undefined && <p className="muted">{hint}</p>}
      {error !== '' && <p className="field-error">{error}</p>}
    </div>
  )
}
