import { Alert, Button, Empty, Result, Skeleton } from 'antd'
import { RefreshCw } from 'lucide-react'
import { errorMessage } from '../api'

export function PageLoading() {
  return <div className="panel"><Skeleton active paragraph={{ rows: 7 }} /></div>
}

export function PageError({ error, retry }: { error: unknown; retry: () => void }) {
  return (
    <Result
      status="error"
      title="Could not load this workspace"
      subTitle={errorMessage(error)}
      extra={<Button icon={<RefreshCw size={16} />} onClick={retry}>Try again</Button>}
    />
  )
}

export function InlineError({ error }: { error: unknown }) {
  return <Alert showIcon type="error" title={errorMessage(error)} />
}

export function EmptyState({ description }: { description: string }) {
  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={description} />
}
