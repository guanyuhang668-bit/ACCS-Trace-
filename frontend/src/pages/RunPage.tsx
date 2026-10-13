import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Col,
  Descriptions,
  Divider,
  Drawer,
  Grid,
  Input,
  Modal,
  Progress,
  Radio,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tabs,
  Tag,
  Timeline,
  Tooltip,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  ArrowLeft,
  Check,
  Download,
  Eye,
  FileJson,
  FileSpreadsheet,
  Search,
  SkipForward,
  Square,
  X,
} from 'lucide-react'
import { lazy, Suspense, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, ApiError, errorMessage } from '../api'
import { PageError, PageLoading } from '../components/QueryState'
import { PageHeader } from '../components/PageHeader'
import { ReliabilityTag, ReviewTag, RunStatusTag, ScoreDisplay } from '../components/Status'
import { formatDate, shortId, titleCase } from '../format'
import type {
  CandidateSummary,
  ReliabilityLevel,
  ReviewStatus,
} from '../types'

const terminalStatuses = ['completed', 'failed', 'cancelled', 'interrupted']
const ScoreChart = lazy(() => import('../components/ScoreChart').then((module) => ({ default: module.ScoreChart })))

export function RunPage() {
  const { runId = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [messageApi, contextHolder] = message.useMessage()
  const [selectedCandidate, setSelectedCandidate] = useState<string | null>(null)
  const [reliability, setReliability] = useState<ReliabilityLevel | undefined>()
  const [review, setReview] = useState<ReviewStatus | undefined>()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const [reviewNote, setReviewNote] = useState('')
  const [exportOpen, setExportOpen] = useState(false)
  const [exportFormat, setExportFormat] = useState<'csv' | 'json'>('csv')
  const [includeRejected, setIncludeRejected] = useState(true)
  const screens = Grid.useBreakpoint()
  const desktopDrawer = screens.md !== false

  const run = useQuery({
    queryKey: ['run', runId],
    queryFn: () => api.getRun(runId),
    enabled: Boolean(runId),
    refetchInterval: (query) =>
      query.state.data && !terminalStatuses.includes(query.state.data.status) ? 1200 : false,
  })
  const models = useQuery({ queryKey: ['models'], queryFn: api.listModels })
  const candidates = useQuery({
    queryKey: ['candidates', runId, reliability, review, search, page, pageSize],
    queryFn: () =>
      api.listCandidates(runId, {
        reliability,
        review,
        search,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      }),
    enabled: run.data?.status === 'completed',
  })
  const analytics = useQuery({
    queryKey: ['analytics', runId],
    queryFn: () => api.getAnalytics(runId),
    enabled: run.data?.status === 'completed',
  })
  const detail = useQuery({
    queryKey: ['candidate', selectedCandidate],
    queryFn: () => api.getCandidate(selectedCandidate ?? ''),
    enabled: Boolean(selectedCandidate),
  })

  const currentModel = models.data?.find((item) => item.id === run.data?.model_release_id)

  const cancel = useMutation({
    mutationFn: () => api.cancelRun(runId),
    onSuccess: (updated) => queryClient.setQueryData(['run', runId], updated),
    onError: (error) => messageApi.error(errorMessage(error)),
  })
  const retry = useMutation({
    mutationFn: () => api.retryRun(runId),
    onSuccess: (updated) => {
      queryClient.setQueryData(['run', runId], updated)
      messageApi.success('Run queued for retry')
    },
    onError: (error) => messageApi.error(errorMessage(error)),
  })
  const decide = useMutation({
    mutationFn: (decision: ReviewStatus) => {
      if (!selectedCandidate || !detail.data) throw new Error('Candidate details are not loaded.')
      return api.reviewCandidate(selectedCandidate, {
        decision,
        note: reviewNote,
        reviewer: 'local-user',
        expected_version: detail.data.review_version,
      })
    },
    onSuccess: async (updated) => {
      queryClient.setQueryData(['candidate', selectedCandidate], updated)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['candidates', runId] }),
        queryClient.invalidateQueries({ queryKey: ['analytics', runId] }),
      ])
      setReviewNote('')
      messageApi.success(`Marked ${updated.review_status}`)
    },
    onError: async (error) => {
      if (error instanceof ApiError && error.status === 409) {
        await detail.refetch()
      }
      messageApi.error(errorMessage(error))
    },
  })
  const exportRun = useMutation({
    mutationFn: () => api.createExport(runId, exportFormat, includeRejected),
    onSuccess: (artifact) => {
      setExportOpen(false)
      window.location.assign(`/api/exports/${artifact.id}/download`)
      messageApi.success(`Prepared ${artifact.row_count} rows for download`)
    },
    onError: (error) => messageApi.error(errorMessage(error)),
  })

  const columns: ColumnsType<CandidateSummary> = [
    {
      title: 'Issue',
      dataIndex: 'issue_key',
      key: 'issue',
      width: 150,
      render: (value: string, item) => (
        <div className="primary-cell compact"><strong>{value}</strong><span>{item.issue_title}</span></div>
      ),
    },
    {
      title: 'Commit',
      dataIndex: 'commit_sha',
      key: 'commit',
      render: (value: string, item) => (
        <div className="primary-cell compact"><code>{shortId(value)}</code><span>{item.commit_title}</span></div>
      ),
    },
    {
      title: analytics.data?.calibration_validated ? 'Probability' : 'Raw score',
      key: 'score',
      width: 120,
      sorter: false,
      render: (_, item) => <ScoreDisplay raw={item.raw_score} calibrated={item.calibrated_probability} compact />,
    },
    {
      title: 'Triage',
      dataIndex: 'reliability_level',
      key: 'triage',
      width: 125,
      render: (value: ReliabilityLevel) => <ReliabilityTag value={value} />,
    },
    {
      title: 'Review',
      dataIndex: 'review_status',
      key: 'review',
      width: 115,
      render: (value: ReviewStatus) => <ReviewTag value={value} />,
    },
    {
      title: '',
      key: 'view',
      width: 54,
      align: 'right',
      render: (_, item) => (
        <Tooltip title="Inspect candidate">
          <Button
            type="text"
            icon={<Eye size={17} />}
            aria-label={`Inspect ${item.issue_key} and ${shortId(item.commit_sha)}`}
            onClick={(event) => {
              event.stopPropagation()
              setSelectedCandidate(item.id)
            }}
          />
        </Tooltip>
      ),
    },
  ]

  if (run.isPending) return <PageLoading />
  if (run.isError) return <PageError error={run.error} retry={() => void run.refetch()} />

  const summary = run.data.summary_json
  const summaryNumber = (key: string, fallback = 0) =>
    typeof summary[key] === 'number' ? summary[key] as number : fallback
  const statusContent = (
    <section className="panel run-progress-panel">
      <div className="progress-copy">
        <RunStatusTag value={run.data.status} />
        <h2>{run.data.stage}</h2>
        <p>Run <code>{shortId(run.data.id)}</code> keeps its configuration and model identity in the local audit store.</p>
      </div>
      <Progress
        type="circle"
        percent={run.data.progress}
        status={run.data.status === 'failed' ? 'exception' : run.data.status === 'completed' ? 'success' : 'active'}
        size={132}
      />
      {run.data.error_message && <Alert type="error" showIcon title={run.data.error_code ?? 'Run failed'} description={run.data.error_message} />}
      {(run.data.status === 'failed' || run.data.status === 'interrupted') && (
        <Button type="primary" loading={retry.isPending} onClick={() => retry.mutate()}>
          Retry run
        </Button>
      )}
      {!terminalStatuses.includes(run.data.status) && (
        <Button danger icon={<Square size={15} />} loading={cancel.isPending} onClick={() => cancel.mutate()}>
          Cancel run
        </Button>
      )}
    </section>
  )

  const candidatesTab = (
    <div className="workspace-section">
      <div className="candidate-toolbar">
        <Input.Search
          allowClear
          prefix={<Search size={15} />}
          placeholder="Issue key or commit SHA"
          onSearch={(value) => { setSearch(value.trim()); setPage(1) }}
          className="candidate-search"
        />
        <Select
          allowClear
          placeholder="All triage levels"
          value={reliability}
          onChange={(value) => { setReliability(value); setPage(1) }}
          options={(['recommended', 'review', 'rejected'] as ReliabilityLevel[]).map((value) => ({ value, label: titleCase(value) }))}
        />
        <Select
          allowClear
          placeholder="All review states"
          value={review}
          onChange={(value) => { setReview(value); setPage(1) }}
          options={(['unreviewed', 'accepted', 'rejected', 'skipped'] as ReviewStatus[]).map((value) => ({ value, label: titleCase(value) }))}
        />
      </div>
      {!analytics.data?.calibration_validated && (
        <Alert
          type="warning"
          showIcon
          className="scope-alert"
          title="Raw model scores"
          description="No deployment-validated calibration profile was applied. Values in this run must not be interpreted as calibrated probabilities."
        />
      )}
      <Table
        rowKey="id"
        loading={candidates.isPending}
        columns={columns}
        dataSource={candidates.data?.items ?? []}
        scroll={{ x: 940 }}
        onRow={(item) => ({ onClick: () => setSelectedCandidate(item.id), className: 'clickable-row' })}
        pagination={{
          current: page,
          pageSize,
          total: candidates.data?.total ?? 0,
          showSizeChanger: true,
          showTotal: (total) => `${total} candidates`,
          onChange: (nextPage, nextSize) => { setPage(nextPage); setPageSize(nextSize) },
        }}
        locale={{ emptyText: candidates.isError ? errorMessage(candidates.error) : 'No candidates match these filters.' }}
      />
    </div>
  )

  const reliabilityTab = (
    <div className="workspace-section">
      <div className="section-heading">
        <div><h2>Score distribution</h2><p>Candidate counts across fixed score intervals for this run.</p></div>
        <Tag color={analytics.data?.calibration_validated ? 'green' : 'gold'}>
          {analytics.data?.calibration_validated ? 'Deployment calibrated' : 'Raw scores'}
        </Tag>
      </div>
      {analytics.isError ? (
        <Alert type="error" showIcon title={errorMessage(analytics.error)} />
      ) : (
        <div className="chart-wrap">
          <Suspense fallback={<div className="chart-loading">Loading score distribution…</div>}>
            <ScoreChart
              bins={analytics.data?.score_bins ?? []}
              calibrationValidated={analytics.data?.calibration_validated ?? false}
            />
          </Suspense>
        </div>
      )}
      <Descriptions bordered size="small" column={{ xs: 1, sm: 2 }}>
        <Descriptions.Item label="Source domain">{analytics.data?.source_domain ?? 'Loading'}</Descriptions.Item>
        <Descriptions.Item label="Calibration">
          {analytics.data?.calibration_validated ? 'Deployment validated' : 'Not deployment validated'}
        </Descriptions.Item>
      </Descriptions>
    </div>
  )

  return (
    <>
      {contextHolder}
      <PageHeader
        eyebrow={
          <button className="link-button" onClick={() => navigate(`/projects/${run.data.project_id}`)}>
            <ArrowLeft size={14} />Project workspace
          </button>
        }
        title={<Space wrap>Recovery run <code>{shortId(run.data.id)}</code><RunStatusTag value={run.data.status} /></Space>}
        description={`${run.data.stage} · created ${formatDate(run.data.created_at)}`}
        actions={run.data.status === 'completed' ? (
          <Button type="primary" icon={<Download size={16} />} onClick={() => setExportOpen(true)}>Export</Button>
        ) : undefined}
      />
      {currentModel?.is_demo && (
        <Alert
          className="scope-alert page-alert"
          type="warning"
          showIcon
          title="Demo runtime — not an ACCS model"
          description="This run verifies the product workflow with deterministic synthetic scoring; it is not research evidence."
        />
      )}
      {run.data.status !== 'completed' ? statusContent : (
        <>
          <Row gutter={[12, 12]} className="summary-strip">
            <Col xs={12} lg={6}><div className="metric"><Statistic title="Candidates" value={summaryNumber('candidate_count', candidates.data?.total ?? 0)} /></div></Col>
            <Col xs={12} lg={6}><div className="metric metric-positive"><Statistic title="Recommended" value={summaryNumber('recommended')} /></div></Col>
            <Col xs={12} lg={6}><div className="metric metric-review"><Statistic title="Review" value={summaryNumber('review')} /></div></Col>
            <Col xs={12} lg={6}><div className="metric metric-muted"><Statistic title="Rejected" value={summaryNumber('rejected')} /></div></Col>
          </Row>
          <section className="panel workspace-panel">
            <Tabs
              items={[
                { key: 'candidates', label: 'Candidates', children: candidatesTab },
                { key: 'reliability', label: 'Reliability', children: reliabilityTab },
              ]}
            />
          </section>
        </>
      )}

      <Drawer
        title="Candidate review"
        size={desktopDrawer ? 680 : '100%'}
        open={Boolean(selectedCandidate)}
        onClose={() => { setSelectedCandidate(null); setReviewNote('') }}
        destroyOnHidden
      >
        {detail.isPending ? <PageLoading /> : detail.isError ? (
          <PageError error={detail.error} retry={() => void detail.refetch()} />
        ) : detail.data ? (
          <div className="candidate-detail">
            <div className="detail-title-row">
              <div><span>Issue</span><h2>{detail.data.issue_key}</h2></div>
              <ReliabilityTag value={detail.data.reliability_level} />
            </div>
            <div className="evidence-block"><h3>Issue text</h3><p>{detail.data.issue_text}</p></div>
            <div className="evidence-block"><h3>Commit <code>{shortId(detail.data.commit_sha)}</code></h3><p>{detail.data.commit_text}</p></div>
            {detail.data.commit_code && <div className="evidence-block code-block"><h3>Change evidence</h3><pre>{detail.data.commit_code}</pre></div>}
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="Score">
                <ScoreDisplay raw={detail.data.raw_score} calibrated={detail.data.calibrated_probability} />
              </Descriptions.Item>
              <Descriptions.Item label="Model">{detail.data.model_name} {detail.data.model_version}</Descriptions.Item>
              <Descriptions.Item label="Source domain">{detail.data.source_domain}</Descriptions.Item>
              <Descriptions.Item label="Issue created">{formatDate(detail.data.issue_created_at)}</Descriptions.Item>
              <Descriptions.Item label="Commit date">{formatDate(detail.data.commit_date)}</Descriptions.Item>
              <Descriptions.Item label="Time delta">{(detail.data.time_delta_hours / 24).toFixed(1)} days</Descriptions.Item>
              <Descriptions.Item label="Review state"><ReviewTag value={detail.data.review_status} /></Descriptions.Item>
            </Descriptions>
            <Divider />
            <h3>Reviewer decision</h3>
            <Input.TextArea
              rows={3}
              maxLength={2000}
              showCount
              value={reviewNote}
              onChange={(event) => setReviewNote(event.target.value)}
              placeholder="Optional audit note"
            />
            <Space wrap className="review-actions">
              <Button type="primary" icon={<Check size={16} />} loading={decide.isPending} onClick={() => decide.mutate('accepted')}>Accept</Button>
              <Button danger icon={<X size={16} />} loading={decide.isPending} onClick={() => decide.mutate('rejected')}>Reject</Button>
              <Button icon={<SkipForward size={16} />} loading={decide.isPending} onClick={() => decide.mutate('skipped')}>Skip</Button>
            </Space>
            {detail.data.decisions.length > 0 && (
              <>
                <Divider />
                <h3>Decision history</h3>
                <Timeline
                  items={detail.data.decisions.map((decision) => ({
                    color: decision.decision === 'accepted' ? 'green' : decision.decision === 'rejected' ? 'red' : 'blue',
                    content: (
                      <div><strong>{titleCase(decision.decision)}</strong> · {formatDate(decision.created_at)}<p>{decision.note || 'No note'}</p></div>
                    ),
                  }))}
                />
              </>
            )}
          </div>
        ) : null}
      </Drawer>

      <Modal
        title="Export recovery results"
        open={exportOpen}
        onCancel={() => setExportOpen(false)}
        okText="Create and download"
        okButtonProps={{ icon: <Download size={16} />, loading: exportRun.isPending }}
        onOk={() => exportRun.mutate()}
      >
        <div className="export-options">
          <Typography.Text strong>Format</Typography.Text>
          <Radio.Group value={exportFormat} onChange={(event) => setExportFormat(event.target.value)}>
            <Radio.Button value="csv"><Space><FileSpreadsheet size={15} />CSV</Space></Radio.Button>
            <Radio.Button value="json"><Space><FileJson size={15} />JSON</Space></Radio.Button>
          </Radio.Group>
          <a href={`/api/runs/${runId}/manifest`} target="_blank" rel="noreferrer">
            <Button icon={<FileJson size={15} />}>View run manifest</Button>
          </a>
          <div className="switch-row">
            <div><strong>Include rejected candidates</strong><span>Preserve the complete triage record.</span></div>
            <Switch checked={includeRejected} onChange={setIncludeRejected} />
          </div>
        </div>
      </Modal>
    </>
  )
}
