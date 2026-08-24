'use client'

import { BackgroundJobsIndicator } from '@/components/common/BackgroundJobsIndicator'
import { AppSidebar } from './AppSidebar'
import { SetupBanner } from './SetupBanner'

interface AppShellProps {
  children: React.ReactNode
}

export function AppShell({ children }: AppShellProps) {
  return (
    <div className="flex h-screen overflow-hidden">
      <AppSidebar />
      <main className="flex-1 flex flex-col min-h-0 overflow-hidden">
        <SetupBanner />
        <BackgroundJobsIndicator />
        {children}
      </main>
    </div>
  )
}
