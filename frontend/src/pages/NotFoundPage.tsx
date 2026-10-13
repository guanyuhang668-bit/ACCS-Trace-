import { Button, Result } from 'antd'
import { ArrowLeft } from 'lucide-react'
import { Link } from 'react-router-dom'

export function NotFoundPage() {
  return (
    <Result
      status="404"
      title="Workspace not found"
      subTitle="The requested local route does not exist."
      extra={<Link to="/projects"><Button type="primary" icon={<ArrowLeft size={16} />}>Back to projects</Button></Link>}
    />
  )
}
