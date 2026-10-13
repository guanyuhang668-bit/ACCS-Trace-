import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Form, Input, Modal, Space, Table, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ArrowRight, FolderPlus, Plus } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, errorMessage } from '../api'
import { EmptyState, PageError, PageLoading } from '../components/QueryState'
import { PageHeader } from '../components/PageHeader'
import { formatDate } from '../format'
import { isValidProjectKey } from '../projectScope'
import type { ProjectSummary } from '../types'

interface CreateValues {
  name: string
  description?: string
  project_key?: string
}

export function ProjectsPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [form] = Form.useForm<CreateValues>()
  const [open, setOpen] = useState(false)
  const [messageApi, contextHolder] = message.useMessage()
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.listProjects })
  const createProject = useMutation({
    mutationFn: (values: CreateValues) =>
      api.createProject({
        name: values.name,
        description: values.description ?? '',
        ...(values.project_key?.trim() ? { project_key: values.project_key.trim() } : {}),
      }),
    onSuccess: async (project) => {
      await queryClient.invalidateQueries({ queryKey: ['projects'] })
      setOpen(false)
      form.resetFields()
      messageApi.success('Project created')
      navigate(`/projects/${project.id}`)
    },
    onError: (error) => messageApi.error(errorMessage(error)),
  })

  const columns: ColumnsType<ProjectSummary> = [
    {
      title: 'Project',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, project) => (
        <div className="primary-cell">
          <Link to={`/projects/${project.id}`}>{name}</Link>
          <span>{project.description || 'No description'}</span>
        </div>
      ),
    },
    { title: 'Datasets', dataIndex: 'revision_count', key: 'revisions', width: 110 },
    { title: 'Runs', dataIndex: 'run_count', key: 'runs', width: 90 },
    {
      title: 'Created',
      dataIndex: 'created_at',
      key: 'created',
      width: 170,
      render: formatDate,
    },
    {
      title: '',
      key: 'open',
      align: 'right',
      width: 58,
      render: (_, project) => (
        <Button
          type="text"
          aria-label={`Open ${project.name}`}
          icon={<ArrowRight size={17} />}
          onClick={() => navigate(`/projects/${project.id}`)}
        />
      ),
    },
  ]

  return (
    <>
      {contextHolder}
      <PageHeader
        title="Projects"
        description="Issue-Commit recovery workspaces and their auditable run history."
        actions={
          <Button type="primary" icon={<Plus size={17} />} onClick={() => setOpen(true)}>
            New project
          </Button>
        }
      />
      {projects.isPending ? (
        <PageLoading />
      ) : projects.isError ? (
        <PageError error={projects.error} retry={() => void projects.refetch()} />
      ) : (
        <section className="panel table-panel" aria-label="Project list">
          <Table
            rowKey="id"
            columns={columns}
            dataSource={projects.data}
            pagination={false}
            scroll={{ x: 720 }}
            locale={{
              emptyText: (
                <EmptyState description="No projects yet. Create one to import Issue and Commit data." />
              ),
            }}
          />
        </section>
      )}
      <Modal
        title={
          <Space><FolderPlus size={18} /><span>Create project</span></Space>
        }
        open={open}
        onCancel={() => setOpen(false)}
        okText="Create project"
        confirmLoading={createProject.isPending}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={(values) => createProject.mutate(values)}>
          <Form.Item
            label="Project name"
            name="name"
            rules={[{ required: true, whitespace: true }, { min: 2 }, { max: 160 }]}
          >
            <Input autoFocus placeholder="Apache Tika" />
          </Form.Item>
          <Form.Item label="Description" name="description" rules={[{ max: 2000 }]}>
            <Input.TextArea rows={3} placeholder="Optional project context" />
          </Form.Item>
          <Form.Item
            label="Project key"
            name="project_key"
            extra="Used to match project-scoped model releases, for example TIKA."
            rules={[
              { max: 255 },
              {
                validator: async (_, value: string | undefined) => {
                  if (!value?.trim() || isValidProjectKey(value)) return
                  throw new Error('Use letters, numbers, underscores, #, and optional / separators.')
                },
              },
            ]}
          >
            <Input placeholder="TIKA" autoComplete="off" />
          </Form.Item>
          {createProject.isError && (
            <Typography.Text type="danger">{errorMessage(createProject.error)}</Typography.Text>
          )}
        </Form>
      </Modal>
    </>
  )
}
