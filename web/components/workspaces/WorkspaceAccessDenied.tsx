'use client'

import Link from 'next/link'
import { useTranslation } from 'react-i18next'

export function WorkspaceAccessDenied() {
  const { t } = useTranslation()
  return (
    <section role="status" className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-6">
      <h2 className="text-base font-semibold">{t('Workspaces are managed by your administrator')}</h2>
      <p className="mt-2 text-sm leading-relaxed text-[var(--muted-foreground)]">
        {t('Your account cannot manage workspace folders or resources. Contact your administrator to request access.')}
      </p>
      <div className="mt-4 flex flex-wrap gap-4 text-sm">
        <Link className="underline underline-offset-4" href="/settings/general">{t('Personal settings')}</Link>
      </div>
    </section>
  )
}
