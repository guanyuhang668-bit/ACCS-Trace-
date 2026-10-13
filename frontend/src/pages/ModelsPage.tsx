import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Descriptions, Empty, Form, Input, Modal, Space, Table, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { Boxes, CheckCircle2, CircleAlert, DatabaseZap, PackagePlus, ShieldCheck, ShieldQuestion } from 'lucide-react'
import { useState } from 'react'
import { api, errorMessage } from '../api'
import { PageError, PageLoading } from '../components/QueryState'
import { PageHeader } from '../components/PageHeader'
import { modelScope, modelScopeDescription } from '../projectScope'
import type { CalibrationProfile, ModelRelease } from '../types'

function metadataText(model: ModelRelease, ...keys: string[]): string | undefined {
  for (const key of keys) {
    const value = model.metadata_json[key]
    if (typeof value === 'string' && value.trim()) return value
    if (value && typeof value === 'object') return 'Embedded manifest metadata'
  }
  return undefined
}

function packageEvidence(model: ModelRelease) {
  if (model.is_demo) {
    return {
      path: 'Built-in demo (no checkpoint)',
      manifest: 'Not applicable',
      checksum: 'Not applicable',
      inputSchema: metadataText(model, 'input_schema') ?? 'Not declared',
      complete: false,
    }
  }
  const path = model.package_path ?? metadataText(model, 'package_path', 'package_root')
  const manifest = model.manifest_path ?? metadataText(model, 'manifest_path', 'package_manifest', 'manifest')
  const checksum = model.checksum_sha256
    ?? metadataText(model, 'checksum_sha256', 'package_sha256', 'sha256')
  return {
    path: path ?? 'Not exposed by current API',
    manifest: manifest ?? 'Not exposed by current API',
    checksum: checksum ?? 'Not exposed by current API',
    inputSchema: metadataText(model, 'input_schema')
      ?? (model.runtime_kind === 'accs_torchscript_v1'
        ? 'issue-commit dual-input · 512 tokens'
        : 'Not declared'),
    complete: Boolean(path && manifest && checksum),
  }
}

function EvidenceValue({ value }: { value: string }) {
  const unavailable = value.startsWith('Not exposed') || value === 'Not declared'
  return unavailable ? (
    <Typography.Text type="warning">{value}</Typography.Text>
  ) : (
    <Typography.Text code copyable={value !== 'Not applicable'}>{value}</Typography.Text>
  )
}

export function ModelsPage() {
  const queryClient = useQueryClient()
  const [form] = Form.useForm<{ packageName: string }>()
  const [messageApi, contextHolder] = message.useMessage()
  const [registerOpen, setRegisterOpen] = useState(false)
  const models = useQuery({ queryKey: ['models'], queryFn: api.listModels })
  const registerModel = useMutation({
    mutationFn: ({ packageName }: { packageName: string }) => api.registerModel(packageName),
    onSuccess: async (model) => {
      await queryClient.invalidateQueries({ queryKey: ['models'] })
      setRegisterOpen(false)
      form.resetFields()
      messageApi.success(`Registered ${model.name} ${model.version}`)
    },
    onError: (error) => messageApi.error(errorMessage(error)),
  })
  if (models.isPending) return <PageLoading />
  if (models.isError) return <PageError error={models.error} retry={() => void models.refetch()} />

  const profileColumns: ColumnsType<CalibrationProfile> = [
    { title: 'Profile', dataIndex: 'name' },
    { title: 'Source domain', dataIndex: 'source_domain' },
    {
      title: 'Thresholds',
      render: (_, item) => `${item.threshold_low.toFixed(2)} / ${item.threshold_high.toFixed(2)}`,
      width: 130,
    },
    {
      title: 'Deployment status',
      dataIndex: 'is_deployment_validated',
      width: 170,
      render: (value: boolean) => value ? <Tag color="green">Validated</Tag> : <Tag color="gold">Not validated</Tag>,
    },
  ]

  return (
    <>
      {contextHolder}
      <PageHeader
        title="Model releases"
        description="Frozen scoring runtimes and deployment-calibration evidence available to Recover."
        actions={
          <Button type="primary" icon={<PackagePlus size={16} />} onClick={() => setRegisterOpen(true)}>
            Register package
          </Button>
        }
      />
      <Alert
        type="info"
        showIcon
        className="page-alert"
        title="Controlled local package registration"
        description="Registration accepts one directory already present under data/models, verifies its manifest and hashes, then loads the TorchScript runtime on CPU. It never creates a deployment-calibration profile."
      />
      <div className="model-list">
        {models.data.length === 0 && <section className="panel"><Empty description="No model releases registered." /></section>}
        {models.data.map((model: ModelRelease) => {
          const evidence = packageEvidence(model)
          const scope = modelScope(model)
          const supportedKeys = scope.kind === 'scoped'
            ? Array.from(scope.keys).join(', ')
            : scope.kind === 'dynamic'
              ? 'Any valid configured key (no fixed list)'
              : scope.kind === 'global'
                ? '* (demo workflow only)'
                : 'Not declared · release blocked'
          return <section className="panel model-panel" key={model.id}>
            <div className="model-heading">
              <div className="model-icon"><Boxes size={21} /></div>
              <div><h2>{model.name} <span>{model.version}</span></h2><p>{model.source_domain}</p></div>
              <Space wrap>
                <Tag color={model.is_available ? 'green' : 'red'} icon={model.is_available ? <CheckCircle2 size={13} /> : <CircleAlert size={13} />}>
                  {model.is_available ? 'Listed available' : 'Unavailable'}
                </Tag>
                <Tag color={model.is_demo ? 'gold' : 'cyan'}>{model.is_demo ? 'DEMO' : model.runtime_kind.toUpperCase()}</Tag>
                {!model.is_demo && (
                  <Tag
                    color={evidence.complete ? 'green' : 'gold'}
                    icon={evidence.complete ? <ShieldCheck size={13} /> : <ShieldQuestion size={13} />}
                  >
                    {evidence.complete ? 'Package evidence listed' : 'Package evidence incomplete'}
                  </Tag>
                )}
              </Space>
            </div>
            {model.is_demo && (
              <Alert
                type="warning"
                showIcon
                className="scope-alert"
                title="Demo runtime — not an ACCS model"
                description="Deterministic heuristic scores support workflow and test verification only."
              />
            )}
            {scope.kind === 'dynamic' && (
              <Alert
                type="info"
                showIcon
                className="scope-alert"
                title="Dynamic project-key scope"
                description="This only declares how preprocessing handles a configured project key. It does not establish cross-domain validity; the release source domain and package evidence still constrain use."
              />
            )}
            <Descriptions size="small" column={{ xs: 1, sm: 2 }}>
              <Descriptions.Item label="Runtime"><Space><DatabaseZap size={14} />{model.runtime_kind}</Space></Descriptions.Item>
              <Descriptions.Item label="Release ID"><code>{model.id}</code></Descriptions.Item>
              <Descriptions.Item label="Scope">{modelScopeDescription(model)}</Descriptions.Item>
              <Descriptions.Item label="Supported project keys">
                <Typography.Text type={scope.kind === 'missing' ? 'warning' : undefined}>
                  {supportedKeys}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="Local package path"><EvidenceValue value={evidence.path} /></Descriptions.Item>
              <Descriptions.Item label="Manifest"><EvidenceValue value={evidence.manifest} /></Descriptions.Item>
              <Descriptions.Item label="SHA-256"><EvidenceValue value={evidence.checksum} /></Descriptions.Item>
              <Descriptions.Item label="Input schema"><EvidenceValue value={evidence.inputSchema} /></Descriptions.Item>
            </Descriptions>
            <h3>Calibration profiles</h3>
            <Table
              rowKey="id"
              size="small"
              columns={profileColumns}
              dataSource={model.calibration_profiles}
              pagination={false}
              scroll={{ x: 650 }}
              locale={{ emptyText: 'No deployment calibration profiles.' }}
            />
          </section>
        })}
      </div>
      <Modal
        title={<Space><PackagePlus size={18} /><span>Register ACCS package</span></Space>}
        open={registerOpen}
        onCancel={() => setRegisterOpen(false)}
        okText="Validate and register"
        confirmLoading={registerModel.isPending}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Alert
          type="warning"
          showIcon
          className="scope-alert"
          title="Local CPU dependencies are required"
          description="The directory must contain manifest.json and all declared artifacts. PyTorch, tokenizer, preprocessing, checksum, and CPU tensor-contract failures stop registration without saving a release."
        />
        <Form
          form={form}
          layout="vertical"
          onFinish={({ packageName }) => registerModel.mutate({ packageName })}
        >
          <Form.Item
            name="packageName"
            label="Package directory name"
            extra="Resolved as data/models/<directory>/manifest.json"
            rules={[
              { required: true, whitespace: true },
              { max: 128 },
              {
                pattern: /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/,
                message: 'Use one directory name containing only letters, numbers, hyphens, or underscores.',
              },
            ]}
          >
            <Input autoFocus placeholder="accs_release_v1" autoComplete="off" />
          </Form.Item>
          {registerModel.isError && <Alert type="error" showIcon title={errorMessage(registerModel.error)} />}
        </Form>
      </Modal>
    </>
  )
}
