import { Button, Drawer, Grid, Layout, Menu, Tooltip, Typography } from 'antd'
import { Boxes, FolderKanban, Menu as MenuIcon, Network, X } from 'lucide-react'
import { useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'

const { Header, Sider, Content } = Layout

const navigation = [
  { key: '/projects', icon: <FolderKanban size={18} />, label: 'Projects' },
  { key: '/models', icon: <Boxes size={18} />, label: 'Model releases' },
]

function Navigation({ onSelect }: { onSelect?: () => void }) {
  const location = useLocation()
  const navigate = useNavigate()
  const selected = location.pathname.startsWith('/models') ? '/models' : '/projects'
  return (
    <Menu
      mode="inline"
      selectedKeys={[selected]}
      items={navigation}
      onClick={({ key }) => {
        navigate(key)
        onSelect?.()
      }}
    />
  )
}

function Brand() {
  return (
    <div className="brand" aria-label="ACCS-Trace">
      <span className="brand-mark"><Network size={19} /></span>
      <span>
        <strong>ACCS-Trace</strong>
        <small>Link recovery workspace</small>
      </span>
    </div>
  )
}

export function AppShell() {
  const screens = Grid.useBreakpoint()
  const desktop = screens.lg !== false
  const [drawerOpen, setDrawerOpen] = useState(false)

  return (
    <Layout className="app-layout">
      {desktop && (
        <Sider width={232} theme="light" className="app-sider">
          <Brand />
          <Navigation />
          <div className="release-scope">
            <span>Release scope</span>
            <strong>Recover</strong>
            <small>Adapt and Evaluate are not enabled in this release.</small>
          </div>
        </Sider>
      )}
      <Layout>
        <Header className="app-header">
          {!desktop && (
            <Tooltip title="Open navigation">
              <Button
                aria-label="Open navigation"
                type="text"
                icon={<MenuIcon size={20} />}
                onClick={() => setDrawerOpen(true)}
              />
            </Tooltip>
          )}
          {!desktop && <Brand />}
          <Typography.Text className="local-indicator">Local workspace</Typography.Text>
        </Header>
        <Content className="app-content"><Outlet /></Content>
      </Layout>
      <Drawer
        className="mobile-nav"
        placement="left"
        size={280}
        open={!desktop && drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={<Brand />}
        closeIcon={<X size={18} />}
      >
        <Navigation onSelect={() => setDrawerOpen(false)} />
        <div className="release-scope">
          <span>Release scope</span>
          <strong>Recover</strong>
          <small>Adapt and Evaluate are not enabled in this release.</small>
        </div>
      </Drawer>
    </Layout>
  )
}
