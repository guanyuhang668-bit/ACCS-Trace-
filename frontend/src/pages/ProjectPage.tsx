import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Col,
  Divider,
  Form,
  Input,
  InputNumber,
  Progress,
  Row,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { UploadFile } from 'antd/es/upload/interface'
import { ArrowLeft, Check, Database, FileUp, Play, RotateCw } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, errorMessage } from '../api'
import { PageError, PageLoading } from '../components/QueryState'
import { PageHeader } from '../components/PageHeader'
import { RunStatusTag } from '../components/Status'
import { formatDate, shortId } from '../format'
import {
  modelMatchesProject,
  modelScope,
  modelScopeDescription,
  normalizeProjectKey,
  projectKeyFor,
  isValidProjectKey,
} from '../projectScope'
import type { DatasetRevision, ProjectDetail, RunSummary } from '../types'

interface RunValues {
  revisionId: string
  modelId?: string
  calibrationId?: string
  daysBefore: number
  daysAfter: number
  unresolvedWindowDays: number
  maxCandidatesPerIssue: number
}

export function ProjectPage() {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [messageApi, contextHolder] = message.useMessage()
  const [issuesFiles, setIssuesFiles] = useState<UploadFile[]>([])
  const [commitsFiles, setCommitsFiles] = useState<UploadFile[]>([])
  const [projectKeyDraft, setProjectKeyDraft] = useState<string | null>(null)
  const [runForm] = Form.useForm<RunValues>()
  const selectedModelId = Form.useWatch('modelId', runForm)

  const project = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.getProject(projectId),
    enabled: Boolean(projectId),
  })
  const models = useQuery({ queryKey: ['models'], queryFn: api.listModels })
  const runs = useQuery({
    queryKey: ['runs', projectId],
    queryFn: () => api.listRuns(projectId),
    enabled: Boolean(projectId),
    refetchInterval: (query) =>
      query.state.data?.some((run) => !['completed', 'failed', 'cancelled'].includes(run.status))
        ? 1500
        : false,
  })

  const availableModels = useMemo(
    () => models.data?.filter((model) => model.is_available) ?? [],
    [models.data],
  )
  const projectKey = project.data ? projectKeyFor(project.data) : ''
  const matchingModels = useMemo(
    () => availableModels.filter((model) => modelMatchesProject(model, projectKey)),
    [availableModels, projectKey],
  )
  const blockedModels = useMemo(
    () => availableModels.filter((model) => !model.is_demo && modelScope(model).kind === 'missing'),
    [availableModels],
  )
  const selectedModel = matchingModels.find((model) => model.id === selectedModelId)

  useEffect(() => {
    if (!project.data) return
    if (!runForm.getFieldValue('revisionId') && project.data.revisions.length) {
      runForm.setFieldsValue({ revisionId: project.data.revisions.at(-1)?.id })
    }
  }, [project.data, runForm])

  useEffect(() => {
    if (selectedModelId && !matchingModels.some((model) => model.id === selectedModelId)) {
      runForm.setFieldsValue({ modelId: undefined, calibrationId: undefined })
    }
  }, [matchingModels, runForm, selectedModelId])

  const importData = useMutation({
    mutationFn: () => {
      const issues = issuesFiles[0]?.originFileObj
      const commits = commitsFiles[0]?.originFileObj
      if (!issues || !commits) throw new Error('Select both CSV files before importing.')
      return api.importDataset(projectId, issues, commits)
    },
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['project', projectId] })
      await queryClient.invalidateQueries({ queryKey: ['projects'] })
      setIssuesFiles([])
      setCommitsFiles([])
      runForm.setFieldValue('revisionId', result.revision.id)
      result.warnings.forEach((warning) => messageApi.warning(warning))
      messageApi.success(`Imported ${result.revision.issue_count} issues and ${result.revision.commit_count} commits`)
    },
    onError: (error) => messageApi.error(errorMessage(error)),
  })

  const createRun = useMutation({
    mutationFn: (values: RunValues) => {
      const model = matchingModels.find((item) => item.id === values.modelId)
      if (!model) throw new Error('Select a model release compatible with this project key.')
      const profile = model?.calibration_profiles.find(
        (item) => item.id === values.calibrationId && item.is_deployment_validated,
      )
      return api.createRun({
        project_id: projectId,
        dataset_revision_id: values.revisionId,
        model_release_id: model.id,
        calibration_profile_id: profile?.id ?? null,
        mode: 'recover',
        days_before: values.daysBefore,
        days_after: values.daysAfter,
        unresolved_window_days: values.unresolvedWindowDays,
        max_candidates_per_issue: values.maxCandidatesPerIssue,
      })
    },
    onSuccess: async (run) => {
      await queryClient.invalidateQueries({ queryKey: ['runs', projectId] })
      navigate(`/runs/${run.id}`)
    },
    onError: (error) => messageApi.error(errorMessage(error)),
  })

  const updateProjectKey = useMutation({
    mutationFn: (value: string) => api.updateProject(projectId, { project_key: value || null }),
    onSuccess: (updated) => {
      const normalized = updated.project_key ?? ''
      queryClient.setQueryData<ProjectDetail>(['project', projectId], (current) => (
        current ? { ...current, project_key: normalized || null } : current
      ))
      setProjectKeyDraft(null)
      runForm.setFieldsValue({ modelId: undefined, calibrationId: undefined })
      messageApi.success(normalized ? `Project key set to ${normalized}` : 'Project key cleared')
    },
    onError: (error) => messageApi.error(errorMessage(error)),
  })

  if (project.isPending) return <PageLoading />
  if (project.isError) return <PageError error={project.error} retry={() => void project.refetch()} />
  const projectKeyLocked = project.data.run_count > 0

  const saveProjectKeyDraft = () => {
    if (projectKeyLocked) {
      messageApi.warning('The project key is locked after the first run.')
      return
    }
    const normalized = normalizeProjectKey(projectKeyDraft ?? projectKey)
    if (normalized && !isValidProjectKey(normalized)) {
      messageApi.error('Use letters, numbers, underscores, #, and optional / separators.')
      return
    }
    updateProjectKey.mutate(normalized)
  }

  const revisionColumns: ColumnsType<DatasetRevision> = [
    { title: 'Revision', dataIndex: 'id', render: (value: string) => <code>{shortId(value)}</code> },
    { title: 'Issues', dataIndex: 'issue_count', width: 90 },
    { title: 'Commits', dataIndex: 'commit_count', width: 100 },
    { title: 'Imported', dataIndex: 'created_at', width: 170, render: formatDate },
  ]
  const runColumns: ColumnsType<RunSummary> = [
    {
      title: 'Run',
      dataIndex: 'id',
      render: (value: string) => <Link to={`/runs/${value}`}><code>{shortId(value)}</code></Link>,
    },
    { title: 'Status', dataIndex: 'status', render: (value) => <RunStatusTag value={value} /> },
    {
      title: 'Progress',
      key: 'progress',
      width: 170,
      render: (_, run) => <Progress percent={run.progress} size="small" status={run.status === 'failed' ? 'exception' : undefined} />,
    },
    { title: 'Created', dataIndex: 'created_at', width: 170, render: formatDate },
  ]

  const importTab = (
    <div className="workspace-section">
      <div className="section-heading">
        <div><h2>Dataset revisions</h2><p>CSV input is validated and stored as an immutable revision.</p></div>
      </div>
      <Row gutter={[16, 16]}>
        <Col xs={24} md={12}>
          <Upload.Dragger
            accept=".csv,text/csv"
            maxCount={1}
            fileList={issuesFiles}
            beforeUpload={() => false}
            onChange={({ fileList }) => setIssuesFiles(fileList.slice(-1))}
          >
            <FileUp size={25} />
            <strong>Issues CSV</strong>
            <span>Issue_KEY, Issue_Text, creationdate, resolutiondate</span>
          </Upload.Dragger>
        </Col>
        <Col xs={24} md={12}>
          <Upload.Dragger
            accept=".csv,text/csv"
            maxCount={1}
            fileList={commitsFiles}
            beforeUpload={() => false}
            onChange={({ fileList }) => setCommitsFiles(fileList.slice(-1))}
          >
            <FileUp size={25} />
            <strong>Commits CSV</strong>
            <span>Commit_SHA, Commit_Text, Commit_Code, commitdate</span>
          </Upload.Dragger>
        </Col>
      </Row>
      <div className="form-actions">
        <Button
          type="primary"
          icon={<Database size={16} />}
          loading={importData.isPending}
          disabled={!issuesFiles.length || !commitsFiles.length}
          onClick={() => importData.mutate()}
        >
          Validate and import
        </Button>
      </div>
      <Divider />
      <Table
        rowKey="id"
        columns={revisionColumns}
        dataSource={project.data.revisions}
        pagination={false}
        scroll={{ x: 620 }}
        locale={{ emptyText: 'No dataset revisions imported.' }}
      />
    </div>
  )

  const recoverTab = (
    <div className="workspace-section">
      <div className="section-heading">
        <div><h2>Recover missing links</h2><p>Generate candidates and score them with a frozen model release.</p></div>
        <Tag color="cyan">Recover</Tag>
      </div>
      <div className="project-scope-control">
        <div className="project-scope-copy">
          <Space wrap size={8}>
            <Typography.Text strong>Project key</Typography.Text>
            {projectKeyLocked && <Tag>Locked after first run</Tag>}
          </Space>
          <Typography.Text type="secondary">
            {projectKey || 'Not configured'}
          </Typography.Text>
          <Typography.Text type="secondary" className="project-scope-note">
            Used to match project-scoped model releases. It cannot be changed after the first run.
          </Typography.Text>
        </div>
        <Space.Compact className="project-scope-input">
          <Input
            aria-label="Project key"
            value={projectKeyDraft ?? projectKey}
            placeholder="TIKA"
            maxLength={255}
            disabled={projectKeyLocked}
            onChange={(event) => setProjectKeyDraft(event.target.value)}
            onPressEnter={saveProjectKeyDraft}
          />
          <Button
            type="default"
            icon={<Check size={16} />}
            loading={updateProjectKey.isPending}
            disabled={projectKeyLocked}
            onClick={saveProjectKeyDraft}
          >
            Set key
          </Button>
        </Space.Compact>
      </div>
      {models.isError && (
        <Alert
          className="scope-alert"
          type="error"
          showIcon
          title="Model releases could not be loaded"
          description={errorMessage(models.error)}
        />
      )}
      {!models.isPending && !models.isError && availableModels.length === 0 && (
        <Alert
          className="scope-alert"
          type="info"
          showIcon
          title="No model releases are available"
          description="Open Model releases and register a validated package before queueing a recovery run."
        />
      )}
      {!models.isPending && !models.isError && availableModels.length > 0 && matchingModels.length === 0 && (
        <Alert
          className="scope-alert"
          type="error"
          showIcon
          title="No compatible model release"
          description={
            projectKey
              ? `No available release declares support for project key ${projectKey}. Register a package with an explicit project key declaration.`
              : 'Set this project\'s key, then register a package with a matching project key declaration. Real releases without a declared key are blocked.'
          }
        />
      )}
      {blockedModels.length > 0 && (
        <Alert
          className="scope-alert"
          type="warning"
          showIcon
          title={`${blockedModels.length} real model release${blockedModels.length === 1 ? '' : 's'} blocked`}
          description="These releases do not declare a project key and are hidden from Recover until their package metadata is corrected."
        />
      )}
      {selectedModel?.is_demo && (
        <Alert
          className="scope-alert"
          type="warning"
          showIcon
          title="Demo runtime — not an ACCS model"
          description="Results are deterministic synthetic scores for workflow testing. Do not report them as ACCS evidence."
        />
      )}
      <Form
        form={runForm}
        layout="vertical"
        initialValues={{ daysBefore: 3, daysAfter: 14, unresolvedWindowDays: 90, maxCandidatesPerIssue: 200 }}
        onFinish={(values) => createRun.mutate(values)}
      >
        <Row gutter={16}>
          <Col xs={24} md={12}>
            <Form.Item name="revisionId" label="Dataset revision" rules={[{ required: true }]}>
              <Select
                placeholder="Select a revision"
                options={project.data.revisions.map((revision) => ({
                  value: revision.id,
                  label: `${shortId(revision.id)} · ${revision.issue_count} issues / ${revision.commit_count} commits`,
                }))}
              />
            </Form.Item>
          </Col>
          <Col xs={24} md={12}>
            <Form.Item name="modelId" label="Model release" rules={[{ required: true }]}>
              <Select
                loading={models.isPending}
                placeholder="Select a model"
                options={matchingModels.map((model) => ({
                  value: model.id,
                  label: `${model.name} ${model.version}${model.is_demo ? ' · DEMO' : ''}`,
                }))}
              />
            </Form.Item>
            {selectedModel && (
              <Typography.Text type="secondary" className="model-scope-hint">
                Release scope: {modelScopeDescription(selectedModel)}
              </Typography.Text>
            )}
          </Col>
          <Col xs={24} md={12}>
            <Form.Item name="calibrationId" label="Deployment calibration">
              <Select
                allowClear
                placeholder="Raw model score only"
                options={(selectedModel?.calibration_profiles ?? []).map((profile) => ({
                  value: profile.id,
                  label: profile.name,
                  disabled: !profile.is_deployment_validated,
                  title: profile.is_deployment_validated ? undefined : 'Not deployment validated',
                }))}
              />
            </Form.Item>
          </Col>
        </Row>
        <Row gutter={16}>
          <Col xs={12} md={6}>
            <Form.Item name="daysBefore" label="Days before" rules={[{ required: true }]}>
              <InputNumber min={0} max={365} className="full-width" />
            </Form.Item>
          </Col>
          <Col xs={12} md={6}>
            <Form.Item name="daysAfter" label="Days after" rules={[{ required: true }]}>
              <InputNumber min={0} max={365} className="full-width" />
            </Form.Item>
          </Col>
          <Col xs={12} md={6}>
            <Form.Item name="unresolvedWindowDays" label="Unresolved window (days)" rules={[{ required: true }]}>
              <InputNumber min={1} max={3650} className="full-width" />
            </Form.Item>
          </Col>
          <Col xs={12} md={6}>
            <Form.Item name="maxCandidatesPerIssue" label="Candidate limit" rules={[{ required: true }]}>
              <InputNumber min={1} max={5000} className="full-width" />
            </Form.Item>
          </Col>
        </Row>
        {!selectedModel?.calibration_profiles.some((profile) => profile.is_deployment_validated) && selectedModel && (
          <Alert
            className="scope-alert"
            type="info"
            showIcon
            title="Raw model score only"
            description="This release has no deployment-validated calibration profile. Scores will not be presented as probabilities."
          />
        )}
        <Button
          type="primary"
          htmlType="submit"
          icon={<Play size={16} />}
          loading={createRun.isPending}
          disabled={!project.data.revisions.length || !matchingModels.length || !selectedModelId}
        >
          Queue recovery run
        </Button>
      </Form>
    </div>
  )

  const runsTab = (
    <div className="workspace-section">
      <div className="section-heading">
        <div><h2>Run history</h2><p>Status, progress, and archived recovery results.</p></div>
        <Button type="text" icon={<RotateCw size={16} />} onClick={() => void runs.refetch()} aria-label="Refresh runs" />
      </div>
      <Table
        rowKey="id"
        loading={runs.isPending}
        columns={runColumns}
        dataSource={runs.data ?? []}
        pagination={{ pageSize: 10, hideOnSinglePage: true }}
        scroll={{ x: 700 }}
        locale={{ emptyText: 'No recovery runs yet.' }}
      />
    </div>
  )

  return (
    <>
      {contextHolder}
      <PageHeader
        eyebrow={<Link to="/projects"><Space size={5}><ArrowLeft size={14} />Projects</Space></Link>}
        title={project.data.name}
        description={project.data.description || 'Issue-Commit link recovery workspace'}
        actions={
          <Space wrap size={[10, 6]}>
            <Tag color={projectKey ? 'cyan' : 'default'}>
              Project key: {projectKey || 'Not configured'}
            </Tag>
            <Typography.Text type="secondary">
              {project.data.revision_count} revisions · {project.data.run_count} runs
            </Typography.Text>
          </Space>
        }
      />
      <section className="panel workspace-panel">
        <Tabs
          defaultActiveKey={project.data.revisions.length ? 'recover' : 'data'}
          items={[
            { key: 'data', label: 'Data', children: importTab },
            { key: 'recover', label: 'Recover', children: recoverTab },
            { key: 'runs', label: 'Runs', children: runsTab },
          ]}
        />
      </section>
    </>
  )
}
